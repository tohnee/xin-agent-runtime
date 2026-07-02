# Xin Agent Runtime 架构说明

> 面向新成员的简明架构文档

---

## 一、什么是 Xin Agent Runtime？

Xin Agent Runtime 是一个企业级 Agent 开发运行时底座，由两个层次联合构成：

```
┌─────────────────────────────────────────────────────┐
│              Xin Agent Runtime                       │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │         XRuntime（企业扩展层）                │   │
│  │  协议适配 / RBAC / 多租户 / 知识库 /         │   │
│  │  Workspace 沙箱 / 模型治理 / 可观测性         │   │
│  └──────────────────┬──────────────────────────┘   │
│                     │ 依赖                           │
│  ┌──────────────────▼──────────────────────────┐   │
│  │       AgentScope（执行内核）                  │   │
│  │  Agent / Model / Toolkit / Workspace /       │   │
│  │  Storage / MessageBus / FastAPI Service       │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

**一句话理解**：AgentScope 是"发动机"，XRuntime 是"整车控制系统"，二者联合构成 Xin Agent Runtime。

---

## 二、AgentScope — 执行内核

### 定位

AgentScope 是 Agent 的**运行内核**，负责"怎么跑 Agent"。它直接包含在本仓库的 `src/agentscope/` 目录中。

### 核心组件

| 组件 | 路径 | 职责 |
|------|------|------|
| `Agent` | `agentscope.agent` | 单一核心 Agent 类，ReAct loop、模型调用、工具调用 |
| `Model` | `agentscope.model` | 多模型 Provider 适配（OpenAI/Anthropic/DashScope 等 9 个） |
| `Toolkit` | `agentscope.tool` | 工具系统（Bash/Read/Write/Edit/Grep/Glob） |
| `Workspace` | `agentscope.workspace` | 沙箱（Local/Docker/E2B） |
| `Middleware` | `agentscope.middleware` | 生命周期切面（reply/reasoning/acting/model_call） |
| `App` | `agentscope.app` | FastAPI 服务层（create_app / Storage / MessageBus） |
| `Formatter` | `agentscope.formatter` | 各模型消息格式转换 |

### 代码中的使用

```python
from agentscope.agent import Agent
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, Bash, Read, Write, Edit
from agentscope.message import UserMsg

agent = Agent(
    name="Friday",
    model=OpenAIChatModel(...),
    toolkit=Toolkit(tools=[Bash(), Read(), Write()]),
)
```

> **为什么 `import agentscope` 是正确的？** 因为 `agentscope` 包就在本仓库中，是项目代码的一部分。它不是外部依赖，而是基础组件。

---

## 三、XRuntime — 企业扩展层

### 定位

XRuntime 是**企业外壳**，负责"怎么安全、可控、可观测地跑 Agent"。代码在 `src/xruntime/`。

### 核心组件

| 组件 | 路径 | 职责 |
|------|------|------|
| Protocol Adapter | `_gateway/` | Anthropic / Claude Code / OpenCode 三协议入口 |
| AuthMiddleware | `_gateway/_auth.py` | API Key + JWT 认证 |
| RuntimeExecutionPlan | `_gateway/_plan.py` | 统一执行计划 + permissions 收紧 |
| RBAC | `_runtime/_tenant/` | 四级角色权限矩阵（默认 deny） |
| Knowledge | `_runtime/_knowledge/` | LLM-Wiki BM25 检索 + per-KB ACL |
| Workspace | `_runtime/_workspace.py` | 生产拒绝 local + path traversal guard |
| ModelRouter | `_runtime/_model_governance.py` | 模型能力注册 + tenant allowlist |
| Middleware | `_runtime/_middleware/` | Audit / Quota / RBAC / Redaction |
| Langfuse | `_runtime/_langfuse.py` | LLM 追踪（no-op by default） |

### 代码中的使用

```python
from xruntime import create_xruntime_extension, mount_protocol_adapters
from agentscope.app import create_app

ext = create_xruntime_extension(config=...)
app = create_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=workspace_manager,
    extra_agent_middlewares=ext["extra_agent_middlewares"],
)
mount_protocol_adapters(app, ext["adapter_registry"])
```

---

## 四、两者的关系

### 依赖方向

```
XRuntime ──依赖──→ AgentScope（只使用公共 API）
AgentScope ──不依赖──→ XRuntime（内核不感知扩展层）
```

这是**单向依赖**：XRuntime 调用 AgentScope 的公共接口（`create_app`、`Agent`、`AgentEvent` 等），但 AgentScope 不引用 XRuntime 的任何代码。这保证了：

1. AgentScope 内核可以独立使用（不需要 XRuntime）
2. XRuntime 必须搭配 AgentScope 使用
3. AgentScope 升级时，只要公共 API 不变，XRuntime 不受影响

### 集成点

| 集成点 | AgentScope 提供 | XRuntime 使用 |
|--------|----------------|--------------|
| App 工厂 | `create_app(extra_agent_middlewares=...)` | 注入企业中间件 |
| 协议路由 | FastAPI app | `mount_protocol_adapters(app, ...)` |
| Agent 事件 | `AgentEvent` 25 种类型 | Protocol adapter 序列化为 SSE |
| Workspace | `WorkspaceManagerBase` | `WorkspaceManagerFactory` 选择后端 |
| Storage | `RedisStorage` | tenant key-prefix 隔离 |
| Middleware | `MiddlewareBase` 生命周期 hook | Audit/Quota/RBAC/Redaction |

### 状态边界

| 状态 | 归属 | 说明 |
|------|------|------|
| Agent 状态 | AgentScope | `AgentState`、`SessionRecord` |
| 会话状态 | AgentScope | `ChatService` 管理 |
| 认证状态 | XRuntime | `AuthPrincipal` 绑定到 `request.state` |
| 配额状态 | XRuntime | `QuotaTracker` per-session |
| RBAC 状态 | XRuntime | `TenantPolicy` + `Principal` |
| 知识库 | XRuntime | `KnowledgeRegistry` + `LlmWikiAdapter` |

---

## 五、数据流

```
Client Request (Anthropic/Claude Code/OpenCode)
    │
    ▼
AuthMiddleware ── API Key / JWT → AuthPrincipal
    │
    ▼
RateLimitMiddleware ── 429 if exceeded
    │
    ▼
ProtocolAdapter.parse_request() → XRuntimeRequest
    │
    ▼
Gateway Handler
    ├── Anti-spoofing: principal.tenant_id 覆盖客户端值
    ├── build_plan_from_request() → RuntimeExecutionPlan
    └── current_tenant.set(effective_tenant)
    │
    ▼
AgentScope ChatService.run()
    ├── Enterprise Middlewares (Audit/Quota/RBAC/Redaction/Knowledge)
    ├── Agent.reply_stream() → AgentEvent stream
    │   ├── Model call (via ModelRouter)
    │   ├── Tool call (with check_permissions)
    │   └── Workspace (Docker sandbox)
    └── Events streamed back
    │
    ▼
ProtocolAdapter.serialize_event_stream() → SSE response
    │
    ▼
Client Response
```

---

## 六、包结构

```
src/
├── agentscope/              # 执行内核（基础组件）
│   ├── agent/               # Agent 类
│   ├── model/               # 9 个模型 Provider
│   ├── tool/                # 工具系统
│   ├── formatter/           # 消息格式转换
│   ├── middleware/          # 生命周期切面
│   ├── workspace/           # 沙箱后端
│   ├── app/                 # FastAPI 服务层
│   └── credential/          # 凭证管理
│
├── xruntime/                # 企业扩展层
│   ├── _gateway/            # 协议适配 + 认证 + 限流
│   ├── _runtime/            # 中间件 + 知识库 + 租户 + Workspace
│   ├── _infra/              # 租户隔离 + 指标
│   ├── _config.py           # YAML 配置
│   └── _server.py           # 服务启动入口
│
└── xruntime_sdk/            # 客户端 SDK
```

---

## 七、常见问题

### Q: 为什么不把 agentscope 改名？
A: `agentscope` 是 Python 包名，代码中 `import agentscope` 是技术实现。改名会破坏与上游生态的兼容性，且没有实际收益。品牌层面我们已经是 "Xin Agent Runtime"。

### Q: agentscope 是外部依赖还是项目代码？
A: 是**项目代码**。`src/agentscope/` 目录就在本仓库中，`pip install -e .` 会同时安装 `agentscope` 和 `xruntime` 两个包。

### Q: 能只用 agentscope 不用 xruntime 吗？
A: 可以。AgentScope 内核可以独立使用。XRuntime 是可选的企业扩展。

### Q: 能只用 xruntime 不用 agentscope 吗？
A: 不行。XRuntime 依赖 AgentScope 的 Agent、Model、Workspace 等核心抽象。

### Q: 升级 agentscope 时要注意什么？
A: 只要 `create_app`、`Agent`、`AgentEvent`、`MiddlewareBase` 等公共 API 不变，XRuntime 不受影响。AGENTS.md 中记录了所有依赖的公共接口。
