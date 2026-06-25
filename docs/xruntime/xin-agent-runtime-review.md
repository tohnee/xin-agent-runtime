结论先行
我对仓库的判断是：
AgentScope 与 XRuntime 是“核心运行内核 + 企业扩展外壳”的关系，而不是两个并列运行时。
代码和文档都明确说明：XRuntime 不是 standalone runtime，而是挂载到 AgentScope FastAPI 应用之上的扩展层，提供协议适配、企业中间件、多租户、网关安全、指标、DAG 编排等能力；AgentScope 负责 Agent、模型调用、工具调用、Workspace、Storage、MessageBus、ChatService 等执行内核。代码入口 xruntime.__init__ 明确写明 XRuntime extends AgentScope，并通过 create_app(... extra_agent_middlewares=...) 与 mount_protocol_adapters(...) 组合使用。 
二者“部分解耦”，但不是完全解耦。
解耦点在于：XRuntime 通过 AgentScope 暴露的 create_app 扩展参数、协议适配器、统一请求模型和中间件工厂接入；AgentScope 的 Agent/Model/Workspace 本身不需要感知 XRuntime。强耦合点在于：XRuntime 深度依赖 AgentScope 的 FastAPI app、Storage、MessageBus、WorkspaceManager、ChatService run 流程和事件流语义；同时 build_xruntime_app 当前硬编码使用 RedisStorage、RedisMessageBus、LocalWorkspaceManager，运行时可替换性还不足。 
功能上互补性较好：AgentScope 偏“Agent 编程框架/运行内核”，XRuntime 偏“企业运行时网关/治理层”。
官方运维文档也给出了同样的表述：AgentScope 提供 Agent + ChatService + Storage，XRuntime 提供协议转换、安全、多租户、审计。 
作为企业级 Agent 开发运行时底座：目前已经具备雏形和不少关键模块，但距离“成熟企业级底座”还有差距。
主要差距集中在：多租户隔离粒度、生产级存储抽象、沙箱默认安全策略、权限与审计的强约束闭环、模型路由/降级/成本治理、插件安全边界、可观测性完整性、部署资产、测试覆盖与文档一致性。
一、仓库整体架构拆解
1. 顶层定位
pyproject.toml 当前项目名仍是 agentscope，并以 AgentScope 为主包，同时增加了 xruntime 和 xruntime_sdk 两个包。项目基础依赖包含模型 SDK、MCP、OTel、Socket.IO、工具解析、JSON Schema 等；XRuntime 作为 optional extra，依赖 agentscope[full]、claude-agent-sdk、pyyaml。 
这意味着仓库不是“从零实现一个新 runtime”，而是在 AgentScope 代码基线里加入 XRuntime 企业扩展。

2. AgentScope 核心层
AgentScope 的核心抽象主要包括：
模块	角色
agentscope.agent.Agent	单一核心 Agent 类，负责 ReAct loop、模型调用、工具调用、事件流、权限检查、上下文管理
agentscope.model.*	多模型 Provider 适配层
agentscope.formatter.*	各模型消息格式转换
agentscope.tool.*	工具系统
agentscope.middleware.*	Agent 生命周期切面
agentscope.workspace.*	Local / Docker / E2B 工作区与沙箱
agentscope.app.*	FastAPI 服务层，含 Storage、MessageBus、WorkspaceManager、ChatService、Session、Team、Scheduler 等
Agent 类构造函数中注入 model、toolkit、middlewares、state、offloader、model_config、context_config、react_config，这说明 AgentScope 的核心设计是“单 Agent 类 + 可组合依赖”。 
AgentScope 的中间件系统支持 reply、reasoning、acting、model_call、system_prompt 等关键生命周期切点，适合做审计、限流、预算、脱敏、工具执行拦截等企业治理。 

AgentScope 的 App 工厂 create_app 是企业化集成的重要扩展点：它要求传入 storage、message_bus、workspace_manager，并允许注入 extra_agent_middlewares、extra_agent_tools、custom_subagent_templates、custom_agent_cls。这为 XRuntime 作为扩展层提供了较干净的接入面。 

3. XRuntime 企业扩展层
XRuntime 的公开入口说明其提供：
协议适配：Anthropic Messages API、Claude Code SDK、OpenCode SDK；
企业中间件：审计、配额、RBAC、敏感信息脱敏；
多租户隔离；
DAG orchestrator；
模型解析器；
YAML 配置；
Prometheus 指标。 
create_xruntime_extension 返回给 AgentScope app 使用的扩展项，包括：
extra_agent_middlewares
adapter_registry
config
model_resolver
middleware_state_cache 
默认协议适配器注册了三类协议：Anthropic、Claude Code、OpenCode。 
企业中间件工厂会按配置注入 Audit、Quota、RBAC、SecretRedaction、KnowledgeMiddleware。 

4. 服务启动链路
build_xruntime_app 当前做了以下事情：
加载 XRuntimeConfig；
可选配置 OTel；
创建 XRuntime extension；
懒加载 AgentScope app、RedisStorage、RedisMessageBus、LocalWorkspaceManager；
基于第一个 tenant 构建 Redis key prefix；
创建 Redis storage 和 message bus；
创建 LocalWorkspaceManager；
调用 create_app(...) 注入企业中间件；
加载 Auth / RateLimit ASGI 中间件；
挂载 health / ready；
挂载协议适配路由。 
这是一条清晰的“AgentScope 内核 + XRuntime 外壳”装配链路。
二、逐个模型拆解
1. 模型抽象基类：ChatModelBase
ChatModelBase 定义了统一模型属性：credential、model name、stream、max_retries、retry_delay、context_size。
它还提供：

基于 _get_retryable_exceptions() 的 provider-specific retry；
从 _models/*.yaml 自动加载模型卡；
统一 __call__ 入口，将 messages、tools、tool_choice 转发给具体 _call_api。 
这个抽象是合理的：它把“模型配置、模型卡、重试、上下文大小、工具调用参数”统一起来，把 provider 差异下沉到子类。
2. 当前支持的 Chat Model Provider
agentscope.model.__init__ 导出了以下模型类：
AnthropicChatModel
DashScopeChatModel
DeepSeekChatModel
GeminiChatModel
OllamaChatModel
OpenAIChatModel
XAIChatModel
MoonshotChatModel
OpenAIResponseModel 
OpenAI Chat Completions
OpenAIChatModel 面向 OpenAI Chat Completions API，支持 max_tokens、thinking_enable、reasoning_effort、temperature、top_p、parallel_tool_calls 等参数，说明已考虑 GPT-5.x / o 系列 reasoning 参数和并行工具调用。 
评价：

优点：参数体系较完整，支持 reasoning effort，适合新一代模型。
风险：Chat Completions 与 Responses API 并存，企业 runtime 最终需要统一抽象 reasoning、tool result、multimodal、structured output 的行为差异，否则上层 Agent 行为会因模型后端不同而不一致。
OpenAI Responses
OpenAIResponseModel 被单独导出，说明仓库区分了 Chat Completions 与 Responses API 两套实现。 
评价：

优点：保留 OpenAI 新 API 的能力空间。
建议：企业底座应明确推荐 Responses API 作为长期路径，或至少在模型解析器中配置 provider capability，避免业务侧不知道该选哪个 OpenAI backend。
Anthropic
AnthropicChatModel 支持 max_tokens、thinking_enable、thinking_budget，默认 context_size 为 200000，并支持自定义 formatter 和 client_kwargs。 
评价：

优点：适配 Claude 的 thinking budget 语义；上下文默认较大；与 XRuntime 的 Anthropic 协议适配天然互补。
风险：XRuntime 的 /v1/messages 是协议入口，AgentScope 的 AnthropicChatModel 是模型调用出口，两者不要混淆。协议兼容不等于完全模拟 Anthropic server 行为，尤其在 tool use、streaming event、usage、stop_reason、cache control 等字段上需要持续对齐。
DashScope
DashScopeChatModel 使用 OpenAI-compatible endpoint，支持 text-only 和 multimodal，并提供 thinking、temperature、top_p、top_k 等参数。 
评价：

优点：对国内企业常用模型平台支持较好；OpenAI-compatible 实现降低维护成本。
风险：OpenAI-compatible 并不代表所有工具调用、音视频、多模态、reasoning 字段完全一致，需要 capability 层做差异声明。
Gemini
GeminiChatModel 包含 JSON Schema flatten / sanitize 逻辑，专门处理 Gemini 不支持 $defs、$ref、additionalProperties、nullable anyOf 等 JSON Schema 差异。 
评价：

优点：说明实现关注了工具 schema 在不同模型上的兼容性，这是企业 Agent 底座里非常关键的一环。
建议：将 provider schema normalization 能力提升为公共 capability 层，而不是分散在各 provider 实现里。
Ollama
OllamaChatModel 支持本地模型，credential 可为空，默认连接 localhost，并支持 thinking_enable、temperature 等。
评价：

优点：适合本地私有化、边缘部署、离线开发。
风险：Ollama 模型的 tool calling 能力、上下文大小、thinking 输出格式差异很大，企业运行时需要更严格的 model capability registry，否则“同一个 Agent 在不同模型下表现不同”的问题会很明显。
DeepSeek、Moonshot、XAI
这几个 provider 被统一导出，表明模型层覆盖面较广。 
评价：

优点：多 provider 是企业底座必要能力。
建议：补充统一的“模型能力矩阵”：是否支持 streaming、tool call、parallel tool call、structured output、reasoning、vision、audio、context size、JSON mode、prompt cache、batch、成本单价、区域合规等。
3. 模型卡设计
ChatModelBase.list_models() 默认读取具体子类旁边的 _models/*.yaml，并转换为 ModelCard。 
这是很好的设计，因为：

模型元数据与 provider 实现同目录；
可扩展自定义 YAML 目录；
有利于 UI、模型选择器、模型解析器、配置化运行。
但企业级仍需增强：
模型卡需要增加 capability 字段；
增加价格、区域、合规标签；
增加 deprecation / preferred replacement；
增加 provider health / routing metadata；
增加 SLA class，如 prod、experimental、local-only。
三、XRuntime 协议适配层分析
1. 统一请求模型
XRuntime 将所有协议统一成 XRuntimeRequest，字段包括 protocol、prompt、session_id、user_id、tenant_id、system_prompt、allowed_tools、disallowed_tools、permission_mode、tool_mode、max_turns、metadata。 
这是一个正确方向：协议层负责“翻译”，执行层只消费统一请求。

2. Adapter 抽象
ProtocolAdapter 要求实现：
parse_request
serialize_event_stream
并强调 adapter 无状态，session state 保存在 runtime core 的 SessionRecord / AgentState。 
这个设计是比较干净的：状态与协议适配解耦，适合横向扩展更多 wire protocol，例如 OpenAI Responses-compatible、LangGraph-compatible、A2A、MCP Sampling 等。

3. Anthropic Messages Adapter
Anthropic adapter 做了：
Anthropic request 到 XRuntimeRequest；
Anthropic tool schema 与 AS OpenAI function schema 互转；
AgentEvent 到 Anthropic SSE event stream。 
评价：
非常适合作为“Anthropic SDK 可直接调用企业 Agent Runtime”的兼容层。
需要继续补齐：完整 usage、stop reason、error event、tool result、image/media content block、cache_control、metadata、thinking signature 等协议细节。
4. Claude Code Adapter
Claude Code adapter 将 Claude Agent SDK 的 options 映射为 XRuntimeRequest，包括 permission_mode、allowed_tools、disallowed_tools、mcp_servers、agents、cwd、resume、hooks、model、fallback_model、max_budget_usd、sandbox、plugins、add_dirs 等。 
评价：

很适合“企业内部 Claude Code-compatible agent runtime”。
但很多字段目前进入 metadata，后续必须有明确执行闭环：例如 sandbox 是否真正切换 Workspace、max_budget_usd 是否真正参与成本控制、hooks 是否执行、add_dirs 是否映射到 workspace mount。否则只是“协议解析已收口”，不是“功能语义已落地”。
5. OpenCode Adapter
OpenCode adapter 将 opencode.json 风格配置解析为 agents、mcp_servers、skills、permissions、plugins，并把 lowercase tool name 映射到 AgentScope 内置工具名。 
评价：

这对“声明式 Agent 配置”很有价值。
建议将 OpenCode config schema 固化为 JSON Schema，并在入口严格校验，避免 metadata 里出现未验证配置导致运行期失败。
四、AgentScope 与 XRuntime 是否解耦？
1. 已经解耦的地方
1.1 XRuntime 通过 AgentScope app 扩展点接入
AgentScope create_app 明确支持 extra_agent_middlewares、extra_agent_tools、custom_subagent_templates、custom_agent_cls。 
XRuntime 的 extension 返回 extra_agent_middlewares 和 adapter registry，然后挂载到 app。 

这是一种良好的插件式集成方式。

1.2 协议层与执行层通过 XRuntimeRequest 解耦
协议 adapter 不直接执行 Agent，而是转换为统一请求模型。 
1.3 Agent 本身不感知 XRuntime
Agent 构造函数只接受 model、toolkit、middlewares、state 等通用依赖，并没有引入 xruntime 包。 
1.4 Workspace 作为沙箱边界由 AgentScope 管理
文档明确：XRuntime 不创建沙箱，而是委托 AS 的 ChatService + WorkspaceManager；Agent 只看到统一 WorkspaceBase 接口。 
2. 仍然耦合较强的地方
2.1 XRuntime 依赖 AgentScope 内部事件语义
XRuntime stream 关闭依赖 REPLY_END / EXCEED_MAX_ITERS 等 AgentEvent 类型。 
如果 AgentScope 事件类型变更，XRuntime 需要同步修改。

2.2 XRuntime server 硬编码 AgentScope 具体实现
build_xruntime_app 当前硬编码 RedisStorage、RedisMessageBus、LocalWorkspaceManager。 
这限制了企业部署时对 Postgres、Kafka/NATS、DockerWorkspace/E2BWorkspace、Kubernetes Workspace 等替换能力。

2.3 tenant 处理存在“配置 tenant”与“请求 tenant”割裂风险
server 启动时只取 config.tenants[0].id 构建 key prefix。 
但 XRuntimeRequest 每个请求都有 tenant_id 字段。 

如果 storage key prefix 是启动时固定 tenant，而请求头里可以传不同 tenant，则需要确认实际请求路径是否会重新创建 tenant-scoped storage / key config。否则多租户隔离可能是“单实例单租户隔离”，不是“同一进程多租户动态隔离”。

2.4 XRuntime 的很多企业字段仍停留在 metadata
Claude Code adapter 将 sandbox、plugins、add_dirs、budget 等放入 metadata。 
这本身没问题，但企业级底座需要确保 metadata 后续被明确消费并可审计。

五、功能互补性分析
1. AgentScope 提供“执行内核”
AgentScope 覆盖：
Agent loop；
模型抽象；
formatter；
tool / toolkit；
permission engine；
middleware；
workspace；
app service；
storage；
message bus；
scheduler/team；
tracing。
Agent 构造函数体现了模型、工具、中间件、状态、上下文压缩、ReAct 配置等运行内核要素。 
App 工厂体现了多会话服务、存储、消息总线、工作区管理和扩展注入。 

2. XRuntime 提供“企业外壳”
XRuntime 覆盖：
企业协议入口；
API Key Auth；
RateLimit；
Audit / Quota / RBAC / Redaction；
Tenant key prefix；
Metrics；
DAG Orchestrator；
Plugin；
Config；
SDK。
XRuntime 运维文档已经把这层定位讲得很清楚。 
3. 互补是否完善？
结论：方向正确，闭环尚不完整。
互补较完善的部分：

协议入口 → AS ChatService → Agent → Workspace → Event stream；
企业中间件通过 AS middleware hook 注入；
多模型 provider 可被模型解析器调度；
Local/Docker/E2B Workspace 为沙箱提供底层能力。
互补未完全闭环的部分：
XRuntime 配置中的 workspace 后端选择没有在 build_xruntime_app 里落地，当前固定 LocalWorkspaceManager。 
多租户请求级隔离和启动级 key prefix 之间需要进一步梳理。 
DAG Orchestrator 是通用 executor 模式，还没有从代码层看到与 ChatService 的生产级持久化调度闭环；其注释也说明生产中 executor 驱动 agent session，但当前类本身只接收 callable。 
MetricsCollector 是内存指标，适合单进程，但企业多副本部署下需要 Prometheus scrape 或集中后端；代码注释也说明指标为 in-memory。 
六、是否能作为企业级 Agent 开发运行时底座？
1. 已具备的企业级能力
1.1 多协议接入
当前支持 Anthropic、Claude Code、OpenCode 三种入口协议，且通过 adapter registry 设计可扩展。 
1.2 多模型 Provider
支持 OpenAI、Anthropic、DashScope、DeepSeek、Gemini、Ollama、XAI、Moonshot、OpenAI Responses。 
1.3 Agent 生命周期中间件
AgentScope 的中间件 hook 能覆盖 reply、reasoning、acting、model_call、system_prompt。 
XRuntime 利用该机制注入企业中间件。 

1.4 Workspace / 沙箱抽象
文档列出 Local、Docker、E2B 三种后端，并明确安全边界。 
1.5 服务化基础
AgentScope app factory 支持 storage、message_bus、workspace_manager，并挂载内置 routers。 
1.6 可观测性雏形
XRuntime 支持 OTel setup、metrics collector、审计中间件。 
2. 当前不足以直接称为“成熟企业级底座”的原因
2.1 默认沙箱不安全
build_xruntime_app 默认使用 LocalWorkspaceManager。 
而沙箱文档明确 LocalWorkspace 是无隔离，Agent 可以访问宿主机当前用户权限范围内的文件和命令。 

企业生产环境不应默认 LocalWorkspace。
至少应支持配置切换 Docker/E2B，并在生产 profile 下拒绝 LocalWorkspace 或给出显式风险确认。

2.2 存储与消息总线后端单一
配置里写了 storage backend 可为 redis/postgres，但 server 实现只实例化 RedisStorage。 
企业级通常需要：

Postgres / MySQL 持久化；
Redis 只做 cache / lock / pubsub；
对象存储保存 artifacts；
Kafka/NATS/Pulsar 做事件流；
数据备份、恢复、迁移、TTL、归档。
2.3 多租户隔离需要加强
XRuntime 有 tenant prefixer，文档说明通过 tenant:{tid}: 前缀隔离 Redis keys。 
但 server 启动时只使用第一个 tenant 构建 key config。 

企业级多租户通常需要：

请求级 tenant resolution；
tenant-aware storage wrapper；
per-tenant credentials；
per-tenant model allowlist；
per-tenant quotas；
per-tenant audit retention；
per-tenant encryption key；
防止 header spoofing 的认证绑定。
2.4 权限默认 admin 过宽
XRuntime enterprise middleware 中 RBAC 默认给 session 分配 admin，注释说明 allow all。 
这对轻量嵌入方便，但企业生产默认应是 least privilege：

默认 deny；
按 tenant / user / project / workspace role 授权；
destructive tools 需要确认；
Bash 网络、文件路径、命令白名单；
Write/Edit 受工作区边界限制；
secrets 读取默认禁止。
2.5 指标与审计还偏单进程/文件级
MetricsCollector 的指标存在内存里，注释说明 export 给 Prometheus scraper。 
企业多副本下必须考虑：

指标 label cardinality；
request_id / trace_id 贯穿；
audit 不可篡改；
审计落库或对象存储；
安全事件告警；
成本与 token 归因。
2.6 模型治理不足
模型 Provider 多，但企业级还需要：
模型 capability registry；
模型 fallback 策略；
成本预算；
速率限制；
provider health check；
区域合规；
PII 数据出境策略；
prompt caching 策略；
灰度发布和 A/B。
当前 ChatModelBase 有 retry 和 model card，但还不足以承载完整模型治理。 
七、关键优化建议
P0：生产安全与多租户闭环
1. 将 Workspace 后端配置真正接入 server
当前 build_xruntime_app 固定使用 LocalWorkspaceManager。 
建议：

workspace:
  backend: docker # local | docker | e2b
  local:
    basedir: ./xruntime-workspaces
  docker:
    image: ...
    network_policy: deny_by_default
  e2b:
    template: ...
并在 build_xruntime_app 中根据配置创建：
LocalWorkspaceManager；
DockerWorkspaceManager；
E2BWorkspaceManager。
生产 profile 下默认 docker 或 e2b，禁止无确认使用 local。
2. 重构 tenant-aware storage/message_bus
当前 tenant key prefix 在启动时绑定第一个 tenant。 
建议：

引入 TenantAwareStorage wrapper；
根据认证后的 tenant_id 动态 prefix；
tenant_id 不应仅信任 header，应由 API key/JWT claims 决定；
storage、message bus、workspace path、audit、quota 都以 tenant_id 为一级命名空间。
3. RBAC 默认最小权限
当前默认 admin allow all。 
建议：

默认 role 改为 viewer 或 developer_limited；
由配置或认证 claims 分配 role；
destructive tool 必须明确 allow；
Bash 默认只允许只读命令；
Edit/Write 需要 workspace path guard；
外部网络访问需单独授权。
P1：企业运行时能力补齐
4. 模型能力矩阵与路由
基于现有模型卡机制扩展字段。 ChatModelBase.list_models() 已经有从 _models/*.yaml 读取模型卡的基础。 
建议模型卡增加：

capabilities:
  streaming: true
  tool_call: true
  parallel_tool_call: true
  structured_output: true
  reasoning: true
  vision: true
  audio: false
  json_schema_level: openai | gemini_limited | none
governance:
  pii_allowed: false
  regions: ["us", "cn"]
  cost:
    input_per_1m: ...
    output_per_1m: ...
routing:
  tier: prod
  fallback: ...
5. 将 Claude Code/OpenCode metadata 落地
Claude Code adapter 已解析 model、fallback_model、max_budget_usd、sandbox、plugins、add_dirs。 
建议建立 RequestExecutionPlan：

model → ModelResolver；
sandbox → WorkspaceManager；
max_budget_usd → BudgetMiddleware；
plugins → PluginRegistry；
add_dirs → Workspace mount；
hooks → Hook runner；
mcp_servers → Workspace MCP registration。
6. DAG Orchestrator 服务化
当前 Orchestrator 是 callable executor 模式，适合单进程库调用。 
建议增加：

workflow definition storage；
workflow run record；
step run record；
retry policy；
human approval step；
compensation / rollback；
distributed scheduler；
event stream；
UI 状态查询 API。
P2：可观测性、审计与运维
7. 指标从 in-memory 走向 production scrape
MetricsCollector 当前 in-memory。 
建议：

暴露 /metrics；
增加 request latency、model latency、tool latency、queue latency；
增加 token/cost by tenant/user/model/agent；
控制 label cardinality；
与 OTel trace_id 关联。
8. 审计日志不可篡改
建议审计记录至少包含：
tenant_id；
user_id；
session_id；
agent_id；
tool name；
tool args hash；
output hash；
model name；
token usage；
approval decision；
policy version；
trace_id；
timestamp；
result/error。
并支持落到 Postgres / ClickHouse / S3 / SIEM。
9. 健康检查增强
当前 server 挂载 health / ready。 
建议 ready 检查：

Redis storage ping；
message bus ping；
workspace backend availability；
model provider health；
plugin health；
migration version；
config validation。
P3：开发体验与文档一致性
10. 文档与实现对齐
文档中写了 XRuntime 支持 Docker/E2B 沙箱选择、多租户、生产部署，但 server 默认 LocalWorkspace，配置中 storage backend 也未完全实现。 
建议文档标注：

已实现；
实验性；
设计中；
未实现；
生产不推荐。
11. 增加架构 ADR
建议新增：
docs/xruntime/ADR-001-extension-not-runtime.md
docs/xruntime/ADR-002-tenant-isolation.md
docs/xruntime/ADR-003-workspace-security.md
docs/xruntime/ADR-004-protocol-adapter-contract.md
docs/xruntime/ADR-005-model-capability-routing.md
12. SDK 稳定化
SDK Guide 已展示 query、session、admin client、plugin 开发。 
建议补充：

typed events；
streaming iterator；
retry；
timeout；
idempotency key；
upload/download artifacts；
workflow API；
tenant-scoped admin API；
examples for CI/CD coding agents。
八、架构评分
维度	评分	说明
核心 Agent 抽象	8/10	单 Agent 类 + model/tool/middleware/state 组合，清晰
多模型支持	8/10	provider 多，模型卡机制好；缺 capability/cost/governance
协议适配	7.5/10	Anthropic/Claude Code/OpenCode 方向好；协议细节需补齐
解耦性	7/10	extension 接入良好；server 层硬编码较多
企业安全	5.5/10	有 RBAC/审计/脱敏/限流雏形；默认权限和 LocalWorkspace 风险较高
多租户	5.5/10	有 prefix/context 设计；请求级动态隔离需增强
沙箱	6/10	AS 有 Local/Docker/E2B；XRuntime server 未配置化落地
可观测性	6/10	OTel/metrics/audit 有基础；生产级聚合和追踪闭环不足
编排能力	6/10	DAG orchestrator 雏形好；缺服务化和持久化执行闭环
企业底座成熟度	6.5/10	可作为 PoC/内部门户/二次开发底座；生产化还需 P0/P1 补强
九、推荐演进路线
阶段 1：企业安全基线
优先做：
Workspace backend 配置化，生产默认 Docker/E2B；
tenant-aware storage wrapper；
RBAC 默认最小权限；
tool path guard / command policy；
API key/JWT 与 tenant/user/role 绑定；
audit 落库；
/ready 检查真实依赖。
阶段 2：模型治理与成本治理
继续做：
模型 capability registry；
ModelResolver 支持 fallback、policy、cost；
token/cost middleware；
provider health check；
per-tenant model allowlist；
PII / region policy。
阶段 3：工作流与平台化
再做：
Orchestrator 持久化；
workflow API；
workflow event stream；
human approval；
artifact store；
plugin sandbox；
enterprise admin UI。
十、最终判断
这个仓库可以作为企业级 Agent Runtime 底座的“强基础版本”，尤其适合继续演进为内部统一 Agent 平台。
但如果要直接用于严肃生产环境，我建议至少先完成以下阻断项：

不要生产默认 LocalWorkspace。 当前文档明确 LocalWorkspace 无隔离，server 又默认 LocalWorkspace，这是最大安全风险。 
补齐请求级多租户隔离。 当前请求模型有 tenant_id，但 server key prefix 固定取第一个 tenant，需要进一步验证和重构。 
RBAC 默认不能 admin allow all。 企业运行时默认应 least privilege。 
把 Claude Code/OpenCode metadata 语义真正落地。 否则协议兼容只是入口兼容，不是能力兼容。 
建立模型 capability / cost / governance 层。 当前多模型已经可用，但企业级模型治理还不完整。 
本次检查命令
Testing / Checks
✅ pwd && rg --files -g 'AGENTS.md' -g 'README*' -g 'pyproject.toml' -g 'src/**' -g 'docs/**' -g 'examples/**' | head -200
✅ find .. -name AGENTS.md -print
✅ sed -n '1,240p' pyproject.toml && sed -n '1,220p' README_zh.md
✅ find src/xruntime -maxdepth 4 -type f | sort | xargs -r sed -n '1,220p'
✅ find src/agentscope/model -maxdepth 3 -type f | sort | head -120 | xargs -r sed -n '1,180p'
✅ sed -n '1,260p' docs/xruntime/SDK-GUIDE.md; sed -n '1,220p' docs/xruntime/OPS-GUIDE.md; sed -n '1,220p' docs/xruntime/SANDBOX-ARCHITECTURE.md
✅ sed -n '1,260p' src/agentscope/agent/_agent.py; sed -n '1,220p' src/agentscope/model/_base.py; sed -n '1,180p' src/agentscope/middleware/_base.py
✅ sed -n '1,220p' src/xruntime/_gateway/_extension.py; sed -n '1,260p' src/xruntime/_runtime/_orchestrator.py; sed -n '1,220p' src/xruntime/_server.py
✅ find src/agentscope/model -maxdepth 2 -name '_model.py' -o -name '__init__.py' | sort
✅ find src/agentscope/model -maxdepth 3 -path '*/_models/*.yaml' | sort
✅ find tests -maxdepth 3 -type f | sort
✅ git status --short
✅ nl -ba src/xruntime/__init__.py | sed -n '1,80p'; nl -ba src/xruntime/_server.py | sed -n '1,180p'; nl -ba src/xruntime/_gateway/_extension.py | sed -n '1,220p'
✅ nl -ba src/agentscope/agent/_agent.py | sed -n '60,180p'; nl -ba src/agentscope/model/_base.py | sed -n '20,180p'; nl -ba src/agentscope/middleware/_base.py | sed -n '1,170p'
✅ nl -ba docs/xruntime/OPS-GUIDE.md | sed -n '20,120p'; nl -ba docs/xruntime/SANDBOX-ARCHITECTURE.md | sed -n '20,170p'; nl -ba pyproject.toml | sed -n '1,120p'
✅ nl -ba src/agentscope/model/__init__.py | sed -n '1,80p'
✅ nl -ba src/agentscope/app/_app.py | sed -n '1,180p'; nl -ba src/xruntime/_runtime/_orchestrator.py | sed -n '1,260p'; nl -ba src/xruntime/_config.py | sed -n '1,220p'
✅ nl -ba src/xruntime/_gateway/_adapter.py src/xruntime/_gateway/_request.py src/xruntime/_gateway/_anthropic_adapter.py src/xruntime/_gateway/_claude_code_adapter.py src/xruntime/_gateway/_opencode_adapter.py src/xruntime/_infra/_tenant.py src/xruntime/_infra/_metrics.py | sed -n '1,160p'
