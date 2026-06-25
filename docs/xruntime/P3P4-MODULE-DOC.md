# XRuntime P3 编排与平台 + P4 生产化 — 模块文档

> 版本: v0.1.0
> 阶段: P3 (W9-W11) + P4 (W12-W13)
> 日期: 2026-06-23

## 概述

P3 完成了 DAG 编排器、统一 SDK 包、插件体系。
P4 完成了部署基础设施、认证鉴权、限流、Prometheus 指标。

新增 47 项测试，累计 218 项全部通过。

## P3 模块清单

### 1. Orchestrator — DAG 工作流引擎

**文件**: `src/xruntime/_runtime/_orchestrator.py`

**职责**: 声明式 DAG 工作流，调度多 agent 会话，步骤间结果传递，
失败策略（retry/abort/continue）。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `WorkflowStep` | 步骤 (id/name/agent/prompt/depends_on/on_failure/max_retries) |
| `Workflow` | 工作流 (id/name/steps)，含 topological_order() + 环检测 |
| `WorkflowResult` | 执行结果 (status/step_results/step_status/errors) |
| `WorkflowStatus` | PENDING/RUNNING/COMPLETED/FAILED |
| `StepStatus` | PENDING/RUNNING/COMPLETED/FAILED/SKIPPED |
| `Orchestrator` | 引擎 (run(workflow) → WorkflowResult) |
| `parse_workflow_yaml()` | YAML 解析 |

**执行特性**:
- 拓扑排序 + 环检测
- 同层无依赖步骤并行执行 (`asyncio.gather`)
- 步骤结果通过 context 传递给依赖步骤
- retry: 按 max_retries 重试，退避间隔
- abort: 首个失败步骤终止整个工作流
- 依赖失败: 后续步骤标记 SKIPPED

**测试**: 19 项

---

### 2. SDK 包 — `xruntime_sdk`

**文件**: `src/xruntime_sdk/__init__.py`

**核心类型**:

| 类型 | 说明 |
|---|---|
| `XRuntimeClient` | 统一客户端 (query/health/ready)，支持三协议 |
| `AdminClient` | 管理客户端 (server_info) |
| `create_client()` | 工厂函数 |

**三协议调用**:
```python
client = XRuntimeClient("http://localhost:8900", tenant_id="acme")
# Anthropic
result = await client.query("anthropic", prompt="Hi", model="claude-sonnet-4-20250514")
# Claude Code
result = await client.query("claude_code", prompt="Fix bug", options={"allowed_tools": ["Read"]})
# OpenCode
result = await client.query("opencode", prompt="Find TODOs", agent="coder")
```

**测试**: 10 项

---

### 3. 插件体系

**文件**: `src/xruntime/_runtime/_plugin.py`

**核心类型**:

| 类型 | 说明 |
|---|---|
| `XRuntimePlugin` | ABC: name/version/initialize(context)/shutdown() |
| `PluginContext` | 上下文 (config/adapter_registry/middleware_registry) |
| `PluginRegistry` | 注册/查询/初始化/关闭 全生命周期管理 |

**测试**: 6 项

---

## P4 模块清单

### 4. 认证鉴权

**文件**: `src/xruntime/_gateway/_auth.py`

**核心类型**: `AuthMiddleware` (Starlette `BaseHTTPMiddleware`)

**特性**:
- API Key 认证 (`x-api-key` header)
- 公开路由白名单 (`/health`, `/ready`, `/docs`, `/redoc`, `/openapi.json`)
- 无有效 key → 401 Unauthorized

**测试**: 3 项

---

### 5. 限流

**文件**: `src/xruntime/_gateway/_ratelimit.py`

**核心类型**: `RateLimiter`

**特性**:
- 滑动窗口算法
- 按 client_id 独立限流
- `check(client_id) -> bool` 异步接口
- 窗口过期自动重置

**测试**: 4 项

---

### 6. Prometheus 指标

**文件**: `src/xruntime/_infra/_metrics.py`

**核心类型**: `MetricsCollector`

**指标**:
- `xruntime_active_sessions` (gauge) — 按租户活跃会话数
- `xruntime_tool_calls_total` (counter) — 工具调用总次数
- `xruntime_tokens_total` (counter) — token 消耗 (input/output)

**测试**: 5 项

---

### 7. 部署基础设施

**文件**:
- `deploy/Dockerfile` — Docker 镜像 (python:3.11-slim)
- `deploy/docker-compose.yml` — Redis + XRuntime
- `deploy/helm/xruntime.yaml` — K8s Deployment + Service + HPA + Redis
- `src/xruntime/_server.py` — 服务器入口

**K8s 特性**:
- 2 副本起步，HPA 扩到 10 (CPU 70%)
- livenessProbe (/health) + readinessProbe (/ready)
- 资源限制 (250m-1000m CPU, 512Mi-1Gi Mem)

---

## 测试汇总

| 测试文件 | 测试数 | 覆盖模块 |
|---|---|---|
| `test_orchestrator.py` | 19 | Orchestrator DAG |
| `test_sdk_plugins_p4.py` | 28 | SDK + Plugins + Auth + RateLimit + Metrics |
| **P3+P4 合计** | **47** | |
| **累计 (P0-P4)** | **218** | **全部通过** |

## 运行测试

```bash
cd agentscope
python3 -m pytest tests/xruntime/ -v -p no:cacheprovider
```

## 文件结构 (P3+P4 新增)

```
src/xruntime/
├── _runtime/
│   ├── _orchestrator.py          # DAG 工作流引擎
│   └── _plugin.py                # 插件体系
├── _gateway/
│   ├── _auth.py                  # 认证中间件
│   └── _ratelimit.py             # 限流器
├── _infra/
│   └── _metrics.py               # Prometheus 指标
├── _server.py                    # 服务器入口
src/xruntime_sdk/
└── __init__.py                   # SDK 客户端

deploy/
├── Dockerfile
├── docker-compose.yml
└── helm/xruntime.yaml

tests/xruntime/
├── test_orchestrator.py          # 19 项
└── test_sdk_plugins_p4.py        # 28 项
```

## 完成进度总览

| 阶段 | 状态 | 测试数 |
|---|---|---|
| P0 地基 | ✅ | 42 |
| P1 协议适配 | ✅ | 68 |
| P2 企业运行时 | ✅ | 61 |
| P3 编排与平台 | ✅ | 35 |
| P4 生产化 | ✅ | 12 |
| **合计** | | **218** |

## 待完善 (P5)

- 迁移框架验收 (手动迁移指南)
- 三协议全链路验收用例集
- 企业场景验收 (代码工程/编排/平台接入/自动化各一)
- 文档定稿 (运维/SDK/迁移/插件开发指南)
- 生产模式 event stream 对接 AS ChatService
