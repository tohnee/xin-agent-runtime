# Evals 集成 CI/CD 实施步骤文档

> 生成日期：2026-07-01
> 适用项目：`xin-agent-runtime`（包名 `agentscope` + 企业扩展 `xruntime`）
> 参考蓝本：Vercel Eve `defineEval` + Braintrust/JUnit reporter
> 关联文档：`docs/竟品分析/vercel Eve Agent Stack.md`、`docs/xruntime/CI-CD-GUIDE.md`

---

## 一、概述

### 1.1 为什么要引入 Evals

当前项目测试体系完全建立在 `pytest` + `MockModel` 之上，覆盖了：

- **单元层**：中间件行为（Audit / Quota / RBAC / SecretRedaction）、租户隔离（`TenantKeyPrefixer` / `TenantContext`）、知识库 ACL（`KnowledgeAclStore`）。
- **集成层**：`tests/xruntime/integration/test_workspace_rbac_integration.py`（31 个端到端安全场景）、`tests/xruntime/integration/test_e2e_full_runtime.py`（7 个中间件链路）。
- **E2E 层**：`tests/xruntime/e2e/test_real_llm_e2e.py`（默认 `--run-e2e` 才跑，依赖 `ARK_API_KEY`）。

这套体系能验证「**代码是否按契约执行**」，但无法回答下面三类问题，而这正是 Agent 走向生产的卡点：

| 痛点 | 现状 | Evals 能补的缺口 |
|------|------|------------------|
| **行为漂移** | MockModel 只断言「调用过某工具」，不能断言「在多轮对话中是否仍然按团队规则回复」 | 用真实 / 近真实模型跑场景，断言回复语义、工具调用顺序、中间件副作用 |
| **回归不可见** | prompt / 工具描述 / 中间件顺序改动后，只有单元测试通过，但 Agent 行为可能已劣化 | 把 Agent 当黑盒，跑场景集，给出 pass/fail + 失败 trace |
| **企业特性未被场景化验证** | RBAC、多租户、知识库 ACL 都是单元断言，没有「以租户 A 身份发问 → 必须命中租户 A 知识库」这种端到端场景 | 用 Eval 描述业务场景，CI 上每 PR 跑一次 |

### 1.2 目标

1. **引入 `define_eval` 风格的 Python DSL**，让 Agent 行为评测可像 Eve 一样声明式编写。
2. **复用现有 MockModel + fakeredis 基建**，CI 上 0 成本跑「离线 Eval 集」。
3. **可选接入真实模型**（`ARK_API_KEY` / `XRUNTIME_MODEL_*`），在 nightly job 跑「在线 Eval 集」。
4. **产出 JUnit XML + JSON**，在 GitHub Actions / PR Check 上可视化，并兼容 Braintrust（可选）。
5. **与 pytest 边界清晰**：Evals 只做「Agent 行为黑盒评测」，不替代单元测试（详见第八章）。

### 1.3 非目标

- 不重写 `agentscope.agent.Agent`。
- 不替换 pytest，Evals 运行器以 pytest plugin / 独立 CLI 双形态存在。
- 不引入 Vercel 全家桶；Reporter 优先 JUnit + JSON，Braintrust 为可选适配器。

---

## 二、设计原则

参考 Eve 的 `defineEval` 但做以下 Python 生态适配：

| Eve 设计 | 本项目适配 | 理由 |
|----------|-----------|------|
| `defineEval({ test(t) {...} })` | `@define_eval` 装饰器 + `EvalContext t` | Python 习惯用装饰器 + 上下文对象，比 JS object literal 更类型友好 |
| `t.send(...)` 发到部署的 app | `t.send(...)` 走 `EvalRunner`，可指向 in-process `build_xruntime_app()` 或远程 `XRUNTIME_EVAL_TARGET` | 本项目已有 `build_xruntime_app()` in-process 组装能力，CI 默认 in-process 跑，零外部依赖 |
| `t.calledTool("run_sql")` | `t.called_tool("Bash")` + `t.tool_input_matches(...)` | snake_case 对齐 PEP 8；工具名沿用 AS `ToolBase` 子类名 |
| `t.check(t.reply, includes(...))` | `t.check(t.reply, includes("..."))` + `t.reply_contains(...)` 快捷方法 | 同上 |
| Braintrust / JUnit reporter | JUnit XML（默认）+ JSON + Braintrust（可选） | CI 必须 JUnit；Braintrust 需额外 secret，列为 Phase 4 |
| `eve eval` CLI | `python -m xruntime.eval run` + `pytest tests/evals` 双入口 | 既要 CI 友好（pytest），也要 REPL 友好（CLI） |
| 无多租户语义 | **新增** `t.as_tenant(tid)` / `t.as_role(role)` / `t.expect_blocked()` | 本项目核心差异化是企业中间件，Eval 必须能断言这些 |
| 文件系统即接口 | `evals/` 目录约定 + `@define_eval` 装饰器 | 不引入 Eve 的「目录即 Agent」哲学，仅借用「目录即 Eval 集」 |

**核心原则**：

1. **离线优先**：默认 Eval 必须能在只有 `MockModel` 的环境跑通，真实模型 Eval 单独打 tag。
2. **黑盒优先**：Eval 只通过 `XRuntimeClient` / 协议适配器入口与 Agent 交互，不直接 import 中间件内部状态（断言副作用时通过 `MiddlewareStateCache` 暴露的只读 API）。
3. **场景即文档**：每个 Eval 的 `description` 即业务规则，PR 审查时能当 spec 读。
4. **失败可复现**：Eval 失败时必须落 trace（事件流 + 中间件决策日志），存为 artifact。
5. **不污染源码**：Eval 代码全部放 `tests/evals/`，不进 `src/xruntime/`，不进 `agentscope` 核心。

---

## 三、架构设计

### 3.1 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Eval 定义层                              │
│  tests/evals/*.py  →  @define_eval 装饰器 + EvalContext DSL    │
│  (多租户隔离 / RBAC / 知识库检索 / 工具调用顺序 / 回复内容)     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 收集
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Eval 运行器                              │
│  EvalRunner                                                     │
│   ├── InProcessTarget  → build_xruntime_app() + fakeredis       │
│   └── RemoteTarget     → XRUNTIME_EVAL_TARGET=http://...        │
│  (注入 MockModel / 真实模型；管理 tenant / session 生命周期)    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 执行 + 收集 AgentEvent 流
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        断言库                                   │
│  AssertionLib                                                   │
│   ├── 回复：includes / regex / equals / not_contains            │
│   ├── 工具：called_tool / tool_input_matches / tool_order       │
│   ├── 中间件：audit_logged / quota_remaining / rbac_denied      │
│   └── 租户：storage_key_prefixed / no_cross_tenant_leak         │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 产出 EvalResult
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Reporter                                 │
│   ├── ConsoleReporter (默认，ANSI 彩色)                         │
│   ├── JUnitReporter   → eval-results.xml                        │
│   ├── JsonReporter    → eval-results.json (含 trace)            │
│   └── BraintrustReporter (可选，Phase 4)                        │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Eval 定义层

- **位置**：`tests/evals/<domain>/test_*.py`，命名遵循 pytest 约定便于 `pytest tests/evals` 直接发现。
- **形态**：每个 Eval 是一个被 `@define_eval` 装饰的 `async def`，接收 `EvalContext`。
- **收集**：`EvalCollector` 扫描 `tests/evals/`，按 `domain` 分组，按 `tags` 过滤（`offline` / `online` / `security` / `kb`）。

### 3.3 Eval 运行器

两种 target，由环境变量 `XRUNTIME_EVAL_TARGET` 切换：

- **`in-process`（默认）**：调用 `build_xruntime_app(config)`，用 `fakeredis` 替换真实 Redis（复用 `tests/xruntime/` 已有 fakeredis fixture 模式），`httpx.AsyncClient` 通过 ASGI transport 直连，不占端口。
- **`remote`**：`XRUNTIME_EVAL_TARGET=http://localhost:8900`，走真实 HTTP，用于 nightly / staging。

运行器负责：
- 每个 Eval 独立 `tenant_id` + `session_id`，避免状态污染。
- 自动注入 `MockModel`（离线）或 `ModelResolver` 解析的真实模型（在线）。
- 收集 `AgentEvent` 流 + `MiddlewareStateCache` 快照作为 trace。

### 3.4 断言库

断言不抛异常，而是记录 `AssertionResult(passed, message, evidence)`，由运行器汇总。这样单个 Eval 内多条断言可全部跑完，失败信息更完整（对齐 Eve 的 `t.check` 语义）。

### 3.5 Reporter

- **JUnit XML**：每个 Eval = 1 个 testcase，domain = testsuite，失败时 `<failure>` 含 trace 摘要 + `<system-out>` 含完整事件流。GitHub Actions `dorny/test-reporter` 直接消费。
- **JSON**：结构化全量结果 + trace，供 Braintrust 适配器或本地 dashboard 消费。
- **Console**：开发时 REPL 友好，类似 pytest 的 `.` / `F` / `E` 进度条。

---

## 四、API 设计

### 4.1 核心数据类

```python
# src/xruntime/_eval/_models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


class EvalStatus(str, Enum):
    """Final status of a single eval run."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"      # 未捕获异常（非断言失败）
    SKIPPED = "skipped"


@dataclass
class AssertionResult:
    """One assertion outcome inside an eval.

    Args:
        name (`str`): Human-readable assertion name.
        passed (`bool`): Whether the assertion held.
        message (`str`): Failure message (empty when passed).
        evidence (`dict`): Captured evidence for debugging.
    """

    name: str
    passed: bool
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Aggregate result of one eval.

    Args:
        eval_id (`str`): Unique id (domain.name).
        description (`str`): Human-readable spec.
        status (`EvalStatus`): Final status.
        assertions (`list`): Per-assertion outcomes.
        trace (`dict`): AgentEvent stream + middleware snapshot.
        duration_ms (`int`): Wall-clock duration.
    """

    eval_id: str
    description: str
    status: EvalStatus
    assertions: list[AssertionResult] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class EvalSpec:
    """Static spec of an eval, collected before running.

    Args:
        eval_id (`str`): ``domain.name`` identifier.
        description (`str`): Spec text shown in reports.
        domain (`str`): Grouping key (e.g. ``security``).
        tags (`list`): ``offline`` / ``online`` / ``security``.
        fn (`Callable`): The decorated async function.
    """

    eval_id: str
    description: str
    domain: str
    tags: list[str]
    fn: Callable[[Any], Awaitable[None]]
```

### 4.2 `define_eval` 装饰器

```python
# src/xruntime/_eval/_define.py
from __future__ import annotations

from typing import Any, Callable, Awaitable

from ._models import EvalSpec


def define_eval(
    description: str,
    *,
    domain: str = "general",
    tags: tuple[str, ...] = ("offline",),
) -> Callable[[Callable[[Any], Awaitable[None]]], EvalSpec]:
    """Declare an agent behavior eval.

    Mirrors Eve's ``defineEval`` but as a Python decorator. The
    decorated function receives an :class:`EvalContext` and may call
    ``t.send`` / ``t.check`` / ``t.called_tool`` / ``t.as_tenant``.

    Args:
        description (`str`): Human-readable spec; shown in reports.
        domain (`str`): Grouping key for the test suite.
        tags (`tuple`): ``offline`` (default) runs in CI without
            API keys; ``online`` requires a real model.

    Returns:
        `EvalSpec`: Registered spec, collected by ``EvalCollector``.
    """

    def _wrap(
        fn: Callable[[Any], Awaitable[None]],
    ) -> EvalSpec:
        return EvalSpec(
            eval_id=f"{domain}.{fn.__name__}",
            description=description,
            domain=domain,
            tags=list(tags),
            fn=fn,
        )

    return _wrap
```

### 4.3 `EvalContext`（断言 DSL 入口）

```python
# src/xruntime/_eval/_context.py
from __future__ import annotations

from typing import Any

from ._models import AssertionResult


class EvalContext:
    """Per-eval DSL handle passed into ``@define_eval`` functions.

    Provides Eve-style ``send`` / ``check`` / ``called_tool`` plus
    XRuntime-specific ``as_tenant`` / ``as_role`` / ``expect_blocked``.
    """

    def __init__(self, runner: Any, eval_id: str) -> None:
        self._runner = runner
        self._eval_id = eval_id
        self._results: list[AssertionResult] = []
        self._tenant_id: str = "eval-default-tenant"
        self._role: str = "viewer"
        self.reply: str = ""
        self.events: list[dict] = []

    # --- identity / scoping -------------------------------------
    def as_tenant(self, tenant_id: str) -> "EvalContext":
        """Act as ``tenant_id`` for subsequent sends."""
        self._tenant_id = tenant_id
        return self

    def as_role(self, role: str) -> "EvalContext":
        """Act as ``role`` (owner/admin/contributor/viewer)."""
        self._role = role
        return self

    # --- interaction --------------------------------------------
    async def send(self, message: str) -> str:
        """Send a user turn; populate ``self.reply`` and ``events``."""
        self.reply, self.events = await self._runner.send(
            tenant_id=self._tenant_id,
            role=self._role,
            message=message,
        )
        return self.reply

    # --- assertions (non-raising) -------------------------------
    def check(
        self,
        value: Any,
        matcher: "Matcher",
        name: str = "",
    ) -> None:
        """Assert ``value`` satisfies ``matcher`` (Eve parity)."""
        ok, msg = matcher.match(value)
        self._results.append(
            AssertionResult(
                name=name or matcher.__class__.__name__,
                passed=ok,
                message=msg,
                evidence={"value": str(value)[:500]},
            ),
        )

    def reply_contains(self, needle: str) -> None:
        """Shortcut: ``check(self.reply, includes(needle))``."""
        self.check(self.reply, includes(needle), name="reply_contains")

    def called_tool(
        self,
        tool_name: str,
        *,
        times: int | None = None,
    ) -> None:
        """Assert a tool was called (optionally exact count)."""
        calls = [
            e for e in self.events
            if e.get("type") == "TOOL_CALL"
            and e.get("tool_name") == tool_name
        ]
        ok = len(calls) > 0 if times is None else len(calls) == times
        self._results.append(
            AssertionResult(
                name=f"called_tool:{tool_name}",
                passed=ok,
                message=(
                    f"expected {times or '>=1'} call(s), "
                    f"got {len(calls)}"
                ),
                evidence={"calls": calls},
            ),
        )

    def tool_input_matches(
        self,
        tool_name: str,
        matcher: "Matcher",
    ) -> None:
        """Assert a tool call's input satisfies ``matcher``."""
        # ... see AssertionLib section
        ...

    def expect_blocked(self, *, by: str = "rbac") -> None:
        """Assert the last send was blocked by a middleware.

        Args:
            by (`str`): ``rbac`` / ``quota`` / ``redaction``.
        """
        blocked = any(
            e.get("type") == "MIDDLEWARE_DENY"
            and e.get("middleware", "").lower() == by
            for e in self.events
        )
        self._results.append(
            AssertionResult(
                name=f"expect_blocked_by:{by}",
                passed=blocked,
                message=f"no {by} deny event observed",
                evidence={"events": self.events},
            ),
        )

    def audit_logged(self, tool_name: str) -> None:
        """Assert ``tool_name`` appears in the audit log."""
        entries = self._runner.audit_entries(self._tenant_id)
        ok = any(e.tool_name == tool_name for e in entries)
        self._results.append(
            AssertionResult(
                name=f"audit_logged:{tool_name}",
                passed=ok,
                message=f"no audit entry for {tool_name}",
                evidence={"entries": [e.to_dict() for e in entries]},
            ),
        )

    def no_cross_tenant_leak(self, other_tenant: str) -> None:
        """Assert no storage key leaks into ``other_tenant``."""
        leaked = self._runner.scan_tenant_keys(other_tenant)
        ok = len(leaked) == 0
        self._results.append(
            AssertionResult(
                name=f"no_leak_to:{other_tenant}",
                passed=ok,
                message=f"leaked keys: {leaked}",
                evidence={"leaked_keys": leaked},
            ),
        )

    # --- internal -----------------------------------------------
    @property
    def results(self) -> list[AssertionResult]:
        return self._results
```

### 4.4 断言 Matcher

```python
# src/xruntime/_eval/_matchers.py
from __future__ import annotations

import re
from typing import Any


class Matcher:
    """Base matcher (Eve parity)."""

    def match(self, value: Any) -> tuple[bool, str]:
        raise NotImplementedError


class includes(Matcher):
    """Substring matcher (Eve parity, lowercase name)."""

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def match(self, value: Any) -> tuple[bool, str]:
        ok = self.needle in str(value)
        return ok, "" if ok else f"missing {self.needle!r}"


class matches_regex(Matcher):
    """Regex matcher."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    def match(self, value: Any) -> tuple[bool, str]:
        ok = re.search(self.pattern, str(value)) is not None
        return ok, "" if ok else f"no match for /{self.pattern}/"


class equals(Matcher):
    """Exact equality matcher."""

    def __init__(self, expected: Any) -> None:
        self.expected = expected

    def match(self, value: Any) -> tuple[bool, str]:
        ok = value == self.expected
        return ok, "" if ok else f"got {value!r}"


class not_contains(Matcher):
    """Negated substring matcher."""

    def __init__(self, needle: str) -> None:
        self.needle = needle

    def match(self, value: Any) -> tuple[bool, str]:
        ok = self.needle not in str(value)
        return ok, "" if ok or f"found forbidden {self.needle!r}"


class has_keys(Matcher):
    """Dict-key presence matcher (for tool_input_matches)."""

    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def match(self, value: Any) -> tuple[bool, str]:
        missing = [k for k in self.keys if k not in (value or {})]
        return (not missing, f"missing keys {missing}")
```

### 4.5 运行器入口

```python
# src/xruntime/_eval/_runner.py
from __future__ import annotations

import asyncio
import os
from typing import Any

from ._collector import EvalCollector
from ._context import EvalContext
from ._models import EvalResult, EvalSpec, EvalStatus
from ._reporter import (
    ConsoleReporter,
    JsonReporter,
    JUnitReporter,
    Reporter,
)


class EvalRunner:
    """Collect and run evals against an in-process or remote target.

    Args:
        target (`str`): ``in-process`` (default) or a URL.
        tags (`list`): Only run evals whose tags intersect this list.
        reporters (`list`): Output reporters.
    """

    def __init__(
        self,
        target: str | None = None,
        *,
        tags: list[str] | None = None,
        reporters: list[Reporter] | None = None,
    ) -> None:
        self.target = target or os.environ.get(
            "XRUNTIME_EVAL_TARGET",
            "in-process",
        )
        self.tags = tags or ["offline"]
        self.reporters = reporters or [
            ConsoleReporter(),
            JUnitReporter(path="eval-results.xml"),
            JsonReporter(path="eval-results.json"),
        ]

    async def run(self, evals_dir: str = "tests/evals") -> int:
        """Run collected evals; return exit code (0 = pass)."""
        specs = EvalCollector(evals_dir).collect(tags=self.tags)
        results: list[EvalResult] = []
        for spec in specs:
            results.append(await self._run_one(spec))
        for r in self.reporters:
            r.report(results)
        return (
            0
            if all(r.status == EvalStatus.PASSED for r in results)
            else 1
        )

    async def _run_one(self, spec: EvalSpec) -> EvalResult:
        ctx = EvalContext(self, spec.eval_id)
        status = EvalStatus.PASSED
        trace: dict[str, Any] = {}
        try:
            await self._setup_target()
            await spec.fn(ctx)
            if any(not a.passed for a in ctx.results):
                status = EvalStatus.FAILED
            trace = self._capture_trace()
        except Exception as exc:  # noqa: BLE001
            status = EvalStatus.ERROR
            trace["exception"] = repr(exc)
        return EvalResult(
            eval_id=spec.eval_id,
            description=spec.description,
            status=status,
            assertions=ctx.results,
            trace=trace,
        )

    # --- target lifecycle ---------------------------------------
    async def _setup_target(self) -> None:
        if self.target == "in-process":
            self._app = self._build_in_process_app()
        else:
            self._base_url = self.target

    def _build_in_process_app(self) -> Any:
        """Assemble build_xruntime_app() with fakeredis + MockModel."""
        # Lazy import to keep ``import xruntime.eval`` lightweight.
        import fakeredis.aioredis  # type: ignore
        from xruntime._config import XRuntimeConfig
        from xruntime._server import build_xruntime_app

        config = XRuntimeConfig()
        # Inject MockModel via env so ModelResolver picks it up.
        os.environ["XRUNTIME_MODEL_PROVIDER"] = "mock"
        return build_xruntime_app(config=config)

    # --- transport (called by EvalContext.send) -----------------
    async def send(
        self,
        *,
        tenant_id: str,
        role: str,
        message: str,
    ) -> tuple[str, list[dict]]:
        """Send one turn; return (reply_text, event_list)."""
        # In-process: httpx.ASGITransport against self._app.
        # Remote: httpx.AsyncClient against self._base_url.
        ...

    def audit_entries(self, tenant_id: str) -> list[Any]:
        """Read audit log for ``tenant_id`` from MiddlewareStateCache."""
        ...

    def scan_tenant_keys(self, tenant_id: str) -> list[str]:
        """Scan fakeredis for keys leaking into ``tenant_id``."""
        ...

    def _capture_trace(self) -> dict[str, Any]:
        """Snapshot AgentEvent stream + middleware decisions."""
        ...
```

### 4.6 CLI 入口

```python
# src/xruntime/_eval/__main__.py
import asyncio
import sys

from ._runner import EvalRunner


def main() -> int:
    """``python -m xruntime.eval run`` entrypoint."""
    return asyncio.run(EvalRunner().run())


if __name__ == "__main__":
    sys.exit(main())
```

---

## 五、实施步骤（分 Phase）

### Phase 1：基础框架（eval 定义 + 运行器 + Mock 模型支持）

**目标**：能在本地 `python -m xruntime.eval run` 跑通 1 个最小 Eval，使用 `MockModel` + `fakeredis`。

**任务清单**：

1. 新建 `src/xruntime/_eval/` 包，文件结构：
   ```
   src/xruntime/_eval/
   ├── __init__.py        # 导出 define_eval / EvalRunner
   ├── __main__.py        # CLI 入口
   ├── _models.py         # EvalSpec / EvalResult / AssertionResult
   ├── _define.py         # @define_eval
   ├── _context.py        # EvalContext DSL
   ├── _matchers.py       # includes / matches_regex / equals ...
   ├── _collector.py      # 扫描 tests/evals/ 收集 EvalSpec
   ├── _runner.py         # InProcessTarget / RemoteTarget
   ├── _target_inproc.py  # build_xruntime_app + fakeredis 装配
   ├── _target_remote.py  # httpx 远程 target
   └── _reporter.py       # Console / JUnit / Json Reporter
   ```
2. 在 `src/xruntime/__init__.py` 暴露 `define_eval`（仅符号，不强制 import）。
3. 在 `pyproject.toml` 的 `[project.optional-dependencies] xruntime-dev` 中新增 `httpx>=0.27`（若未存在）、`fakeredis>=2.20`、`junit-xml>=1.9`。
4. 在 `tests/evals/` 下新建第一个 smoke Eval：
   ```python
   # tests/evals/smoke/test_smoke.py
   from xruntime.eval import define_eval, includes


   @define_eval(
       "Agent echoes a greeting (MockModel sanity).",
       domain="smoke",
       tags=("offline",),
   )
   async def test_agent_greets(t) -> None:
       await t.send("Say hello.")
       t.reply_contains("hello")
   ```
5. 让 `MockModel` 能被 `ModelResolver` 识别：在 `src/xruntime/_runtime/_model_resolver.py` 注册一个 `mock` provider，返回 `tests/utils.py` 的 `MockModel` + `MockCredential`（通过 lazy import + 配置 `mock_chat_responses` 注入预期回复）。
6. 跑通：`python -m xruntime.eval run`，产出 `eval-results.xml` + `eval-results.json`。

**验收**：本地命令 exit code 0，JUnit XML 含 1 个 passed testcase。

### Phase 2：CI 集成（GitHub Actions workflow）

**目标**：每个 PR / push 到 main 自动跑离线 Eval 集，失败阻断合并。

**任务清单**：

1. 新建 `.github/workflows/evals.yml`（完整 YAML 见第六章）。
2. 在 CI 上缓存 `fakeredis` 与 `MockModel`，确保 0 外部依赖。
3. 上传 artifact：`eval-results.xml` / `eval-results.json` / `eval-traces/`（失败 Eval 的完整 trace）。
4. 接入 `dorny/test-reporter@v1` 把 JUnit 渲染到 PR Check。
5. 在 PR 模板 `.github/PULL_REQUEST_TEMPLATE.md` 加一行：「如果改动影响 Agent 行为，请在 `tests/evals/` 补充 Eval」。

**验收**：开一个只改 prompt 的 PR，CI 上 Eval job 能跑且失败时阻断合并。

### Phase 3：高级断言（中间件行为、多租户隔离验证）

**目标**：Eval 能断言企业中间件的副作用，覆盖 RBAC / Quota / Audit / 多租户隔离。

**任务清单**：

1. 扩展 `EvalContext`：
   - `expect_blocked(by=...)`：扫 `AgentEvent` 中的 `MIDDLEWARE_DENY`。
   - `audit_logged(tool_name)`：从 `MiddlewareStateCache.audit_logger` 读 per-tenant entries。
   - `quota_remaining(expected_range)`：读 `QuotaTracker` 状态。
   - `no_cross_tenant_leak(other_tenant)`：扫 fakeredis keys。
2. 在 `EvalRunner` 暴露 `audit_entries(tenant_id)` / `scan_tenant_keys(tenant_id)` 只读 API，供 `EvalContext` 调用；这些 API 走 `ext["middleware_state_cache"]` 句柄。
3. 新建 `tests/evals/security/` 与 `tests/evals/tenant/` 目录，补 3+ 场景 Eval（见第七章示例）。
4. 在 `XRuntimeConfig` 加 `eval` 段（`max_iters`、`session_ttl_secs`、`trace_dir`），让 Eval 行为可配置。

**验收**：`tests/evals/security/test_rbac_viewer_cannot_ingest.py` 能断言 viewer 角色被 RBAC 拒绝且 audit log 留痕。

### Phase 4：Reporter + 仪表盘

**目标**：Eval 结果可视化，可选接入 Braintrust 做长期趋势追踪。

**任务清单**：

1. `JsonReporter` 输出 schema 固定，含 `eval_id / status / assertions / trace / git_sha / branch / run_url`。
2. 新增 `BraintrustReporter`（可选，需 `BRAINTRUST_API_KEY`）：把每次 Eval run 上传为 experiment，支持跨 commit 对比。
3. 在 `docs/xruntime/` 加 `EVALS-DASHBOARD.md`，说明如何用 GitHub Pages + 静态 JSON 渲染趋势图（轻量方案，不引入外部服务）。
4. nightly job（`schedule: cron: "0 2 * * *"`）跑 `online` tag Eval，上传到 Braintrust。

**验收**：nightly run 后能在 Braintrust 看到 commit 维度的 pass rate 曲线。

---

## 六、GitHub Actions 集成

### 6.1 完整 workflow：`.github/workflows/evals.yml`

```yaml
name: Agent Evals

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  # Phase 4: nightly online evals against staging
  schedule:
    - cron: "0 2 * * *"   # 02:00 UTC daily

jobs:
  offline-evals:
    name: Offline Evals (MockModel)
    runs-on: ubuntu-latest
    # Skip nightly trigger for offline job (it runs on every PR already)
    if: github.event_name != 'schedule'
    timeout-minutes: 10
    env:
      GRPC_VERBOSITY: ERROR
      XRUNTIME_EVAL_TARGET: in-process
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          pip install -e ".[xruntime-dev]"

      - name: Run offline evals
        run: |
          python -m xruntime.eval run \
            --tags offline \
            --junit eval-results.xml \
            --json eval-results.json \
            --trace-dir eval-traces

      - name: Upload eval artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: eval-results-offline
          path: |
            eval-results.xml
            eval-results.json
            eval-traces/
          retention-days: 14

      - name: Publish JUnit report to PR
        if: always()
        uses: dorny/test-reporter@v1
        with:
          name: Agent Evals Report
          path: eval-results.xml
          reporter: java-junit
          fail-on-error: "true"

  online-evals:
    name: Online Evals (real model)
    # Only on nightly or manual dispatch; requires real API key
    if: github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    timeout-minutes: 20
    env:
      GRPC_VERBOSITY: ERROR
      XRUNTIME_EVAL_TARGET: ${{ secrets.XRUNTIME_STAGING_URL }}
      XRUNTIME_MODEL_PROVIDER: ${{ secrets.XRUNTIME_MODEL_PROVIDER }}
      XRUNTIME_MODEL_API_KEY: ${{ secrets.XRUNTIME_MODEL_API_KEY }}
      XRUNTIME_MODEL_NAME: ${{ secrets.XRUNTIME_MODEL_NAME }}
      BRAINTRUST_API_KEY: ${{ secrets.BRAINTRUST_API_KEY }}
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          pip install -e ".[xruntime-dev]"

      - name: Run online evals
        run: |
          python -m xruntime.eval run \
            --tags online \
            --junit eval-results-online.xml \
            --json eval-results-online.json \
            --trace-dir eval-traces-online \
            --reporter braintrust

      - name: Upload online eval artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: eval-results-online
          path: |
            eval-results-online.xml
            eval-results-online.json
            eval-traces-online/
          retention-days: 30
```

### 6.2 与现有 workflow 的关系

| 现有 workflow | 职责 | 与 Evals 的边界 |
|---------------|------|-----------------|
| `xruntime-ci.yml` | Lint + 单元/集成测试（148 企业测试） | 不动；Evals 是新增的并行 job |
| `unittest.yml` | 同上（另一份矩阵） | 不动 |
| `pre-commit.yml` | black / flake8 / mypy | 不动 |
| **`evals.yml`（新）** | Agent 行为黑盒评测 | 只跑 `tests/evals/`，不跑 `tests/xruntime/` |

**依赖顺序**：`lint` → `test` + `offline-evals` 并行 → 都过才允许 merge（用 branch protection rule 把 `offline-evals` 列为 required check）。

### 6.3 Branch Protection 配置

在 GitHub Settings → Branches → `main` protection rule 中：

- Required checks：`Lint (flake8 + black)`、`Enterprise Tests (ubuntu-latest)`、`Offline Evals (MockModel)`
- 不要求 `Online Evals`（nightly only）
- `Require status checks to pass before merging`：✅
- `Require branches to be up to date before merging`：✅

---

## 七、示例 Eval

### 7.1 多租户隔离 Eval

```python
# tests/evals/tenant/test_tenant_isolation.py
"""Eval: Tenant A's data must never surface when asking as Tenant B."""
from __future__ import annotations

from xruntime.eval import define_eval, includes, not_contains


@define_eval(
    "Tenant A 文档不会泄露给 Tenant B 的 Agent 回复。",
    domain="tenant",
    tags=("offline", "security"),
)
async def test_no_cross_tenant_data_leak(t) -> None:
    # 1. 作为 tenant-acme 注入一份机密文档到知识库
    await t.as_tenant("tenant-acme").send(
        "请把以下内容存入知识库："
        "ACME 财报净利 1.2 亿（机密，仅限 acme 内部）。"
    )

    # 2. 切换为 tenant-acme 验证能查到
    await t.as_tenant("tenant-acme").send("ACME 净利是多少？")
    t.reply_contains("1.2 亿")

    # 3. 切换为 tenant-globex，同样问题不应泄露
    await t.as_tenant("tenant-globex").send("ACME 净利是多少？")
    t.check(
        t.reply,
        not_contains("1.2 亿"),
        name="globex_reply_must_not_leak",
    )

    # 4. 断言存储层无跨租户 key 泄漏
    t.no_cross_tenant_leak("tenant-globex")
```

### 7.2 RBAC 权限 Eval

```python
# tests/evals/security/test_rbac_viewer_cannot_ingest.py
"""Eval: Viewer 角色不能写入知识库，且拒绝会被 audit。"""
from __future__ import annotations

from xruntime.eval import define_eval


@define_eval(
    "Viewer 角色尝试 ingest 文档时被 RBAC 拒绝且留 audit 痕迹。",
    domain="security",
    tags=("offline", "security", "rbac"),
)
async def test_viewer_cannot_ingest(t) -> None:
    await t.as_tenant("tenant-acme").as_role("viewer").send(
        "请把『产品路线图』存入知识库 finance。"
    )

    # 断言 1：被 RBAC 中间件拒绝
    t.expect_blocked(by="rbac")

    # 断言 2：拒绝事件出现在 audit log
    t.audit_logged(tool_name="ingest_document")

    # 断言 3：回复里不包含成功字样
    from xruntime.eval import not_contains
    t.check(t.reply, not_contains("已存入"), name="no_success_phrase")
```

### 7.3 知识库检索 Eval

```python
# tests/evals/kb/test_kb_retrieval_accuracy.py
"""Eval: Agent 必须先调用检索工具再回答 KB 问题。"""
from __future__ import annotations

from xruntime.eval import (
    define_eval,
    includes,
)


@define_eval(
    "Agent 回答 KB 问题时必须先调用 retrieve 工具，且回复包含来源。",
    domain="kb",
    tags=("offline", "kb"),
)
async def test_kb_query_calls_retrieve_first(t) -> None:
    # 前置：tenant-acme 的 finance KB 已被 fixture 灌入财报文档
    await t.as_tenant("tenant-acme").send(
        "根据 finance 知识库，Q3 营收是多少？"
    )

    # 断言 1：调用了 retrieve 工具
    t.called_tool("retrieve")

    # 断言 2：retrieve 是第一个被调用的工具
    tool_order = [
        e["tool_name"]
        for e in t.events
        if e.get("type") == "TOOL_CALL"
    ]
    from xruntime.eval import equals
    t.check(
        tool_order[0] if tool_order else "",
        equals("retrieve"),
        name="retrieve_must_be_first_tool",
    )

    # 断言 3：回复包含「净额」字样（团队术语规则）
    t.reply_contains("净额")

    # 断言 4：回复引用了来源
    t.check(
        t.reply,
        includes("finance"),
        name="reply_cites_source_kb",
    )
```

### 7.4 Quota 限流 Eval（补充示例）

```python
# tests/evals/security/test_quota_enforcement.py
"""Eval: 超过 session 配额后请求被 QuotaMiddleware 拒绝。"""
from __future__ import annotations

from xruntime.eval import define_eval


@define_eval(
    "Session 超过 max_turns 后被 QuotaMiddleware 阻断。",
    domain="security",
    tags=("offline", "quota"),
)
async def test_quota_blocks_after_max_turns(t) -> None:
    # 配置 max_turns=3（通过 XRuntimeConfig.eval 注入）
    await t.as_tenant("tenant-acme").send("第 1 轮")
    await t.as_tenant("tenant-acme").send("第 2 轮")
    await t.as_tenant("tenant-acme").send("第 3 轮")
    await t.as_tenant("tenant-acme").send("第 4 轮（应被拒）")

    t.expect_blocked(by="quota")
```

---

## 八、与现有测试的关系

### 8.1 边界划分

| 维度 | pytest（`tests/xruntime/`） | Evals（`tests/evals/`） |
|------|----------------------------|-------------------------|
| **对象** | 单元 / 集成（白盒） | Agent 行为（黑盒） |
| **入口** | 直接 import 中间件 / store 类 | 仅通过 `EvalRunner.send` → 协议适配器 / ASGI |
| **模型** | `MockModel`（固定响应） | `MockModel`（离线）/ 真实模型（在线） |
| **断言** | `assert x == y`（抛异常） | `t.check(...)`（记录，不抛） |
| **失败粒度** | 单个 test case 失败 | 单个 Eval 内多条断言全部跑完，聚合失败 |
| **场景** | 「`RbacMiddleware` 拒绝 viewer 调用 ingest」 | 「以 viewer 身份发 ingest 请求 → 被 RBAC 拒 + 留 audit + 回复无成功字样」 |
| **运行频率** | 每 PR | 每 PR（离线）+ nightly（在线） |
| **Reporter** | pytest 默认 | JUnit + JSON +（可选）Braintrust |

### 8.2 互不替代

- **pytest 不可被 Evals 替代**：中间件单元逻辑、数据类序列化、`TenantKeyPrefixer` 边界条件等仍需白盒断言。
- **Evals 不可被 pytest 替代**：pytest 难以表达「多轮对话 + 中间件副作用 + 回复语义」的端到端场景，且 pytest 断言一抛即停，无法聚合失败信息。

### 8.3 共享基建

| 基建 | 共享方式 |
|------|----------|
| `MockModel` / `MockCredential`（`tests/utils.py`） | `EvalRunner` 通过 `ModelResolver` 的 `mock` provider 间接复用 |
| `fakeredis` | `InProcessTarget` 用 `fakeredis.aioredis.FakeRedis` 替换 `RedisStorage` / `RedisMessageBus` |
| `XRuntimeConfig` | Eval 直接读 `config.eval` 段；fixture 可覆盖 |
| `build_xruntime_app()` | `InProcessTarget` 直接调用，不重新组装 |
| `--run-e2e` 约定 | 离线 Eval 永不要求 `--run-e2e`；在线 Eval 由 `--tags online` 触发，等价语义 |

### 8.4 渐进迁移建议

1. 新增场景一律写 Eval（除非是纯函数单测）。
2. 现有 `tests/xruntime/integration/test_workspace_rbac_integration.py`（31 场景）可逐步抽取业务语义重的部分改写为 Eval，原 pytest 保留作为「契约层」。
3. 不强行迁移单元测试。

---

## 九、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **MockModel 离线 Eval 与真实模型行为偏差大** | 离线全绿但线上 Agent 已坏 | (1) nightly `online` tag 跑真实模型；(2) Eval description 必须写明「MockModel 假设：回复包含 X」便于审查假设是否还成立 |
| **Eval 间状态污染** | A Eval 的 session 数据泄漏到 B Eval | `EvalRunner` 每个 Eval 用独立 `tenant_id` + `session_id`，且 `InProcessTarget` 每 Eval 重建 fakeredis 实例 |
| **Eval 跑得慢** | CI 时长膨胀阻断 PR | (1) 离线 Eval 单条 < 2s，总时长 < 60s；(2) `--tags` 分组，PR 只跑 `offline`；(3) 用 `pytest-xdist` 风格的并发（Phase 4） |
| **fakeredis 与真实 Redis 行为差异** | 离线过、线上挂 | (1) fakeredis 已被现有 148 测试验证；(2) nightly online Eval 兜底；(3) 关键 Eval 同时打 `offline` + `online` tag，线上验证 |
| **Eval 断言脆弱（prompt 微调即挂）** | 团队不敢改 prompt | (1) `includes` 用关键词而非全等；(2) 失败时落 trace 便于人审；(3) 允许 `xfail` tag 标记已知 flaky |
| **Braintrust 引入外部依赖** | 供应链 / 成本 / 隐私 | (1) Braintrust 为 Phase 4 可选项，需 `BRAINTRUST_API_KEY` 才启用；(2) 默认只发 `eval_id / status / git_sha`，不发用户数据；(3) 离线 Eval 永远不外发 |
| **`build_xruntime_app()` 在 CI 启动失败** | 整个 Eval job 挂 | (1) `_setup_target` 失败转成 `EvalStatus.ERROR` 而非抛；(2) smoke Eval 作为 canary，第一个跑，挂了快速失败 |
| **Eval 代码本身有 bug** | 假阴性/假阳性 | (1) smoke Eval（7.1 前置）验证 DSL 可用；(2) 每个 Eval 必须在 PR 评审时人审；(3) `EvalStatus.ERROR` 与 `FAILED` 分开统计 |
| **协议适配器行为变更未同步 Eval** | Eval 假设过期 | (1) Eval 优先走 `XRuntimeClient`（SDK）而非裸协议；(2) 协议适配器单测变更时在 PR 模板提醒检查 Eval |
| **多租户 Eval 误用同一 fakeredis** | 假阴性（隔离假成功） | `InProcessTarget` 强制每 Eval `flushdb()`，且 `no_cross_tenant_leak` 扫描全 key 空间 |

---

## 十、落地清单（可直接执行）

```bash
# 1. 创建包骨架
mkdir -p src/xruntime/_eval tests/evals/{smoke,security,tenant,kb}

# 2. 安装 dev 依赖（已在 pyproject.toml 加好）
pip install -e ".[xruntime-dev]"

# 3. 写最小 smoke eval
#    tests/evals/smoke/test_smoke.py（见 Phase 1）

# 4. 本地跑通
python -m xruntime.eval run --tags offline

# 5. 加 CI workflow
#    .github/workflows/evals.yml（见第六章）

# 6. 配 branch protection（GitHub UI）
#    Required check: "Offline Evals (MockModel)"

# 7. 补 Phase 3 高级断言 + 3 个示例 Eval（见第七章）

# 8. （可选）Phase 4 接 Braintrust
```

---

## 附录 A：文件落点速查

| 路径 | 角色 |
|------|------|
| `src/xruntime/_eval/` | Eval 框架实现（包内 `_` 前缀，遵循 XRuntime 约定） |
| `src/xruntime/__init__.py` | 暴露 `define_eval` 符号（lazy） |
| `tests/evals/<domain>/test_*.py` | Eval 定义（pytest 可发现） |
| `.github/workflows/evals.yml` | CI 集成 |
| `eval-results.xml` / `eval-results.json` | CI artifact |
| `docs/xruntime/EVALS-DASHBOARD.md` | Phase 4 仪表盘说明 |
| `pyproject.toml` | `[xruntime-dev]` 加 `fakeredis` / `junit-xml` / `httpx` |

## 附录 B：环境变量速查

| 变量 | 默认 | 作用 |
|------|------|------|
| `XRUNTIME_EVAL_TARGET` | `in-process` | Eval 运行 target，可设为 URL 走远程 |
| `XRUNTIME_MODEL_PROVIDER` | `mock`（Eval 离线时） | `ModelResolver` 解析模型来源 |
| `XRUNTIME_MODEL_API_KEY` | — | 在线 Eval 必填 |
| `XRUNTIME_MODEL_NAME` | — | 在线 Eval 必填 |
| `BRAINTRUST_API_KEY` | — | Phase 4 可选，启用 Braintrust 上报 |
| `GRPC_VERBOSITY` | `ERROR` | CI 复用现有约定 |
