# P3-A Workflow Control Flow Step Types — 使用指南

> 生成日期: 2026-07-01
> 基础: P3-A 已完成 — 4 个新 Step 类型 + WorkflowBuilder 扩展
> 测试: 60 个测试全部通过, _steps.py 100% 覆盖率
> 回归: 1143 passed, 0 failures

## 目录

1. [概览](#1-概览)
2. [ConditionalStep — 条件分支](#2-conditionalstep--条件分支)
3. [LoopStep — 循环节点](#3-loopstep--循环节点)
4. [SubWorkflowStep — 子工作流](#4-subworkflowstep--子工作流)
5. [TimerStep — 持久化定时器](#5-timerstep--持久化定时器)
6. [组合使用](#6-组合使用)
7. [API 速查](#7-api-速查)

---

## 1. 概览

P3-A 在 P2 的 DAG 工作流引擎基础上新增 4 个 Step 类型,对齐 Vercel Eve Agent Stack 的控制流能力:

| Step 类型 | 用途 | Eve 对标 |
|-----------|------|----------|
| `ConditionalStep` | 运行时条件分支(if/else) | Conditional branches |
| `LoopStep` | 循环执行直到满足退出条件 | Iterative refinement |
| `SubWorkflowStep` | 嵌套子工作流 | Subagents |
| `TimerStep` | 持久化定时器(sleep) | Durable sleep/timer |

所有新 Step 类型:

- 继承 `WorkflowStep`,无缝集成到现有 DAG
- 通过 `WorkflowBuilder` 链式 API 创建
- 向后兼容(不使用新类型时 workflow 行为不变)
- 支持与普通 step 混合使用

### 导入

```python
from xruntime._runtime._workflow import (
    WorkflowBuilder,
    FunctionExecutor,
    run_workflow,
    ConditionalStep,
    LoopStep,
    SubWorkflowStep,
    TimerStep,
)
```

---

## 2. ConditionalStep — 条件分支

### 概念

`ConditionalStep` 包装一组 inner steps 和一个 condition 谓词。当 `condition(context)` 返回 `True` 时执行 inner steps,返回 `False` 时跳过(输出空字符串)。

### 关键行为

- **condition 异常 fail-closed**: 谓词抛异常时返回 `False`(跳过分支,不崩溃 workflow)
- **inner steps 顺序执行**: 按列表顺序,共享 context
- **branch 输出**: 最后一个 inner step 的输出(condition=False 时为空字符串)
- **多 branch 独立评估**: 多个 ConditionalStep 互斥由 condition 互斥保证

### 示例 1: 基本条件分支

```python
from xruntime._runtime._workflow import (
    WorkflowBuilder, FunctionExecutor, run_workflow,
)
from xruntime._runtime._orchestrator import WorkflowStep

def step_fn(step, ctx):
    if step.id == "classify":
        return "urgent"  # 模拟分类结果
    return f"executed-{step.id}"

wf = (
    WorkflowBuilder()
    .id("wf-conditional")
    .step(id="classify", agent="a", prompt="classify input")
    .branch(
        id="branch-urgent",
        agent="a",
        prompt="handle urgent",
        condition=lambda ctx: ctx.get("classify") == "urgent",
        inner_steps=[
            WorkflowStep(id="escalate", name="Escalate",
                         agent="a", prompt="escalate"),
            WorkflowStep(id="notify", name="Notify",
                         agent="a", prompt="notify team"),
        ],
        depends_on=["classify"],
    )
    .step(id="done", agent="a", prompt="finalize",
          depends_on=["branch-urgent"])
    .build()
)

executor = FunctionExecutor(step_fn)
result = await run_workflow(wf, executor)
# result.step_results["branch-urgent"] = "executed-notify"
```

### 示例 2: 多分支互斥

```python
wf = (
    WorkflowBuilder()
    .id("wf-multi-branch")
    .step(id="classify", agent="a", prompt="classify")
    .branch(
        id="branch-urgent",
        agent="a",
        condition=lambda ctx: ctx.get("classify") == "urgent",
        inner_steps=[WorkflowStep(id="escalate", name="E",
                                   agent="a", prompt="p")],
        depends_on=["classify"],
    )
    .branch(
        id="branch-normal",
        agent="a",
        condition=lambda ctx: ctx.get("classify") == "normal",
        inner_steps=[WorkflowStep(id="queue", name="Q",
                                   agent="a", prompt="p")],
        depends_on=["classify"],
    )
    .build()
)
# classify=urgent → branch-urgent 执行, branch-normal 跳过
```

### 示例 3: 嵌套条件

```python
wf = (
    WorkflowBuilder()
    .branch(
        id="outer",
        agent="a",
        condition=lambda ctx: ctx.get("level") == "high",
        inner_steps=[
            WorkflowStep(id="inner-check", name="Inner",
                         agent="a", prompt="check"),
        ],
    )
    .build()
)
```

---

## 3. LoopStep — 循环节点

### 概念

`LoopStep` 重复执行其 `agent`/`prompt`,直到 `condition(context)` 返回 `False` 或达到 `max_iterations`。

### 关键行为

- **condition 在每次迭代前评估**: True 继续, False 退出
- **context 传播**: 每次迭代的输出存入 `context[step_id]`,下次迭代可访问
- **max_iterations 硬上限**: 防止无限循环
- **max_iterations=0**: 零迭代(空输出)
- **on_failure 策略**:
  - `"abort"` (默认): 迭代异常 → step 失败(None)
  - `"continue"`: 异常 → 空输出,继续下一次迭代

### 示例 1: 质量迭代提升

```python
def refine_step(step, ctx):
    quality = ctx.get("quality", 0.1)
    # 模拟每次迭代提升 0.3
    new_quality = quality + 0.3
    return f"quality={new_quality}"

wf = (
    WorkflowBuilder()
    .id("wf-refine-loop")
    .loop(
        id="refine",
        agent="coder",
        prompt="refine output until quality >= 0.9",
        condition=lambda ctx: ctx.get("quality", 0.1) < 0.9,
        max_iterations=10,
    )
    .build()
)

executor = FunctionExecutor(refine_step)
result = await run_workflow(wf, executor)
# 3 次迭代后 quality >= 0.9,退出
```

### 示例 2: 固定次数迭代

```python
wf = (
    WorkflowBuilder()
    .id("wf-fixed-iter")
    .loop(
        id="process-batch",
        agent="worker",
        prompt="process next batch item",
        condition=lambda ctx: True,  # 永远 True
        max_iterations=5,  # 固定 5 次
    )
    .build()
)
```

### 示例 3: 带依赖的循环

```python
wf = (
    WorkflowBuilder()
    .id("wf-loop-dep")
    .step(id="init", agent="a", prompt="initialize")
    .loop(
        id="refine",
        agent="a",
        prompt="refine",
        condition=lambda ctx: ctx.get("needs_more", True),
        max_iterations=3,
        depends_on=["init"],  # init 完成后才开始循环
    )
    .build()
)
```

### 示例 4: 容错循环

```python
wf = (
    WorkflowBuilder()
    .id("wf-loop-continue")
    .loop(
        id="retry-task",
        agent="a",
        prompt="attempt task",
        condition=lambda ctx: True,
        max_iterations=5,
        on_failure="continue",  # 某次迭代失败不终止
    )
    .build()
)
```

---

## 4. SubWorkflowStep — 子工作流

### 概念

`SubWorkflowStep` 将一个完整的子 `Workflow` 作为父工作流的一个 step 执行。子工作流按自己的拓扑序运行,最后一步的输出作为父 step 的输出。

### 关键行为

- **共享父 context**: 子工作流的 steps 可访问父 dep 的输出
- **独立拓扑序**: 子工作流有自己的 DAG,按自己的 `topological_order()` 执行
- **失败传播**: 子 step 的 `on_failure="abort"` → 父 step 失败
- **空子工作流**: 无 steps 时返回空字符串

### 示例 1: 研究子工作流

```python
from xruntime._runtime._orchestrator import Workflow, WorkflowStep

# 子工作流: 搜索 → 总结
research_sub_wf = Workflow(
    id="research-sub",
    name="Research Sub",
    steps=[
        WorkflowStep(id="search", name="Search",
                     agent="researcher", prompt="search web"),
        WorkflowStep(id="summarize", name="Summarize",
                     agent="writer", prompt="summarize findings",
                     depends_on=["search"]),
    ],
)

# 父工作流: 规划 → 研究 → 报告
parent_wf = (
    WorkflowBuilder()
    .id("wf-parent")
    .step(id="plan", agent="planner", prompt="plan research")
    .subworkflow(
        id="research",
        workflow=research_sub_wf,
        depends_on=["plan"],
    )
    .step(id="report", agent="writer", prompt="write report",
          depends_on=["research"])
    .build()
)
# research 的输出 = summarize 的输出
# report 可通过 ctx["research"] 访问
```

### 示例 2: 多步子工作流

```python
sub_wf = Workflow(
    id="pipeline",
    name="Pipeline",
    steps=[
        WorkflowStep(id="extract", name="Extract",
                     agent="a", prompt="extract data"),
        WorkflowStep(id="transform", name="Transform",
                     agent="a", prompt="transform",
                     depends_on=["extract"]),
        WorkflowStep(id="load", name="Load",
                     agent="a", prompt="load to db",
                     depends_on=["transform"]),
    ],
)

wf = (
    WorkflowBuilder()
    .id("wf-etl")
    .subworkflow(id="etl", workflow=sub_wf)
    .build()
)
# etl 输出 = load 的输出
```

### 示例 3: 子工作流访问父 context

```python
def step_fn(step, ctx):
    if step.id == "seed":
        return "seed-data"
    if step.id == "use-seed":
        return f"got:{ctx.get('seed', 'MISSING')}"
    return f"out-{step.id}"

sub_wf = Workflow(
    id="sub",
    steps=[WorkflowStep(id="use-seed", name="UseSeed",
                         agent="x", prompt="use seed")],
)

wf = (
    WorkflowBuilder()
    .id("wf-sub-ctx")
    .step(id="seed", agent="a", prompt="produce seed")
    .subworkflow(id="sub", workflow=sub_wf, depends_on=["seed"])
    .build()
)
# sub 中的 use-seed 可访问 ctx["seed"] = "seed-data"
```

---

## 5. TimerStep — 持久化定时器

### 概念

`TimerStep` 暂停 workflow 执行 `duration_seconds` 秒。在 checkpoint 模式下,保存 `SLEEPING` checkpoint,崩溃后可恢复。

### 关键行为

- **输出**: 始终为空字符串(timers 只 gate,不 produce)
- **duration_seconds <= 0**: no-op(立即返回)
- **checkpoint 模式**: 保存 SLEEPING + wake_at,resume 检查是否到时间
- **非 checkpoint 模式**: 简单 `asyncio.sleep`

### 示例 1: 基本定时器

```python
wf = (
    WorkflowBuilder()
    .id("wf-timer")
    .step(id="start", agent="a", prompt="start process")
    .sleep(id="wait", duration_seconds=60, depends_on=["start"])
    .step(id="continue", agent="a", prompt="continue after wait",
          depends_on=["wait"])
    .build()
)
# start → 等待 60 秒 → continue
```

### 示例 2: 零时长 no-op

```python
wf = (
    WorkflowBuilder()
    .id("wf-noop-timer")
    .sleep(id="noop", duration_seconds=0)  # 立即返回
    .step(id="after", agent="a", prompt="p", depends_on=["noop"])
    .build()
)
# noop 不阻塞,after 立即执行
```

### 示例 3: 带 checkpoint 的定时器

```python
from xruntime._runtime._workflow import (
    InMemoryCheckpointStore, run_workflow,
)

store = InMemoryCheckpointStore()
wf = (
    WorkflowBuilder()
    .id("wf-durable-timer")
    .step(id="s1", agent="a", prompt="p")
    .sleep(id="wait", duration_seconds=0.1, depends_on=["s1"])
    .step(id="s2", agent="a", prompt="p", depends_on=["wait"])
    .build()
)

# 带 checkpoint 运行 — SLEEPING checkpoint 被保存
result = await run_workflow(wf, FunctionExecutor(lambda s, c: "ok"), store=store)
# 崩溃后可 resume:检查 wake_at,未到则继续等待,已到则继续执行
```

---

## 6. 组合使用

### 示例: 全栈控制流

```python
from xruntime._runtime._orchestrator import Workflow, WorkflowStep

# 子工作流
research_sub = Workflow(
    id="research-sub", name="Research",
    steps=[
        WorkflowStep(id="search", name="Search",
                     agent="researcher", prompt="search"),
        WorkflowStep(id="summarize", name="Summarize",
                     agent="writer", prompt="summarize",
                     depends_on=["search"]),
    ],
)

# 主工作流: 4 种新 step 类型组合
wf = (
    WorkflowBuilder()
    .id("wf-full-stack")
    .step(id="start", agent="a", prompt="initialize")

    # 1. 条件分支
    .branch(
        id="branch",
        agent="a",
        condition=lambda ctx: ctx.get("start", "").startswith("ok"),
        inner_steps=[
            WorkflowStep(id="b1", name="B1",
                         agent="a", prompt="branch step"),
        ],
        depends_on=["start"],
    )

    # 2. 循环精炼
    .loop(
        id="refine",
        agent="coder",
        prompt="refine output",
        condition=lambda ctx: True,
        max_iterations=3,
        depends_on=["branch"],
    )

    # 3. 子工作流
    .subworkflow(
        id="research",
        workflow=research_sub,
        depends_on=["refine"],
    )

    # 4. 定时器等待
    .sleep(
        id="wait",
        duration_seconds=0,  # no-op for testing
        depends_on=["research"],
    )

    .step(id="end", agent="a", prompt="finalize",
          depends_on=["wait"])
    .build()
)

result = await run_workflow(wf, FunctionExecutor(lambda s, c: f"out-{s.id}"))
assert result.status == "COMPLETED"
```

---

## 7. API 速查

### WorkflowBuilder 新方法

| 方法 | 产生 | 必需参数 | 可选参数 |
|------|------|----------|----------|
| `.branch()` | ConditionalStep | `id`, `agent` | `condition`(默认 True), `inner_steps`, `depends_on`, `on_failure`, `max_retries` |
| `.loop()` | LoopStep | `id`, `agent`, `prompt` | `condition`(默认 False), `max_iterations`(默认 3), `depends_on`, `on_failure`, `max_retries` |
| `.subworkflow()` | SubWorkflowStep | `id`, `workflow` | `agent`, `prompt`, `depends_on`, `on_failure`, `max_retries` |
| `.sleep()` | TimerStep | `id`, `duration_seconds` | `agent`, `prompt`, `depends_on`, `on_failure`, `max_retries` |

### Step 类型字段

```python
ConditionalStep(
    id, name, agent, prompt,          # WorkflowStep 基础字段
    condition: Callable[[dict], bool], # 谓词(默认 always-True)
    inner_steps: list[WorkflowStep],  # 分支内 steps(默认空)
    depends_on, on_failure, max_retries,
)

LoopStep(
    id, name, agent, prompt,          # WorkflowStep 基础字段
    condition: Callable[[dict], bool], # 谓词(默认 always-False)
    max_iterations: int,               # 硬上限(默认 1)
    depends_on, on_failure, max_retries,
)

SubWorkflowStep(
    id, name, agent, prompt,          # WorkflowStep 基础字段
    sub_workflow: Workflow,            # 嵌套子工作流
    depends_on, on_failure, max_retries,
)

TimerStep(
    id, name, agent, prompt,          # WorkflowStep 基础字段
    duration_seconds: int,             # 睡眠时长(默认 0)
    depends_on, on_failure, max_retries,
)
```

### 执行语义速查

| 行为 | ConditionalStep | LoopStep | SubWorkflowStep | TimerStep |
|------|-----------------|----------|-----------------|-----------|
| condition=True | 执行 inner steps | 执行一次迭代 | N/A | N/A |
| condition=False | 跳过(空输出) | 退出循环 | N/A |