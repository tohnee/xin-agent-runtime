# XRuntime 企业级 Agent Runtime 重构、开发、测试与验收计划

> 版本: v0.1.0  
> 日期: 2026-06-25  
> 状态: 规划稿  
> 范围: AgentScope + XRuntime 企业级 Agent 开发运行时底座

## 1. 背景与目标

本仓库当前以 AgentScope 为 Agent 执行内核，并在其上增加 XRuntime 作为企业级运行时扩展层。现有代码已经具备以下基础：

- AgentScope 提供 Agent、Model、Toolkit、Middleware、Workspace、Storage、MessageBus、FastAPI service 等核心运行能力。
- XRuntime 提供协议适配、企业中间件、多租户 key prefix、DAG orchestrator、模型解析、YAML 配置、Prometheus-style metrics、知识库框架等扩展能力。
- XRuntime 已有 Anthropic Messages API、Claude Code SDK、OpenCode SDK 三类协议入口的 adapter 雏形。
- XRuntime 已有 `llm_wiki` 知识库后端 skeleton，但当前仍以本地文件、Markdown 分节和 keyword matching 为主。

本计划的目标是把当前系统演进为企业级 Agent Runtime 底座：

1. 明确 AgentScope 与 XRuntime 的边界，避免重复实现运行内核。
2. 建立 WeKnora 风格的多租户、工作空间、知识库、RBAC 权限体系。
3. 将 LLM-Wiki 作为 XRuntime 的一等知识库后端，支持 AOT 编译、权限过滤、审计与后续 hybrid retrieval。
4. 将 Claude API、Claude Code SDK、OpenCode SDK 接入统一纳入 XRuntime 协议层，而不是侵入 AgentScope 内核。
5. 引入 Langfuse 作为可选 LLM observability 后端，补充 OTel、metrics、audit。
6. 建立系统化 TDD 开发、测试、验收流程。

## 2. 核心架构定位

### 2.1 AgentScope 的职责

AgentScope 是运行内核，负责执行相关能力：

- Agent ReAct loop、上下文、状态、事件流。
- Chat model provider 抽象和模型调用。
- Formatter、tool calling、permission engine。
- Workspace / sandbox 后端。
- Storage、MessageBus、ChatService、Session、Team、Scheduler。
- Agent middleware hook 点。

AgentScope 应保持协议无关、企业策略无关、租户产品形态无关。

### 2.2 XRuntime 的职责

XRuntime 是企业级扩展外壳，负责平台化治理能力：

- 协议适配: Anthropic Messages API、Claude Code SDK、OpenCode SDK、后续 OpenAI-compatible / A2A 等。
- Gateway 安全: API key / JWT、tenant binding、rate limit。
- 多租户: tenant / workspace / membership / per-KB ownership。
- RBAC: Owner / Admin / Contributor / Viewer 和细粒度 action policy。
- 知识库治理: LLM-Wiki、RAG、KB ACL、检索权限过滤、知识审计。
- 企业中间件: audit、quota、RBAC、redaction、knowledge injection。
- 可观测性: OTel、Prometheus metrics、Langfuse、audit log。
- 部署装配: storage、message bus、workspace manager、protocol routes。

### 2.3 最合理的边界

Claude API、Claude Code SDK、OpenCode SDK 不应直接接入 AgentScope 核心层。最合理设计是：

```text
Client SDK / Protocol
  ↓
XRuntime Gateway Protocol Adapter
  ↓
XRuntimeRequest / RuntimeExecutionPlan
  ↓
Tenant / RBAC / Model / Workspace / Knowledge policy
  ↓
AgentScope ChatService / Agent / Workspace / Model
  ↓
AgentEvent stream
  ↓
XRuntime Protocol Adapter serialize_event_stream
  ↓
Client SDK / Protocol response
```

这样设计的原因：

1. AgentScope 保持稳定内核，不被外部 wire protocol 污染。
2. XRuntime 可独立扩展协议兼容层。
3. 企业治理可以在协议进入内核前完成，例如认证、租户绑定、模型选择、权限裁剪、工具策略、知识库范围过滤。
4. 不同协议可共享统一 `XRuntimeRequest`、`RuntimeExecutionPlan`、审计和观测模型。
5. AgentScope 只需要处理统一的 Agent 执行语义和事件流。

## 3. Claude API、Claude Code SDK、OpenCode SDK 接入设计

### 3.1 Anthropic / Claude API 接入

#### 现状

XRuntime 已有 Anthropic Messages API adapter，负责：

- 将 `POST /v1/messages` 请求转换为 `XRuntimeRequest`。
- 将 Anthropic tool schema 与 AgentScope/OpenAI-style function schema 互转。
- 将 AgentScope `AgentEvent` stream 转换为 Anthropic SSE event stream。

#### 目标设计

Anthropic Messages API 应作为 XRuntime Gateway 的 protocol adapter，不作为 AgentScope model provider 的替代品。

需要区分两个概念：

| 能力 | 所属层 | 说明 |
|---|---|---|
| `AnthropicChatModel` | AgentScope model layer | Agent 执行时调用 Claude 模型。 |
| `AnthropicMessagesAdapter` | XRuntime protocol layer | 外部 Anthropic SDK 以 Anthropic API 格式调用 XRuntime。 |

#### 后续增强

1. 完整支持 Anthropic SSE 事件语义。
2. 支持 tool_use / tool_result 映射。
3. 支持 usage、stop_reason、error event。
4. 支持 thinking block、cache_control、metadata。
5. 支持 stateful session header，如 `x-session-id`。
6. 通过 `tenant_id` / API key claims 做租户绑定，不信任可伪造 header。

### 3.2 Claude Code SDK 接入

#### 现状

XRuntime 已有 Claude Code adapter，将 `query(prompt, options)` 风格请求映射为 `XRuntimeRequest`，包括：

- `permission_mode`
- `allowed_tools`
- `disallowed_tools`
- `mcp_servers`
- `agents`
- `cwd`
- `resume`
- `hooks`
- `model`
- `fallback_model`
- `max_budget_usd`
- `sandbox`
- `plugins`
- `add_dirs`

当前不少字段进入 metadata，尚需执行闭环。

#### 目标设计

Claude Code SDK 应作为“开发者 Agent 协议入口”，由 XRuntime 接入，再转换为 RuntimeExecutionPlan。

建议新增 `RuntimeExecutionPlan`：

```python
class RuntimeExecutionPlan(BaseModel):
    protocol: ProtocolType
    tenant_id: str
    user_id: str
    session_id: str | None
    agent_name: str
    prompt: str
    system_prompt: str | None
    model_config_name: str | None
    fallback_model_config_name: str | None
    max_turns: int | None
    max_budget_usd: float | None
    permission_mode: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    workspace_policy: WorkspacePolicy
    mcp_servers: list[McpServerConfig]
    skills: list[SkillConfig]
    plugins: list[PluginConfig]
    knowledge_scope: KnowledgeScope
    metadata: dict[str, Any]
```

Claude Code adapter 只负责 parse 和 serialize，实际执行语义由 plan builder / policy resolver 完成。

#### 后续增强

1. `sandbox` 绑定 WorkspaceManagerFactory。
2. `max_budget_usd` 绑定 Quota/BudgetMiddleware。
3. `model` / `fallback_model` 绑定 ModelRouter。
4. `allowed_tools` / `disallowed_tools` 绑定 RBAC + permission engine。
5. `mcp_servers` 动态注册到 workspace。
6. `add_dirs` 转换为 workspace mount policy，并做 path guard。
7. `hooks` 映射到 XRuntime lifecycle hooks，并进入 audit。

### 3.3 OpenCode SDK 接入

#### 现状

XRuntime 已有 OpenCode adapter，解析 inline `opencode.json` fragment：

- agents
- mcp
- skills
- permissions
- plugins

并将 lowercase tool name 映射到 AgentScope tool 名称。

#### 目标设计

OpenCode SDK 应作为“声明式项目级 Agent 配置协议入口”，由 XRuntime 负责解析与治理。

OpenCode config 不应直接写入 AgentScope state，而应先经过：

```text
OpenCode config
  ↓ parse
XRuntimeRequest.metadata
  ↓ validate schema
RuntimeExecutionPlan
  ↓ policy resolution
AgentScope app/session/materialization
```

#### 后续增强

1. 为 OpenCode config 增加 JSON Schema 校验。
2. agents 配置映射到 AgentBlueprintConfig / SubAgentTemplate。
3. skills 配置映射到 workspace skills，但必须受 tenant 和 path policy 限制。
4. permissions 配置只能收紧权限，不能绕过 tenant-level policy。
5. plugins 配置必须通过 plugin allowlist。
6. OpenCode agent selection 与 XRuntime agent blueprint 统一。

## 4. 多租户 RBAC 设计

### 4.1 领域模型

参考 WeKnora 风格，XRuntime 后续采用三层权限边界：

```text
User
  ↓ membership
Tenant / Workspace
  ↓ ownership / ACL
Knowledge Base
  ↓ source / chunk / tool / model
Resource
```

### 4.2 角色模型

四级角色：

```text
Owner > Admin > Contributor > Viewer
```

| 角色 | 定位 |
|---|---|
| Owner | 租户所有者，最高权限，可删除租户、转移所有权、管理关键配置。 |
| Admin | 租户管理员，可管理知识库、成员、模型配置、数据源、审计。 |
| Contributor | 内容贡献者，可上传、编辑、同步文档，维护知识库内容。 |
| Viewer | 只读访问者，可查看知识库、发起问答、使用受限 Agent 查询。 |

### 4.3 Action 矩阵

| Action | Owner | Admin | Contributor | Viewer |
|---|---:|---:|---:|---:|
| tenant:read | ✅ | ✅ | ✅ | ✅ |
| tenant:manage | ✅ | ✅ | ❌ | ❌ |
| tenant:delete | ✅ | ❌ | ❌ | ❌ |
| member:invite | ✅ | ✅ | ❌ | ❌ |
| member:role_update | ✅ | ✅ | ❌ | ❌ |
| kb:create | ✅ | ✅ | 可配置 | ❌ |
| kb:read | ✅ | ✅ | ✅ | ✅ |
| kb:query | ✅ | ✅ | ✅ | ✅ |
| kb:update | ✅ | ✅ | ✅ | ❌ |
| kb:delete | ✅ | ✅ / 可配置 | ❌ | ❌ |
| doc:ingest | ✅ | ✅ | ✅ | ❌ |
| doc:update | ✅ | ✅ | ✅ | ❌ |
| doc:delete | ✅ | ✅ | 可配置 | ❌ |
| tool:execute | 策略控制 | 策略控制 | 受限 | 只读 |
| model:use | 策略控制 | 策略控制 | 策略控制 | 受限 |
| audit:read | ✅ | ✅ | ❌ | ❌ |

### 4.4 必须满足的安全原则

1. 默认 deny。
2. 租户来自认证上下文，不信任客户端 header。
3. 知识库检索必须按 tenant + user + authorized KB scope 过滤。
4. Agent 工具调用必须受 RBAC 和 permission engine 双重约束。
5. OpenCode / Claude Code 请求中的 permissions 只能收紧，不得放宽 tenant policy。
6. 所有敏感操作进入 audit log。

## 5. LLM-Wiki 知识库设计

### 5.1 设计原则

LLM-Wiki 不只是普通 RAG，而是 AOT knowledge compiler：

```text
raw source
  ↓ normalize / redact
chunks
  ↓ LLM extract entities / claims / relationships
wiki pages
  ↓ validate / link / index
compiled knowledge
  ↓ BM25 + vector + graph retrieval
context injection / agent tool search
```

### 5.2 目录结构

```text
/var/lib/xruntime/kb/
└── tenants/
    └── {tenant_id}/
        └── kbs/
            └── {kb_id}/
                ├── raw/
                ├── wiki/
                ├── index/
                │   ├── bm25.json
                │   ├── vectors.json
                │   ├── graph.json
                │   └── manifest.json
                ├── schema/
                │   └── WIKI_SCHEMA.md
                └── audit/
                    └── knowledge-audit.jsonl
```

### 5.3 检索设计

分阶段实现：

1. MVP: Markdown page + manifest + keyword / BM25。
2. v1: embedding vector retrieval。
3. v2: typed knowledge graph。
4. v3: reciprocal rank fusion。
5. v4: confidence、supersession、retention decay、contradiction detection。

### 5.4 知识库权限过滤

所有检索必须执行：

```text
query
  ↓
authenticated principal
  ↓
tenant membership
  ↓
role + per-KB ACL
  ↓
authorized kb_ids
  ↓
retrieval metadata filter
  ↓
rerank / context injection
```

## 6. 可观测性设计

### 6.1 三类观测数据

| 类型 | 后端 | 目的 |
|---|---|---|
| Metrics | Prometheus / MetricsCollector | 运行指标、延迟、吞吐、错误率。 |
| Trace | OTel / Langfuse | 请求链路、模型调用、工具调用、知识检索。 |
| Audit | JSONL / Redis / DB / SIEM | 合规追责、安全审计、不可抵赖。 |

### 6.2 Langfuse 接入

Langfuse 作为可选 LLM observability backend，采集：

- session trace；
- agent reply span；
- model generation；
- tool call span；
- knowledge retrieve span；
- knowledge ingest span；
- workflow step span；
- token usage；
- estimated cost；
- tenant_id / user_id / session_id / agent_id / model metadata。

Langfuse 不替代 audit。Audit 仍负责合规与安全追责。

## 7. 开发计划

### Milestone 0: 测试护栏与文档

目标：先建立重构护栏。

任务：

1. 保存本规划文档。
2. 新增 ADR 文档目录。
3. 整理测试分类。
4. 固化协议 adapter contract。
5. 固化 knowledge adapter contract。

验收：

- 文档合入。
- 不改变运行代码。
- 现有测试可继续运行。

### Milestone 1: RBAC 与租户模型

任务：

1. 新增 TenantRole、TenantMember、TenantWorkspace、Principal。
2. 新增 Action enum 和 RolePolicy。
3. 默认权限从 admin allow-all 改为 least privilege。
4. API key / JWT claims 绑定 tenant_id / user_id / role。

TDD 测试：

- Owner/Admin/Contributor/Viewer 权限矩阵。
- 同一用户在不同租户不同角色。
- 未邀请用户无权限。
- disabled member 无权限。
- header tenant spoofing 被拒绝。

验收：

- 默认 deny。
- Viewer 只读。
- Contributor 可写文档但不能管理成员。
- Admin 不能删除 tenant。
- Owner 可管理 tenant。

### Milestone 2: Knowledge Scope 与 RBAC 贯穿

任务：

1. KnowledgeQuery 增加 user_id、kb_ids。
2. KnowledgeSource / KnowledgeChunk 标准化 tenant_id、kb_id metadata。
3. KnowledgeRegistry retrieve 增加权限过滤。
4. KnowledgeMiddleware 注入时只使用授权 KB。
5. SearchKnowledgeTool / IngestKnowledgeTool 权限化。

TDD 测试：

- Viewer 只能查询授权 KB。
- Viewer 不能 ingest。
- Contributor 可以 ingest。
- 不同 tenant 相同 source_id 不冲突。
- Agent auto-injection 不泄露未授权 KB。

验收：

- RAG 检索层不会越权。
- Agent 工具层不会越权。
- 检索结果带 citation 和 metadata。

### Milestone 3: LLM-Wiki MVP

任务：

1. tenant/kb scoped path resolver。
2. raw/wiki/index/audit 目录。
3. markdown frontmatter。
4. manifest index。
5. BM25 retriever。
6. 知识操作 audit。

TDD 测试：

- compile 写 markdown page。
- page 包含 tenant_id、kb_id、source_id、source_hash。
- BM25 能检索 exact keyword。
- source_id collision 被隔离。
- ingest 前 secret redaction。

验收：

- `knowledge.backend: llm_wiki` 可作为可选知识库后端。
- 支持 tenant/kb 隔离。
- 支持基础检索和注入。

### Milestone 4: Protocol ExecutionPlan

任务：

1. 新增 RuntimeExecutionPlan。
2. Anthropic / Claude Code / OpenCode adapter 统一输出 plan。
3. Claude Code metadata 字段落地。
4. OpenCode config 增加 JSON Schema 校验。
5. permissions 只能收紧不能放宽。

TDD 测试：

- Claude Code sandbox 映射到 workspace policy。
- Claude Code max_budget_usd 映射到 budget policy。
- OpenCode tools 映射到 AgentScope tool names。
- OpenCode permissions 无法绕过 tenant policy。
- Anthropic tool schema roundtrip。

验收：

- 三种协议接入都通过 XRuntime。
- AgentScope 不直接感知协议。
- plan 可审计、可观测、可测试。

### Milestone 5: Workspace 生产化

任务：

1. 新增 WorkspaceConfig。
2. 新增 WorkspaceManagerFactory。
3. 生产默认 Docker / E2B。
4. LocalWorkspace 生产需显式 override。
5. workspace path 包含 tenant/session。
6. path traversal guard。

TDD 测试：

- 默认 backend 是 docker。
- production local workspace 被拒绝。
- workspace path tenant scoped。
- path traversal 被拒绝。

验收：

- 生产默认不使用 LocalWorkspace。
- 沙箱选择由配置控制。

### Milestone 6: Model Governance

任务：

1. ModelCapabilityRegistry。
2. ModelRouter。
3. tenant model allowlist。
4. fallback model。
5. cost / token budget。
6. provider health。

TDD 测试：

- tenant 不允许的模型被拒绝。
- tool-capable 任务选择支持 tools 的模型。
- fallback_model 生效。
- cost 超限阻断。

验收：

- 模型选择可治理。
- 成本可归因。

### Milestone 7: Langfuse

任务：

1. LangfuseConfig。
2. Noop exporter。
3. Langfuse exporter。
4. model/tool/knowledge/workflow span。
5. secret redaction。

TDD 测试：

- disabled 时 no-op。
- model call 生成 generation。
- tool call 生成 span。
- knowledge retrieve 生成 span。
- payload 不包含 secrets。

验收：

- Langfuse 可开关。
- 不影响主流程。
- trace 可按 tenant/user/session 归因。

## 8. 测试计划

### 8.1 Unit Tests

覆盖：

- RBAC policy。
- Tenant context。
- Knowledge path resolver。
- LLM-Wiki compiler。
- Protocol request parsing。
- Model capability。
- Langfuse exporter。

命令：

```bash
pytest tests/xruntime/unit -p no:cacheprovider
```

### 8.2 Contract Tests

覆盖：

- ProtocolAdapter contract。
- KnowledgeAdapter contract。
- Storage tenant isolation contract。
- WorkspaceManagerFactory contract。

命令：

```bash
pytest tests/xruntime/contract -p no:cacheprovider
```

### 8.3 Integration Tests

覆盖：

- XRuntime app request path。
- tenant auth + storage prefix。
- knowledge middleware + RBAC。
- Claude Code / OpenCode config to execution plan。
- Langfuse no-op and mocked exporter。

命令：

```bash
pytest tests/xruntime/integration -p no:cacheprovider
```

### 8.4 E2E Tests

覆盖：

- Claude Code SDK compatible request。
- OpenCode SDK config driven request。
- Anthropic Messages API request。
- tenant-scoped KB retrieve。
- Agent tool permission enforcement。

命令：

```bash
pytest tests/xruntime/e2e -p no:cacheprovider
```

### 8.5 Full Regression

命令：

```bash
pytest tests -p no:cacheprovider
pre-commit run --all-files
```

## 9. 验收标准

### 9.1 安全验收

- 默认 deny。
- header tenant spoofing 不可越权。
- Viewer 不能写 KB。
- Viewer 不能通过 RAG 检索未授权 KB。
- Agent tool calling 不能绕过 RBAC。
- 所有高风险操作有 audit。
- secrets 在 audit / Langfuse payload 中被 redacted。

### 9.2 功能验收

- Anthropic API、Claude Code SDK、OpenCode SDK 都从 XRuntime 接入。
- 三种协议共享 RuntimeExecutionPlan。
- `llm_wiki` 可作为 knowledge backend 启用。
- KnowledgeMiddleware 可按 static_control / agent_control / both 工作。
- Workspace 可通过配置切换 local / docker / e2b。
- Langfuse 可选启用，不启用时 no-op。

### 9.3 生产验收

- LocalWorkspace 生产默认禁用。
- Redis key tenant scoped。
- KB 文件 tenant/kb scoped。
- Metrics / trace / audit 可按 tenant 聚合。
- 模型使用可按 tenant allowlist 控制。
- 成本可按 tenant/user/session/model 归因。

## 10. 下一步执行建议

建议下一步按以下顺序开发：

1. RBAC domain + role matrix。
2. 默认最小权限替换 admin allow-all。
3. KnowledgeQuery scope 扩展。
4. KnowledgeMiddleware / tools 权限过滤。
5. LLM-Wiki tenant/kb scoped layout。
6. RuntimeExecutionPlan。
7. Claude Code / OpenCode metadata 落地。
8. Langfuse config + no-op exporter。
9. WorkspaceConfig + factory。
10. ModelCapabilityRegistry。

每一步都必须遵循：

```text
write failing test → implement minimal code → pass test → add edge tests → docs → commit
```
