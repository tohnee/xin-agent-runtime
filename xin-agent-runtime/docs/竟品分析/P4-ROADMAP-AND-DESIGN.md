# P4 迭代功能路线图与设计文档

> 生成日期：2026-07-01
> 基础：P3 全阶段交付验收通过（1245 passed, 13 模块 100% 覆盖率）
> 重点：**性能优化 + 高级编排能力**
> 开发方法：TDD（Red → Green → Refactor）
> 状态：规划草案 v0.1

---

## 一、P4 规划背景

### 1.1 P3 交付基础

P3 完成了四大能力域：

1. **控制流扩展**：条件分支 / 循环 / 子工作流 / 定时器
2. **HITL 集成**：ApprovalStep + ApprovalStore
3. **凭证硬化**：Redis 持久化 / 自动轮换 / Scope 层级
4. **可观测性**：OTel Tracing / Per-step Metrics / 审计日志

### 1.2 P4 差距分析

基于 P3 完成情况和 Vercel Eve / Temporal / Airflow 等竞品分析，P3 后仍存在以下差距：

| 能力 | P3 现状 | 差距 | P4 对应 |
|------|---------|------|---------|
| **工作流并行执行** | 层内并行 (asyncio.gather) | 无并行度限制 + 无资源池 | P4-A |
| **批量并发控制** | 无 | 无 semaphore / rate limiter | P4-A |
| **工作流缓存** | 无 | 重复执行相同输入浪费资源 | P4-A |
| **异步事件驱动** | 无 | 不支持外部事件触发 workflow | P4-B |
| **工作流版本管理** | 无 | 无版本化 + 灰度发布 | P4-B |
| **分布式执行** | 单进程 | 无多节点调度 | P4-C |
| **性能基准** | 无 | 无基准数据 + 无瓶颈分析 | P4-D |
| **连接池优化** | 每次 new connection | 无 Redis / HTTP 连接池 | P4-D |
| **内存优化** | 全量 context 传递 | 大 workflow 内存膨胀 | P4-D |

### 1.3 P4 设计原则

1. **性能优先**：每个新功能必须有性能基准对比
2. **不破坏 P3 API**：所有新功能向后兼容
3. **可测量**：每个优化必须有指标验证
4. **渐进式**：每个 Phase 独立可交付

---

## 二、P4 路线图总览

```
P4-A: 并发与性能优化     ──────────┐
  (并行池 + 限流 + 缓存 + 连接池)    │
                                    │
P4-B: 高级编排能力       ──────────┤
  (事件驱动 + 版本管理 + 灰度)       │
                                    │
P4-C: 分布式执行         ──────────┤
  (多节点调度 + 任务队列)            │
                                    │
P4-D: 性能基准与调优     ──────────┘
  (基准套件 + 瓶颈分析 + 优化验证)
```

| Phase | 主题 | 预估模块数 | 预估测试数 | 优先级 |
|-------|------|-----------|-----------|--------|
| P4-A | 并发与性能优化 | 4 | 50+ | P0（核心性能） |
| P4-B | 高级编排能力 | 3 | 40+ | P1（高级功能） |
| P4-C | 分布式执行 | 3 | 35+ | P2（规模化） |
| P4-D | 性能基准与调优 | 2 | 20+ | P1（可测量） |

---

## 三、P4-A：并发与性能优化

### 3.1 目标

提升 workflow 执行性能：并行度控制、限流、结果缓存、连接池复用。

### 3.2 设计

#### 3.2.1 并行执行池（ConcurrencyPool）

```python
from xruntime._runtime._workflow import WorkflowConfig

config = WorkflowConfig(
    max_concurrent_steps=10,  # 全局并行度上限
    per_agent_concurrency=3,  # 每 agent 并行度
)
```

**新增类**：`ConcurrencyPool` — 基于 `asyncio.Semaphore` 的并行度控制器。

**特性**：
- 全局 `max_concurrent_steps` 限制并行 step 数
- 每 agent 独立 semaphore（防止单 agent 占满资源）
- 超时等待（避免死锁）
- 指标暴露（当前并发数 / 等待数）

#### 3.2.2 速率限制器（RateLimiter）

```python
config = WorkflowConfig(
    rate_limit_rps=100,  # 每秒最大请求数
    rate_limit_burst=20,  # 突发容量
)
```

**新增类**：`TokenBucketRateLimiter` — 令牌桶算法。

#### 3.2.3 结果缓存（ResultCache）

```python
from xruntime._runtime._workflow import ResultCache, InMemoryResultCache

cache = InMemoryResultCache(max_size=1000, ttl_seconds=3600)
result = await run_workflow(wf, executor, cache=cache)
```

**新增类**：`ResultCache` (ABC) + `InMemoryResultCache` + `RedisResultCache`

**缓存键**：`hash(workflow_id + step_id + context_hash)`
**TTL 过期**：自动清除
**命中率指标**：cache_hit_total / cache_miss_total

#### 3.2.4 连接池（ConnectionPool）

```python
from xruntime._runtime._credential import RedisCredentialStore

store = RedisCredentialStore(
    redis_url="redis://localhost:6379/0",
    pool_size=10,           # 连接池大小
    pool_timeout=5.0,       # 获取连接超时
)
```

**优化**：复用 Redis 连接，避免每次操作新建连接。

### 3.3 文件结构

```
src/xruntime/_runtime/_workflow/
├── _concurrency.py      # 【新增】ConcurrencyPool + RateLimiter
├── _cache.py            # 【新增】ResultCache + InMemoryResultCache
└── _config.py           # 扩展：max_concurrent_steps / rate_limit_*

src/xruntime/_runtime/_credential/
└── _redis_store.py      # 扩展：连接池
```

### 3.4 TDD 测试计划

| 测试文件 | 测试数 | 覆盖场景 |
|----------|--------|----------|
| `test_concurrency.py` | 15 | semaphore 并发限制、超时、per-agent 限制 |
| `test_rate_limiter.py` | 10 | 令牌桶、突发、速率限制 |
| `test_result_cache.py` | 15 | 缓存命中/未命中、TTL、LRU 淘汰 |
| `test_connection_pool.py` | 10 | 连接复用、超时、池耗尽 |

---

## 四、P4-B：高级编排能力

### 4.1 目标

扩展编排能力：事件驱动触发、工作流版本管理、灰度发布。

### 4.2 设计

#### 4.2.1 事件驱动触发（EventTrigger）

```python
from xruntime._runtime._workflow import EventTrigger, EventBus

bus = EventBus()
trigger = EventTrigger(
    event="github.pr.opened",
    workflow_id="review-pr",
    bus=bus,
)
# 外部系统发布事件 → 自动触发 workflow
await bus.publish("github.pr.opened", {"pr_id": 123})
```

**新增类**：`EventBus` + `EventTrigger` + `EventListener`

#### 4.2.2 工作流版本管理（WorkflowVersion）

```python
wf_v1 = WorkflowBuilder().id("review-pr:v1").build()
wf_v2 = WorkflowBuilder().id("review-pr:v2").build()

registry = WorkflowRegistry()
registry.register(wf_v1, version="v1", default=False)
registry.register(wf_v2, version="v2", default=True)  # 灰度到 v2
```

**新增类**：`WorkflowRegistry` + `WorkflowVersion`

#### 4.2.3 灰度发布（CanaryDeployment）

```python
deploy = CanaryDeployment(
    registry=registry,
    workflow_id="review-pr",
    canary_percent=10,  # 10% 流量到 v2
)
```

### 4.3 文件结构

```
src/xruntime/_runtime/_workflow/
├── _events.py           # 【新增】EventBus + EventTrigger
├── _registry.py         # 【新增】WorkflowRegistry + Version
└── _canary.py           # 【新增】CanaryDeployment
```

### 4.4 TDD 测试计划

| 测试文件 | 测试数 | 覆盖场景 |
|----------|--------|----------|
| `test_event_bus.py` | 15 | 发布/订阅、多监听者、错误隔离 |
| `test_workflow_registry.py` | 12 | 注册/查询/版本切换/默认 |
| `test_canary.py` | 13 | 灰度比例、流量分配、回滚 |

---

## 五、P4-C：分布式执行

### 5.1 目标

支持多节点分布式 workflow 执行，解决单进程瓶颈。

### 5.2 设计

#### 5.2.1 分布式任务队列（TaskQueue）

```python
from xruntime._runtime._workflow import RedisTaskQueue

queue = RedisTaskQueue(redis_url="redis://localhost:6379/0")
await queue.enqueue(workflow_id="wf-1", step_id="s1", context={...})
task = await queue.dequeue()
```

#### 5.2.2 分布式锁（DistributedLock）

```python
lock = DistributedLock(redis_url="redis://localhost:6379/0")
async with lock.acquire("workflow:wf-1:step-s1"):
    # 互斥执行
```

#### 5.2.3 Worker 进程

```python
# 启动 worker 进程消费任务队列
python -m xruntime.worker --queue redis://localhost:6379/0 --concurrency 4
```

### 5.3 文件结构

```
src/xruntime/_runtime/_workflow/
├── _distributed.py      # 【新增】TaskQueue + DistributedLock
└── _worker.py           # 【新增】Worker 进程

src/xruntime/
└── _worker.py           # 【新增】CLI 入口
```

### 5.4 TDD 测试计划

| 测试文件 | 测试数 | 覆盖场景 |
|----------|--------|----------|
| `test_task_queue.py` | 15 | 入队/出队/确认/重试 |
| `test_distributed_lock.py` | 10 | 获取/释放/超时/互斥 |
| `test_worker.py` | 10 | 消费/心跳/优雅退出 |

---

## 六、P4-D：性能基准与调优

### 6.1 目标

建立性能基准套件，持续监控关键指标，验证优化效果。

### 6.2 设计

#### 6.2.1 基准套件（BenchmarkSuite）

```python
from xruntime._runtime._workflow import BenchmarkSuite

suite = BenchmarkSuite()
result = await suite.run(
    workflow=my_workflow,
    iterations=100,
    concurrency=10,
)
# result: {avg_ms, p50_ms, p95_ms, p99_ms, throughput}
```

**基准场景**：
- 线性 10-step workflow
- 并行 10-step workflow
- 嵌套 3 层子工作流
- 100-step 大 workflow
- ConditionalStep 分支
- LoopStep 迭代

#### 6.2.2 性能回归检测

```yaml
# .github/workflows/perf-benchmark.yml
- name: Run benchmarks
  run: python -m xruntime.benchmark --compare-with-baseline
```

**特性**：
- 每次 PR 运行基准
- 与 main 分支基准对比
- 性能回退 > 10% 时标记
- 生成基准报告（Markdown）

### 6.3 文件结构

```
src/xruntime/_runtime/_workflow/
└── _benchmark.py        # 【新增】BenchmarkSuite

scripts/
└── benchmark.py         # 【新增】CLI 基准运行器

.github/workflows/
└── perf-benchmark.yml   # 【新增】CI 性能基准
```

### 6.4 TDD 测试计划

| 测试文件 | 测试数 | 覆盖场景 |
|----------|--------|----------|
| `test_benchmark.py` | 10 | 基准运行、统计、对比 |
| `test_perf_regression.py` | 10 | 回退检测、阈值、报告 |

---

## 七、P4 实施计划

### 7.1 Phase 优先级与依赖

```
P4-A (并发性能) ──────┐
                      ├──→ P4-C (分布式)
P4-D (基准) ──────────┤
                      │
P4-B (高级编排) ──────┘ (独立，可并行)
```

### 7.2 建议执行顺序

| 顺序 | Phase | 理由 |
|------|-------|------|
| 1 | P4-D | 先建基准，再优化（可测量） |
| 2 | P4-A | 核心性能优化 |
| 3 | P4-B | 高级功能（独立） |
| 4 | P4-C | 分布式（依赖 A 的连接池） |

### 7.3 验收标准

| 门禁 | 标准 |
|------|------|
| 测试通过率 | 100% |
| 新增模块覆盖率 | ≥ 95% |
| 全量回归 | 0 failed |
| 性能基准 | 无回退（或回退 < 5%） |
| P50 延迟 | ≤ P3 基准的 80% |

---

## 八、P4 预期收益

| 维度 | P3 基准 | P4 目标 | 提升 |
|------|---------|---------|------|
| 10-step workflow P50 | ~50ms | ~20ms | 60% |
| 100-step workflow P50 | ~500ms | ~200ms | 60% |
| 并行 10-step | ~50ms | ~15ms | 70% |
| 重复执行（缓存命中） | ~50ms | ~1ms | 98% |
| Redis 连接开销 | ~5ms/op | ~0.5ms/op | 90% |
| 最大并发 workflow | 1 | 100+ | 100x |
