# XRuntime P1 协议适配 — 模块文档

> 版本: v0.1.0
> 阶段: P1 协议适配 (W3-W5)
> 日期: 2026-06-23

## 概述

P1 阶段完成了三协议适配层的实现，使同一个 XRuntime 会话可被
Anthropic Messages API、Claude Code SDK、OpenCode SDK 三种方式驱动。

三协议适配器均继承 `ProtocolAdapter` ABC，实现 `parse_request`
(入站) 和 `serialize_event_stream` (出站)，内核统一使用 `AgentEvent`
事件流，协议差异完全隔离在适配层。

全部 110 项测试通过 (42 P0 + 20 Anthropic + 24 Claude Code + 24 OpenCode)。

## 模块清单

### 1. Anthropic Messages API 适配器

**文件**: `src/xruntime/_gateway/_anthropic_adapter.py`

**协议**: `POST /v1/messages` (官方 Anthropic Messages API)

**入站映射**:

| Anthropic 字段 | XRuntimeRequest 字段 |
|---|---|
| `messages[-1].content` (最后 user 消息) | `prompt` |
| `system` | `system_prompt` |
| `tools[].input_schema` | `metadata["tools"]` (转为 AS OpenAI 格式) |
| `model` | `metadata["model"]` |
| `max_tokens` | `metadata["max_tokens"]` |
| `tool_choice` | `metadata["tool_choice"]` |
| `x-session-id` header | `session_id` |
| `x-tenant-id` header | `tenant_id` |

**出站映射** (AgentEvent → Anthropic SSE):

| AgentEvent | Anthropic SSE 事件 |
|---|---|
| `REPLY_START` | `message_start` |
| `TEXT_BLOCK_START` | `content_block_start` (text) |
| `TEXT_BLOCK_DELTA` | `content_block_delta` (text_delta) |
| `TEXT_BLOCK_END` | `content_block_stop` |
| `THINKING_BLOCK_*` | `content_block_start/delta/stop` (thinking) |
| `TOOL_CALL_*` | `content_block_start/delta/stop` (tool_use) |
| `REPLY_END` | `message_delta` + `message_stop` |

**工具 schema 转换**:
- `convert_anthropic_tools_to_as()`: Anthropic `{name, description, input_schema}` → AS `{type:"function", function:{name, description, parameters}}`
- `convert_as_tools_to_anthropic()`: 反向转换

**会话模式**: Anthropic API 本身无状态，XRuntime 通过 `x-session-id` header
实现有状态会话；无 header 时按请求创建新会话。

**测试**: 20 项 (test_anthropic_adapter.py)

---

### 2. Claude Code SDK 适配器

**文件**: `src/xruntime/_gateway/_claude_code_adapter.py`

**协议**: `POST /v1/claude-code/query` (HTTP) / `XRuntimeTransport` (SDK Transport)

**入站映射** (`ClaudeAgentOptions` 字段):

| ClaudeAgentOptions 字段 | XRuntimeRequest 字段 |
|---|---|
| `prompt` | `prompt` |
| `system_prompt` | `system_prompt` |
| `permission_mode` | `permission_mode` (via PERMISSION_MODE_MAP) |
| `allowed_tools` | `allowed_tools` |
| `disallowed_tools` | `disallowed_tools` |
| `max_turns` | `max_turns` |
| `resume` | `session_id` |
| `mcp_servers` | `metadata["mcp_servers"]` |
| `agents` (subagents) | `metadata["agents"]` |
| `cwd` | `metadata["cwd"]` |
| `model` / `fallback_model` | `metadata["model"]` / `["fallback_model"]` |
| `can_use_tool` | `metadata["can_use_tool"]` |
| `hooks` | `metadata["hooks"]` |
| `max_budget_usd` | `metadata["max_budget_usd"]` |
| `sandbox` | `metadata["sandbox"]` |
| `plugins` | `metadata["plugins"]` |
| `add_dirs` | `metadata["add_dirs"]` |

**权限模式映射** (`PERMISSION_MODE_MAP`):

| Claude Code `permission_mode` | AS `PermissionMode` |
|---|---|
| `default` | `default` |
| `acceptEdits` | `accept_edits` |
| `bypassPermissions` | `bypass` |
| `plan` | `explore` |
| `dontAsk` | `dont_ask` |

**出站映射** (AgentEvent → Claude Code 消息):

| AgentEvent | Claude Code 消息 |
|---|---|
| `REPLY_START` | `SystemMessage(subtype="init")` |
| `TEXT_BLOCK_END` | `AssistantMessage` (含 TextBlock) |
| `THINKING_BLOCK_END` | `AssistantMessage` (含 ThinkingBlock) |
| `TOOL_CALL_END` | `AssistantMessage` (含 ToolUseBlock) |
| `REPLY_END` | `ResultMessage(subtype="success")` |
| `EXCEED_MAX_ITERS` + `REPLY_END` | `ResultMessage(subtype="error_max_turns")` |

**帧格式**: JSON + newline (匹配 `Transport.read_messages`)

**测试**: 24 项 (test_claude_code_adapter.py)

---

### 3. OpenCode SDK 适配器

**文件**: `src/xruntime/_gateway/_opencode_adapter.py`

**协议**: `POST /v1/opencode` (JSON)

**配置兼容** (`parse_opencode_config`):
- `agents.*` → agent 蓝图 (含工具名映射)
- `mcp.*` → MCP 服务声明
- `skills.*` → skill 目录声明
- `permissions.*` → 权限配置
- `plugins.*` → 插件声明

**内置工具映射** (`BUILTIN_TOOL_MAP`):

| OpenCode 工具名 | AS 工具类名 |
|---|---|
| `bash` | `Bash` |
| `read` | `Read` |
| `write` | `Write` |
| `edit` | `Edit` |
| `glob` | `Glob` |
| `grep` | `Grep` |
| `task` | `TaskCreate` |

**入站映射**:

| OpenCode 字段 | XRuntimeRequest 字段 |
|---|---|
| `prompt` | `prompt` |
| `agent` | `metadata["agent_name"]` |
| `session_id` | `session_id` |
| `config` (opencode.json fragment) | `metadata["opencode_config"]` |
| `config.agents[name].system_prompt` | `system_prompt` |
| `config.agents[name].tools` | `allowed_tools` (映射后) |
| `config.permissions.mode` | `permission_mode` |

**出站映射** (AgentEvent → OpenCode 事件):

| AgentEvent | OpenCode 事件 |
|---|---|
| `REPLY_START` | `session_start` |
| `TEXT_BLOCK_DELTA` | `text_delta` |
| `TOOL_CALL_START` | `tool_call` |
| `TOOL_CALL_END` | `tool_result` |
| `REPLY_END` | `session_end` |

**测试**: 24 项 (test_opencode_adapter.py)

---

## 测试汇总

| 测试文件 | 测试数 | 覆盖模块 |
|---|---|---|
| `test_config.py` | 13 | `_config.py` |
| `test_app.py` | 7 | `_gateway/_app.py` |
| `test_request_adapter.py` | 18 | `_request.py` + `_adapter.py` |
| `test_gateway_smoke.py` | 4 | 网关路由端到端 |
| `test_anthropic_adapter.py` | 20 | `_anthropic_adapter.py` |
| `test_claude_code_adapter.py` | 24 | `_claude_code_adapter.py` |
| `test_opencode_adapter.py` | 24 | `_opencode_adapter.py` |
| **合计** | **110** | **全部通过** |

## 运行测试

```bash
cd agentscope
python3 -m pytest tests/xruntime/ -v -p no:cacheprovider
```

## 文件结构 (P1 新增)

```
src/xruntime/_gateway/
├── _anthropic_adapter.py      # Anthropic Messages API 适配器
├── _claude_code_adapter.py    # Claude Code SDK 适配器
└── _opencode_adapter.py       # OpenCode SDK 适配器

tests/xruntime/
├── test_anthropic_adapter.py  # 20 项测试
├── test_claude_code_adapter.py # 24 项测试
└── test_opencode_adapter.py   # 24 项测试
```

## 待完善 (后续阶段)

- **P1 W4 剩余**: `XRuntimeTransport` (Transport ABC 实现) + `RedisSessionStore`
  — 需安装 `claude-agent-sdk` 后对拍验证
- **P2**: 生产模式 `_production_event_stream` 对接 AS `ChatService`
- **P2**: 企业中间件 (Audit/Quota/Rbac/Approval/SecretRedaction)
- **P3**: Orchestrator + SDK 包 + 插件体系
