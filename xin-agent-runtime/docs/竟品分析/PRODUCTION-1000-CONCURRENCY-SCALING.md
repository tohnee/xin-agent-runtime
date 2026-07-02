# 生产环境 1000+ 并发扩展规划

> 日期：2026-07-01
> 基础：P4-A 并发优化 + P4-C 分布式执行模块已完成
> 当前测试：1371 passed, 9 个新模块 100% 覆盖率
> 目标：从单进程 10 并发 → 多节点 1000+ 并发

---

## 一、当前架构能力

### 1.1 P4 已完成的并发基础设施

| 组件 | 文件 | 能力 | 当前限制 |
|------|------|------|----------|
| ConcurrencyPool | `_concurrency.py` | 全局 + per-agent semaphore | 单进程 |
| RateLimiter | `_rate_limiter.py` | 令牌桶速率限制 | 单进程 |
| ResultCache | `_cache.py` | LRU + TTL 缓存 | 单进程(InMemory) |
| TaskQueue | `_distributed.py` | FIFO 任务队列 | 单进程(InMemory) |
| DistributedLock | `_distributed.py` | asyncio.Lock | 单进程 |

### 1.2 瓶颈分析

```
单进程瓶颈:
  ├─ GIL: Python 单进程无法真正并行 CPU 密集任务
  ├─ 内存: 每个 workflow step 持有 context 副本
  ├─ asyncio: 单事件循环 ~500 并发(经验值)
  └─ Redis: 单连接 ~10k QPS(瓶颈在网络)
```

---

## 二、1000+ 并发扩展路线图

### 阶段 1: 单进程优化 (10 → 100 并发)

**已完成**: P4-A ConcurrencyPool + RateLimiter

```python
from xruntime._runtime._workflow import (
    ConcurrencyPool, WorkflowConfig,
)

config = WorkflowConfig(
    max_concurrent_steps=100,      # 全局并行度
    per_agent_concurrency=10,       # 每 agent
)
```

**关键优化**:
- ✅ ConcurrencyPool 限制并行度(防止内存溢出)
- ✅ RateLimiter 保护下游 API
- ✅ ResultCache 缓存重复执行
- ⬜ Redis 连接池(复用连接)

**预估性能**: 100 并发 workflow, P50 ~50ms

---

### 阶段 2: 多进程 + Redis 共享 (100 → 300 并发)

**目标**: 使用 uvicorn 多 worker + Redis 共享状态

#### 2.1 部署架构

```
┌─────────────────────────────────────────┐
│              Nginx / HAProxy            │
│            (负载均衡 + TLS)              │
└──────────┬──────────┬──────────┬────────┘
           │          │          │
    ┌──────▼──┐ ┌────▼────┐ ┌──▼──────┐
    │ Worker 1│ │ Worker 2│ │ Worker 3│
    │ (uvicorn│ │ (uvicorn│ │ (uvicorn│
    │  :8901) │ │  :8902) │ │  :8903) │
    └────┬────┘ └────┬────┘ └────┬────┘
         │           │           │
         └─────┬─────┴─────┬────┘
               │           │
        ┌──────▼──┐  ┌────▼────┐
        │  Redis  │  │ Jaeger  │
        │ (共享)  │  │ (共享)  │
        └─────────┘  └─────────┘
```

#### 2.2 关键改造

1. **Redis TaskQueue**: 替换 `InMemoryTaskQueue`

```python
# 目标实现(P4-C 已设计，需扩展)
class RedisTaskQueue(InMemoryTaskQueue):
    def __init__(self, redis_url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url)
        self._key = "xruntime:taskqueue"

    async def enqueue(self, workflow_id, step_id, context):
        task = Task(workflow_id=workflow_id, step_id=step_id,
                    context=context)
        await self._redis.lpush(
            self._key,
            json.dumps(task.to_dict()),
        )
        return task

    async def dequeue(self):
        data = await self._redis.brpop(self._key, timeout=1)
        if data is None:
            return None
        return Task.from_json(data[1])
```

2. **Redis DistributedLock**: 替换 asyncio.Lock

```python
class RedisDistributedLock(DistributedLock):
    def __init__(self, redis_url: str):
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(redis_url)
        self._lock_key = "xruntime:lock:"

    async def acquire(self, key, timeout=30.0):
        token = str(uuid.uuid4())
        acquired = await self._redis.set(
            self._lock_key + key, token,
            nx=True, ex=int(timeout),
        )
        if acquired:
            self._tokens[key] = token
            return True
        return False
```

3. **Redis ResultCache**: 替换 InMemoryResultCache

```python
class RedisResultCache(ResultCache):
    def __init__(self, redis_url, key_prefix="wf:cache:"):
        self._redis = aioredis.from_url(redis_url)
        self._prefix = key_prefix

    async def get(self, key):
        val = await self._redis.get(self._prefix + key)
        return val.decode() if val else None

    async def set(self, key, value, ttl=None):
        await self._redis.set(
            self._prefix + key, value,
            ex=ttl,
        )
```

#### 2.3 uvicorn 多 worker 启动

```bash
# 3 个 worker 进程
uvicorn xruntime._server:app \
  --host 0.0.0.0 \
  --port 8900 \
  --workers 3 \
  --loop uvloop \
  --http httptools
```

**预估性能**: 300 并发(3 worker × 100), P50 ~80ms

---

### 阶段 3: 水平扩展 (300 → 1000 并发)

**目标**: Kubernetes 集群 + Redis Cluster + 自动伸缩

#### 3.1 K8s 部署架构

```yaml
# k8s/xruntime-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xruntime
spec:
  replicas: 10                    # 10 个 Pod
  selector:
    matchLabels:
      app: xruntime
  template:
    metadata:
      labels:
        app: xruntime
    spec:
      containers:
        - name: xruntime
          image: xruntime:latest
          ports:
            - containerPort: 8900
          env:
            - name: XRUNTIME_CREDENTIAL_BROKER_REDIS_URL
              value: "redis://redis-cluster:6379/0"
            - name: XRUNTIME_OBSERVABILITY_OTEL_ENDPOINT
              value: "http://otel-collector:4317"
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8900
          readinessProbe:
            httpGet:
              path: /ready
              port: 8900
---
apiVersion: v1
kind: HorizontalPodAutoscaler
metadata:
  name: xruntime-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: xruntime
  minReplicas: 5
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

#### 3.2 Redis Cluster 配置

```yaml
# Redis Cluster: 6 节点(3 主 + 3 从)
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-cluster
spec:
  replicas: 6
  # 每节点 4GB → 总 24GB
  # 预估 QPS: 100k+
```

#### 3.3 分布式 TaskQueue Worker

```python
# 独立 worker 进程消费任务队列
# k8s/worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xruntime-worker
spec:
  replicas: 5                    # 5 个 worker Pod
  template:
    spec:
      containers:
        - name: worker
          image: xruntime:latest
          command: ["python", "-m", "xruntime.worker"]
          env:
            - name: TASK_QUEUE_URL
              value: "redis://redis-cluster:6379/1"
            - name: WORKER_CONCURRENCY
              value: "20"          # 每 worker 20 并发
```

#### 3.4 容量计算

| 组件 | 数量 | 每实例并发 | 总并发 |
|------|------|-----------|--------|
| XRuntime API Pod | 10 | 50 | 500 |
| Worker Pod | 5 | 20 | 100 |
| asyncio 协程/Pod | — | 50 | — |
| **总并发** | — | — | **1000+** |

**预估性能**: 1000 并发 workflow, P50 ~200ms

---

## 三、关键配置参数

### 3.1 ConcurrencyPool 生产配置

```python
# 10 Pod × 100 并发 = 1000 总并发
pool = ConcurrencyPool(
    max_concurrent=100,       # 每 Pod 100 并发
    per_agent_limit=10,       # 每 agent 10 并发
    acquire_timeout=30.0,     # 30s 超时
)
```

### 3.2 RateLimiter 生产配置

```python
# 保护下游 LLM API(如 1000 RPM)
limiter = TokenBucketRateLimiter(
    rate=16.6,     # ~1000 RPM / 60
    burst=50,      # 突发 50 请求
)
```

### 3.3 Redis 连接池

```python
# 每 Pod 连接池
store = RedisCredentialStore(
    redis_url="redis://redis-cluster:6379/0",
    pool_size=20,           # 连接池大小
    pool_timeout=5.0,        # 获取连接超时
)
# 10 Pod × 20 连接 = 200 总连接
```

---

## 四、监控与告警扩展

### 4.1 新增告警规则

```yaml
# 1000+ 并发场景新增告警
groups:
  - name: scalability_alerts
    rules:
      - alert: HighConcurrencyUtilization
        expr: |
          xruntime_concurrency_utilization > 0.9
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Concurrency pool > 90% utilized"

      - alert: TaskQueueBacklog
        expr: |
          xruntime_taskqueue_pending_count > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Task queue backlog > 100"

      - alert: WorkerPodDown
        expr: |
          kube_deployment_status_replicas_available
          <
          kube_deployment_spec_replicas
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Worker pods unavailable"

      - alert: RedisClusterDegraded
        expr: |
          redis_cluster_state != "ok"
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Redis cluster degraded"
```

### 4.2 Grafana 仪表板

```
Dashboard: XRuntime Production
├─ Row: Throughput
│  ├─ Panel: Workflows/sec (by Pod)
│  ├─ Panel: Steps/sec (by agent)
│  └─ Panel: Task queue depth
├─ Row: Latency
│  ├─ Panel: P50/P95/P99 (by scenario)
│  └─ Panel: Step duration heatmap
├─ Row: Resources
│  ├─ Panel: CPU/Memory (by Pod)
│  ├─ Panel: Concurrency utilization
│  └─ Panel: Redis connections
├─ Row: Errors
│  ├─ Panel: Error rate (by workflow)
│  ├─ Panel: Timeout count
│  └─ Panel: Retry count
└─ Row: Business
   ├─ Panel: Active workflows
   ├─ Panel: Approval pending
   └─ Panel: Credential rotations
```

---

## 五、扩展里程碑

| 阶段 | 并发目标 | 关键改造 | 预估周期 |
|------|---------|---------|---------|
| 1 (已完成) | 10→100 | ConcurrencyPool + RateLimiter + Cache | ✅ |
| 2 | 100→300 | uvicorn 多 worker + Redis 共享 | 2 周 |
| 3 | 300→1000 | K8s 集群 + Redis Cluster + HPA | 4 周 |
| 4 | 1000→5000 | 多区域部署 + 智能路由 | 8 周 |

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Redis 单点故障 | 中 | 高 | Redis Cluster + Sentinel |
| Pod OOM | 低 | 中 | 资源限制 + HPA |
| 网络分区 | 低 | 高 | 多可用区部署 |
| GIL 瓶颈 | 中 | 中 | 多进程 + asyncio |
| Trace 丢失 | 低 | 低 | OTel BatchSpanProcessor |
| 连接池耗尽 | 中 | 高 | 连接池监控 + 告警 |
