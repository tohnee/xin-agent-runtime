# P3 功能扩展路线图与设计文档

> 生成日期：2026-07-01
> 基础：P2 全阶段交付物（Workflow SDK + Credential Broker + OpenAI Adapter + 集成测试）已验收通过
> 参考竞品：Vercel Eve Agent Stack 深度调研报告
> 开发方法：TDD（Red → Green → Refactor）
> 状态：规划草案 v0.1

---

## 一、P3 规划背景

### 1.1 P2 交付基础

P2 完成了两项核心基础设施：

1. **Workflow SDK**：DAG 工作流引擎 + Checkpoint 持久化 + resume 恢复 + 链式 Builder API
2. **Credential Broker**：短期凭证签发 + TTL + scope/audience 校验 + Docker 安全注入

### 1.2 与 Vercel Eve 的差距分析

基于 [Vercel Eve 调研报告](./vercel%20Eve%20Agent%20Stack.md) 第八章「对本项目的启发建议」，P2 后仍存在以下差距：

| Eve 的能力 | 本项目现状 | 差距 | P3 对应 |
|-----------|----------|------|---------|
| **Durable sleep/timer** | P2 有 checkpoint，无 timer | workflow 不能暂停等待外部事件或定时 | P3-A |
| **条件分支** | P2 仅支持 DAG 依赖 | 不支持 if/else 运行时分支 | P3-A |
| **子工作流** | 无 | 不支持 workflow 嵌套 | P3-A |
| **Workflow 级 HITL** | P1-A 有 ApprovalMiddleware（agent 级） | workflow step 级无审批门控 | P3-B |
| **Redis 持久化** | P2 仅 InMemoryCheckpointStore | 生产环境 checkpoint 不持久 | P3-C |
| **凭证自动轮换** | P2 有 TTL，无主动轮换 | 凭证过期后才重新签发，无平滑过渡 | P3-C |
| **OTel Tracing** | MetricsCollector（非标准） | 无标准化分布式追踪 | P3-D |

### 1.3 P3 设计原则

1. **TDD 强制**：每个功能先写失败测试，再实现，最后重构
2. **不破坏 P2 API**：WorkflowBuilder / run_workflow / resume_workflow 向后兼容
3. **渐进式增强**：每个 Phase 独立可交付，不依赖后续 Phase
4. **安全优先**：所有新功能必须通过安全验证（无密钥泄漏、fail-closed）
5. **覆盖率门禁**：每个新增模块目标 100% 覆盖率，整体 ≥ 95%

---

## 二、P3 路线图总览

```
P3-A: Workflow 控制流扩展   ──────────┐
  (条件分支 + 循环 + 子工作流 + 定时器) │
                                      │
P3-B: Workflow HITL 集成    ──────────┤
  (审批步骤 + 暂停/恢复 + 超时)        │
                                      │
P3-C: Credential Broker 生产硬化 ─────┤
  (Redis 持久化 + 自动轮换 + 细粒度权限)│
                                      │
P3-D: Workflow 可观测性     ──────────┘
  (OTel Tracing + 指标 + 审计)
```

| Phase | 主题 | 预估模块数 | 预估测试数 | 优先级 |
|-------|------|-----------|-----------|--------|
| P3-A | Workflow 控制流扩展 | 4 | 60+ | P0（核心能力） |
| P3-B | Workflow HITL 集成 | 3 | 40+ | P1（安全合规） |
| P3-C | Credential Broker 硬化 | 3 | 35+ | P1（生产就绪） |
| P3-D | Workflow 可观测性 | 3 | 30+ | P2（运维能力） |

---

## 三、P3-A：Workflow 控制流扩展

### 3.1 目标

扩展 P2 的 Workflow SDK，支持运行时条件分支、循环、子工作流嵌套和持久化定时器，对齐 Eve 的 Workflow SDK 能力。

### 3.2 设计

#### 3.2.1 条件分支（ConditionalStep）

```python
from xruntime._runtime._workflow import WorkflowBuilder

wf = (
    WorkflowBuilder()
    .id("wf-conditional")
    .step(id="classify", agent="a", prompt="classify input")
    .branch(
        id="branch-1",
        condition=lambda ctx: ctx["classify"] == "urgent",
        steps=[
            {"id": "escalate", "agent": "a", "prompt": "escalate"},
        ],
    )
    .branch(
        id="branch-2",
        condition=lambda ctx: ctx["classify"] == "normal",
        steps=[
            {"id": "queue", "agent": "a", "prompt": "queue"},
        ],
    )
    .step(id="done", agent="a", prompt="finalize", depends_on=["branch-1", "branch-2"])
    .build()
)
```

**新增类**：`ConditionalStep`（继承 WorkflowStep，增加 `condition: Callable[[dict], bool]`）

**执行语义**：
- `condition(context)` 返回 `True` → 执行该分支的 steps
- `condition(context)` 返回 `False` → 跳过该分支所有 steps（标记 SKIPPED）
- 多个 branch 互斥（第一个匹配的 branch 执行，后续跳过）

#### 3.2.2 循环节点（LoopStep）

```python
wf = (
    WorkflowBuilder()
    .id("wf-loop")
    .step(id="init", agent="a", prompt="init")
    .loop(
        id="refine-loop",
        agent="a",
        prompt="refine output",
        condition=lambda ctx: ctx.get("quality") < 0.9,
        max_iterations=5,
        depends_on=["init"],
    )
    .build()
)
```

**新增类**：`LoopStep`（增加 `condition` + `max_iterations`）

**执行语义**：
- 每次 iteration 执行 step，检查 condition
- condition 为 True 且未达 max_iterations → 继续
- condition 为 False 或达 max_iterations → 退出循环
- 每次 iteration 保存 checkpoint（durable）

#### 3.2.3 子工作流（SubWorkflowStep）

```python
sub_wf = (
    WorkflowBuilder()
    .id("sub-research")
    .step(id="search", agent="a", prompt="search")
    .step(id="summarize", agent="a", prompt="summarize", depends_on=["search"])
    .build()
)

wf = (
    WorkflowBuilder()
    .id("wf-parent")
    .step(id="plan", agent="a", prompt="plan")
    .subworkflow(
        id="research",
        workflow=sub_wf,
        depends_on=["plan"],
    )
    .step(id="report", agent="a", prompt="report", depends_on=["research"])
    .build()
)
```

**新增类**：`SubWorkflowStep`（携带 `sub_workflow: Workflow`）

**执行语义**：
- 子工作流作为父工作流的一个 step 执行
- 子工作流的 checkpoint 独立保存（namespace: `{parent_wf_id}/{step_id}`）
- 子工作流失败 → 父工作流 step 失败（遵循 on_failure 策略）
- 子工作流的结果（最后一个 step 的输出）作为父 step 的输出

#### 3.2.4 持久化定时器（DurableTimer）

```python
wf = (
    WorkflowBuilder()
    .id("wf-timer")
    .step(id="start", agent="a", prompt="start")
    .sleep(id="wait", duration_seconds=3600, depends_on=["start"])
    .step(id="continue", agent="a", prompt="continue", depends_on=["wait"])
    .build()
)
```

**新增类**：`TimerStep`（`duration_seconds: int`）

**执行语义**：
- 到达 sleep step 时，保存 checkpoint（status=SLEEPING + wake_at timestamp）
- Orchestrator 检查 `wake_at`，未到时间则暂停 workflow
- 到时间后 resume 继续执行后续 steps
- 崩溃后 resume：检查 wake_at，未到则继续等待，已到则继续

### 3.3 文件结构

```
src/xruntime/_runtime/_workflow/
├── _sdk.py              # 扩展 WorkflowBuilder（新增 .branch() / .loop() / .subworkflow() / .sleep()）
├── _orchestrator.py     # 扩展 CheckpointedOrchestrator（处理新 step 类型）
├── _checkpoint.py       # 新增 SLEEPING status + wake_at 字段
├── _steps.py            # 【新增】ConditionalStep / LoopStep / SubWorkflowStep / TimerStep
└── __init__.py          # 导出新类
```

### 3.4 TDD 测试计划

| 测试文件 | 测试类 | 测试数 | 覆盖场景 |
|----------|--------|--------|----------|
| `test_conditional_step.py` | TestConditionalStep | 15 | condition True/False、多分支互斥、嵌套分支 |
| `test_loop_step.py` | TestLoopStep | 12 | condition 退出、max_iterations、checkpoint per iteration |
| `test_subworkflow.py` | TestSubWorkflow | 18 | 子工作流执行、独立 checkpoint、失败传播、resume |
| `test_timer_step.py` | TestTimerStep | 15 | sleep + wake、checkpoint SLEEPING、resume after wake |

**Red → Green → Refactor 流程**：
1. Red：先写测试，导入不存在的类，全部 FAIL
2. Green：实现 `_steps.py` + 扩展 orchestrator，使测试 PASS
3. Refactor：优化代码结构，确保 100% 覆盖

---

## 四、P3-B：Workflow HITL 集成

### 4.1 目标

将 P1-A 的 ApprovalMiddleware（agent 级审批）与 P2 的 Workflow SDK 集成，实现 workflow step 级的人类审批门控，对齐 Eve 的 `needsApproval` 语义。

### 4.2 设计

#### 4.2.1 审批步骤（ApprovalStep）

```python
wf = (
    WorkflowBuilder()
    .id("wf-with-approval")
    .step(id="draft", agent="a", prompt="draft email")
    .approval(
        id="approve-email",
        approver="manager@company.com",
        timeout_seconds=3600,  # 1 小时超时
        on_timeout="reject",   # 超时视为拒绝
        depends_on=["draft"],
    )
    .step(id="send", agent="a", prompt="send email", depends_on=["approve-email"])
    .build()
)
```

**新增类**：`ApprovalStep`（继承 WorkflowStep）

**字段**：
- `approver: str` — 审批人标识（user_id / email / role）
- `timeout_seconds: int` — 审批超时
- `on_timeout: str` — 超时策略（"reject" / "approve" / "abort"）

**执行语义**：
1. 到达 ApprovalStep → 保存 checkpoint（status=WAITING_APPROVAL + approval_request_id）
2. 发送审批通知（通过 webhook / email / Slack）
3. 暂停 workflow，等待外部调用 `approve_step(workflow_id, step_id, decision)`
4. 收到决策 → 保存 checkpoint → 继续执行
5. 超时 → 按 on_timeout 策略处理

#### 4.2.2 审批存储（ApprovalStore）

```python
from xruntime._runtime._workflow import ApprovalStore, InMemoryApprovalStore

store = InMemoryApprovalStore()
request = await store.create_request(
    workflow_id="wf-1",
    step_id="approve-email",
    approver="manager@company.com",
    timeout_seconds=3600,
)
# 外部审批
await store.submit_decision(request.request_id, decision="approved", user_id="manager")
```

**新增类**：`ApprovalStore`（ABC）+ `InMemoryApprovalStore`

**方法**：
- `create_request(workflow_id, step_id, approver, timeout_seconds) -> ApprovalRequest`
- `submit_decision(request_id, decision, user_id) -> None`
- `get_request(request_id) -> ApprovalRequest | None`
- `list_pending(approver: str) -> list[ApprovalRequest]`
- `check_timeout(request_id) -> bool`（超时则自动决策）

#### 4.2.3 审批网关 API

```
POST /v1/workflows/{workflow_id}/steps/{step_id}/approval
{
    "decision": "approved" | "rejected",
    "user_id": "manager@company.com",
    "comment": "Looks good"
}
```

**新增路由**：挂载到 FastAPI app，走 AuthMiddleware + RBAC 校验

### 4.3 文件结构

```
src/xruntime/_runtime/_workflow/
├── _approval.py         # 【新增】ApprovalStep + ApprovalStore + InMemoryApprovalStore
├── _orchestrator.py     # 扩展：处理 ApprovalStep 的暂停/恢复
├── _checkpoint.py       # 新增 WAITING_APPROVAL status
└── __init__.py          # 导出 ApprovalStore 等

src/xruntime/_gateway/
└── _workflow_router.py  # 【新增】审批网关路由
```

### 4.4 TDD 测试计划

| 测试文件 | 测试类 | 测试数 | 覆盖场景 |
|----------|--------|--------|----------|
| `test_approval_step.py` | TestApprovalStep | 12 | 创建审批、等待、approved、rejected |
| `test_approval_store.py` | TestApprovalStore | 15 | CRUD、超时、pending 列表、并发安全 |
| `test_workflow_approval_integration.py` | TestWorkflowApproval | 13 | 端到端：暂停→审批→恢复、超时策略、RBAC |

---

## 五、P3-C：Credential Broker 生产硬化

### 5.1 目标

将 P2 的 Credential Broker 从 InMemory 提升到生产级：Redis 持久化、凭证自动轮换、细粒度 scope 访问控制。

### 5.2 设计

#### 5.2.1 Redis 持久化（RedisCredentialStore）

```python
from xruntime._runtime._credential import RedisCredentialStore

store = RedisCredentialStore(
    redis_url="redis://localhost:6379",
    key_prefix="tenant:{tid}:creds:",
    ttl_seconds=3600,
)
broker = CredentialBroker(store=store)
```

**新增类**：`RedisCredentialStore`（实现与 InMemoryCheckpointStore 相同的 ABC）

**特性**：
- 凭证按 `tenant:{tid}:creds:{cred_id}` 存储
- TTL 自动过期（Redis EXPIRE）
- LRU 淘汰（Redis ZSET 按 accessed_at 排序）
- 多租户隔离（key 前缀 + tenant context 校验）

#### 5.2.2 自动轮换（AutoRotation）

```python
broker = CredentialBroker(
    config=CredentialBrokerConfig(
        auto_rotate=True,
        rotation_buffer_seconds=300,  # 过期前 5 分钟主动轮换
    ),
)
```

**新增机制**：
- 后台任务定期扫描即将过期的凭证
- 在 `rotation_buffer_seconds` 时间窗口内主动签发新凭证
- 旧凭证标记为 `rotating`，保留 grace period 后撤销
- 网关的 `drain_invalidations()` 返回已轮换的 credential_id

#### 5.2.3 细粒度 Scope 访问控制

```python
config = CredentialBrokerConfig(
    allowed_scopes=["chat", "embed", "tool_use", "admin"],
    scope_hierarchy={
        "admin": ["chat", "embed", "tool_use"],  # admin 包含其他所有
        "tool_use": ["chat"],
    },
)
```

**新增机制**：
- scope 层级（admin > tool_use > chat）
- 签发时自动展开层级（请求 "tool_use" → 实际授予 ["tool_use", "chat"]）
- 校验时检查层级（有 "admin" 的凭证自动通过 "chat" 校验）

### 5.3 文件结构

```
src/xruntime/_runtime/_credential/
├── _broker.py               # 扩展：auto_rotate 逻辑
├── _redis_store.py          # 【新增】RedisCredentialStore
├── _scope_hierarchy.py      # 【新增】scope 层级解析
├── _config.py               # 扩展：auto_rotate / rotation_buffer_seconds / scope_hierarchy
└── __init__.py              # 导出 RedisCredentialStore
```

### 5.4 TDD 测试计划

| 测试文件 | 测试类 | 测试数 | 覆盖场景 |
|----------|--------|--------|----------|
| `test_redis_credential_store.py` | TestRedisCredentialStore | 15 | save/load/delete、TTL、LRU、多租户隔离（fakeredis） |
| `test_credential_auto_rotation.py` | TestAutoRotation | 12 | 主动轮换、grace period、旧凭证撤销、drain |
| `test_scope_hierarchy.py` | TestScopeHierarchy | 8 | 层级展开、层级校验、循环检测 |

---

## 六、P3-D：Workflow 可观测性

### 6.1 目标

为 Workflow 执行添加标准化可观测性：OTel 分布式追踪、per-step 指标、审计日志集成。

### 6.2 设计

#### 6.2.1 OTel Tracing

```python
from xruntime._runtime._workflow import WorkflowTracer

tracer = WorkflowTracer(
    service_name="xruntime-workflow",
    otlp_endpoint="http://localhost:4317",
)

result = await run_workflow(wf, executor, store=store, tracer=tracer)
```

**新增类**：`WorkflowTracer`

**Span 结构**：
```
workflow.run (root span)
├── workflow.step.s1 (child span)
│   ├── attributes: step_id, agent, status, duration_ms
│   └── event: step.started, step.completed
├── workflow.step.s2
│   ├── attributes: step_id, agent, status, duration_ms
│   └── event: step.started, step.failed, step.retried
└── workflow.checkpoint.save (event)
    └── attributes: checkpoint_id, parent_checkpoint_id
```

#### 6.2.2 Per-Step 指标

```python
from xruntime._runtime._workflow import WorkflowMetrics

metrics = WorkflowMetrics(
    prometheus_endpoint="http://localhost:9090",
)
```

**指标**：
- `workflow_step_duration_seconds{workflow,step,status}` — 直方图
- `workflow_step_total{workflow,step,status}` — 计数器
- `workflow_checkpoint_save_total{workflow}` — 计数器
- `workflow_resume_total{workflow}` — 计数器

#### 6.2.3 审计日志集成

```python
# 自动记录到 AuditMiddleware 的 audit log
{
    "action": "workflow.step.executed",
    "workflow_id": "wf-1",
    "step_id": "s1",
    "agent": "coder",
    "status": "COMPLETED",
    "duration_ms": 1234,
    "tenant_id": "tenant-1",
    "timestamp": "2026-07-01T12:00:00Z",
}
```

### 6.3 文件结构

```
src/xruntime/_runtime/_workflow/
├── _tracer.py            # 【新增】WorkflowTracer（OTel）
├── _metrics.py           # 【新增】WorkflowMetrics（Prometheus）
├── _audit.py             # 【新增】WorkflowAuditLogger
└── __init__.py           # 导出
```

### 6.4 TDD 测试计划

| 测试文件 | 测试类 | 测试数 | 覆盖场景 |
|----------|--------|--------|----------|
| `test_workflow_tracer.py` | TestWorkflowTracer | 12 | span 创建、属性、事件、no-op 模式 |
| `test_workflow_metrics.py` | TestWorkflowMetrics | 10 | 指标记录、标签、直方图 |
| `test_workflow_audit.py` | TestWorkflowAudit | 8 | 审计日志格式、tenant 隔离 |

---

## 七、P3 实施计划

### 7.1 Phase 优先级与依赖

```
P3-A (控制流) ──────┐
                    ├──→ P3-D (可观测性)
P3-B (HITL) ────────┤
                    │
P3-C (Broker 硬化) ──┘ (独立，可并行)
```

- **P3-A 和 P3-B 有顺序依赖**：HITL 的 ApprovalStep 需要控制流的 step 类型扩展
- **P3-C 完全独立**：可与其他 Phase 并行开发
- **P3-D 依赖 A/B**：需要先有新 step 类型才能追踪

### 7.2 建议执行顺序

| 顺序 | Phase | 理由 |
|------|-------|------|
| 1 | P3-C | 独立、生产就绪阻断点、风险最低 |
| 2 | P3-A | 核心能力、其他 Phase 的基础 |
| 3 | P3-B | 依赖 P3-A 的 step 类型扩展 |
| 4 | P3-D | 依赖 P3-A/B 的新 step 类型 |

### 7.3 每个 Phase 的 TDD 流程

```
1. Red 阶段
   ├── 创建测试文件
   ├── 编写全部测试用例（导入不存在的类 → 全部 FAIL）
   └── 确认测试失败原因为 ImportError / AttributeError

2. Green 阶段
   ├── 实现新增模块（最小可用）
   ├── 扩展现有模块（orchestrator / sdk / config）
   ├── 运行测试 → 全部 PASS
   └── 运行覆盖率 → 目标 100%

3. Refactor 阶段
   ├── 优化代码结构
   ├── 补充边界测试（覆盖遗漏行）
   ├── 全量回归测试
   └── 更新文档
```

---

## 八、验收标准

### 8.1 每个 Phase 的验收门禁

| 门禁 | 标准 |
|------|------|
| 测试通过率 | 100%（0 failed） |
| 新增模块覆盖率 | ≥ 95%（目标 100%） |
| 全量回归 | 0 failed（无回归） |
| 安全验证 | 无密钥泄漏、fail-closed |
| TDD 合规 | Red → Green → Refactor 全流程 |

### 8.2 P3 整体验收

| 维度 | 目标 |
|------|------|
| 新增模块数 | 13+ |
| 新增测试数 | 165+ |
| 整体覆盖率 | ≥ 95% |
| 全量回归 | 1157+ passed（992 + 165） |
| Eve 能力对齐 | 条件分支 / 循环 / 子工作流 / 定时器 / HITL / OTel |

---

## 九、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| P3-A 控制流复杂度超预期 | 中 | 高 | 分步交付：先 ConditionalStep，再 LoopStep |
| P3-B 审批超时处理复杂 | 中 | 中 | 参照 P1-A ApprovalMiddleware 的超时模式 |
| P3-C Redis 多租户隔离 bug | 低 | 高 | 复用 TenantAwareRedisStorage 的前缀模式 + fakeredis 测试 |
| P3-D OTel 依赖引入 | 低 | 低 | 使用 opentelemetry-sdk（已在 extra 中） |

---

## 十、附录：P2 → P3 能力对比

| 能力 | P2 状态 | P3 目标 | Eve 对标 |
|------|---------|---------|----------|
| DAG 工作流 | ✅ 线性 + 并行 | + 条件分支 + 循环 | Workflow SDK |
| Checkpoint 持久化 | ✅ InMemory | + Redis | Workflow SDK |
| Resume 恢复 | ✅ | + 超时恢复 | Workflow SDK |
| 子工作流 | ❌ | ✅ SubWorkflowStep | Eve subagents |
| 持久化定时器 | ❌ | ✅ TimerStep | Workflow SDK sleep |
| Step 级 HITL | ❌ | ✅ ApprovalStep | needsApproval |
| 凭证 Redis 持久化 | ❌ | ✅ RedisCredentialStore | — |
| 凭证自动轮换 | ❌ | ✅ AutoRotation | — |
| OTel Tracing | ❌ | ✅ WorkflowTracer | OTel 原生 |
| Per-step 指标 | ❌ | ✅ WorkflowMetrics | — |
| 审计日志 | ✅ AuditMiddleware | + Workflow 审计 | Hooks |
