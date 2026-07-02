# XRuntime 多租户隔离架构设计文档

## 1. 概述

本文档描述 XRuntime 的多租户隔离（Multi-Tenant Isolation）架构设计，
涵盖数据隔离、模型治理、工具治理三个维度。基于 0630 review 中指出的
"Redis key prefix 启动时固定 tenant，不是真正 per-request 多租户隔离"
问题，进行了全面重构。

## 2. 设计目标

- **数据强隔离**：不同租户的 Storage、MessageBus 数据在共享 Redis 上
  完全隔离，互不干扰
- **动态 per-request**：租户上下文在每个请求上动态解析，而非启动时固定
- **async 安全**：使用 `contextvars.ContextVar` 传递租户上下文，
  异步任务间不串扰
- **透明无侵入**：对 AgentScope 核心层完全透明，通过 Wrapper 模式实现
- **可观测**：关键节点有详细日志输出，便于排查多租户隔离问题

## 3. 租户上下文传递机制

### 3.1 核心组件：`current_tenant` contextvar

**位置**：`src/xruntime/_infra/_tenant.py`

```python
current_tenant: ContextVar[str | None] = ContextVar(
    "current_tenant",
    default=None,
)
```

- 使用 Python 标准库 `contextvars.ContextVar`
- async 安全，每个协程有独立的上下文
- 不会在不同请求/任务之间串扰

### 3.2 上下文生命周期

```
请求进入
    │
    ▼
AuthMiddleware 解析 principal（API Key / JWT）
    │
    ▼
Protocol Adapter 解析 XRuntimeRequest
    │
    ▼
Gateway Handler 设置 current_tenant.set(tenant_id)
    │
    ├──► Storage 操作 ──► TenantAwareRedisStorage._resolve_key_config()
    │                         读取 current_tenant.get()
    │
    ├──► MessageBus 操作 ──► TenantAwareMessageBus._prefix()
    │                            读取 current_tenant.get()
    │
    └──► Model Resolver ──► 传入 tenant_id 参数
                               检查 tenant model_allowlist
    │
    ▼
Stream 结束 / 异常 → current_tenant.clear()
```

### 3.3 防欺骗（Anti-Spoofing）

**位置**：`src/xruntime/_gateway/_extension.py` `_handler` 函数

- 如果 AuthMiddleware 解析出了 principal（认证通过），
  其 `tenant_id` / `user_id` **优先**于请求体中携带的值
- 客户端不能通过修改请求体中的 `tenant_id` 来冒充其他租户
- （当前简化实现直接使用 principal 的值；完整实现应在
  检测到 mismatch 时返回 403）

## 4. Storage 多租户隔离

### 4.1 问题（修复前）

```python
# 旧实现：启动时用第一个 tenant 构造 key_config，之后固定
storage = RedisStorage(..., key_prefix=f"tenant:{first_tenant_id}:")
```

- 所有请求共享同一个 tenant prefix
- 多租户只是名义上的，数据完全混在一起

### 4.2 解决方案：`TenantAwareRedisStorage`

**位置**：`src/xruntime/_infra/_tenant_storage.py`

采用 **Wrapper 模式 + 动态 key_config 解析**：

```
应用代码
    │
    ▼ 调用 storage.get_session(...)
TenantAwareRedisStorage
    │
    ├──► __getattr__ 拦截 "key_config" 属性访问
    │       │
    │       ▼
    │   _resolve_key_config()
    │       │
    │       ├──► current_tenant.get()   ← 从 contextvar 读取
    │       └──► build_tenant_key_config(tenant_id, ...)  ← 动态构造
    │
    ▼
底层 RedisStorage（使用动态解析的 key_config）
```

### 4.3 关键设计点

1. **`__getattr__` 拦截**：`key_config` 每次访问都重新计算，
   确保不同请求拿到不同租户的 prefix
2. **延迟解析**：只有真正访问 `key_config` 时才解析，
   不访问的方法直接透传，开销极小
3. **Fail Closed**：如果 `current_tenant` 未设置，
   抛出 `TenantIsolationError`，而不是静默使用空前缀
4. **透明委托**：所有其他方法/属性通过 `__getattr__` 直接委托给
   底层 `RedisStorage`，保持接口完全兼容

### 4.4 Redis Key 命名空间

Key 格式：`tenant:{tid}:{agentscope_key}`

示例：

| 租户 | Session 存储 Key |
|------|-----------------|
| tenant-a | `tenant:tenant-a:agentscope:session:meta:sess_123` |
| tenant-b | `tenant:tenant-b:agentscope:session:meta:sess_123` |

即使 session_id 相同（不同租户各自创建），数据也完全隔离。

## 5. MessageBus 多租户隔离

### 5.1 问题（修复前）

MessageBus 的所有 channel / stream key 都是全局的，没有 tenant 前缀：

- `agentscope:session:events:{session_id}`
- `agentscope:session:lock:{session_id}`
- `agentscope:session:cancel`
- `agentscope:bg_tasks:{session_id}`

不同租户如果 session_id 碰撞，会互相干扰。

### 5.2 解决方案：`TenantAwareMessageBus`

**位置**：`src/xruntime/_infra/_tenant_message_bus.py`

显式重写所有涉及 key 的方法，在调用底层 bus 前加上 tenant prefix：

```python
def _k(self, key: str) -> str:
    return f"{self._prefix()}{key}"

async def session_publish_event(self, session_id, event):
    key = self._k(f"agentscope:session:events:{session_id}")
    return await self._bus.log_append(key, event, ...)
```

### 5.3 覆盖的方法

| 分类 | 方法 |
|------|------|
| Session 运行协调 | `session_run`, `session_is_running` |
| Session 事件 | `session_publish_event`, `session_read_events`, `session_subscribe_events` |
| Session 取消 | `session_publish_cancel`, `session_subscribe_cancel` |
| Session 清理 | `session_purge` |
| 后台任务 | `bg_task_register`, `bg_task_unregister`, `bg_task_exists`, `bg_task_list`, `bg_task_purge` |

未重写的方法通过 `__getattr__` 直接透传到底层 bus。

### 5.4 Pub/Sub Channel 隔离效果

```
租户 A 的 session_123 事件流：
  tenant:tenant-a:agentscope:session:events:session_123

租户 B 的 session_123 事件流：
  tenant:tenant-b:agentscope:session:events:session_123

两者完全独立，互不干扰
```

## 6. 模型治理（Model Governance）

### 6.1 Tenant Model Allowlist

**位置**：
- 配置：`src/xruntime/_config.py` `TenantConfig.model_allowlist`
- 执行：`src/xruntime/_runtime/_model_resolver.py`

#### 配置方式

```yaml
tenants:
  - id: tenant-a
    name: "租户 A"
    model_allowlist:
      - gpt-4-provider
      - claude-3-provider
  - id: tenant-b
    name: "租户 B"
    # 不设置 model_allowlist 表示无限制
```

#### 执行流程

```
resolve(model_config_name, tenant_id=...)
    │
    ▼
查找 tenant 配置 → 获取 model_allowlist
    │
    ├─► allowlist 为 None → 继续正常解析（无限制）
    ├─► model_config_name 在 allowlist 中 → 继续解析
    └─► model_config_name 不在 allowlist 中 → 返回 None（拒绝）
```

#### 关键特性

1. **白名单机制**：只能显式允许，不能显式拒绝
2. **None = 不限制**：未配置表示不做限制（向后兼容）
3. **在解析源头拦截**：在 `_resolve_source` 阶段就拦截，
   比在更上层检查更可靠
4. **`resolve` 和 `resolve_provider` 都生效**：两个入口都检查

### 6.2 Gateway 层集成

在 `_materialize_session` 中调用 resolver 时传入 `tenant_id`：

```python
provider = state.model_resolver.resolve_provider(
    model_config_name,
    state.config,
    tenant_id=tenant_id,  # ← 传入当前请求的 tenant
)
```

如果模型不在 tenant 的 allowlist 中，返回 400 错误。

## 7. 工具治理（Tool Governance）

### 7.1 Tenant Tool Allowlist

**位置**：
- 配置：`src/xruntime/_config.py` `TenantConfig.tool_allowlist`
- 执行：`src/xruntime/_gateway/_plan.py` `build_plan_from_request`

#### 执行逻辑

```
最终可用工具 = 请求的 allowed_tools ∩ tenant_tool_allowlist
```

- 如果 tenant 没有配置 allowlist（`None`），不做额外限制
- 如果 tenant 配置了 allowlist，最终工具集合是两者的交集
- 请求可以请求更少的工具，但不能请求 tenant 不允许的工具

#### Gateway 层集成

在 `_handler` 中从配置查找 tenant 的 tool allowlist：

```python
tenant_tool_allowlist: set[str] | None = None
for tenant_cfg in state.config.tenants:
    if tenant_cfg.id == effective_tenant:
        if tenant_cfg.tool_allowlist is not None:
            tenant_tool_allowlist = set(tenant_cfg.tool_allowlist)
        break

plan = build_plan_from_request(
    xrt_request,
    tenant_tool_allowlist=tenant_tool_allowlist,
    ...
)
```

## 8. 流式 Backpressure

### 8.1 问题（修复前）

```python
queue = asyncio.Queue()  # 无界队列
```

- 如果客户端消费速度 << 生产速度，队列会无限增长
- 内存耗尽风险
- 没有背压机制

### 8.2 解决方案：有界队列

**位置**：`src/xruntime/_gateway/_extension.py` `_serialize_stream`

```python
STREAM_QUEUE_MAXSIZE = 1000  # 可配置常量

queue: asyncio.Queue[dict | None] = asyncio.Queue(
    maxsize=STREAM_QUEUE_MAXSIZE,
)
```

#### Backpressure 行为

```
生产者（ChatService 事件）
    │
    ├──► queue.put(event)
    │       │
    │       ├──► 队列未满 → 立即放入，继续执行
    │       └──► 队列已满 → 阻塞等待（自动背压）
    │
消费者（HTTP SSE 流）
    │
    └──► queue.get() → 发送到客户端 → 消费掉一个 slot
```

- 当客户端慢时，生产者自然被阻塞，不会无限积累
- 保护服务端内存，防止 OOM
- 默认值 1000 是经验值，兼顾突发流量和内存限制

## 9. Server 启动流程

**位置**：`src/xruntime/_server.py` `build_xruntime_app`

```
加载配置
    │
    ▼
创建 RedisStorage
    │
    ▼ 包装
TenantAwareRedisStorage(storage, prefix_template)
    │
    ▼
创建 RedisMessageBus
    │
    ▼ 包装
TenantAwareMessageBus(bus, prefix_template)
    │
    ▼
create_app(
    storage=tenant_aware_storage,
    message_bus=tenant_aware_message_bus,
    ...
)
    │
    ▼
mount_protocol_adapters(...)  ← 注入 handler
    │
    ▼
日志输出：Multi-tenant isolation enabled + 各 tenant 配置
```

## 10. 可观测性：日志设计

### 10.1 Logger 命名空间

| Logger Name | 职责 | 级别 |
|-------------|------|------|
| `xruntime.server` | Server 启动、配置 | INFO |
| `xruntime.gateway` | 请求入口、协议、认证 | INFO |
| `xruntime.gateway.materialize` | Session 物化、模型解析 | INFO/WARNING |
| `xruntime.tenant.storage` | Storage tenant prefix 解析 | DEBUG |
| `xruntime.tenant.message_bus` | MessageBus tenant prefix 解析 | DEBUG |

### 10.2 关键日志点

#### Server 启动时（INFO）

```
Multi-tenant isolation enabled: storage=TenantAwareRedisStorage,
  message_bus=TenantAwareMessageBus, prefix_template=tenant:{tid}:,
  configured_tenants=2
  tenant tenant-a (租户 A): tools=read,write, models=gpt-4,claude-3
  tenant tenant-b (租户 B): tools=all (no restriction), models=all (no restriction)
```

#### 请求入口（INFO）

```
Request received: protocol=anthropic_messages, tenant=tenant-a,
  user=user123, session=sess_456, tool_mode=server
```

#### 认证主体（INFO）

```
Authenticated principal: tenant=tenant-a, user=user123, role=admin
```

#### 模型解析（INFO / WARNING）

```
Resolving model: tenant=tenant-a, model_config=gpt-4-provider
Model resolved: tenant=tenant-a, provider=openai, model=gpt-4
Model resolution FAILED for tenant=tenant-a, model_config=claude-3-provider
  (not in tenant allowlist or not configured)
```

#### Prefix 解析（DEBUG）

```
Resolving storage key_config for tenant=tenant-a, prefix_template=tenant:{tid}:
Resolved message bus prefix for tenant=tenant-a: tenant:tenant-a:
```

### 10.3 日志级别调整

开发/调试时：

```python
import logging
logging.getLogger("xruntime.tenant").setLevel(logging.DEBUG)
```

生产环境默认 INFO 级别，DEBUG 级别的 prefix 解析日志不会输出，避免刷屏。

## 11. 测试覆盖

### 11.1 新增测试文件

| 测试文件 | 测试数 | 覆盖内容 |
|---------|--------|---------|
| `test_tenant_aware_storage.py` | 7 | Storage 多租户隔离、prefix 解析、错误处理 |
| `test_tenant_aware_message_bus.py` | 5 | MessageBus 多租户隔离、事件/锁/任务隔离 |
| `test_stream_backpressure.py` | 5 | 有界队列、背压行为、慢消费者场景 |
| `test_tenant_tool_allowlist.py` | 5 | 工具白名单、交集计算、None 表示无限制 |
| `test_tenant_model_allowlist.py` | 6 | 模型白名单、resolve/resolve_provider 双入口 |

### 11.2 测试验证结果

```
682 passed, 18 skipped, 1 warning in 8.88s
```

新增 28 个测试全部通过，无回归。

## 12. 安全边界

### 12.1 Fail Closed

- `current_tenant` 未设置时，Storage 和 MessageBus 都抛出
  `TenantIsolationError`，而不是静默使用空前缀
- 防止因代码路径遗漏导致的跨租户数据泄露

### 12.2 认证优先

- AuthMiddleware 解析的 principal 优先级高于请求体中的 tenant_id
- 防止客户端通过修改请求体冒充其他租户

### 12.3 白名单机制

- 工具和模型都使用白名单机制，只能显式允许
- 默认无限制（向后兼容），配置后自动收紧

## 13. 未来优化方向

### 13.1 近期

- 分布式限流（Redis-backed Rate Limiter）
- 沙箱资源限制（Docker CPU/Memory 配额）
- API Key 持久化 + Hash at Rest

### 13.2 中期

- RBAC 策略持久化（Postgres/Redis）
- 审计日志集中存储（SIEM 对接）
- 模型成本预算控制（per-tenant / per-user）

### 13.3 长期

- 多 Region 部署 + 数据驻留
- 专有租户部署模式（Dedicated Tenant）
- 数据分类分级 + DLP 扫描

## 14. 相关文件索引

| 模块 | 文件路径 |
|------|---------|
| 租户上下文 | `src/xruntime/_infra/_tenant.py` |
| 租户 Storage | `src/xruntime/_infra/_tenant_storage.py` |
| 租户 MessageBus | `src/xruntime/_infra/_tenant_message_bus.py` |
| 模型解析器 | `src/xruntime/_runtime/_model_resolver.py` |
| Gateway 扩展 | `src/xruntime/_gateway/_extension.py` |
| 配置模型 | `src/xruntime/_config.py` |
| Server 入口 | `src/xruntime/_server.py` |
| 执行计划 | `src/xruntime/_gateway/_plan.py` |
