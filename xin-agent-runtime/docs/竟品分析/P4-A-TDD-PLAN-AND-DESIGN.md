# P4-A: 并发与性能优化 — TDD 开发计划与设计文档

> 生成日期：2026-07-01
> 基础：P4-D 基准套件已完成（31 tests, 100% coverage）
> 重点：ConcurrencyPool + RateLimiter + ResultCache + ConnectionPool
> 开发方法：TDD（Red → Green → Refactor）

---

## 一、设计目标

| 组件 | 目标 | 预期收益 |
|------|------|----------|
| ConcurrencyPool | 全局 + per-agent 并行度控制 | 防止资源耗尽 |
| RateLimiter | 令牌桶速率限制 | 防止下游过载 |
| ResultCache | step 结果缓存 | 重复执行 50ms→1ms |
| ConnectionPool | Redis 连接池 | 5ms→0.5ms/op |

---

## 二、ConcurrencyPool 设计

### 2.1 类设计

```python
class ConcurrencyPool:
    """全局 + per-agent 并行度控制器。

    基于 asyncio.Semaphore，提供：
    - 全局 max_concurrent_steps 限制
    - 每 agent 独立 semaphore
    - 超时等待（避免死锁）
    - 当前并发数 / 等待数指标
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        per_agent_limit: int = 3,
        acquire_timeout: float = 30.0,
    ) -> None: ...

    async def acquire(self, agent: str) -> "PoolSlot":
        """获取一个执行槽（全局 + agent 级）。

        Returns:
            PoolSlot: 上下文管理器，自动释放。
        """
        ...

    @property
    def active_count(self) -> int:
        """当前活跃执行数。"""
        ...

    @property
    def waiting_count(self) -> int:
        """当前等待数。"""
        ...

    @property
    def utilization(self) -> float:
        """利用率 (0.0-1.0)。"""
        ...
```

### 2.2 执行流程

```
step 开始
  │
  ▼
pool.acquire(agent="coder")
  │  ├─ 全局 semaphore.acquire()  ← 超时 30s
  │  └─ agent semaphore.acquire() ← 超时 30s
  ▼
执行 step (async)
  │
  ▼
PoolSlot.__exit__()
  ├─ agent semaphore.release()
  └─ 全局 semaphore.release()
```

### 2.3 TDD 测试计划 (15 tests)

| 测试类 | 测试数 | 覆盖场景 |
|--------|--------|----------|
| TestConcurrencyPoolConstruction | 3 | 构造、默认值、参数 |
| TestConcurrencyPoolAcquire | 4 | 获取/释放、async with 语法 |
| TestConcurrencyPoolGlobalLimit | 3 | 全局并行度限制、超时 |
| TestConcurrencyPoolPerAgent | 3 | per-agent 限制、不同 agent 独立 |
| TestConcurrencyPoolMetrics | 2 | active_count / waiting_count / utilization |

---

## 三、RateLimiter 设计

### 3.1 类设计

```python
class TokenBucketRateLimiter:
    """令牌桶速率限制器。

    参数：
    - rate: 每秒令牌数
    - burst: 突发容量
    - refill_interval: 令牌补充间隔(秒)

    算法：
    1. 桶初始满(burst 个令牌)
    2. 每 refill_interval 秒补充 rate*refill_interval 个令牌
    3. acquire() 消耗 1 个令牌
    4. 桶空时 acquire() 阻塞等待(或超时)
    """

    def __init__(
        self,
        rate: float = 100.0,
        burst: int = 20,
    ) -> None: ...

    async def acquire(self, timeout: float = 30.0) -> bool:
        """获取一个令牌。

        Returns:
            bool: True 成功, False 超时。
        """
        ...

    @property
    def available_tokens(self) -> float:
        """当前可用令牌数。"""
        ...
```

### 3.2 TDD 测试计划 (10 tests)

| 测试类 | 测试数 | 覆盖场景 |
|--------|--------|----------|
| TestRateLimiterConstruction | 2 | 构造、参数校验 |
| TestRateLimiterAcquire | 3 | 获取令牌、桶空阻塞、超时 |
| TestRateLimiterBurst | 2 | 突发容量、令牌耗尽 |
| TestRateLimiterRefill | 2 | 令牌补充、补充后可获取 |
| TestRateLimiterConcurrency | 1 | 并发 acquire 线程安全 |

---

## 四、ResultCache 设计

### 4.1 类设计

```python
class ResultCache:
    """Step 结果缓存 ABC。"""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...
    async def invalidate(self, key: str) -> bool: ...
    async def clear(self) -> int: ...

class InMemoryResultCache(ResultCache):
    """LRU + TTL 内存缓存。"""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600) -> None: ...

class RedisResultCache(ResultCache):
    """Redis 分布式缓存。"""

    def __init__(self, redis_url: str, key_prefix: str = "wf:cache:") -> None: ...
```

### 4.2 缓存键策略

```python
def cache_key(workflow_id: str, step_id: str, context: dict) -> str:
    """hash(workflow_id + step_id + sorted(context))"""
    import hashlib, json
    ctx_hash = hashlib.sha256(
        json.dumps(context, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"wf:{workflow_id}:{step_id}:{ctx_hash}"
```

### 4.3 TDD 测试计划 (15 tests)

| 测试类 | 测试数 | 覆盖场景 |
|--------|--------|----------|
| TestInMemoryResultCache | 6 | get/set/invalidate/clear、TTL、LRU |
| TestRedisResultCache | 5 | fakeredis、TTL、多 tenant 隔离 |
| TestCacheKeyGeneration | 2 | 确定性哈希、context 变化 |
| TestCacheIntegration | 2 | run_workflow(cache=) 集成 |

---

## 五、ConnectionPool 设计

### 5.1 RedisCredentialStore 连接池

```python
class RedisCredentialStore:
    def __init__(
        self,
        redis_url: str,
        pool_size: int = 10,
        pool_timeout: float = 5.0,
    ) -> None:
        # 使用 redis.ConnectionPool 替代每次 from_url
        import redis.asyncio as aioredis
        self._pool = aioredis.ConnectionPool.from_url(
            redis_url,
            max_connections=pool_size,
            socket_connect_timeout=pool_timeout,
        )

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = aioredis.Redis(connection_pool=self._pool)
        return self._client
```

### 5.2 TDD 测试计划 (10 tests)

| 测试类 | 测试数 | 覆盖场景 |
|--------|--------|----------|
| TestConnectionPool | 4 | 连接复用、池大小限制、超时 |
| TestRedisStoreWithPool | 3 | fakeredis + 连接池、CRUD 正常 |
| TestPoolExhaustion | 2 | 池耗尽等待、超时抛异常 |
| TestPoolMetrics | 1 | 活跃连接数指标 |

---

## 六、文件结构

```
src/xruntime/_runtime/_workflow/
├── _concurrency.py      # 【新增】ConcurrencyPool
├── _rate_limiter.py      # 【新增】TokenBucketRateLimiter
├── _cache.py            # 【新增】ResultCache + InMemoryResultCache + RedisResultCache
├── _benchmark.py        # ✅ 已完成 (P4-D)
├── _config.py           # 扩展：max_concurrent_steps / rate_limit_rps / cache_*
└── __init__.py          # 导出

src/xruntime/_runtime/_credential/
└── _redis_store.py      # 扩展：连接池
```

---

## 七、执行计划

| 顺序 | 任务 | 预估测试 | 依赖 |
|------|------|----------|------|
| 1 | ConcurrencyPool TDD | 15 | 无 |
| 2 | RateLimiter TDD | 10 | 无 |
| 3 | ResultCache TDD | 15 | 无 |
| 4 | ConnectionPool TDD | 10 | 无 |
| 5 | Config 扩展 + 集成测试 | 10 | 1-4 |
| 6 | 全量回归 + 基准对比 | — | 5 |

**总计**：50+ tests，4 个新模块，预期 P50 延迟降低 60%。
