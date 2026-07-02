系统 Review 报告：Xin Agent Runtime 企业级 Agent 开发运行时评估
结论先行：该仓库已经具备“企业级 Agent Runtime 底座”的雏形与相当完整的模块分层，尤其在协议接入、AgentScope 执行集成、权限/审计/配额/脱敏中间件、Workspace 沙箱选择、模型 Provider 解析、多租户上下文等方面已有明确实现。但如果按“大型企业生产级平台”标准审视，目前仍存在几类关键差距：身份体系仍偏静态配置/内存态，UUM/OIDC/SCIM/组织架构同步尚未真正产品化；租户隔离在 Redis Storage 启动时采用单 tenant key_config，尚未达到真正 per-request 多租户存储路由；沙箱隔离依赖 Workspace 后端选择，但运行时 plan 中的 sandbox/cwd/add_dirs 尚未完整贯穿到实际 Workspace 生命周期；高并发稳定性有异步流、Redis 消息总线、并行 DAG 基础，但缺少系统性 backpressure、队列、限流分布式化、熔断、Pod 横向扩展一致性设计。
1. 总体架构成熟度评估
1.1 是否提供了一整套企业级 Agent 开发框架？
评价：基本具备，但更准确地说是“企业级 Agent Runtime 扩展层 + AgentScope 执行内核”，而不是完全自研的一体化平台。
README 中定义的核心架构是：Client SDK / Protocol → XRuntime Gateway → RuntimeExecutionPlan → Tenant/RBAC/Model/Workspace/Knowledge Policy → AgentScope ChatService/Agent/Workspace/Model → AgentEvent Stream → Protocol Adapter。这个分层覆盖协议、治理、执行、事件流等关键环节。 

README 明确列出多协议接入、多租户隔离、RBAC、知识库治理、企业中间件、Workspace 沙箱、模型治理、可观测性和统一执行计划等能力。 

从代码看，XRuntime 的扩展点不是另起一套执行引擎，而是把中间件工厂、协议适配器、模型解析器等挂到 AgentScope app 上。create_xruntime_extension 返回 extra_agent_middlewares、adapter_registry、config、model_resolver、middleware_state_cache 等，说明其定位是 AgentScope 的企业扩展层。 

优势：

协议层、治理层、执行层、事件流层边界清晰。
没有把 Agent 执行、工具调用、模型调用全部耦合到网关层，利于替换协议适配器或 Provider。
已经内置企业常见横切能力：审计、配额、RBAC、脱敏、知识库中间件、Langfuse/OTel/Prometheus 等。
不足：
“企业级开发框架”还缺少完整的开发者体验闭环：Agent blueprint 管理 UI/API、租户/用户/角色生命周期 API、插件市场治理、灰度发布、审计检索、策略模拟、运行诊断面板等。
Admin API 目前偏只读管理，并且认证依赖网关 principal，缺少企业常见的组织、用户、角色、Key、策略、模型额度全生命周期管理。
文档中宣传的能力较完整，但部分能力在代码中仍是轻量实现或尚未完整接入实际执行链路。
2. 协议和模型接入能力
2.1 是否支持 Claude API？
支持 Anthropic Messages API 风格的协议适配和 Anthropic 模型 Provider。
协议入口 /v1/messages 被映射到 ProtocolType.ANTHROPIC，并注册了 AnthropicMessagesAdapter。 

Anthropic adapter 的注释说明其目标是把官方 Messages API wire format 转成内部 XRuntimeRequest，并把 AgentEvent 转回 Anthropic 流式事件。 

模型解析器支持 anthropic Provider，并把 anthropic 映射到 AnthropicCredential 与 AnthropicChatModel。

判断：

如果你的“Claude API”指 Anthropic Messages API 的兼容入口：已有适配。
如果你的“Claude API”指 100% 兼容官方 Anthropic API 所有字段、错误码、SSE framing、tool_use 细节、beta headers、prompt caching、thinking encryption 等：从当前代码看还不能断言完全兼容，需要进一步 contract test 覆盖官方 SDK。
当前 Anthropic adapter 的 docstring 说序列化为 Anthropic SSE，但也提到输出 chunk 是 JSON + newline，gateway 可再加 SSE layer；实际路由返回 application/x-ndjson，不是标准 text/event-stream。这对“官方 SDK 直接无缝接入”可能是兼容性风险。 
2.2 是否支持 Claude SDK / Claude Code SDK？
支持 Claude Code SDK 风格协议适配，但更像服务端模拟/转换层，不是直接运行 Claude Code SDK runtime。
/v1/claude-code/query 被映射到 ProtocolType.CLAUDE_CODE。 

ClaudeCodeAdapter 明确解析 query(prompt, options) / ClaudeSDKClient wire format，并处理 system_prompt、permission_mode、allowed_tools、disallowed_tools、mcp_servers、agents、max_turns、cwd、resume、can_use_tool、hooks 等字段。 

map_claude_options_to_request 把 Claude Code 的 permission_mode 映射到内部权限模式，并提取模型、fallback、预算、sandbox、plugins、add_dirs 等元数据。 

关键风险：

Adapter 把 cwd、sandbox、add_dirs 放进 metadata，但后续 _materialize_session 创建 Session 时只设置 workspace_id = f"xruntime:{tenant_id}"，没有看到把 cwd/add_dirs/sandbox 精确传给 WorkspaceManager 的完整链路。 
can_use_tool 和 hooks 当前只记录为布尔 metadata，未看到实际 hook callback 或 per-tool dynamic approval 的完整实现。 
因此：协议层支持 Claude Code SDK 风格请求，但 SDK 高级行为并未完全等价实现。
2.3 是否支持 OpenCode SDK？
支持 OpenCode JSON 协议和 opencode.json 片段解析。
/v1/opencode 被映射到 ProtocolType.OPENCODE。 

OpenCodeAdapter 支持解析 prompt、agent、config、session_id，并把 opencode.json 的 agents、mcp、skills、permissions、plugins 结构解析到 metadata。 

OpenCode 内置工具名会映射到 AgentScope 工具名，例如 bash → Bash、read → Read、write → Write、edit → Edit。 

风险：

parse_opencode_config 完成结构解析，但是否完整把 MCP、skills、plugins、permissions 动态挂入实际 Agent/Workspace，需要更多集成代码验证。
当前 _resolve_model_config_name 对 OpenCode agent blueprint 的模型选择依赖 state.config.agents 中同名 blueprint，而不是完全信任 inline opencode config。 
2.4 是否支持自定义 Provider / 私有模型 / Claude API 代理？
部分支持。
模型解析器支持三类来源：运行时注册、环境变量、配置文件 model_providers。 

register_provider 允许注册自定义 Provider 名称到自定义 CredentialBase 子类，但注释要求还需要注册到 CredentialFactory 才能从 storage rehydrate。 

ModelProviderConfig 有 base_url 字段，可以支持 Claude API 代理或 OpenAI-compatible endpoint，但需要确认对应 Provider Credential/Model 是否使用该 base_url。 

建议：

增加正式的 Provider plugin API，支持：
provider_name
credential_schema
model_class
base_url
secret source，如 Vault/KMS
health check
cost metadata
对 Claude API 代理、自建 Anthropic-compatible endpoint、OpenAI-compatible endpoint 做 contract test。
3. 沙箱隔离评估
3.1 是否存在沙箱隔离？
存在，但沙箱边界来自 AgentScope Workspace 后端，而不是 XRuntime 自己直接执行沙箱。
沙箱文档明确指出：LocalWorkspace 工具在宿主机进程执行，无隔离；Docker/E2BWorkspace 工具在沙箱内执行。 

LocalWorkspace 的安全级别被明确标注为“无隔离”，Agent 可以访问宿主机当前用户权限范围内的文件和命令。 

DockerWorkspace 使用容器内 MCP Gateway，工具调用经 HTTP POST 到容器内 /mcps/{name}/tools/{tool}，文件/进程隔离依赖容器。 

E2BWorkspace 使用远程云沙箱，并说明沙箱用户为非 root。 

3.2 什么时候启用沙箱？
服务启动时根据环境变量选择 Workspace backend：
XRUNTIME_PRODUCTION=1/true/yes 时，默认 backend 是 docker。
非生产时默认 backend 是 local。
XRUNTIME_WORKSPACE_BACKEND 可显式设置 local/docker/e2b。
生产环境下 local 默认被拒绝，除非 XRUNTIME_ALLOW_LOCAL_WORKSPACE=1/true/yes。 
WorkspaceManagerFactory 在 production=True 且 backend 为 local 且未显式允许时抛出 ValueError。 
WorkspaceManagerFactory 支持创建 LocalWorkspaceManager、DockerWorkspaceManager、E2BWorkspaceManager。 

3.3 沙箱隔离的关键问题
问题 1：LocalWorkspace 极高风险，仅适合开发环境。
内置 Read 工具描述明确说它可以直接读取本地文件系统，并假设能读取机器上的所有文件。 

这意味着只要选择 local backend，Agent 文件读取和 Bash 执行就不是真正隔离。

问题 2：request-level sandbox metadata 未完全贯穿。

Claude Code adapter 把 sandbox 和 add_dirs 写入 metadata。 

RuntimeExecutionPlan 中也有 workspace policy 的概念，能承载 backend 和 add_dirs。 

但实际 _materialize_session 创建 session 只设置 workspace_id = f"xruntime:{tenant_id}"，没有看到将当前请求的 workspace_policy.backend/add_dirs/cwd 传递给 WorkspaceManager 的代码。 

建议：

强制在生产禁用 local backend，这一点已有；建议进一步在代码工具层加二次保护，避免误配置。
将 RuntimeExecutionPlan.workspace_policy 与 SessionConfig.workspace_id、WorkspaceManager backend、mount dirs、cwd 严格打通。
对 Docker/E2B 加资源限制：CPU、内存、磁盘、进程数、网络策略、超时、最大输出、最大文件大小。
对 add_dirs 做 allowlist，不允许任意宿主机目录 bind mount。
对 network egress 做企业策略控制，例如默认 deny，仅允许白名单域名或内网代理。
4. 企业级权限控制、用户数据隔离、UUM 接入
4.1 当前权限模型
代码定义了四级角色：Owner/Admin/Contributor/Viewer。 
Action 包括 tenant、member、KB、document、tool、model、audit 等 16 类细粒度动作。 

默认策略矩阵中：

Owner 拥有全部 Action。
Admin 可以管理大部分 tenant/KB/member/doc/tool/model/audit，但不能删除 tenant。
Contributor 可以查询和维护 KB 内容、执行工具、使用模型。
Viewer 只能读取 tenant/KB、查询 KB、使用模型。 
Agent 工具层还有 RbacMiddleware，按 session role 对工具名 pattern 进行 allow/deny，默认 deny。 
4.2 当前认证与身份来源
服务启动时从 XRUNTIME_API_KEY_RECORDS 读取 API key 记录，并绑定 tenant_id/user_id/role/kb_ids/active。
JWT 通过 XRUNTIME_JWT_SECRET 启用，使用 JwtClaimsParser 解析。 

ApiKeyRecord 能绑定 key、tenant、user、role、KB scope、active 状态。 

ApiKeyStore 当前是内存实现，并且注释明确说生产应替换成持久化实现，并对 secret at rest 做 hash。 

JwtClaimsParser 注释明确指出它不是完整 OIDC client；生产需要校验 issuer、audience、expiry、key rotation、algorithms。 

4.3 Anti-spoofing
Gateway handler 会优先使用 request.state.principal 中的认证 tenant/user，而不是客户端 header/body 中的 tenant/user。 
这对防止 x-tenant-id / x-user-id 伪造是正确方向。

4.4 用户数据隔离
当前有 Redis key prefix 机制。Server 启动时对 RedisStorage 应用 build_tenant_key_config，把 Redis key namespace 加上 tenant prefix。 
tenant infra 中说明 AgentScope 原始 Redis key 按 user_id 组织，XRuntime 增加 tenant:{tid}: 前缀。 

TenantContext 使用 contextvars，适合 asyncio 并发请求隔离。 

关键不足：

build_xruntime_app 当前只用 config.tenants[0].id 或 "default" 构造一次 RedisStorage.key_config。这意味着一个进程启动后 Storage key prefix 是固定 tenant，而不是 per request 动态 tenant。 
虽然 gateway 设置了 current_tenant，但 RedisStorage key_config 已在 app 启动时确定；如果底层 Storage 不读取 contextvar，则多租户运行时隔离不完整。 
message_bus 初始化没有使用 tenant prefix，可能导致 session event channel 在多租户同 Redis 中需要进一步隔离。
API key store、membership store 都是内存态，不适合多实例部署，也不适合企业用户生命周期管理。 
4.5 是否支持接入企业 UUM？
目前只能说“具备初步 JWT/API key 接入点，不具备完整企业 UUM 集成”。
已有能力：

API key → principal。
HS256 JWT → principal。
role/kb_ids claim 能映射到 AuthPrincipal。 
缺失能力：
OIDC discovery/JWKS。
SAML。
LDAP/AD。
企业 UUM 组织、部门、用户、用户组同步。
SCIM。
多租户 user mapping。
group-to-role / group-to-policy 映射。
token expiry/audience/issuer/key rotation 强校验。
session revocation / key rotation / audit correlation。
ABAC/ReBAC，例如按项目、知识库、数据分类、环境、时间、IP、设备 posture 授权。
建议：
新增 IdentityProvider 抽象：
OidcProvider
SamlProvider
ScimDirectorySync
EnterpriseUumProvider
JWT 改为 OIDC/JWKS 验证：
iss
aud
exp
nbf
kid
alg allowlist
引入持久化 TenantMembershipStore：
Redis/Postgres。
支持多实例一致。
支持 role/group/policy version。
支持企业用户与租户映射：
external_user_id
tenant_id
groups
roles
attributes
Admin API 增加用户、角色、Key、策略生命周期管理。
5. 高并发与高稳定性评估
5.1 已有并发基础
Gateway 使用 FastAPI async route 和 StreamingResponse 流式返回。 
每次请求会订阅 message bus，并在 on_ready 时通过 chat_run_registry.spawn 启动 ChatService.run，避免先执行后订阅导致漏事件。 

_serialize_stream 使用 asyncio.Queue 作为 feeder 和 adapter stream 之间的缓冲，并在 terminal event 后结束。

Orchestrator 支持 DAG 拓扑排序，并把同层可运行步骤用 asyncio.gather 并发执行。 

5.2 限流与配额
Server 支持通过 XRUNTIME_RATE_LIMIT 启动 RateLimitMiddleware，而不是只把 limiter 放在 app state。 
QuotaMiddleware 支持 token、tool call、cost 三类限制。 

5.3 稳定性风险
风险 1：限流可能是进程内限流，不适合多副本横向扩展。
如果 RateLimiter 不是 Redis/集中式实现，则多 Pod 下每个实例各自计数，无法保证租户级全局 QPS。

风险 2：asyncio.Queue 默认无界。

高并发、大输出、慢客户端时，无界 queue 可能造成内存压力。应设置 maxsize 并实现 backpressure / drop / cancel 策略。

风险 3：任务执行缺少系统级队列和优先级。

当前请求直接 spawn chat run，适合中小规模服务；企业高并发一般需要：

per-tenant queue
priority
max concurrent sessions
admission control
overload shed
timeout budget
retry policy
idempotency key
风险 4：Quota 默认值为空。
QuotaMiddleware(QuotaConfig(), tracker=quota_tracker) 在 middleware factory 中使用空 QuotaConfig，意味着默认没有 token/tool/cost 上限，只是具备框架能力。 

风险 5：缓存是进程内。

GatewayState 缓存 credential/agent id 在进程内，横向扩容时每个实例独立缓存，虽然不一定错误，但会影响一致性和热更新。 

建议：

RateLimiter 改为 Redis Lua/token bucket，按 tenant/user/api_key/model 维度限流。
为 _serialize_stream queue 设置 maxsize，慢客户端触发 cancel。
引入 per-tenant 并发控制，例如 semaphore + Redis lease。
为 ChatService.run 加超时、取消传播、心跳。
事件流支持 resume/replay offset。
引入 circuit breaker：
model provider 熔断
workspace backend 熔断
Redis 熔断
KB backend 熔断
所有关键操作增加 metrics：
active_sessions
queue_depth
tool_latency
model_latency
workspace_create_latency
stream_disconnect_total
sandbox_oom_total
6. 企业级 Agent 开发能力完整性
6.1 Agent 构建与执行
_materialize_session 会解析模型 Provider、创建/缓存 Credential、创建/缓存 Agent、创建/恢复 Session，然后由 ChatService 执行。 
Agent 的 system prompt 和 max_iters 可来自请求或 blueprint。 

Session 中会注入 PermissionContext，包括 allowed_tools 与 disallowed_tools。 

评价：

已经能自动 materialize Agent/Session，对协议入口友好。
但 Agent blueprint 管理仍偏配置文件，缺少企业级版本化、审批、发布、回滚、环境隔离。
6.2 工具与权限
OpenCode 工具名能映射 AgentScope 内置工具。 
Claude Code allowed/disallowed tools 能进入 request。 
_build_permission_context 将工具 allow/deny 转为 AgentScope permission rules。 
风险：
tenant_tool_allowlist 在实际 build_plan_from_request 调用中传的是 None，所以“权限只能收紧”的 tenant allowlist 没有真正启用。 
RBAC middleware 是 session role → tool pattern 的二层控制，但角色分配依赖 membership store；当前 membership store 来自 env API key records，能力有限。 
6.3 知识库治理
README 宣称有 LLM-Wiki AOT 编译、BM25、per-KB ACL、audit log。 
认证 principal 支持 kb_ids，gateway 会把 principal 的 kb_ids 放入 authorized KB ids。 

风险：

per-KB ACL 与 tenant membership 的持久化和管理 API 需要加强。
authorized_kb_ids 被放入 plan，但后续实际知识库中间件是否始终用它过滤，需要专项审计。
知识库数据隔离不仅是 ACL，还需要存储路径、索引 namespace、embedding collection namespace、cache namespace 全部隔离。
7. 安全评估总览
领域	当前状态	企业级差距	优先级
API Key	支持 env 配置和 principal 绑定	需要持久化、hash at rest、rotation、审计	P0
JWT	支持 HS256 简化解析	需要 OIDC/JWKS/issuer/audience/expiry/key rotation	P0
Tenant isolation	有 contextvars 和 Redis key prefix	当前 Storage prefix 启动时固定 tenant，不是真正 per-request	P0
RBAC	有角色/action 矩阵和工具中间件	缺少持久化策略、ABAC、策略模拟、管理 API	P1
Sandbox	支持 local/docker/e2b，生产默认 docker	request-level sandbox 未完全贯穿；资源/网络限制待补	P0
Secret redaction	有默认 regex 规则	需要 DLP/分类分级/结构化敏感字段策略	P1
Audit	有中间件和 file/memory sink	需要不可篡改、集中检索、SIEM 对接、retention	P1
Rate limit	有开关	需要分布式限流和 tenant/user/model 维度	P0
Model governance	有 resolver/router 基础	allowlist、fallback、预算策略未完整强制化	P1
Admin API	有 admin/owner gate	生命周期管理不足	P1
8. 关键优化建议
P0：上线前必须补强
实现真正 per-request 多租户 Storage/MessageBus 隔离
当前 server 用第一个 tenant 构造 Redis key_config。 
建议实现 TenantAwareRedisStorage，每次 key 构造读取 current_tenant。
MessageBus channel 也应加 tenant prefix。
接入企业 UUM / OIDC
替换简化 JwtClaimsParser。
支持 JWKS、issuer、audience、expiry、kid、alg allowlist。
支持 group/department → role/policy 映射。
沙箱策略强制化
生产禁止 local 已有，但建议在工具执行层再次 fail closed。
sandbox/cwd/add_dirs 从 adapter → plan → session/workspace 完整贯穿。
Docker/E2B 加资源和网络策略。
分布式限流和并发控制
RateLimiter 改 Redis backend。
per-tenant concurrent sessions、tool calls、model calls 限制。
流式 queue 加 backpressure。
Tenant tool allowlist 真正启用
当前 tenant_tool_allowlist=None，应从 tenant policy/config 中取 allowlist 后传入 plan。 
P1：企业生产增强
持久化权限与身份
Postgres/Redis 实现 API key、membership、role、policy。
API key hash at rest。
key rotation 和 revoke。
审计合规
审计日志写入集中式系统。
增加 request_id、trace_id、tenant_id、user_id、session_id、tool_call_id。
防篡改存储或对象存储 WORM。
模型治理
tenant model allowlist 强制执行。
cost budget 按 tenant/user/session/project 聚合。
fallback model 策略和 provider 熔断。
Admin API 产品化
租户、用户、角色、API key、模型、知识库、策略、审计查询 API。
操作审计与审批流。
兼容性测试
Anthropic official SDK contract tests。
Claude Code SDK contract tests。
OpenCode SDK e2e tests。
SSE/NDJSON media type 和 event framing 对齐。
P2：平台能力完善
Agent blueprint 版本化、灰度、回滚。
Prompt/Skill/Tool policy registry。
多环境隔离：dev/staging/prod。
多 region 部署和灾备。
Long-running task queue。
Human-in-the-loop approval。
数据分类分级和 DLP。
9. 针对你的关注点逐项回答
Q1：是否提供了一整套企业级 agent 开发框架？
回答：接近，但还不是完全成熟的一整套企业级平台。
它已经具备企业级 runtime 的主要骨架：协议、Gateway、执行计划、AgentScope 执行、Workspace、模型、RBAC、审计、配额、知识库、可观测性。 

但要成为“完整企业级 agent 开发框架”，还需要补齐身份/组织/权限/审计/部署/运维/开发者门户/策略管理/灰度发布等平台化能力。

Q2：运行时是否支持自定义 Claude API、Claude SDK、OpenCode SDK？
回答：支持基础协议适配与 Provider 扩展。
Claude API：支持 Anthropic Messages API adapter 和 Anthropic provider。 
Claude Code SDK：支持 /v1/claude-code/query 和 options 映射。 
OpenCode SDK：支持 /v1/opencode 和 opencode.json 片段解析。 
自定义 Provider：支持 register_provider，但需要进一步产品化。 
Q3：是否存在沙箱隔离，什么时候启用沙箱？
回答：存在。生产默认 Docker，非生产默认 Local。
Local 无沙箱，工具直接在宿主机执行。 
Docker/E2B 是沙箱。 
XRUNTIME_PRODUCTION=1 时默认 backend 为 docker；非生产默认 local。 
生产环境 local 默认被拒绝。 
Q4：是否满足企业级权限控制和用户数据隔离，是否支持接入企业 UUM？
回答：权限和隔离有基础，但还不满足大型企业生产标准；UUM 只能说有接入雏形。
RBAC 角色/action 矩阵已有。 
API key/JWT principal 绑定已有。 
但 API key store 和 membership store 是内存态，JWT parser 明确不是完整 OIDC client。 
Redis key prefix 当前启动时固定 tenant，真正多租户 per-request 隔离需要重构。 
Q5：是否能支持高并发和高稳定性？
回答：有异步和 Redis 基础，但企业级高并发还需要补强。
已有：

FastAPI async + streaming。
RedisStorage/RedisMessageBus。
chat run spawn。
DAG 同层并发。
RateLimitMiddleware。
QuotaMiddleware。
缺口：
分布式限流。
bounded queue/backpressure。
per-tenant 并发池。
全局任务队列。
熔断/降级。
多实例一致性。
sandbox 资源限制。
10. 最终建议路线图
近期 2-4 周：安全与隔离硬化
TenantAwareRedisStorage / TenantAwareMessageBus。
OIDC/JWKS UUM 集成。
分布式限流。
Docker/E2B sandbox resource policy。
tenant tool/model allowlist 强制执行。
中期 1-2 个月：企业治理产品化
持久化 IAM/RBAC。
Admin API 完整生命周期。
审计中心。
模型治理中心。
SDK contract tests。
长期 3-6 个月：平台化
Agent blueprint registry。
多环境发布。
灰度/回滚。
Workflow queue。
人审审批。
SIEM/DLP/KMS/Vault 集成。
我本次 Review 使用的命令与检查
✅ pwd && rg --files -g 'AGENTS.md' -g 'README.md' -g 'CONTRIBUTING.md' -g 'pyproject.toml' -g 'docs/**' -g 'src/**' | sed -n '1,220p'
✅ sed -n '1,220p' README.md; printf '\n--- pyproject ---\n'; sed -n '1,220p' pyproject.toml; printf '\n--- docs list ---\n'; rg --files docs | sed -n '1,200p'
✅ sed -n '1,240p' src/xruntime/_server.py; printf '\n-- gateway auth --\n'; sed -n '1,240p' src/xruntime/_gateway/_auth.py; printf '\n-- plan --\n'; sed -n '1,220p' src/xruntime/_gateway/_plan.py; printf '\n-- orchestrator --\n'; sed -n '1,260p' src/xruntime/_runtime/_orchestrator.py
✅ sed -n '260,560p' src/xruntime/_runtime/_orchestrator.py; printf '\n-- adapters --\n'; sed -n '1,220p' src/xruntime/_gateway/_anthropic_adapter.py; sed -n '1,220p' src/xruntime/_gateway/_claude_code_adapter.py; sed -n '1,200p' src/xruntime/_gateway/_opencode_adapter.py
✅ sed -n '1,260p' src/xruntime/_runtime/_workspace.py; printf '\n-- tenant policy store --\n'; sed -n '1,240p' src/xruntime/_runtime/_tenant/_policy.py; sed -n '1,260p' src/xruntime/_runtime/_tenant/_store.py; printf '\n-- rbac middleware --\n'; sed -n '1,220p' src/xruntime/_runtime/_middleware/_rbac.py; printf '\n-- quota audit redaction --\n'; sed -n '1,200p' src/xruntime/_runtime/_middleware/_quota.py; sed -n '1,180p' src/xruntime/_runtime/_middleware/_audit.py; sed -n '1,160p' src/xruntime/_runtime/_middleware/_redaction.py
✅ sed -n '1,260p' src/xruntime/_gateway/_extension.py; printf '\n-- model governance/router/resolver --\n'; sed -n '1,220p' src/xruntime/_runtime/_model_governance.py; sed -n '1,220p' src/xruntime/_runtime/_model_router.py; sed -n '1,220p' src/xruntime/_runtime/_model_resolver.py; printf '\n-- config --\n'; sed -n '1,260p' src/xruntime/_config.py
✅ sed -n '260,620p' src/xruntime/_config.py; sed -n '220,520p' src/xruntime/_gateway/_extension.py
✅ sed -n '520,1040p' src/xruntime/_gateway/_extension.py
✅ sed -n '1040,1320p' src/xruntime/_gateway/_extension.py; printf '\n-- tenant infra --\n'; sed -n '1,220p' src/xruntime/_infra/_tenant.py; printf '\n-- admin --\n'; sed -n '1,220p' src/xruntime/_admin_api.py
✅ sed -n '1,220p' src/agentscope/tool/_builtin/_bash.py; sed -n '1,220p' src/agentscope/tool/_builtin/_read.py; sed -n '1,220p' docs/xruntime/SANDBOX-ARCHITECTURE.md; sed -n '1,200p' docs/xruntime/FINAL-SECURITY-ARCHITECTURE.md
✅ git status --short; nl -ba README.md | sed -n '35,115p'; nl -ba src/xruntime/_server.py | sed -n '40,160p'; nl -ba src/xruntime/_gateway/_extension.py | sed -n '65,135p;580,675p;910,1045p'; nl -ba src/xruntime/_runtime/_workspace.py | sed -n '1,130p'; nl -ba src/xruntime/_runtime/_tenant/_store.py | sed -n '1,145p'; nl -ba src/xruntime/_runtime/_tenant/_policy.py | sed -n '1,160p'
✅ nl -ba src/xruntime/_runtime/_model_resolver.py | sed -n '1,220p'; nl -ba src/xruntime/_gateway/_claude_code_adapter.py | sed -n '1,135p'; nl -ba src/xruntime/_gateway/_opencode_adapter.py | sed -n '1,145p'; nl -ba src/xruntime/_gateway/_anthropic_adapter.py | sed -n '1,150p'; nl -ba src/agentscope/tool/_builtin/_read.py | sed -n '20,90p'; nl -ba docs/xruntime/SANDBOX-ARCHITECTURE.md | sed -n '20,140p'