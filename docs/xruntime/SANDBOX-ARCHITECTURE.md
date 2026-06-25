# XRuntime 沙箱与工作区架构详解

> 版本: v0.2.0
> 更新日期: 2026-06-24
> 适用对象: 需要理解 Agent 执行环境安全边界的架构师和开发人员

---

## 目录

1. [核心问题：Agent 代码在哪里执行？](#1-核心问题agent-代码在哪里执行)
2. [三种工作区后端](#2-三种工作区后端)
3. [AS 与 XRuntime 的职责分工](#3-as-与-xruntime-的职责分工)
4. [Agent 构建全链路](#4-agent-构建全链路)
5. [沙箱安全分析](#5-沙箱安全分析)
6. [资源消耗模型](#6-资源消耗模型)
7. [如何选择工作区后端](#7-如何选择工作区后端)
8. [XRuntime 如何切换到 Docker/E2B 沙箱](#8-xruntime-如何切换到-dockere2b-沙箱)

---

## 1. 核心问题：Agent 代码在哪里执行？

当用户发送一个请求（比如"帮我读一下 auth.py 文件"），Agent 会调用 `Read`、`Bash` 等工具来完成任务。**这些工具的代码在哪里运行？** 这就是工作区（Workspace）要回答的问题。

```
用户请求 → XRuntime 协议适配 → AS ChatService → Agent.reply()
                                                        │
                                                        ▼
                                                   调用工具
                                                        │
                                           ┌────────────┴────────────┐
                                           │                         │
                                     LocalWorkspace            Docker/E2BWorkspace
                                     工具在宿主机进程执行          工具在沙箱内执行
                                     (无隔离)                    (容器/云沙箱隔离)
```

**关键认知**：

- **工作区 = 沙箱边界**。选择 `LocalWorkspace` 意味着无沙箱（工具直接在宿主机执行）；选择 `DockerWorkspace` 或 `E2BWorkspace` 意味着有沙箱。
- **Agent 本身不知道后端类型**。三种工作区实现相同的 `WorkspaceBase` 接口，Agent 只看到统一的 `list_tools()`、`list_mcps()`、`list_skills()`。
- **XRuntime 不创建沙箱**。XRuntime 是 AS 的扩展层，它把 Agent 执行完全委托给 AS 的 `ChatService` + `WorkspaceManager`。

---

## 2. 三种工作区后端

### 2.1 LocalWorkspace — 无沙箱（开发模式）

```
宿主机进程 (Python)
├── Agent.reply()
├── Bash(cwd=/data/agent-workdir)
├── Read(), Write(), Edit()
├── Glob(), Grep()
└── MCP 服务器 (stdio, 在宿主机进程内)
```

**工作原理**：
- `list_tools()` 直接返回 6 个内置工具实例：`Bash`、`Edit`、`Glob`、`Grep`、`Read`、`Write`
- 这些工具在**宿主机 Python 进程中执行**，`Bash` 的 `cwd` 设置为工作目录
- MCP 服务器（stdio 类型）作为子进程在宿主机运行
- 文件操作直接读写宿主机文件系统

**磁盘布局**：
```
{workdir}/
├── .mcp           # MCP 客户端配置（JSON）
├── data/          # 卸载的多模态文件
├── skills/        # Skill 目录（每个含 SKILL.md）
└── sessions/      # 按会话 ID组织的上下文和工具结果
    └── {session_id}/
        ├── context.jsonl
        └── tool_result-xxx.txt
```

**安全级别**：⚠️ **无隔离**。Agent 可以访问宿主机上当前用户权限范围内的所有文件和命令。

### 2.2 DockerWorkspace — 容器沙箱

```
宿主机进程 (Python)                    Docker 容器
├── Agent.reply()                      ├── MCP Gateway (FastAPI, 容器内)
├── GatewayMCPClient ←── HTTP ────→    │   ├── MCP 服务器 (stdio, 容器内)
│   (Bearer Token 认证)                │   ├── /health
│                                      │   ├── /mcps (注册/列出)
│                                      │   └── /mcps/{name}/tools/{tool} (调用)
│                                      └── /workspace/ (工作目录, bind-mount)
└── GatewayClient (连接池)
```

**工作原理**：
- `list_tools()` 返回**空列表** `[]`——所有工具都通过容器内的 MCP Gateway 提供
- 容器内运行一个独立的 FastAPI 网关进程（`_mcp_gateway_app.py`），它代理所有 MCP 服务器
- 宿主机通过 `GatewayMCPClient`（HTTP 客户端）与容器网关通信，Bearer Token 认证
- 每次工具调用 = 一次 HTTP POST 到容器内的 `/mcps/{name}/tools/{tool}`
- 镜像按 Dockerfile + COPY 内容的 SHA-256 哈希缓存，避免重复构建

**关键差异**：
| 维度 | LocalWorkspace | DockerWorkspace |
|------|---------------|-----------------|
| 工具执行位置 | 宿主机进程 | 容器内进程 |
| 内置工具 | 6 个（Bash/Edit/Glob/Grep/Read/Write） | 无（全走 MCP Gateway） |
| MCP 服务器 | 宿主机子进程 | 容器内进程 |
| 文件隔离 | 无（宿主机文件系统） | 容器内文件系统（可选 bind-mount） |
| 网络隔离 | 无 | 容器默认网络（有外网） |
| 进程隔离 | 无 | 容器进程命名空间 |

### 2.3 E2BWorkspace — 云沙箱

```
宿主机进程 (Python)                    E2B 云沙箱 (远程 VM)
├── Agent.reply()                      ├── envd (E2B 守护进程)
├── GatewayMCPClient ←── HTTPS ──→     ├── MCP Gateway (FastAPI, 沙箱内)
│   (E2B Proxy + Bearer Token)         │   └── MCP 服务器
│                                      └── /home/user/workspace/ (工作目录)
└── GatewayClient (连接池)
```

**工作原理**：
- 与 DockerWorkspace 类似，但沙箱运行在 E2B 云端 VM 中
- 通过 `e2b.AsyncSandbox` API 管理生命周期
- `close()` 不销毁沙箱，而是 `pause()`（暂停），下次 `initialize()` 自动恢复
- 通过 E2B 代理 + Bearer Token 访问容器内网关
- **沙箱用户为非 root**（`/home/user`），比 Docker 更安全

---

## 3. AS 与 XRuntime 的职责分工

从业务落地视角，完整的请求处理链路如下：

```
┌──────────────────────────────────────────────────────────────┐
│                        XRuntime 职责                          │
│                     (企业外壳 / 协议层)                        │
│                                                              │
│  ① 协议适配: Anthropic/Claude Code/OpenCode → XRuntimeRequest│
│  ② 网关安全: API Key 认证 + 滑动窗口限流                       │
│  ③ 会话管理: 自动创建/恢复会话                                 │
│  ④ 企业中间件: 审计/配额/RBAC/脱敏                             │
│  ⑤ 多租户: contextvars 租户上下文隔离                          │
│  ⑥ 事件流: SSE/NDJSON 协议序列化                              │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                        AS 职责                                │
│                     (运行内核 / 执行层)                        │
│                                                              │
│  ⑦ ChatService.run(): 驱动 Agent 执行                         │
│  ⑧ WorkspaceManager: 获取/缓存工作区 (沙箱)                   │
│  ⑨ Toolkit: 组装工具集 (内置 + MCP + Skill + 调度 + 团队)     │
│  ⑩ Agent.reply_stream(): LLM 推理 + 工具调用循环               │
│  ⑪ Storage: 持久化会话状态/消息/Agent 状态                     │
│  ⑫ MessageBus: 分布式锁 + 事件回放 + 实时订阅                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**谁负责什么？**

| 职责 | XRuntime | AgentScope | 说明 |
|------|----------|-----------|------|
| 协议解析 | ✅ | ❌ | 三种 wire format → 统一请求 |
| 沙箱创建 | ❌ | ✅ | WorkspaceManager 创建/管理工作区 |
| 工具执行 | ❌ | ✅ | 工具在沙箱内运行 |
| LLM 调用 | ❌ | ✅ | Agent 驱动 LLM 推理 |
| 审计日志 | ✅ | ❌ | AuditMiddleware 记录工具调用 |
| 配额管控 | ✅ | ❌ | QuotaMiddleware 限制用量 |
| 权限控制 | ✅ | ❌ | RBAC 中间件 + AS PermissionEngine |
| 会话持久化 | ❌ | ✅ | Storage 保存 SessionRecord |
| 多租户隔离 | ✅ | ❌ | Redis Key 前缀 + contextvars |
| 消息总线 | ❌ | ✅ | 分布式锁 + 事件回放 |

**核心结论**：XRuntime 不创建沙箱，不执行工具，不调用 LLM。它只在 AS 之上增加协议适配、安全管控和企业治理能力。

---

## 4. Agent 构建全链路

以一个 HTTP 请求 `/v1/messages` 为例，完整链路如下：

### 步骤 1: XRuntime 网关层

```
POST /v1/messages
  → AuthMiddleware: 校验 x-api-key
  → RateLimiter: 滑动窗口检查
  → AnthropicMessagesAdapter.parse_request(): 解析请求体
  → _ensure_session(): 在 Storage 中创建/恢复会话
  → ChatService.run() 作为后台任务启动
  → _event_stream(): 开始流式返回事件
```

### 步骤 2: AS ChatService 组装

```
ChatService.run(user_id, session_id, agent_id, input_msg)
  → storage.get_agent(): 加载 Agent 配置
  → storage.get_session(): 加载会话状态
  → workspace_manager.get_workspace():
      → LocalWorkspaceManager: 缓存命中 → 返回已有工作区
      → 缓存未命中 → 创建 LocalWorkspace(workdir=basedir/agent_id)
      → workspace.initialize(): 恢复 MCP 配置 + 扫描 Skills
  → get_toolkit(): 组装工具集
      → workspace.list_tools(): [Bash, Edit, Glob, Grep, Read, Write]
      → workspace.list_mcps(): 已注册的 MCP 客户端列表
      → workspace.list_skills(): 已扫描的 Skill 列表
      → + TaskCreate/TaskList/TaskGet/TaskUpdate (规划工具)
      → + ScheduleCreate/View/Delete/List (调度工具)
      → + TeamCreate/AgentCreate/TeamSay (团队工具, 如适用)
      → + extra_agent_tools() (XRuntime 注入的额外工具)
  → 组装中间件链:
      → InboxMiddleware (消息收件箱)
      → StateChangeMiddleware (状态变更通知)
      → ToolOffloadMiddleware (工具结果卸载)
      → extra_agent_middlewares() ← XRuntime 的企业中间件:
          → AuditMiddleware (审计日志)
          → QuotaMiddleware (配额管控)
          → RbacMiddleware (权限控制)
          → SecretRedactionMiddleware (脱敏)
      → [可选] TTSMiddleware
  → get_model(): 解析模型配置 → 创建 ChatModelBase 实例
  → Agent(name, system_prompt, model, toolkit, middlewares, offloader=workspace)
```

### 步骤 3: Agent 执行循环

```
Agent.reply_stream(input_msg)
  → LLM 推理 (流式)
  → 如果 LLM 请求工具调用:
      → 中间件链: Audit → Quota → RBAC → Redaction → ToolOffload
      → Toolkit.call_tool(tool_call):
          → 内置工具: 直接执行 (LocalWorkspace: 宿主机进程)
          → MCP 工具: GatewayMCPClient → HTTP → 容器内 Gateway → MCP 服务器
      → 工具结果返回给 LLM → 继续推理
  → 事件流通过 MessageBus.session_publish_event() 发布:
      → 写入 Redis Replay Log (供后续重放)
      → 发布到 Redis Pub/Sub (供实时订阅)
  → 最终回复 + Agent 状态持久化到 Storage
```

### 步骤 4: XRuntime 事件流返回

```
_event_stream()
  → message_bus.session_read_events(): 读取已缓冲事件 (重放)
  → message_bus.session_subscribe_events(): 实时订阅新事件
  → _serialize_event(): 通过 Adapter 序列化为协议格式
  → StreamingResponse → HTTP chunked transfer → 客户端
  → chat_task 完成后关闭流
```

---

## 5. 沙箱安全分析

### 5.1 LocalWorkspace 安全评估

| 维度 | 评估 | 风险等级 |
|------|------|---------|
| 文件隔离 | ❌ 无隔离，Agent 可读写当前用户权限下所有文件 | 🔴 高 |
| 进程隔离 | ❌ 无隔离，Bash 可执行任意命令 | 🔴 高 |
| 网络隔离 | ❌ 无隔离，Agent 可访问任意网络资源 | 🔴 高 |
| 用户隔离 | ❌ 以当前用户身份运行 | 🔴 高 |

**适用场景**：仅限本地开发、受信任环境。**绝不应在生产环境直接使用。**

### 5.2 DockerWorkspace 安全评估

| 维度 | 评估 | 风险等级 |
|------|------|---------|
| 文件隔离 | ✅ 容器内文件系统（除非 bind-mount） | 🟡 中 |
| 进程隔离 | ✅ 容器进程命名空间 | 🟢 低 |
| 网络隔离 | ⚠️ 默认 Docker 网络（有外网），Gateway 端口仅绑定 127.0.0.1 | 🟡 中 |
| 用户隔离 | ⚠️ **容器以 root 运行**（未设置 --user） | 🔴 高 |
| 资源限制 | ❌ 无 CPU/内存/PID 限制 | 🟡 中 |
| 能力限制 | ❌ 未 drop capabilities | 🟡 中 |
| 只读根FS | ❌ 未启用 ReadonlyRootfs | 🟡 中 |

**已知安全问题**：
1. **容器以 root 运行** — 如果 Agent 被注入恶意指令，攻击者获得容器内 root 权限
2. **无资源限制** — Agent 可能通过 `fork bomb` 或大文件写入耗尽宿主机资源
3. **无网络隔离** — Agent 可发起任意网络请求（数据泄露风险）
4. **无 capability drop** — 容器保留了默认 Linux capabilities

**安全加固建议**（生产环境必须实施）：
```python
# 在 DockerWorkspace 创建容器时添加:
HostConfig={
    "PortBindings": {...},
    "BindMounts": {...},
    # ↓ 生产环境加固项 ↓
    "User": "1000:1000",              # 非 root 用户
    "Memory": 2 * 1024**3,            # 内存限制 2GB
    "NanoCpus": 2 * 10**9,            # CPU 限制 2 核
    "PidsLimit": 100,                 # 进程数限制
    "CapDrop": ["ALL"],               # 删除所有 capabilities
    "CapAdd": [],                      # 不添加任何 capability
    "ReadonlyRootfs": False,           # 根 FS 需可写（MCP 网关需要）
    "SecurityOpt": ["no-new-privileges"],  # 禁止提权
    "NetworkMode": "bridge",           # 使用 bridge 网络
}
```

### 5.3 E2BWorkspace 安全评估

| 维度 | 评估 | 风险等级 |
|------|------|---------|
| 文件隔离 | ✅ 云端 VM 完全隔离 | 🟢 低 |
| 进程隔离 | ✅ 独立 VM 进程空间 | 🟢 低 |
| 网络隔离 | ✅ E2B 沙箱网络（可控） | 🟢 低 |
| 用户隔离 | ✅ 非 root 用户（/home/user） | 🟢 低 |
| 资源限制 | ✅ E2B 平台级限制 | 🟢 低 |
| 数据持久化 | ✅ pause/resume，不泄露到宿主机 | 🟢 低 |

**适用场景**：生产环境推荐，尤其是多租户场景。

### 5.4 安全对比总结

| 维度 | Local | Docker | E2B |
|------|-------|--------|-----|
| 隔离强度 | ❌ 无 | 🟡 容器级 | 🟢 VM 级 |
| Agent 被 prompt injection 后影响范围 | 宿主机 | 容器内 | 云沙箱内 |
| 适合生产 | ❌ | ⚠️ 需加固 | ✅ |
| 适合多租户 | ❌ | ⚠️ 需加固 | ✅ |
| 运维复杂度 | 低 | 中 | 低（托管） |
| 成本 | 免费 | 宿主机资源 | E2B 按量计费 |

---

## 6. 资源消耗模型

### 6.1 LocalWorkspace

| 资源 | 消耗 | 说明 |
|------|------|------|
| CPU | 与 Agent 进程共享 | 工具在主进程中执行 |
| 内存 | 与 Agent 进程共享 | 无额外开销 |
| 磁盘 | 工作目录大小 | `basedir/agent_id/` |
| 网络 | 宿主机网络 | 无额外开销 |
| 启动时间 | <100ms | 仅文件系统操作 |

### 6.2 DockerWorkspace

| 资源 | 消耗 | 说明 |
|------|------|------|
| CPU | 容器独占（默认无限制） | 建议生产环境设置 `NanoCpus` |
| 内存 | 容器独占（默认无限制） | 建议生产环境设置 `Memory` |
| 磁盘 | 镜像(~500MB) + 工作目录 | 镜像按内容哈希缓存复用 |
| 网络 | 容器网络 | Gateway 端口绑定 127.0.0.1 |
| 启动时间 | 2-10s | 首次构建镜像更久；后续秒级启动 |
| 并发限制 | 宿主机 Docker 并发上限 | 每个会话一个容器 |

### 6.3 E2BWorkspace

| 资源 | 消耗 | 说明 |
|------|------|------|
| CPU | E2B VM 分配 | 按沙箱规格 |
| 内存 | E2B VM 分配 | 按沙箱规格 |
| 磁盘 | E2B 沙箱磁盘 | pause 后保留 |
| 网络 | E2B 代理网络 | HTTPS 代理 |
| 启动时间 | 5-15s | 首次 bootstrap 更久；pause/resume 秒级 |
| 并发限制 | E2B 账户配额 | 按套餐限制 |
| 费用 | 按沙箱运行时间计费 | pause 状态可能也计费 |

### 6.4 WorkspaceManager 缓存

所有三种 Manager 都有缓存机制，避免每次请求重建工作区：

| Manager | 缓存策略 | TTL | 清理 |
|---------|---------|-----|------|
| LocalWorkspaceManager | workspace_id → 实例 | 3600s | 获取时惰性清理过期项 |
| DockerWorkspaceManager | workspace_id → 实例 | 3600s | 后台定时扫描（300s 间隔） |
| E2BWorkspaceManager | workspace_id → 实例 | 3600s | 后台定时扫描（300s 间隔） |

---

## 7. 如何选择工作区后端

```
是否生产环境?
├── 否 → LocalWorkspace (开发够用, 零配置)
└── 是 → 是否多租户?
    ├── 是 → E2BWorkspace (VM 级隔离, 托管运维)
    └── 否 → 是否有 Docker 运维能力?
        ├── 是 → DockerWorkspace (需加固安全配置)
        └── 否 → E2BWorkspace (无需运维)
```

| 场景 | 推荐后端 | 原因 |
|------|---------|------|
| 本地开发调试 | Local | 零配置，启动快 |
| CI/CD 自动化测试 | Local 或 Docker | 可控环境 |
| 单租户生产（内网） | Docker（加固后） | 成本低 |
| 多租户生产 | E2B | VM 级隔离，安全 |
| 需要运行不可信代码 | E2B | 最高安全级别 |
| 需要GPU/特殊硬件 | Local | Docker/E2B 不支持 |
| 需要持久化工作目录 | Docker (bind-mount) | 文件挂载宿主机 |

---

## 8. XRuntime 如何切换到 Docker/E2B 沙箱

当前 `build_xruntime_app()` 硬编码使用 `LocalWorkspaceManager`。切换到 Docker 或 E2B 需要修改 `_server.py`。

### 8.1 切换到 DockerWorkspace

```python
# 在 build_xruntime_app() 中替换:
from agentscope.app.workspace_manager import DockerWorkspaceManager

workspace_manager = DockerWorkspaceManager(
    basedir="/var/lib/xruntime/workspaces",
    default_mcps=[],       # 从 config.mcps 加载
    skill_paths=[s["path"] for s in config.skills],
    ttl=3600.0,
)

app = create_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=workspace_manager,  # ← 传入 Docker 管理器
    extra_agent_middlewares=ext["extra_agent_middlewares"],
)
```

### 8.2 切换到 E2BWorkspace

```python
from agentscope.app.workspace_manager import E2BWorkspaceManager

workspace_manager = E2BWorkspaceManager(
    api_key=os.environ["E2B_API_KEY"],     # E2B 平台 API Key
    template="base",                        # E2B 沙箱模板
    default_mcps=[],
    skill_paths=[s["path"] for s in config.skills],
    ttl=3600.0,
)

app = create_app(
    storage=storage,
    message_bus=message_bus,
    workspace_manager=workspace_manager,  # ← 传入 E2B 管理器
    extra_agent_middlewares=ext["extra_agent_middlewares"],
)
```

### 8.3 通过配置选择后端（推荐做法）

在 `xruntime.yaml` 中添加配置项：

```yaml
workspace:
  backend: local          # local / docker / e2b
  basedir: /var/lib/xruntime/workspaces
  docker:
    image: python:3.11-slim
    host_workdir: true
  e2b:
    api_key: ${E2B_API_KEY}
    template: base
```

然后修改 `build_xruntime_app()` 根据 `config.workspace.backend` 选择 Manager。

> **注意**：当前版本尚未实现配置化的后端选择，上述代码展示了如何手动切换。后续版本将支持配置化选择。

---

## 附录：MCP Gateway 架构详解

Docker/E2B 工作区在容器/沙箱内部运行一个 FastAPI 网关进程，代理所有 MCP 服务器。这是实现沙箱内工具执行的核心机制。

```
┌─ 宿主机 ────────────────────────────────────────────────────┐
│                                                            │
│  Toolkit                                                   │
│    └── GatewayMCPTool("mcp__github__get_issue")            │
│          └── __call__(): HTTP POST                         │
│                                                            │
│  GatewayMCPClient                                          │
│    ├── gateway_url = "http://127.0.0.1:32145"             │
│    ├── token = "a1b2c3d4..." (uuid4().hex, 每次 init 新生成)│
│    └── httpx.AsyncClient (连接池)                          │
│                                                            │
└───────────────┬────────────────────────────────────────────┘
                │ HTTP (Bearer Token)
                ▼
┌─ 容器/沙箱 ────────────────────────────────────────────────┐
│                                                            │
│  FastAPI Gateway (_mcp_gateway_app.py)                     │
│    ├── GET  /health              (无需认证)                 │
│    ├── GET  /mcps                (列出已注册 MCP)           │
│    ├── POST /mcps                (注册新 MCP)               │
│    ├── DELETE /mcps/{name}       (移除 MCP)                 │
│    ├── GET  /mcps/{name}/tools   (列出 MCP 工具)           │
│    └── POST /mcps/{name}/tools/{tool}  (调用工具)          │
│                                                            │
│  MCP 服务器 (stdio, 容器内子进程)                           │
│    ├── github-mcp: npx @github/mcp                        │
│    ├── filesystem-mcp: npx @modelcontextprotocol/server-fs│
│    └── ...                                                  │
│                                                            │
│  /workspace/ (工作目录, Agent 可见的根目录)                  │
│    ├── .mcp                                                 │
│    ├── skills/                                              │
│    └── sessions/                                            │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**设计要点**：
1. **Bearer Token 每次初始化重新生成** — 不持久化，不跨容器泄露
2. **Gateway 端口仅绑定 127.0.0.1** — 外部网络不可达
3. **Gateway 脚本通过绝对路径调用** — 避免 `python -m` 触发 `agentscope.__init__` 重型导入
4. **agentscope 以 `--no-deps` 安装到容器** — 只装 MCP 客户端依赖，不拉全部依赖树
