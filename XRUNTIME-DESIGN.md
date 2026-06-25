# XRuntime 企业级 Agent Runtime 设计方案

> 状态：草案 v0.1（待逐节确认）
> 底座：AgentScope（`src/agentscope/*`，src-layout）
> 定位：现有 `xin-agent-runtime` 的**升级/重写**，需平滑迁移存量会话与配置
> 产物：独立仓库 · 独立可部署服务 · 独立 SDK 包
> 兼容：Claude Code SDK · Anthropic Messages API · OpenCode SDK（三者并重）

---

## 0. 名词与边界

| 术语 | 含义 |
|---|---|
| **XRuntime** | 本框架（运行时 + 服务 + SDK） |
| **AgentScope (AS)** | 底座库，提供 Agent / Toolkit / MCP / Permission / Formatter / Model / Workspace / App 等基础能力 |
| **适配层 (Protocol Adapter)** | 在 AS 事件流 `AgentEvent` 与外部协议（Claude Code / Anthropic SSE / OpenCode）之间双向转换 |
| **会话 (Session)** | 一次持续交互的载体，对应 AS `SessionRecord` + `AgentState` |
| **工作区 (Workspace)** | agent 执行上下文（文件/工具/MCP/skill），对应 AS `WorkspaceBase` |

**与 AS 的关系**：XRuntime 是 AS 之上的**封装与扩展**，不修改 AS 核心，通过以下方式复用：
- 直接复用：`Agent`、`Toolkit`、`MCPClient`、`PermissionEngine`、`Msg`/`ContentBlock`、`ChatModelBase`/`FormatterBase`/`CredentialBase`、`WorkspaceBase`、`MessageBus`/`StorageBase`、`AgentEvent`。
- 扩展点：`MiddlewareBase`（6 钩子）、`ToolMiddlewareBase`、`ProtocolMiddlewareBase`（ASGI 协议中间件）、`custom_agent_cls`（`create_app` 已支持）、`WorkspaceManagerBase`、`SubAgentTemplate`。
- 新增子模块（在 XRuntime 仓内）：协议适配、编排、迁移、企业增强（认证/审计/配额）。

---

## 1. 目标与非目标

### 1.1 目标
1. **三协议接入**：同一个 agent 会话可被 Claude Code SDK、Anthropic Messages API 客户端、OpenCode SDK 三种方式驱动，行为一致。
2. **企业级运行时**：多租户、会话持久化、多实例水平扩展、定时调度、多 agent 团队编排、HITL、权限治理、可观测性。
3. **代码工程 agent**：开箱即用的 Bash/Edit/Read/Write/Glob/Grep + 沙箱工作区（本地/Docker/E2B）。
4. **第三方平台化**：通过 SDK/API 让第三方构建自己的 agent 应用。
5. **企业自动化**：MCP 接入企业系统、定时任务、长期记忆、审批流。
6. **平滑迁移**：从旧 xruntime 迁移存量会话/配置/凭证。

### 1.2 非目标（v1）
- 不自研模型网关（复用 AS model providers + 用户自带凭证）。
- 不重写 AS 的 ReAct 循环（复用 `Agent._reply_impl`）。
- 不做前端 UI（仅暴露协议 SSE，由各 SDK 客户端渲染）。
- 不做多语言 SDK（v1 仅 Python SDK；TypeScript SDK 待 v2）。

---

## 2. 设计原则
1. **不 fork AS，只包装**：XRuntime 依赖 `agentscope` 包，通过子类化/中间件/工厂接入，AS 升级时 XRuntime 可跟随。
2. **协议中立内核**：内核只产生/消费 `AgentEvent`；协议差异隔离在适配层。
3. **可替换后端**：Storage / MessageBus / Workspace / Model 均为接口，默认 Redis + 本地/Docker，可换。
4. **配置即代码**：所有运行时行为可通过 YAML/JSON 配置声明，也可通过 SDK 编程。
5. **安全默认**：默认 `PermissionMode.DEFAULT`，危险操作必走 HITL；企业可配 `BYPASS`/`DONT_ASK` 用于无人值守。
6. **可观测先行**：OpenTelemetry tracing（复用 AS `TracingMiddleware`）+ 结构化审计日志。

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  入口层 (Entry)                                              │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐     │
│  │ Claude Code   │  │ Anthropic     │  │ OpenCode      │     │
│  │ SDK Adapter   │  │ Messages API  │  │ SDK Adapter   │     │
│  │ (SubagentSDK) │  │ Adapter(SSE)  │  │ (opencode)    │     │
│  └──────┬────────┘  └───────┬───────┘  └───────┬───────┘     │
│         └──────────┬────────┴──────────────────┘             │
│                    ▼                                         │
│         ┌────────────────────────┐                           │
│         │  XRuntime Gateway      │  统一入口：鉴权/路由/限流   │
│         └───────────┬────────────┘                           │
├─────────────────────┼────────────────────────────────────────┤
│  运行时核心 (Runtime Core)  ← 复用 + 扩展 AS                  │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ SessionManager  /  Orchestrator  /  Scheduler        │    │
│  │  (复用 AS ChatService/SessionService + 新增编排)      │    │
│  └──────────────────────┬───────────────────────────────┘    │
│                         ▼                                      │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ Agent (AS) + XRuntime Middlewares                    │    │
│  │  - ProtocolBridgeMiddleware (事件归一化)              │    │
│  │  - AuditMiddleware / QuotaMiddleware / RbacMiddleware │    │
│  │  - MigrationShimMiddleware (旧会话兼容)               │    │
│  └──────────────────────┬───────────────────────────────┘    │
│                         ▼                                      │
│  ┌──────────┬───────────┬───────────┬───────────┬─────────┐  │
│  │ Toolkit  │ MCP       │ Skill     │ Permission│ State   │  │
│  │ (AS)     │ (AS)      │ (AS)      │ Engine(AS)│ (AS)    │  │
│  └──────────┴───────────┴───────────┴───────────┴─────────┘  │
│                         ▼                                      │
│  ┌──────────┬───────────┬───────────┬───────────┐            │
│  │ Model    │ Formatter │ Credential│ Workspace │            │
│  │ (AS)     │ (AS)      │ (AS)      │ (AS)      │            │
│  └──────────┴───────────┴───────────┴───────────┘            │
├──────────────────────────────────────────────────────────────┤
│  基础设施层 (Infra)                                           │
│  ┌────────────┐ ┌─────────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Storage    │ │ MessageBus  │ │ OTel     │ │ SecretMgr │  │
│  │ (Redis/PG) │ │ (Redis)     │ │ Collector│ │ (Vault)   │  │
│  └────────────┘ └─────────────┘ └──────────┘ └───────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 分层职责
- **入口层**：三协议适配器 + Gateway（FastAPI）。负责鉴权、租户路由、请求归一化为 `XRuntimeRequest`，响应归一化为各协议 SSE。
- **运行时核心**：会话生命周期、编排、调度。复用 AS `ChatService`/`SessionService`，新增 `Orchestrator`（多 agent 编排）与增强中间件。
- **基础设施层**：可替换后端。默认 Redis；企业可换 PostgreSQL Storage、外部 Secret Manager、OTel Collector。

---

## 4. 协议适配层（核心创新点）

### 4.1 统一内部模型
内部统一用 AS `AgentEvent`（25 种 `EventType`，`AS/event/_event.py:20-60`）作为事件流标准。所有协议适配器做**双向转换**：
- 入站：外部请求 → `XRuntimeRequest` → AS `reply_stream(inputs)` 的 `inputs`（`Msg` / `UserConfirmResultEvent` / `ExternalExecutionResultEvent`）。
- 出站：`AgentEvent` 流 → 各协议的事件序列。

### 4.2 三协议适配器

#### A. Anthropic Messages API 适配器（最有把握，作为基准）
- 路由：`POST /v1/messages`（兼容官方路径），`stream=true` 时输出官方 SSE。
- **入站**：官方请求体 `{"model","messages":[{"role","content"}],"system","tools","tool_choice",...}` → 转为 AS `Msg` 列表 + `SystemMsg`；`tools` 映射为 AS tool schemas（AS 已是 OpenAI function-calling 格式，需做 Anthropic↔OpenAI schema 转换或直接复用 AS `AnthropicChatFormatter` 的反向逻辑）。
- **出站**：`AgentEvent` → 官方 SSE 事件：
  - `ReplyStartEvent` → `message_start`
  - `TextBlockStart/Delta/End` → `content_block_start`(`text`) / `text_delta` / `content_block_stop`
  - `ThinkingBlockStart/Delta/End` → `content_block_start`(`thinking`) / `thinking_delta` / `content_block_stop`
  - `ToolCallStart/Delta/End` → `content_block_start`(`tool_use`) / `input_json_delta` / `content_block_stop`
  - `ToolResultStart/.../End` → 转为 `user` message 回填（Anthropic 无 server-side tool result 事件，工具结果在下一轮 user message 中）— **此处需确认**：是作为服务端自动回填还是回传给客户端执行。XRuntime 默认**服务端执行**（agent 自带 Toolkit），客户端只观察。
  - `ReplyEndEvent` → `message_delta`(stop_reason) + `message_stop`
- 复用：AS 已有 `AGUIProtocolMiddleware`（`AS/app/middleware/_protocol/_agui.py`）作为 ASGI 协议中间件的范本；新增 `AnthropicMessagesProtocolMiddleware` 同构实现。
- **关键差异处理**：Anthropic 官方 API 是**无状态**单轮调用；XRuntime 是**有状态**会话。适配器需把 `messages` 增量与已持久化会话上下文合并：策略 = 用请求中的 `messages` 作为**本轮输入**，历史从 `SessionRecord.state.context` 取（按 session_id 路由）。无 `session_id` 时按 `(user_id, agent_id)` upsert（复用 AS session 幂等语义，`AS/app/_router/_session.py`）。

#### B. Claude Code SDK 适配器（协议已对齐官方文档）

官方 SDK 为 `claude-agent-sdk`（Python）/ `@anthropic-ai/claude-agent-sdk`（TypeScript）。两种调用形态：
- `query(prompt, options, transport) -> AsyncIterator[Message]`：一次性会话。
- `ClaudeSDKClient`：持续会话，支持 `interrupt()`/`set_permission_mode()`/`set_model()`/`rewind_files()`/`toggle_mcp_server()` 等运行时控制。

**`ClaudeAgentOptions` 字段 → AS/XRuntime 映射**（官方已验证）：

| ClaudeAgentOptions 字段 | 映射目标 |
|---|---|
| `system_prompt: str \| SystemPromptPreset` | `AgentRecord.system_prompt` |
| `permission_mode: PermissionMode` | AS `PermissionMode`（见 §7.1 映射表） |
| `allowed_tools: list[str]` | `PermissionContext.allow_rules`（工具名级允许） |
| `disallowed_tools: list[str]` | `PermissionContext.deny_rules`（裸名移除工具；`Bash(rm *)` scoped 规则在所有模式下拒绝） |
| `mcp_servers: dict[str, McpServerConfig]` | AS `MCPClient`（stdio `{command,args,env}` / http / `create_sdk_mcp_server` 进程内） |
| `agents: dict[str, AgentDefinition]` | AS `SubAgentTemplate` + `Agent` 工具（子 agent 经 `Agent` 工具调用，带 `parent_tool_use_id`） |
| `hooks: dict[HookEvent, list[HookMatcher]]` | XRuntime 中间件（`PreToolUse`/`PostToolUse`/`Stop`/`SessionStart`/`SessionEnd`/`UserPromptSubmit` → AS 中间件钩子） |
| `can_use_tool: CanUseTool` | AS `PermissionEngine` + `RequireUserConfirmEvent` HITL 回调 |
| `max_turns: int` | AS `ReActConfig.max_iters` |
| `max_budget_usd: float` | XRuntime `QuotaMiddleware`（USD 配额） |
| `model: str` / `fallback_model: str` | AS `ChatModelConfig` + `ModelConfig.fallback_model` |
| `cwd: str \| Path` | `Workspace.workdir` |
| `add_dirs: list[str \| Path]` | `PermissionContext.working_directories` |
| `resume: str` / `continue_conversation: bool` / `fork_session: bool` | AS `SessionRecord` 复用/续接/分叉 |
| `session_store: SessionStore` | **关键集成点**：XRuntime 实现 `SessionStore` 接口，后端为 Redis Storage |
| `setting_sources: list[SettingSource]` | XRuntime 配置加载源（`.claude/` / `~/.claude/` / 项目级） |
| `sandbox: SandboxSettings` | `Workspace` 后端选择（Docker/E2B） |
| `plugins: list[SdkPluginConfig]` | XRuntime 插件（§6.4） |
| `thinking: ThinkingConfig` / `effort: EffortLevel` | AS model `Parameters`（thinking_enable/thinking_budget/reasoning_effort） |
| `output_format: dict` | AS `generate_structured_output` |
| `include_partial_messages: bool` | 控制 `StreamEvent` 增量事件输出 |

**关键集成点 —— `Transport` 抽象**：
官方 SDK 提供 `Transport` ABC（`connect/write(data:str)/read_messages()->AsyncIterator[dict]/close/is_ready/end_input`），帧格式为 **JSON + newline**。XRuntime 实现 `XRuntimeTransport(Transport)`，把 SDK 的 stdin/stdout 子进程传输替换为 **HTTP/SSE 到 XRuntime Gateway**。这样 `claude-agent-sdk` 客户端无需改动，通过 `query(prompt, options, transport=XRuntimeTransport(...))` 即可驱动 XRuntime 服务端。

**消息类型 → AgentEvent 出站映射**（官方已验证）：
- `SystemMessage(subtype="init", data.session_id)` ← `ReplyStartEvent`（携带 XRuntime session_id）
- `AssistantMessage(content: list[TextBlock|ToolUseBlock|ThinkingBlock])` ← `TextBlock*`/`ToolCallStart/Delta/End`/`ThinkingBlock*` 聚合
- `ResultMessage(result, subtype, total_cost_usd, duration_ms, ...)` ← `ReplyEndEvent` + usage 聚合；`subtype` 映射：`success`/`error_max_turns`←`ExceedMaxItersEvent`/`error_during_execution`
- `StreamEvent`（`include_partial_messages=True` 时）← 逐 `*DeltaEvent`
- `TaskNotificationMessage` ← `CustomEvent(name="bg_task")`（对接 AS `BackgroundTaskManager`）

**会话语义对齐**：
- `query()` 默认每次新会话；`resume=<session_id>` 续接；`continue_conversation=True` 续最近会话；`fork_session=True` 分叉。
- `ClaudeSDKClient` 维护同一 session，多轮 `query()` + `receive_response()`。
- `session_store` + `session_store_flush`（`batched`/`eager`）：XRuntime `RedisSessionStore` 实现，复用 AS `StorageBase` 的 `upsert_session`/`get_session`/`list_sessions`。

**权限模式映射**（官方五步流 vs AS 五模式，见 §7.1）：
- 官方流：hooks → deny → ask → permission_mode → allow → canUseTool
- AS 流（`AS/permission/_engine.py`）：deny → ask → `tool.check_permissions` → allow → 默认 ASK
- 映射：`default`→`DEFAULT`、`acceptEdits`→`ACCEPT_EDITS`、`bypassPermissions`→`BYPASS`、`plan`→`EXPLORE`、`dontAsk`→`DONT_ASK`（官方 `auto` 模式 TS-only，v1 不映射）

**子 agent**：官方 `agents: dict[str, AgentDefinition(description, prompt, tools)]`，经 `Agent` 工具调用，子上下文消息带 `parent_tool_use_id`。映射到 AS `SubAgentTemplate` + `TeamRecord` + `TeamCreate`/`TeamSay`。

**HTTP 端点**：`POST /v1/claude-code/query`（供 SDK-over-HTTP 或非 Python 客户端），帧格式与 `Transport` 一致（JSON+newline 请求，SSE 响应）。

#### C. OpenCode SDK 适配器
> ⚠️ OpenCode SDK 的远程调用协议**待对齐**。OpenCode 当前主要是 CLI + 本地 `opencode.json` 配置体系（agents/skills/plugins/permissions/MCP）。SDK 远程调用接口需确认。

- **配置兼容**：XRuntime 能读取 `opencode.json` 风格配置，映射为：
  - `agents.*` → `AgentRecord` + `SubAgentTemplate`
  - `skills.*` → AS `LocalSkillLoader` 目录
  - `mcp.*` → AS `MCPClient`
  - `permissions.*` → AS `PermissionContext` rules
  - `plugins.*` → XRuntime middleware 插件（见 §6.4）
- **Subagent 兼容**：OpenCode 的 Task/subagent 概念映射到 AS `TeamRecord` + `SubAgentTemplate` + `TeamCreate`/`TeamSay` 工具（`AS/app/_tools/__init__.py:27-37`）。
- **SDK shim**：`xruntime.sdk.opencode` 暴露与 OpenCode 等价的 `run`/`subscribe` 接口，底层走 XRuntime Gateway。
- **工具兼容**：OpenCode 内置工具（bash/read/write/edit/glob/grep/task）与 AS 内置工具（`AS/tool/_builtin/__init__.py:1-23`）几乎一一对应，直接复用。

### 4.3 适配层统一接口
```python
class ProtocolAdapter(ABC):
    @abstractmethod
    async def parse_request(self, raw: Request) -> XRuntimeRequest: ...

    @abstractmethod
    async def serialize_event_stream(
        self, events: AsyncGenerator[AgentEvent, None]
    ) -> AsyncGenerator[bytes, None]: ...
```
每个适配器实现这两个方法。Gateway 根据路由选择适配器。

### 4.4 协议差异决策表（关键）

| 维度 | Anthropic API | Claude Code SDK | OpenCode SDK | XRuntime 策略 |
|---|---|---|---|---|
| 会话状态 | 无状态 | 有状态(resume/continue/fork/session_store) | 有状态 | 内核始终有状态；无状态协议按请求重建上下文 |
| 工具执行 | 客户端回传 tool_result | 服务端执行(SDK 自带) | 服务端执行 | 默认服务端执行；可选 `external_tools=true` 走 HITL external |
| 权限模式 | 无 | 5 模式+5 步流+canUseTool | permissions 配置 | 统一到 AS `PermissionMode`（§7.1） |
| 流式 | SSE(官方事件) | Message 流(SystemMessage/AssistantMessage/ResultMessage/StreamEvent) | SSE/WS | 统一 AgentEvent → 各格式 |
| 多 agent | 无 | agents dict + Agent 工具(parent_tool_use_id) | subagent via Task | 统一到 AS Team + SubAgentTemplate |
| 接入方式 | HTTP POST /v1/messages | `Transport` ABC(JSON+newline) 或 HTTP /v1/claude-code/query | 配置 + SDK | XRuntime 实现 `XRuntimeTransport` + HTTP 端点 |
| Skill | 无 | `.claude/skills/*/SKILL.md` | skills 配置 | AS `LocalSkillLoader`（与 Claude Code 路径一致） |

---

## 5. 会话与状态模型（含迁移）

### 5.1 会话模型（复用 AS）
- `SessionRecord`：`(user_id, agent_id, workspace_id)` 三元组幂等 upsert（`AS/app/_router/_session.py`）。
- `AgentState`：`session_id`、`summary`、`context: list[Msg]`、`permission_context`、`tool_context`、`tasks_context`、`middle_context`（`AS/state/_state.py:141-184`）。
- 持久化：`StorageBase`（`AS/app/storage/_base.py:22-527`），默认 `RedisStorage`。

### 5.2 XRuntime 扩展字段
在 XRuntime 仓内定义 `XRuntimeSessionExt`（不修改 AS，通过 storage 层包装）：
- `tenant_id`：多租户隔离
- `protocol`：创建该会话的协议（用于行为差异）
- `source_session_id`：迁移自旧 xruntime 的原会话 ID
- `version`：schema 版本，用于迁移
- `audit_meta`：审计扩展

### 5.3 旧 xruntime 迁移
> ⚠️ 旧 xruntime 数据 schema **无法提供**。迁移框架保留，但 **v1 不做自动迁移**，改为手动迁移路径。

- **v1 策略**：提供 `Migrator` CLI 骨架 + `MigrationShimMiddleware`，mapper 留空。旧会话无法自动迁移，由用户按手动迁移指南导出/导入（或放弃旧会话重建）。
- `MigrationShimMiddleware`：加载会话时按 `version` 字段触发迁移 mapper（v1 仅识别新 schema，旧 schema 标记为不可迁移并告警）。
- `Migrator` CLI：`xruntime migrate --from <old> --to <new> --dry-run`（v1 仅做结构校验，不做数据转换）。
- 凭证迁移：旧凭证格式 → AS `CredentialBase` 子类（通过 `CredentialFactory.register_credential`，`AS/credential/_factory.py:58-69`），需用户提供旧凭证样例后实现。
- 配置迁移：旧配置文件 → `opencode.json` 风格 + XRuntime 配置。

### 5.4 上下文压缩
复用 AS `Agent._compress_context_impl`（`AS/agent/_agent.py:299-490`）：`trigger_ratio=0.8`、`reserve_ratio=0.1`、`SummarySchema` 5 字段、`tool_result_limit=50000` tokens。企业可覆写 `ContextConfig`。

---

## 6. 工具 / MCP / Skill 体系

### 6.1 工具（复用 AS）
- 内置：`Bash`/`Edit`/`Glob`/`Grep`/`Read`/`Write`/`ResetTools`/`SkillViewer`（`AS/tool/_builtin/__init__.py:1-23`）。
- 任务工具：`TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate`（`AS/tool/_task/`）。
- 团队工具：`TeamCreate`/`AgentCreate`/`TeamSay`/`TeamDelete`。
- 调度工具：`ScheduleCreate`/`View`/`Delete`/`List`（`AS/app/_manager/_scheduler/_scheduler_manager.py`）。
- 自定义工具：`FunctionTool` 包装 callable（`AS/tool/_adapters.py:31-164`）。
- 工具中间件：`ToolMiddlewareBase.on_tool_call` 洋葱（`AS/tool/_base.py:36-91`）——用于审计/限流/重试。

### 6.2 MCP（复用 AS）
- 传输：STDIO / SSE / Streamable HTTP（`AS/mcp/_mcp_client.py:166-213`）。
- 有状态/无状态：`is_stateful`。
- 工具命名：`mcp__{mcp_name}__{tool}`（`AS/tool/_adapters.py:218-219`）。
- 动态管理：`/workspace/mcp` 路由运行时增删（`AS/app/_router/_workspace.py`）。
- **企业 MCP 网关**：复用 AS `_mcp_gateway/`（远程 workspace 的 MCP-over-network 网关）。

### 6.3 Skill（复用 AS）
- `LocalSkillLoader` 读 `SKILL.md` frontmatter（`AS/skill/_local_loader.py:15-171`）。
- Skill 作为**系统提示片段**注入 + `SkillViewer` 工具暴露全文（非直接可调用）。
- 企业可扩展 `SkillLoaderBase`（如从对象存储/数据库加载 skill）。

### 6.4 插件体系（XRuntime 新增）
- 定义 `XRuntimePlugin` 接口：注册中间件、工具、skill、协议适配器、workspace 后端。
- 加载：从 Python entry_points 或 `opencode.json` 的 `plugins.*` 声明。
- 用于第三方扩展（对应 OpenCode plugin 概念）。

---

## 7. 权限模型（复用 AS + 企业增强）

### 7.1 复用 AS PermissionEngine
- 五模式：`DEFAULT`/`ACCEPT_EDITS`/`EXPLORE`/`BYPASS`/`DONT_ASK`（`AS/permission/_types.py:18-86`）。
- 规则：allow/deny/ask，按 tool_name + `rule_content` 匹配（`AS/permission/_rule.py`）。
- Bash 静态分析：tree-sitter 解析命令、文件路径、只读判定（`AS/tool/_builtin/_bash_parser.py`）。
- `bypass_immune`：不可被 BYPASS 跳过的安全 ASK（`AS/permission/_decision.py:33-67`）。
- HITL：`RequireUserConfirmEvent` → agent 挂起 → `UserConfirmResultEvent` 继续（`AS/agent/_agent.py:1388-1404`）。

**Claude Code SDK 权限模式映射**（官方已验证）：

| Claude Code `permission_mode` | AS `PermissionMode` | 说明 |
|---|---|---|
| `default` | `DEFAULT` | 未匹配工具触发 canUseTool/HITL |
| `acceptEdits` | `ACCEPT_EDITS` | 自动批准工作区内文件编辑/FS 命令 |
| `bypassPermissions` | `BYPASS` | 全部批准（deny/ask/hooks 仍先生效） |
| `plan` | `EXPLORE` | 只读探索，文件编辑必走 canUseTool |
| `dontAsk` | `DONT_ASK` | 未预批准一律拒绝，不调 canUseTool |
| `auto`（TS-only） | 不映射（v1） | 模型分类器批准，v2 评估 |

**Claude Code 权限规则映射**：
- `allowed_tools=["Read","Grep"]` → `PermissionContext.allow_rules`（工具名级允许规则）
- `disallowed_tools=["Bash"]`（裸名）→ 从 `Toolkit` 移除工具定义（Claude 看不到）
- `disallowed_tools=["Bash(rm *)"]`（scoped）→ `PermissionContext.deny_rules`（在所有模式含 BYPASS 下拒绝匹配调用）
- `can_use_tool` 回调 → `PermissionEngine` 判定 + `RequireUserConfirmEvent` HITL；`PermissionResultAllow(updated_input)` / `PermissionResultDeny(message, interrupt)` 映射到 AS `PermissionDecision` + `UserConfirmResultEvent`
- 官方 5 步流（hooks→deny→ask→mode→allow→canUseTool）与 AS 评估顺序（deny→ask→`check_permissions`→allow→默认ASK）一致；XRuntime `RbacMiddleware` 作为 hooks 层前置注入。

### 7.2 企业增强（XRuntime 中间件）
- **RbacMiddleware**：在 `PermissionEngine` 之上叠加 RBAC 角色→工具/资源权限映射。
- **ApprovalFlowMiddleware**：多级审批（如危险 bash 需两人审批），基于 HITL 事件扩展。
- **AuditMiddleware**：所有工具调用落审计日志（who/what/input/decision/result）。
- **SecretRedactionMiddleware**：工具输入/输出中敏感数据脱敏（复用 AS `detect-private-key` pre-commit 思路，运行时正则脱敏）。

---

## 8. 多 Agent 编排与调度

### 8.1 团队编排（复用 AS Team）
- `TeamRecord`：leader session + member agent ids（`AS/app/storage/_model/`）。
- 通信：`TeamSay` → 推 `HintBlock` 到成员 inbox + 唤醒（`AS/app/_tools/__init__.py:27-37`）。
- `SubAgentTemplate`：可复用 agent 蓝图（`AS/app/_types.py`）。
- `InboxMiddleware`：drain 收件箱注入提示（`AS/app/middleware/_inbox_middleware.py:24-136`）。

### 8.2 编排器（XRuntime 新增）
AS 目前是**隐式编排**（agent 通过 TeamSay 工具自组织）。XRuntime 新增**显式编排器**：
- `Orchestrator`：声明式 DAG / 工作流（YAML 定义），调度多个 agent 会话。
- 与 AS `SchedulerManager`（APScheduler cron）互补：Scheduler 负责**时间触发**，Orchestrator 负责**任务依赖编排**。
- 事件驱动：编排步骤间通过 `MessageBus` 传递（复用 `inbox_push`/`enqueue_wakeup`，`AS/app/message_bus/_base.py:699-835`）。
- 失败策略：重试/补偿/人工介入（复用 HITL 事件）。

### 8.3 调度（复用 AS）
- `SchedulerManager`：APcheduler cron，触发时创建/复用 session + 推 hint + 唤醒（`AS/app/_manager/_scheduler/_scheduler_manager.py:24-430`）。
- `WakeupDispatcher`：每进程一个，消费唤醒信号，spawn `ChatService.run`（`AS/app/_manager/_wakeup_dispatcher.py:27-191`）。
- `CancelDispatcher`：跨进程取消运行（`AS/app/message_bus` cancel 通道）。

---

## 9. 基础设施层

### 9.1 Storage（复用 + 扩展）
- **v1 默认 `RedisStorage`**（`AS/app/storage/_redis_storage.py`），已确认。
- 新增 `PostgresStorage`（企业级，ACID + 审计友好）——实现 `StorageBase` 13 个抽象方法（`AS/app/storage/_base.py:22-527`）；v1 后期或 v2 提供。
- 多租户：**完全隔离**（已确认）——每租户独立 Redis namespace（key 前缀 `tenant:{tid}:`）或独立 Redis DB index；`MessageBus` 同级隔离。

### 9.2 MessageBus（复用）
- 6 原语 + 领域 helper（`AS/app/message_bus/_base.py:49-989`）。
- 默认 `RedisMessageBus`。单机可选内存实现（开发用）。

### 9.3 Secret Manager（新增）
- 凭证不再明文存 storage；接入 Vault/KMS/环境变量。
- `SecretResolver` 接口，`CredentialBase` 加载时注入。

### 9.4 可观测性（复用 + 扩展）
- 复用 AS `TracingMiddleware`（OpenTelemetry，`AS/middleware/_tracing/_trace.py`）。
- 新增 metrics：会话数/工具调用延迟/token 消耗/队列深度。
- 新增 audit log：独立于 trace，结构化落库。

---

## 10. 工作区（复用 AS）
- `LocalWorkspace`（本地 FS）/`DockerWorkspace`/`E2BWorkspace`（`AS/workspace/__init__.py:5-9`）。
- `WorkspaceManagerBase`（`AS/app/workspace_manager/_base.py:10-73`）。
- 代码工程 agent 默认 LocalWorkspace；企业自动化可 Docker/E2B 沙箱。
- 工作区提供：工具、MCP、skill、offload（上下文/工具结果外存，`AS/workspace/_offload_protocol.py`）。

---

## 11. 部署形态

### 11.1 独立可部署服务
- 基于 AS `create_app`（`AS/app/_app.py:33-181`）扩展为 `create_xruntime_app`：
  - 注入 XRuntime 中间件（Audit/Quota/Rbac/Migration）。
  - 挂载三协议路由（`/v1/messages`、`/v1/claude-code/query`、`/v1/opencode/*`）。
  - 注入 `extra_agent_middlewares`/`extra_agent_tools`/`custom_subagent_templates`。
- 部署：Docker image + docker-compose（Redis）+ k8s Helm chart（多实例 + HPA）。
- 水平扩展：无状态网关 + Redis 共享 storage/messagebus（AS 已支持分布式锁、跨进程取消）。

### 11.2 独立 SDK 包
- `xruntime-sdk`（Python）：三协议的客户端 shim + 运行时管理客户端。
  - `xruntime.sdk.claude_code` — 兼容 Claude Code SDK 调用形态
  - `xruntime.sdk.anthropic` — 兼容 Anthropic Python SDK 调用形态
  - `xruntime.sdk.opencode` — 兼容 OpenCode SDK 调用形态
  - `xruntime.admin` — 会话/agent/凭证/调度管理 API 客户端
- 可独立使用（连远程 XRuntime 服务）或嵌入（直接驱动 AS Agent 本地运行）。

### 11.3 仓库结构（本仓重构，已确认）
在当前 `agentscope/` 仓内新增 `xruntime/` 源码包（与 `src/agentscope/` 并列或作为子目录），不污染 AS 源码：
```
agentscope/                          # 本仓
├── src/agentscope/                  # AS 底座（不修改）
├── src/xruntime/                    # XRuntime 运行时库（src-layout）
│   ├── _gateway/                    # 入口层 + 三协议适配器
│   │   ├── _anthropic_adapter.py
│   │   ├── _claude_code_adapter.py
│   │   ├── _claude_code_transport.py  # XRuntimeTransport(Transport)
│   │   ├── _opencode_adapter.py
│   │   └── _app.py                    # create_xruntime_app
│   ├── _runtime/                    # 编排/迁移/企业中间件
│   │   ├── _orchestrator.py
│   │   ├── _migrator.py
│   │   ├── _session_store.py          # RedisSessionStore (claude-agent-sdk SessionStore)
│   │   ├── _middleware/               # Audit/Quota/Rbac/Approval/SecretRedaction
│   │   └── _plugin.py
│   ├── _infra/                      # SecretResolver/Metrics（PostgresStorage v2）
│   └── _config.py                   # XRuntime 配置 schema
├── src/xruntime_sdk/                # SDK 包
│   ├── _claude_code.py              # 兼容 claude-agent-sdk 调用形态
│   ├── _anthropic.py                # 兼容 anthropic SDK 调用形态
│   ├── _opencode.py                 # 兼容 OpenCode SDK 调用形态
│   └── _admin.py                    # 管理 API 客户端
├── tests/xruntime/
├── examples/xruntime/
├── deploy/                          # docker-compose / helm
└── pyproject.toml                   # 新增 xruntime + xruntime-sdk 两个包，依赖 agentscope
```
- `pyproject.toml` 增加两个 setuptools package 源：`xruntime`、`xruntime_sdk`，均依赖 `agentscope[full]`。
- TS SDK（`@xruntime/sdk`）纳入 **v2**，v1 仅 Python SDK。

---

## 12. 配置体系
- 顶层 `xruntime.yaml`（兼容 `opencode.json` 字段子集）：
  ```yaml
  server: {host, port, auth}
  storage: {backend: redis|postgres, ...}
  message_bus: {backend: redis, ...}
  tenants:
    - id: ...
      credentials: [...]
  agents: [...]        # AgentRecord 蓝图
  subagent_templates: [...]
  skills: [...]
  mcps: [...]
  permissions:
    mode: default
    rules: [...]
  plugins: [...]
  observability: {otel: {...}, audit: {...}}
  migration: {from: ..., schema_map: ...}
  ```
- 环境变量覆盖：`XRUNTIME_*`。
- 热更新：skill/mcp/permission 规则支持运行时更新（复用 AS `/workspace/*` 路由）。

---

## 13. 安全与合规
- 鉴权：Gateway 层 JWT/API Key，多租户隔离。
- 凭证：Secret Resolver，不明文存储。
- 权限：默认 DEFAULT 模式 + RBAC 叠加 + 审批流。
- 审计：全量工具调用审计日志。
- 数据：上下文 offload 加密（workspace offload 落盘加密）。
- 沙箱：危险操作强制 Docker/E2B 工作区。

---

## 14. 与 AgentScope 升级的解耦
- XRuntime 仅依赖 AS 公开/稳定接口：`Agent`、`create_app`、各 `*Base` 抽象类、`AgentEvent`、`Msg`。
- 对 AS 可能变动点做适配 shim（如 `AgentState` 字段变化）。
- 跟随 AS 版本，每版本做兼容性测试矩阵。

---

## 15. 风险与待对齐项

| 项 | 风险 | 缓解 |
|---|---|---|
| Claude Code SDK 字段级协议 | ✅ 已对齐官方文档 | 落地按 §4.2B 映射表实现 |
| OpenCode SDK 远程调用协议 | 不确定是否存在 | v1 先做配置兼容 + 本地 SDK，远程协议待官方 |
| 旧 xruntime schema | **无法提供，无自动迁移** | v1 仅迁移框架骨架 + 手动迁移指南，旧会话建议重建 |
| AS `custom_agent_cls` 稳定性 | AS 内部演进 | 用中间件而非子类化 Agent，减少耦合 |
| Anthropic 无状态 vs XRuntime 有状态 | 行为差异 | 文档明确；提供 `stateless=true` 模式 |
| 三协议工具执行位置差异 | 客户端期望不同 | 协议适配层显式 `external_tools` 开关 |
| Claude Code `Transport` 接口不稳定 | 官方标注 low-level internal API | 适配层做版本探测，跟随 SDK changelog |
| 多租户完全隔离性能 | 独立 namespace/DB 开销 | v1 namespace 前缀方案，v2 评估独立 DB |

---

# 开发计划

## 阶段总览
| 阶段 | 周期 | 目标 | 交付 |
|---|---|---|---|
| P0 地基 | 2 周 | 仓库 + 依赖 + 配置骨架 | 可运行的 `create_xruntime_app` 空壳 |
| P1 协议适配 | 3 周 | 三协议入站/出站打通 | Anthropic API 端到端 + 另两端骨架 |
| P2 企业运行时 | 3 周 | 中间件/迁移/多租户 | 企业中间件 + 迁移 CLI |
| P3 编排与平台 | 3 周 | Orchestrator + SDK + 插件 | SDK 包 + 编排器 |
| P4 生产化 | 2 周 | 部署/可观测/安全 | Helm chart + 监控 + 安全审计 |
| P5 迁移与验收 | 2 周 | 旧 xruntime 迁移 + 全链路 | 迁移工具 + 验收报告 |

## P0 — 地基（2 周）
**目标**：仓库骨架、依赖、配置、最小服务。

1. **W1**
   - 1.1 新仓库结构初始化（`xruntime/` + `xruntime_sdk/` + `tests/` + `deploy/` + `pyproject.toml`），依赖 `agentscope[full]`。
   - 1.2 `XRuntimeConfig` schema（pydantic）+ `xruntime.yaml` 加载 + 环境变量覆盖。
   - 1.3 `create_xruntime_app` 空壳：在 AS `create_app` 基础上注入占位中间件，挂载健康检查路由。
   - 1.4 CI：pre-commit（沿用 AS 规范：black line=79、flake8、mypy、pylint）+ pytest。
2. **W2**
   - 2.1 `XRuntimeRequest` 内部归一化模型定义。
   - 2.2 `ProtocolAdapter` 抽象基类 + 适配器注册机制。
   - 2.3 Gateway 路由框架（按路径选择适配器）。
   - 2.4 端到端冒烟测试：用 AS `MockModel`/`MockCredential`（`tests/utils.py`）跑通一个本地会话。
   - **验收**：`pytest` 通过；`curl /health` OK；MockModel 会话可跑一轮 reply。

## P1 — 协议适配（3 周）
**目标**：三协议端到端。

3. **W3 — Anthropic Messages API 适配器（基准）**
   - 3.1 `AnthropicMessagesAdapter.parse_request`：官方请求体 → `XRuntimeRequest`（Msg + tools + system）。
   - 3.2 `AnthropicMessagesProtocolMiddleware`：`AgentEvent` → 官方 SSE（message_start/content_block_*/message_delta/message_stop）。
   - 3.3 schema 转换：Anthropic tool schema ↔ AS OpenAI function schema（或复用 AS `AnthropicChatFormatter` 反向）。
   - 3.4 有状态/无状态模式：`session_id` 路由 vs 请求重建上下文。
   - 3.5 测试：对拍官方 Anthropic SDK 客户端 + `MockModel`。
4. **W4 — Claude Code SDK 适配器（协议已对齐）**
   - 4.1 `XRuntimeTransport(Transport)`：实现 `connect/write/read_messages/close/is_ready/end_input`，JSON+newline 帧到 Gateway HTTP/SSE。
   - 4.2 `RedisSessionStore`（实现 `claude-agent-sdk` `SessionStore` 接口，后端 AS `StorageBase`）。
   - 4.3 `ClaudeCodeAdapter`（HTTP `/v1/claude-code/query`，供非 Transport 客户端）。
   - 4.4 `ClaudeAgentOptions` 全字段映射（见 §4.2B 表）：system_prompt/permission_mode/allowed_tools/disallowed_tools/mcp_servers/agents/hooks/can_use_tool/max_turns/max_budget_usd/model/fallback_model/cwd/add_dirs/resume/continue_conversation/fork_session/session_store/sandbox/plugins/thinking/output_format。
   - 4.5 出站：`AgentEvent` → `SystemMessage(init)`/`AssistantMessage`/`ResultMessage`/`StreamEvent`/`TaskNotificationMessage`。
   - 4.6 测试：`claude-agent-sdk` 客户端 + `XRuntimeTransport` + `MockModel` 对拍；权限模式 5 种各一例。
5. **W5 — OpenCode SDK 适配器**
   - 5.1 **对齐官方协议**：确认 OpenCode 远程调用是否存在；v1 先做配置兼容 + 本地 SDK。
   - 5.2 `opencode.json` 配置解析器 → AS AgentRecord/SubAgentTemplate/Skill/MCP/Permission。
   - 5.3 内置工具映射（bash/read/write/edit/glob/grep/task ↔ AS 内置）。
   - 5.4 Subagent/Task 映射到 AS Team + SubAgentTemplate。
   - 5.5 `xruntime.sdk.opencode` shim。
   - **验收**：三协议均可驱动同一个 MockModel 会话，事件流语义一致（对照 §4.4 决策表）。

## P2 — 企业运行时（3 周）
**目标**：企业级中间件、多租户、迁移框架。

6. **W6 — 中间件**
   - 6.1 `AuditMiddleware`（on_acting + on_tool_call 钩子，落审计日志）。
   - 6.2 `QuotaMiddleware`（复用 AS `ReplyBudgetControlMiddleware` 思路，扩展为租户级配额）。
   - 6.3 `RbacMiddleware`（角色→工具/资源权限，叠加在 `PermissionEngine` 之上）。
   - 6.4 `SecretRedactionMiddleware`（工具 IO 脱敏）。
7. **W7 — 多租户与存储**
   - 7.1 `XRuntimeSessionExt`（tenant_id/protocol/version/audit_meta）。
   - 7.2 `PostgresStorage`（实现 `StorageBase` 13 抽象方法）。
   - 7.3 多租户隔离（key 前缀 + 查询过滤）。
   - 7.4 `SecretResolver` 接口 + Vault/Env 实现。
8. **W8 — 迁移框架**
   - 8.1 `Migrator` CLI（dry-run + 执行）。
   - 8.2 `MigrationShimMiddleware`（按 version 字段触发迁移）。
   - 8.3 **旧 xruntime schema mapper**（阻塞项：需用户提供旧 schema）。
   - 8.4 凭证迁移 + 配置迁移。
   - **验收**：多租户会话隔离测试；迁移 dry-run 报告；审计日志可查。

## P3 — 编排与平台（3 周）
**目标**：编排器、SDK 包、插件体系。

9. **W9 — Orchestrator**
   - 9.1 声明式 DAG 工作流 YAML schema。
   - 9.2 `Orchestrator` 引擎（调度多 session，事件驱动衔接）。
   - 9.3 失败策略（重试/补偿/HITL 介入）。
   - 9.4 与 AS `SchedulerManager`/`WakeupDispatcher` 协同。
10. **W10 — SDK 包**
   - 10.1 `xruntime.sdk.anthropic`（兼容 Anthropic Python SDK 调用形态）。
   - 10.2 `xruntime.sdk.claude_code` 完善。
   - 10.3 `xruntime.sdk.opencode` 完善。
   - 10.4 `xruntime.admin`（会话/agent/凭证/调度/编排管理客户端）。
   - 10.5 SDK 文档 + 示例。
11. **W11 — 插件体系**
   - 11.1 `XRuntimePlugin` 接口（注册中间件/工具/skill/适配器/workspace）。
   - 11.2 entry_points 加载 + `opencode.json` plugins 声明加载。
   - 11.3 示例插件 2-3 个。
   - **验收**：DAG 工作流端到端跑通；SDK 三协议客户端可用；插件可热加载。

## P4 — 生产化（2 周）
**目标**：可生产部署。

12. **W12 — 部署与可观测**
   - 12.1 Docker image + docker-compose（Redis + XRuntime + OTel collector）。
   - 12.2 Helm chart（多实例 + HPA + PodDisruptionBudget）。
   - 12.3 Metrics（Prometheus exporter：会话数/工具延迟/token/队列）。
   - 12.4 Grafana dashboard 模板。
13. **W13 — 安全与压测**
   - 13.1 Gateway 鉴权（JWT/API Key）+ TLS。
   - 13.2 限流（按租户/会话）。
   - 13.3 安全审计（依赖扫描 + 权限穿透测试）。
   - 13.4 压测（并发会话 + 大上下文压缩）。
   - **验收**：k8s 部署可水平扩展；监控齐备；压测报告。

## P5 — 迁移与验收（2 周）
**目标**：迁移框架验收 + 上线（旧会话不自动迁移，手动迁移）。

14. **W14 — 迁移框架验收**
   - 14.1 `Migrator` CLI dry-run 验证（结构校验，无数据转换）。
   - 14.2 手动迁移指南文档（旧会话重建步骤、凭证迁移样例）。
   - 14.3 灰度方案（新系统并行运行，旧系统只读）。
15. **W15 — 验收**
   - 15.1 三协议全链路验收用例集。
   - 15.2 企业场景验收（代码工程/编排/平台接入/自动化各一）。
   - 15.3 文档定稿（运维/SDK/迁移/插件开发指南）。
   - 15.4 上线。
   - **验收**：三协议验收全通过；迁移框架骨架可用；手动迁移指南完备。

## 关键阻塞项
1. ~~Claude Code SubagentSDK 字段级协议~~ — ✅ 已对齐官方文档（§4.2B）。
2. **OpenCode SDK 远程调用协议**（P1 W5 前）——确认是否存在，否则 v1 仅配置兼容 + 本地 SDK。
3. ~~旧 xruntime 数据 schema~~ — 无法提供，v1 不做自动迁移，改手动迁移指南。

## 并行机会
- P1 W5（OpenCode 配置兼容）可与 P2 并行。
- SDK 文档（P3 W10）可与 P4 部署并行。
- `RedisSessionStore`（P1 W4）可与 Anthropic 适配器（P1 W3）并行。

---

## 已确认的决策（原开放问题）
1. **旧 xruntime schema** — 无法提供 → v1 仅迁移框架骨架 + 手动迁移指南，不自动迁移旧会话。
2. **Claude Code SDK 协议来源** — 官方文档（`docs.claude.com/en/api/agent-sdk/*`），已对齐 `query()`/`ClaudeSDKClient`/`ClaudeAgentOptions`/`Transport`/`SessionStore`/权限模式/消息类型。
3. **多租户隔离粒度** — 完全隔离：v1 用 Redis key 前缀 `tenant:{tid}:`（Storage + MessageBus 同级隔离），v2 评估独立 Redis DB。
4. **v1 默认存储** — Redis（`RedisStorage`）；PostgreSQL 纳入 v1 后期/v2。
5. **SDK 语言** — v1 仅 Python SDK；TypeScript SDK（`@xruntime/sdk`）纳入 **v2**。
6. **仓库策略** — 本仓（`agentscope/`）内重构，新增 `src/xruntime/` + `src/xruntime_sdk/`，不修改 AS 源码。
