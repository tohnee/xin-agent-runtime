# Evals 框架 MVP 架构设计文档

> 生成日期：2026-07-01
> 适用项目：`xin-agent-runtime`（包名 `agentscope` + 企业扩展 `xruntime`）
> 任务编号：P1-B
> 关联文档：`docs/竟品分析/Evals-CI-CD-Implementation.md`（完整 CI/CD 实施步骤）、`docs/竟品分析/vercel Eve Agent Stack.md`（Vercel Eve 调研）
> 前置依赖：P1-A ApprovalMiddleware（已实现）

---

## 一、设计目标

### 1.1 MVP 范围

本设计文档限定在 P1-B MVP 范围内，目标是交付一个可运行的最小 Evals 框架，**不**包含 CI/CD 集成、Braintrust 上报、GitHub Actions workflow 等延展能力（这些在 `Evals-CI-CD-Implementation.md` 已有完整规划，属于 Phase 2-4）。

**MVP 必须达成：**

1. 声明式 `@define_eval` DSL，可定义 Agent 行为评测场景
2. `EvalRunner` 可在本地 in-process 跑通一组 Eval
3. `EvalContext` 提供 5 类核心断言：回复内容、工具调用、中间件副作用、租户隔离、审批门控
4. 三种 Reporter：Console（默认）、JUnit XML、JSON
5. 至少 4 个示范 Eval 覆盖 P1-A 的 ApprovalMiddleware 行为验证

**MVP 不包含：**

- 真实模型在线 Eval（nightly job）
- Braintrust 集成
- GitHub Actions workflow
- pytest plugin 形态
- 并发执行（`pytest-xdist` 风格）

### 1.2 与 P1-A 的闭环

P1-A 刚交付的 `ApprovalMiddleware` 有 29 个单元测试，但都是白盒断言「给定输入，中间件返回某值」。**Evals 框架应能从黑盒视角验证：**

- 「以 always 策略 + approver 拒绝 → Agent 回复中不应包含工具执行结果」
- 「以 once 策略 + 首次批准 → 第二轮同工具调用不应触发 approver」
- 「predicate 策略 + 破坏性命令 → 被 RBAC/Audit 联动记录为 DENY」

这同时也是 Evals 框架自身的 smoke test — 如果 Evals 能跑通 ApprovalMiddleware 的 4 个场景，说明 DSL、Runner、Reporter、Context 全链路可用。

---

## 二、核心架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Eval 定义层（用户视角）                   │
│                                                             │
│  tests/evals/<domain>/test_*.py                             │
│    @define_eval("...", domain="approval", tags=("offline",))│
│    async def test_xxx(t: EvalContext) -> None:              │
│        await t.send("...")                                   │
│        t.reply_contains("...")                              │
│        t.called_tool("Bash")                                │
│        t.expect_blocked(by="approval")                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ 收集
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    EvalCollector                             │
│  扫描 tests/evals/ → 收集 EvalSpec → 按 tags 过滤           │
└──────────────────────────┬──────────────────────────────────┘
                           │ 调度
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    EvalRunner                                │
│  for spec in specs:                                         │
│    ctx = EvalContext(runner, spec.eval_id)                  │
│    await spec.fn(ctx)                                       │
│    result = aggregate(ctx.results)                          │
│  reporters.report(all_results)                              │
└──────────────────────────┬──────────────────────────────────┘
                           │ 依赖
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    EvalTarget（运行目标）                    │
│                                                             │
│  ┌─ InProcessTarget ─────────────────────────────────────┐  │
│  │  build_xruntime_app(config)                            │  │
│  │  + fakeredis.aioredis.FakeRedis                        │  │
│  │  + MockModel via ModelResolver                         │  │
│  │  + httpx.ASGITransport（不占端口）                     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ RemoteTarget ─────────────────────────────────────────┐  │
│  │  XRUNTIME_EVAL_TARGET=http://...                       │  │
│  │  + httpx.AsyncClient（真实 HTTP）                      │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ 通过 EvalContext 暴露
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    EvalContext（DSL 入口）                   │
│                                                             │
│  ── 交互 ──                                                 │
│  await t.send(msg)  → (reply, events)                      │
│  t.as_tenant(tid) / t.as_role(role)                        │
│                                                             │
│  ── 断言（不抛异常，记录 AssertionResult） ──               │
│  t.reply_contains(needle)                                   │
│  t.called_tool(name, times=None)                           │
│  t.tool_input_matches(name, matcher)                       │
│  t.expect_blocked(by="rbac"|"quota"|"approval")            │
│  t.audit_logged(tool_name)                                  │
│  t.no_cross_tenant_leak(other_tenant)                      │
│  t.approval_required_for(tool_name)                        │
│                                                             │
│  ── 状态 ──                                                 │
│  t.reply: str                                               │
│  t.events: list[dict]                                       │
│  t.results: list[AssertionResult]                          │
└──────────────────────────┬──────────────────────────────────┘
                           │ 产出
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Reporter                                  │
│  ConsoleReporter  → stdout（ANSI 彩色）                     │
│  JUnitReporter    → eval-results.xml                        │
│  JsonReporter     → eval-results.json                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、模块划分

### 3.1 文件结构

```
src/xruntime/_eval/
├── __init__.py        # 导出 define_eval / EvalRunner / matchers
├── __main__.py        # CLI 入口：python -m xruntime.eval run
├── _models.py         # EvalSpec / EvalResult / AssertionResult / EvalStatus
├── _define.py         # @define_eval 装饰器
├── _context.py        # EvalContext DSL
├── _matchers.py       # includes / matches_regex / equals / not_contains / has_keys
├── _collector.py      # EvalCollector：扫描 tests/evals/
├── _runner.py         # EvalRunner：调度执行
├── _target_inproc.py  # InProcessTarget：build_xruntime_app + fakeredis
├── _target_remote.py  # RemoteTarget：httpx 远程
└── _reporter.py       # Console / JUnit / Json Reporter
```

### 3.2 职责边界

| 模块 | 职责 | 不负责 |
|------|------|--------|
| `_models.py` | 纯数据类，无业务逻辑 | 任何 I/O |
| `_define.py` | 装饰器收集 EvalSpec 到模块级 registry | 执行 Eval |
| `_context.py` | DSL 入口，断言记录，状态保存 | 直接调 transport |
| `_matchers.py` | 值匹配器，纯函数 | 业务语义 |
| `_collector.py` | 扫描目录，import 模块，收集 EvalSpec | 执行 |
| `_runner.py` | 调度执行 + 错误捕获 + 结果聚合 | 具体断言逻辑 |
| `_target_inproc.py` | 组装 in-process app + fakeredis | Eval 语义 |
| `_target_remote.py` | httpx 远程 transport | in-process 装配 |
| `_reporter.py` | 结果渲染输出 | 执行 Eval |

### 3.3 依赖关系

```
_define.py ─────► _models.py
_context.py ─────► _models.py
_matchers.py ────► (无内部依赖)
_collector.py ───► _define.py, _models.py
_runner.py ─────► _collector.py, _context.py, _models.py, _target_*.py, _reporter.py
_target_inproc.py ─► xruntime._server.build_xruntime_app, fakeredis
_target_remote.py ─► httpx
_reporter.py ────► _models.py
```

无循环依赖。`_context.py` 通过 `runner` 句柄间接访问 target，不直接 import target 模块。

---

## 四、核心接口

### 4.1 数据模型（`_models.py`）

```python
class EvalStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"        # 未捕获异常（非断言失败）
    SKIPPED = "skipped"

@dataclass
class AssertionResult:
    name: str
    passed: bool
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

@dataclass
class EvalResult:
    eval_id: str
    description: str
    status: EvalStatus
    assertions: list[AssertionResult] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

@dataclass
class EvalSpec:
    eval_id: str           # "domain.function_name"
    description: str
    domain: str
    tags: list[str]
    fn: Callable[[Any], Awaitable[None]]
```

### 4.2 装饰器（`_define.py`）

```python
def define_eval(
    description: str,
    *,
    domain: str = "general",
    tags: tuple[str, ...] = ("offline",),
) -> Callable[[Callable[[Any], Awaitable[None]]], EvalSpec]:
    """声明一个 Agent 行为 Eval。"""
    def _wrap(fn):
        spec = EvalSpec(
            eval_id=f"{domain}.{fn.__name__}",
            description=description,
            domain=domain,
            tags=list(tags),
            fn=fn,
        )
        _REGISTRY.append(spec)  # 模块级 list，供 Collector 扫描
        return spec
    return _wrap
```

### 4.3 EvalContext（`_context.py`）

**核心原则：断言不抛异常，记录到 `self._results`，由 Runner 聚合。**

```python
class EvalContext:
    # ── 交互 ──
    async def send(self, message: str) -> str: ...
    def as_tenant(self, tenant_id: str) -> "EvalContext": ...
    def as_role(self, role: str) -> "EvalContext": ...

    # ── 回复断言 ──
    def reply_contains(self, needle: str) -> None: ...
    def reply_matches(self, pattern: str) -> None: ...

    # ── 工具调用断言 ──
    def called_tool(self, name: str, *, times: int | None = None) -> None: ...
    def tool_input_matches(self, name: str, matcher: Matcher) -> None: ...

    # ── 中间件副作用断言 ──
    def expect_blocked(self, *, by: str = "rbac") -> None: ...
    def audit_logged(self, tool_name: str) -> None: ...

    # ── 租户隔离断言 ──
    def no_cross_tenant_leak(self, other_tenant: str) -> None: ...

    # ── 审批门控断言（P1-A 联动） ──
    def approval_required_for(self, tool_name: str) -> None: ...
    def approval_was_cached(self, tool_name: str) -> None: ...
```

### 4.4 Runner（`_runner.py`）

```python
class EvalRunner:
    def __init__(
        self,
        target: str | None = None,    # "in-process" 或 URL
        *,
        tags: list[str] | None = None,
        reporters: list[Reporter] | None = None,
    ): ...

    async def run(self, evals_dir: str = "tests/evals") -> int:
        """收集 + 执行 + 报告；返回 exit code (0=pass)。"""
        specs = EvalCollector(evals_dir).collect(tags=self.tags)
        results = []
        for spec in specs:
            result = await self._run_one(spec)
            results.append(result)
        for r in self.reporters:
            r.report(results)
        return 0 if all(r.status == EvalStatus.PASSED for r in results) else 1

    async def _run_one(self, spec: EvalSpec) -> EvalResult:
        """单个 Eval 执行：异常 → ERROR，断言失败 → FAILED，否则 PASSED。"""
        ctx = EvalContext(self, spec.eval_id)
        try:
            await self._setup_target()
            await spec.fn(ctx)
            status = PASSED if all(a.passed for a in ctx.results) else FAILED
        except Exception as exc:
            status = ERROR
            ctx._results.append(AssertionResult(
                name="__uncaught_exception__",
                passed=False,
                message=repr(exc),
            ))
        return EvalResult(eval_id=spec.eval_id, ...)
```

### 4.5 InProcessTarget（`_target_inproc.py`）

```python
class InProcessTarget:
    """in-process target：build_xruntime_app + fakeredis + MockModel。"""

    async def setup(self) -> None:
        """组装 app，但不监听端口（用 ASGITransport）。"""
        import fakeredis.aioredis
        from xruntime._server import build_xruntime_app
        from xruntime._config import XRuntimeConfig

        self._fake_redis = fakeredis.aioredis.FakeRedis()
        config = XRuntimeConfig()
        # 注入 MockModel
        os.environ["XRUNTIME_MODEL_PROVIDER"] = "mock"
        self._app = build_xruntime_app(config=config)
        self._ext = self._app.state.ext  # middleware_state_cache 句柄

    async def send(self, *, tenant_id, role, message) -> tuple[str, list[dict]]:
        """通过 ASGITransport 调 /v1/chat，返回 (reply, events)。"""
        import httpx
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self._app),
            base_url="http://test",
        ) as client:
            resp = await client.post("/v1/chat", json={...})
            return resp.json()["reply"], resp.json()["events"]

    def audit_entries(self, tenant_id: str) -> list:
        """从 MiddlewareStateCache 读取 audit log。"""
        return self._ext["middleware_state_cache"]._audit_logger.entries

    def approval_state(self, session_id: str) -> dict:
        """读取 ApprovalStateCache 快照。"""
        cache = self._ext["middleware_state_cache"]._approval_state_cache
        return cache._approved.get(session_id, set())
```

---

## 五、与 P1-A ApprovalMiddleware 的联动

### 5.1 新增断言方法

为支持 P1-A 验证，`EvalContext` 新增三个审批相关断言：

| 方法 | 断言内容 | 实现方式 |
|------|---------|---------|
| `approval_required_for(tool_name)` | 该工具在当前 session 被 ApprovalMiddleware 拦截过 | 扫 events 中的 `MIDDLEWARE_APPROVAL_REQUEST` |
| `approval_was_cached(tool_name)` | 该工具在 ONCE 策略下被缓存（第二次未触发 approver） | 读 `ApprovalStateCache.is_approved(session, tool)` |
| `approval_denied(tool_name)` | 该工具被拒绝（approver 返回 approved=False） | 扫 events 中的 `MIDDLEWARE_DENY` 且 `middleware="approval"` |

### 5.2 示例 Eval

```python
# tests/evals/approval/test_approval_always_blocks.py
@define_eval(
    "always 策略 + approver 拒绝 → Agent 回复不含工具执行结果",
    domain="approval",
    tags=("offline",),
)
async def test_always_reject_blocks_tool(t) -> None:
    await t.as_tenant("eval-approval").send("删除 /tmp/old 目录")
    t.expect_blocked(by="approval")
    t.audit_logged(tool_name="Bash")
    t.reply_contains("已被拒绝")  # 或类似拒绝提示
```

---

## 六、TDD 实施计划

### 6.1 Red 阶段（单元测试先行）

测试文件：`tests/xruntime/test_eval_framework.py`

覆盖：
1. `EvalSpec` / `EvalResult` / `AssertionResult` 数据类
2. `@define_eval` 装饰器收集行为
3. `EvalCollector` 扫描目录 + tags 过滤
4. `EvalContext` 断言方法（不抛异常，记录结果）
5. `Matcher` 系列（includes / regex / equals / not_contains / has_keys）
6. `EvalRunner._run_one` 状态判定（PASSED / FAILED / ERROR）
7. `ConsoleReporter` / `JUnitReporter` / `JsonReporter` 输出格式
8. `InProcessTarget` 装配（mock build_xruntime_app）

### 6.2 Green 阶段（实现核心模块）

按依赖顺序实现：
1. `_models.py` — 纯数据类
2. `_matchers.py` — 纯函数 matchers
3. `_define.py` — 装饰器 + registry
4. `_context.py` — DSL 入口
5. `_collector.py` — 目录扫描
6. `_reporter.py` — 三种 reporter
7. `_target_inproc.py` — in-process 装配
8. `_runner.py` — 调度器
9. `__main__.py` — CLI

### 6.3 验收标准

- `tests/xruntime/test_eval_framework.py` 全部通过
- `python -m xruntime.eval run --tags offline` 能跑通至少 1 个 smoke eval
- 输出 `eval-results.xml` + `eval-results.json` 格式正确
- 完整测试套件无回归

---

## 七、与现有测试的边界

| 维度 | pytest（`tests/xruntime/`） | Evals（`tests/evals/`） |
|------|----------------------------|-------------------------|
| 对象 | 单元 / 集成（白盒） | Agent 行为（黑盒） |
| 入口 | 直接 import 中间件 / store 类 | 仅通过 `EvalRunner.send` |
| 断言 | `assert x == y`（抛异常） | `t.check(...)`（记录，不抛） |
| 失败粒度 | 单个 test case 失败 | 单 Eval 内多断言全部跑完，聚合 |
| 模型 | `MockModel`（固定响应） | `MockModel`（离线）/ 真实（在线，P2） |

Evals **不替代** pytest，只做 Agent 行为黑盒评测。中间件单元逻辑仍由 pytest 覆盖。

---

## 八、风险与缓解

| 风险 | 缓解 |
|------|------|
| `build_xruntime_app()` 在测试环境启动失败 | `_run_one` 捕获异常标为 `ERROR`，不传染其他 Eval |
| Eval 间状态污染 | 每个 Eval 用独立 `tenant_id` + `session_id`；`InProcessTarget` 每 Eval `flushdb()` |
| MockModel 响应不真实 | Eval description 必须写明 MockModel 假设；nightly online Eval 兜底（P2） |
| Eval 断言脆弱 | `includes` 用关键词而非全等；允许 `xfail` tag |

---

## 附录：与 `Evals-CI-CD-Implementation.md` 的关系

本文档是 P1-B MVP 的**架构设计**，聚焦模块划分与接口契约。
`Evals-CI-CD-Implementation.md` 是**完整实施步骤**，覆盖 CI/CD 集成、GitHub Actions、Braintrust 等 Phase 2-4 内容。
MVP 实现完成后，可按 `Evals-CI-CD-Implementation.md` 的 Phase 2-4 逐步扩展。
