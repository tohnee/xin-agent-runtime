# XRuntime 部署操作手册

> 版本：v4.0 (P4 全阶段)
> 日期：2026-07-01
> 基础：1371 tests passed, 0 failed, 全模块 100% 覆盖率
> 适用环境：生产 / 预发布 / 本地开发

---

## 目录

1. [部署架构总览](#1-部署架构总览)
2. [前置条件](#2-前置条件)
3. [本地开发环境部署](#3-本地开发环境部署)
4. [可观测性栈部署](#4-可观测性栈部署)
5. [CI/CD 流水线配置](#5-cicd-流水线配置)
6. [配置文件清单与说明](#6-配置文件清单与说明)
7. [启动验证步骤](#7-启动验证步骤)
8. [告警验证](#8-告警验证)
9. [性能基准验证](#9-性能基准验证)
10. [生产环境部署](#10-生产环境部署)
11. [故障排查](#11-故障排查)

---

## 1. 部署架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose 完整栈                      │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │  XRuntime    │  │   Redis      │  │  OTel Coll.  │        │
│  │  (port 8900) │  │  (port 6379) │  │ (port 4317)  │        │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘        │
│         │                  │                  │               │
│         ▼                  ▼                  ▼               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │  Prometheus  │  │   Jaeger     │  │  Alertmgr    │        │
│  │  (port 9090) │  │ (port 16686) │  │ (port 9093)  │        │
│  └──────┬───────┘  └──────────────┘  └──────┬───────┘        │
│         │                                     │               │
│         ▼                                     ▼               │
│  ┌──────────────┐                   ┌──────────────┐         │
│  │  Grafana     │                   │  Slack/PD    │         │
│  │  (port 3000) │                   │  (webhook)   │         │
│  └──────────────┘                   └──────────────┘         │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐                         │
│  │ Elasticsearch│  │  Filebeat    │                         │
│  │ (port 9200)  │  │ (audit logs) │                         │
│  └──────┬───────┘  └──────┬───────┘                         │
│         │                 │                                   │
│         ▼                 ▼                                   │
│  ┌──────────────┐                                         │
│  │  Kibana      │                                         │
│  │  (port 5601) │                                         │
│  └──────────────┘                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 前置条件

### 2.1 系统要求

| 组件 | 最低版本 | 推荐 |
|------|---------|------|
| Python | 3.11+ | 3.11.7 |
| Docker | 24.0+ | 25.0 |
| Docker Compose | v2.20+ | v2.26 |
| Redis | 7.0+ | 7-alpine |
| 可用内存 | 4GB | 8GB+ |
| 可用磁盘 | 20GB | 50GB+ |

### 2.2 Python 依赖

```bash
pip install -e ".[dev,xruntime-dev]"

# 验证关键依赖
python -c "import opentelemetry; print(f'OTel {opentelemetry.__version__}')"
python -c "import redis; print(f'redis-py {redis.__version__}')"
python -c "import pydantic; print(f'pydantic {pydantic.__version__}')"
```

### 2.3 环境变量

```bash
# .env 或 export
export XRUNTIME_OBSERVABILITY_OTEL_ENABLED=true
export XRUNTIME_OBSERVABILITY_OTEL_ENDPOINT=http://localhost:4317
export XRUNTIME_CREDENTIAL_BROKER_REDIS_URL=redis://localhost:6379/0
export XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED=true
export XRUNTIME_OBSERVABILITY_AUDIT_ENABLED=true
```

---

## 3. 本地开发环境部署

### 3.1 启动 Redis

```bash
docker run -d --name xruntime-redis \
  -p 6379:6379 \
  redis:7-alpine \
  redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
```

### 3.2 启动 XRuntime

```bash
python -m xruntime
# 或
uvicorn xruntime._server:app --host 0.0.0.0 --port 8900 --reload
```

### 3.3 验证

```bash
curl http://localhost:8900/health    # {"status": "ok"}
curl http://localhost:8900/ready      # {"status": "ready"}
curl http://localhost:8900/metrics     # Prometheus 文本格式
```

---

## 4. 可观测性栈部署

### 4.1 一键启动

```bash
cd deploy/
docker compose -f docker-compose.observability.yml up -d
docker compose -f docker-compose.observability.yml ps
```

### 4.2 各组件验证 URL

| 组件 | URL | 预期 |
|------|-----|------|
| XRuntime | http://localhost:8900/health | `{"status":"ok"}` |
| Jaeger UI | http://localhost:16686 | Jaeger 搜索页面 |
| Prometheus | http://localhost:9090/-/healthy | `Prometheus is Healthy.` |
| Alertmanager | http://localhost:9093/-/healthy | `OK` |
| Elasticsearch | http://localhost:9200 | cluster info JSON |
| Kibana | http://localhost:5601 | Kibana 首页 |

---

## 5. CI/CD 流水线配置

### 5.1 可观测性检查 (`observability-check.yml`)

触发：每次 push / PR

步骤：
1. `promtool check rules deploy/alerts.yml` — 验证告警语法
2. Python 验证代码中存在告警引用的指标
3. Redis service + smoke test（tracer + metrics + audit 全链路）

### 5.2 性能基准 (`perf-benchmark.yml`)

触发：push 到 main / PR

步骤：
1. 安装依赖
2. 运行 5 个内置基准场景
3. 与 baseline.json 对比
4. 回退 > 10% 标记失败

```bash
# 本地运行基准
python scripts/benchmark.py --all --iterations 100 --output baseline.json
python scripts/benchmark.py --all --compare baseline.json --threshold 10
```

---

## 6. 配置文件清单与说明

| 文件 | 路径 | 用途 |
|------|------|------|
| `alerts.yml` | `deploy/alerts.yml` | 9 条 Prometheus 告警规则 |
| `prometheus.yml` | `deploy/prometheus.yml` | Prometheus 抓取配置 |
| `alertmanager.yml` | `deploy/alertmanager.yml` | 告警通知路由(Slack/PD) |
| `otel-collector-config.yaml` | `deploy/otel-collector-config.yaml` | OTel Collector 配置 |
| `docker-compose.observability.yml` | `deploy/` | 完整可观测性栈 |
| `filebeat.yml` | `deploy/filebeat.yml` | 审计日志收集(JSONL→ES) |
| `observability-check.yml` | `.github/workflows/` | CI 告警+指标验证 |
| `perf-benchmark.yml` | `.github/workflows/` | CI 性能基准+回归检测 |

---

## 7. 启动验证步骤

### 7.1 验证脚本

```bash
#!/bin/bash
# deploy/verify_deployment.sh
set -e

echo "=== 1. 健康检查 ==="
curl -sf http://localhost:8900/health | python -m json.tool

echo "=== 2. 就绪检查 ==="
curl -sf http://localhost:8900/ready | python -m json.tool

echo "=== 3. Prometheus 指标 ==="
METRICS=$(curl -sf http://localhost:8900/metrics)
echo "指标行数: $(echo "$METRICS" | wc -l)"

echo "=== 4. Jaeger 连通性 ==="
curl -sf "http://localhost:16686/api/services" | \
  python -c "import sys,json; d=json.load(sys.stdin); print(f'Services: {len(d[\"data\"])}')"

echo "=== 5. 告警规则 ==="
curl -sf "http://localhost:9090/api/v1/rules" | \
  python -c "
import sys,json
d=json.load(sys.stdin)
groups=d.get('data',{}).get('groups',[])
total=sum(len(g.get('rules',[])) for g in groups)
print(f'已加载 {total} 条告警规则')
"

echo "=== 6. Elasticsearch ==="
curl -sf http://localhost:9200/_cluster/health | \
  python -c "import sys,json; d=json.load(sys.stdin); print(f'ES: {d[\"status\"]}')"

echo "=== 7. Alertmanager ==="
curl -sf http://localhost:9093/-/healthy && echo ""

echo "=== 全部验证通过 ==="
```

---

## 8. 告警验证

### 8.1 模拟失败触发告警 1

```bash
python -c "
import asyncio
from xruntime._runtime._workflow import (
    WorkflowBuilder, FunctionExecutor, run_workflow, WorkflowMetrics,
)

async def main():
    wf = WorkflowBuilder().id('fail-test').step(
        id='s1', agent='a', prompt='p', on_failure='abort',
    ).build()
    executor = FunctionExecutor(
        lambda s, c: (_ for _ in ()).throw(Exception('boom'))
    )
    metrics = WorkflowMetrics()
    result = await run_workflow(wf, executor, metrics=metrics)
    print(f'Status: {result.status}')
    print(f'FAILED: {metrics.get_step_count(\"fail-test\", \"s1\", \"FAILED\")}')

asyncio.run(main())
"
```

### 8.2 检查告警状态

```bash
curl -s "http://localhost:9090/api/v1/alerts" | python -c "
import sys, json
d = json.load(sys.stdin)
for alert in d.get('data', {}).get('alerts', []):
    name = alert['labels']['alertname']
    state = alert['state']
    print(f'{name}: {state}')
"
```

---

## 9. 性能基准验证

```bash
# 运行所有场景
python scripts/benchmark.py --all --iterations 50 --output baseline.json

# 查看结果
cat baseline.json | python -m json.tool

# 对比(后续运行)
python scripts/benchmark.py --all --compare baseline.json --threshold 10
```

预期输出包含 5 个场景：linear_10 / parallel_10 / large_100 / conditional / loop_5。

---

## 10. 生产环境部署

### 10.1 扩展到 1000+ 并发

参见下一节 [1000+ 并发扩展规划](#11-扩展到-1000-并发生产环境规划)。

### 10.2 关键参数调优

| 参数 | 开发默认 | 生产推荐 | 说明 |
|------|---------|---------|------|
| `max_concurrent_steps` | 10 | 200 | 全局并行度 |
| `per_agent_concurrency` | 3 | 20 | 每 agent 并行度 |
| Redis `maxmemory` | 256mb | 2gb | Redis 缓存 |
| Redis pool_size | 10 | 50 | 连接池大小 |
| OTel sampling | 100% | 10% | 采样率 |
| Prometheus scrape | 15s | 10s | 抓取间隔 |
| Audit max_entries | 10000 | 100000 | 审计条目上限 |

---

## 11. 扩展到 1000+ 并发生产环境规划

### 11.1 架构演进路径

```
阶段 1: 单进程 (当前)
  └─ max_concurrent=10, 单 Redis, ~100 wf/s

阶段 2: 多进程 + 连接池
  └─ max_concurrent=200, Redis pool=50, ~500 wf/s

阶段 3: 多节点 + 分布式队列
  └─ N workers + Redis TaskQueue + 分布式锁, 1000+ wf/s
```

### 11.2 阶段 2: 多进程 + 连接池 (500 wf/s)

**关键配置:**

```python
# config.py 生产配置
config = WorkflowConfig(
    max_concurrent_steps=200,
    per_agent_concurrency=20,
    rate_limit_rps=500,
    rate_limit_burst=100,
    cache_enabled=True,
    cache_ttl_seconds=3600,
    redis_pool_size=50,
    redis_pool_timeout=5.0,
)
```

**部署:**

```bash
# 启动 4 个 worker 进程(每进程 max_concurrent=50)
uvicorn xruntime._server:app --port 8900 --workers 4

# 或 gunicorn
gunicorn xruntime._server:app -w 4 -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8900
```

**Redis 优化:**

```bash
redis-server \
  --maxmemory 2gb \
  --maxmemory-policy allkeys-lru \
  --save "" \
  --appendonly no \
  --io-threads 4
```

**验证:**

```bash
# 压测 500 并发
python scripts/benchmark.py --scenario linear_10 \
  --iterations 500 --concurrency 50
# 预期: throughput > 400 wf/s, P95 < 100ms
```

### 11.3 阶段 3: 多节点 + 分布式 (1000+ wf/s)

**架构:**

```
┌─────────────┐     ┌──────────────────────────────────┐
│  Load       │     │         Redis Cluster            │
│  Balancer   ├────►│  ┌────────┐ ┌────────┐ ┌───────┐│
│  (Nginx/    │     │  │Master 1│ │Master 2│ │Master3││
│   HAProxy)  │     │  └───┬────┘ └───┬────┘ └───┬───┘│
└──────┬──────┘     │      │          │          │     │
       │            └──────┼──────────┼──────────┼─────┘
       │                   │          │          │
  ┌────┼───────────────────┼──────────┼──────────┼────┐
  │    ▼                   ▼          ▼          ▼    │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
  │  │ Worker 1 │  │ Worker 2 │  │ Worker N │        │
  │  │ (50 conn)│  │ (50 conn)│  │ (50 conn)│        │
  │  └────┬─────┘  └────┬─────┘  └────┬─────┘        │
  │       │              │              │              │
  │       └──────────────┼──────────────┘              │
  │                      ▼                             │
  │              ┌──────────────┐                     │
  │              │  Task Queue   │  ← InMemoryTaskQueue
  │              │  (Redis)     │     / RedisTaskQueue  │
  │              └──────┬───────┘                     │
  │                     │                             │
  │              ┌──────▼───────┐                     │
  │              │ Distributed  │  ← DistributedLock   │
  │              │ Lock (Redis) │                     │
  │              └──────────────┘                     │
  └────────────────────────────────────────────────────┘
```

**关键组件:**

1. **ConcurrencyPool** — 每 worker 进程 `max_concurrent=50`
2. **RateLimiter** — 全局限流 1000 rps（Redis 令牌桶）
3. **ResultCache** — Redis 分布式缓存（跨 worker 共享）
4. **TaskQueue** — Redis 队列分发任务到 N 个 worker
5. **DistributedLock** — Redis 分布式锁防止重复执行

**部署配置:**

```yaml
# docker-compose.prod.yml (多节点)
services: