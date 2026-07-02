# -*- coding: utf-8 -*-
"""TDD unit tests for the XRuntime Evals framework (P1-B MVP).

Covers the eight core modules defined in
``docs/竟品分析/EVALS-FRAMEWORK-MVP-DESIGN.md``:

1. ``_models.py``    — EvalSpec / EvalResult / AssertionResult / EvalStatus
2. ``_matchers.py``   — includes / matches_regex / equals / not_contains / has_keys
3. ``_define.py``     — @define_eval decorator + registry
4. ``_context.py``    — EvalContext DSL (assertions do not raise)
5. ``_collector.py``  — directory scan + tags filter
6. ``_runner.py``     — _run_one status decision (PASSED / FAILED / ERROR)
7. ``_reporter.py``   — Console / JUnit / Json output
8. ``_target_inproc.py`` — InProcessTarget assembly (mocked)

The tests intentionally avoid a real ``build_xruntime_app`` — they
use ``unittest.mock`` to fabricate the app / target so the framework
logic can be exercised in isolation.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These imports will fail until the eval framework modules exist
# (Red phase of TDD).
from xruntime._eval._models import (  # noqa: E402
    AssertionResult,
    EvalResult,
    EvalSpec,
    EvalStatus,
)
from xruntime._eval._matchers import (  # noqa: E402
    Matcher,
    equals,
    has_keys,
    includes,
    matches_regex,
    not_contains,
)
from xruntime._eval._define import define_eval  # noqa: E402
from xruntime._eval._context import EvalContext  # noqa: E402
from xruntime._eval._collector import EvalCollector  # noqa: E402
from xruntime._eval._runner import EvalRunner  # noqa: E402
from xruntime._eval._reporter import (  # noqa: E402
    ConsoleReporter,
    JsonReporter,
    JUnitReporter,
    Reporter,
)


# ── 1. Data models ───────────────────────────────────────────────────


class TestEvalModels:
    """EvalSpec / EvalResult / AssertionResult / EvalStatus."""

    def test_eval_status_values(self) -> None:
        assert EvalStatus.PASSED.value == "passed"
        assert EvalStatus.FAILED.value == "failed"
        assert EvalStatus.ERROR.value == "error"
        assert EvalStatus.SKIPPED.value == "skipped"

    def test_assertion_result_defaults(self) -> None:
        a = AssertionResult(name="foo", passed=True)
        assert a.name == "foo"
        assert a.passed is True
        assert a.message == ""
        assert a.evidence == {}

    def test_assertion_result_failure(self) -> None:
        a = AssertionResult(
            name="bar",
            passed=False,
            message="expected X got Y",
            evidence={"actual": "Y"},
        )
        assert a.passed is False
        assert "X" in a.message
        assert a.evidence["actual"] == "Y"

    def test_eval_result_defaults(self) -> None:
        r = EvalResult(
            eval_id="domain.test_x",
            description="spec",
            status=EvalStatus.PASSED,
        )
        assert r.eval_id == "domain.test_x"
        assert r.assertions == []
        assert r.trace == {}
        assert r.duration_ms == 0

    def test_eval_spec_defaults(self) -> None:
        async def _fn(_t: Any) -> None:
            pass

        spec = EvalSpec(
            eval_id="domain.fn",
            description="d",
            domain="domain",
            tags=["offline"],
            fn=_fn,
        )
        assert spec.eval_id == "domain.fn"
        assert spec.tags == ["offline"]
        assert callable(spec.fn)


# ── 2. Matchers ──────────────────────────────────────────────────────


class TestMatchers:
    """Five matchers: includes / matches_regex / equals / not_contains / has_keys."""

    def test_includes_pass(self) -> None:
        ok, msg = includes("hello").match("oh hello world")
        assert ok is True
        assert msg == ""

    def test_includes_fail(self) -> None:
        ok, msg = includes("xyz").match("hello world")
        assert ok is False
        assert "xyz" in msg

    def test_matches_regex_pass(self) -> None:
        ok, _ = matches_regex(r"\d{3}-\d{4}").match("123-4567")
        assert ok is True

    def test_matches_regex_fail(self) -> None:
        ok, msg = matches_regex(r"\d{3}").match("abc")
        assert ok is False
        assert "no match" in msg

    def test_equals_pass(self) -> None:
        ok, _ = equals(42).match(42)
        assert ok is True

    def test_equals_fail(self) -> None:
        ok, msg = equals(42).match(43)
        assert ok is False
        assert "43" in msg

    def test_not_contains_pass(self) -> None:
        ok, _ = not_contains("secret").match("public data")
        assert ok is True

    def test_not_contains_fail(self) -> None:
        ok, msg = not_contains("secret").match("this is secret")
        assert ok is False
        assert "secret" in msg

    def test_has_keys_pass(self) -> None:
        ok, _ = has_keys(["a", "b"]).match({"a": 1, "b": 2, "c": 3})
        assert ok is True

    def test_has_keys_fail(self) -> None:
        ok, msg = has_keys(["a", "x"]).match({"a": 1})
        assert ok is False
        assert "x" in msg

    def test_matcher_base_is_abstract(self) -> None:
        m = Matcher()
        with pytest.raises(NotImplementedError):
            m.match("x")


# ── 3. @define_eval decorator ────────────────────────────────────────


class TestDefineEval:
    """@define_eval decorator + module-level registry."""

    def test_define_eval_returns_spec(self) -> None:
        @define_eval("test spec", domain="t", tags=("offline",))
        async def my_eval(_t: Any) -> None:
            pass

        assert isinstance(my_eval, EvalSpec)
        assert my_eval.eval_id == "t.my_eval"
        assert my_eval.description == "test spec"
        assert my_eval.domain == "t"
        assert "offline" in my_eval.tags

    def test_define_eval_default_domain(self) -> None:
        @define_eval("d")
        async def another(_t: Any) -> None:
            pass

        assert another.domain == "general"
        assert another.eval_id == "general.another"

    def test_define_eval_default_tags(self) -> None:
        @define_eval("d")
        async def tagged(_t: Any) -> None:
            pass

        assert "offline" in tagged.tags


# ── 4. EvalContext DSL ───────────────────────────────────────────────


class _StubRunner:
    """Minimal EvalRunner stub for testing EvalContext in isolation."""

    def __init__(
        self,
        reply: str = "ok",
        events: list[dict] | None = None,
        audit: list[Any] | None = None,
    ) -> None:
        self._reply = reply
        self._events = events or []
        self._audit = audit or []
        self.send = AsyncMock(return_value=(self._reply, self._events))

    def audit_entries(self, _tenant: str) -> list[Any]:
        return self._audit

    def scan_tenant_keys(self, _tenant: str) -> list[str]:
        return []

    def approval_state_snapshot(self, _session: str) -> set[str]:
        return set()


class TestEvalContext:
    """EvalContext assertion methods (must not raise on failure)."""

    @pytest.mark.asyncio
    async def test_send_populates_reply_and_events(self) -> None:
        runner = _StubRunner(
            reply="hello world",
            events=[{"type": "TOOL_CALL", "tool_name": "Bash"}],
        )
        ctx = EvalContext(runner, "test.1")
        await ctx.send("hi")
        assert ctx.reply == "hello world"
        assert len(ctx.events) == 1

    @pytest.mark.asyncio
    async def test_reply_contains_pass(self) -> None:
        runner = _StubRunner(reply="hello world")
        ctx = EvalContext(runner, "test.1")
        await ctx.send("hi")
        ctx.reply_contains("hello")
        assert len(ctx.results) == 1
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_reply_contains_fail_does_not_raise(self) -> None:
        runner = _StubRunner(reply="hello world")
        ctx = EvalContext(runner, "test.1")
        await ctx.send("hi")
        ctx.reply_contains("xyz")
        assert len(ctx.results) == 1
        assert ctx.results[0].passed is False
        assert "xyz" in ctx.results[0].message

    @pytest.mark.asyncio
    async def test_called_tool_pass(self) -> None:
        runner = _StubRunner(
            events=[{"type": "TOOL_CALL", "tool_name": "Bash"}],
        )
        ctx = EvalContext(runner, "test.1")
        await ctx.send("run")
        ctx.called_tool("Bash")
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_called_tool_fail(self) -> None:
        runner = _StubRunner(events=[])
        ctx = EvalContext(runner, "test.1")
        await ctx.send("run")
        ctx.called_tool("Bash")
        assert ctx.results[0].passed is False

    @pytest.mark.asyncio
    async def test_called_tool_exact_count(self) -> None:
        runner = _StubRunner(
            events=[
                {"type": "TOOL_CALL", "tool_name": "Bash"},
                {"type": "TOOL_CALL", "tool_name": "Bash"},
            ],
        )
        ctx = EvalContext(runner, "test.1")
        await ctx.send("run")
        ctx.called_tool("Bash", times=2)
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_expect_blocked_by_rbac(self) -> None:
        runner = _StubRunner(
            events=[
                {"type": "MIDDLEWARE_DENY", "middleware": "rbac"},
            ],
        )
        ctx = EvalContext(runner, "test.1")
        await ctx.send("ingest")
        ctx.expect_blocked(by="rbac")
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_expect_blocked_by_approval(self) -> None:
        runner = _StubRunner(
            events=[
                {"type": "MIDDLEWARE_DENY", "middleware": "approval"},
            ],
        )
        ctx = EvalContext(runner, "test.1")
        await ctx.send("delete")
        ctx.expect_blocked(by="approval")
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_expect_blocked_fail(self) -> None:
        runner = _StubRunner(events=[])
        ctx = EvalContext(runner, "test.1")
        await ctx.send("safe")
        ctx.expect_blocked(by="rbac")
        assert ctx.results[0].passed is False

    @pytest.mark.asyncio
    async def test_audit_logged_pass(self) -> None:
        @dataclass
        class _Entry:
            tool_name: str

        runner = _StubRunner(audit=[_Entry(tool_name="Bash")])
        ctx = EvalContext(runner, "test.1")
        await ctx.send("run")
        ctx.audit_logged("Bash")
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_audit_logged_fail(self) -> None:
        runner = _StubRunner(audit=[])
        ctx = EvalContext(runner, "test.1")
        await ctx.send("run")
        ctx.audit_logged("Bash")
        assert ctx.results[0].passed is False

    @pytest.mark.asyncio
    async def test_as_tenant_chains(self) -> None:
        runner = _StubRunner()
        ctx = EvalContext(runner, "test.1")
        ret = ctx.as_tenant("acme")
        assert ret is ctx
        assert ctx._tenant_id == "acme"

    @pytest.mark.asyncio
    async def test_as_role_chains(self) -> None:
        runner = _StubRunner()
        ctx = EvalContext(runner, "test.1")
        ret = ctx.as_role("admin")
        assert ret is ctx
        assert ctx._role == "admin"

    @pytest.mark.asyncio
    async def test_check_with_matcher(self) -> None:
        runner = _StubRunner(reply="hello")
        ctx = EvalContext(runner, "test.1")
        await ctx.send("hi")
        ctx.check(ctx.reply, includes("hello"), name="custom")
        assert ctx.results[0].name == "custom"
        assert ctx.results[0].passed is True

    @pytest.mark.asyncio
    async def test_multiple_assertions_all_recorded(self) -> None:
        """Multiple assertions must all run, even if earlier ones fail."""
        runner = _StubRunner(
            reply="hello",
            events=[{"type": "TOOL_CALL", "tool_name": "Bash"}],
        )
        ctx = EvalContext(runner, "test.1")
        await ctx.send("hi")
        ctx.reply_contains("xyz")  # fail
        ctx.reply_contains("hello")  # pass
        ctx.called_tool("Bash")  # pass
        ctx.called_tool("Write")  # fail
        assert len(ctx.results) == 4
        assert ctx.results[0].passed is False
        assert ctx.results[1].passed is True
        assert ctx.results[2].passed is True
        assert ctx.results[3].passed is False


# ── 5. EvalCollector ─────────────────────────────────────────────────


class TestEvalCollector:
    """Directory scan + tags filter."""

    def test_collect_from_directory(self) -> None:
        """Collector should import .py files and gather EvalSpec objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a minimal eval module
            eval_file = os.path.join(tmpdir, "test_demo.py")
            with open(eval_file, "w") as f:
                f.write(
                    "from xruntime._eval._define import define_eval\n"
                    "\n"
                    "@define_eval('demo', domain='demo', tags=('offline',))\n"
                    "async def test_demo(t):\n"
                    "    pass\n"
                )
            collector = EvalCollector(tmpdir)
            specs = collector.collect(tags=["offline"])
            assert len(specs) == 1
            assert specs[0].eval_id == "demo.test_demo"

    def test_collect_filters_by_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            eval_file = os.path.join(tmpdir, "test_mixed.py")
            with open(eval_file, "w") as f:
                f.write(
                    "from xruntime._eval._define import define_eval\n"
                    "\n"
                    "@define_eval('offline one', domain='d', tags=('offline',))\n"
                    "async def test_offline(t):\n"
                    "    pass\n"
                    "\n"
                    "@define_eval('online one', domain='d', tags=('online',))\n"
                    "async def test_online(t):\n"
                    "    pass\n"
                )
            collector = EvalCollector(tmpdir)
            offline = collector.collect(tags=["offline"])
            online = collector.collect(tags=["online"])
            assert len(offline) == 1
            assert offline[0].eval_id == "d.test_offline"
            assert len(online) == 1
            assert online[0].eval_id == "d.test_online"

    def test_collect_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            specs = EvalCollector(tmpdir).collect()
            assert specs == []


# ── 6. EvalRunner ────────────────────────────────────────────────────


class TestEvalRunner:
    """EvalRunner._run_one status decision."""

    @pytest.mark.asyncio
    async def test_run_one_passed(self) -> None:
        @define_eval("passing", domain="r", tags=("offline",))
        async def _ok(t: Any) -> None:
            t._results.append(
                AssertionResult(name="x", passed=True),
            )

        runner = EvalRunner(target="in-process", reporters=[])
        runner._setup_target = AsyncMock()
        result = await runner._run_one(_ok)
        assert result.status == EvalStatus.PASSED

    @pytest.mark.asyncio
    async def test_run_one_failed(self) -> None:
        @define_eval("failing", domain="r", tags=("offline",))
        async def _fail(t: Any) -> None:
            t._results.append(
                AssertionResult(name="x", passed=False, message="nope"),
            )

        runner = EvalRunner(target="in-process", reporters=[])
        runner._setup_target = AsyncMock()
        result = await runner._run_one(_fail)
        assert result.status == EvalStatus.FAILED

    @pytest.mark.asyncio
    async def test_run_one_error_on_exception(self) -> None:
        @define_eval("erroring", domain="r", tags=("offline",))
        async def _boom(t: Any) -> None:
            raise RuntimeError("unexpected")

        runner = EvalRunner(target="in-process", reporters=[])
        runner._setup_target = AsyncMock()
        result = await runner._run_one(_boom)
        assert result.status == EvalStatus.ERROR
        assert "unexpected" in str(result.trace.get("exception", ""))

    @pytest.mark.asyncio
    async def test_run_returns_exit_code(self) -> None:
        """run() returns 0 when all pass, 1 otherwise."""
        runner = EvalRunner(target="in-process", reporters=[])
        runner._setup_target = AsyncMock()

        # Mock collector to return a passing spec
        @define_eval("ok", domain="r", tags=("offline",))
        async def _pass(t: Any) -> None:
            t._results.append(AssertionResult(name="x", passed=True))

        with patch("xruntime._eval._runner.EvalCollector") as MockCollector:
            MockCollector.return_value.collect.return_value = [_pass]
            exit_code = await runner.run(evals_dir="dummy")
        assert exit_code == 0


# ── 7. Reporters ─────────────────────────────────────────────────────


def _sample_results() -> list[EvalResult]:
    return [
        EvalResult(
            eval_id="demo.test_pass",
            description="passes",
            status=EvalStatus.PASSED,
            assertions=[AssertionResult(name="a", passed=True)],
            duration_ms=10,
        ),
        EvalResult(
            eval_id="demo.test_fail",
            description="fails",
            status=EvalStatus.FAILED,
            assertions=[
                AssertionResult(name="a", passed=True),
                AssertionResult(name="b", passed=False, message="boom"),
            ],
            duration_ms=20,
        ),
    ]


class TestConsoleReporter:
    def test_report_writes_to_stdout(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        reporter = ConsoleReporter()
        reporter.report(_sample_results())
        out = capsys.readouterr().out
        assert "test_pass" in out
        assert "test_fail" in out
        assert "PASSED" in out or "passed" in out
        assert "FAILED" in out or "failed" in out


class TestJsonReporter:
    def test_report_writes_valid_json(self, tmp_path) -> None:
        path = tmp_path / "eval-results.json"
        reporter = JsonReporter(path=str(path))
        reporter.report(_sample_results())
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["eval_id"] == "demo.test_pass"
        assert data[1]["status"] == "failed"
        assert data[1]["assertions"][1]["message"] == "boom"


class TestJUnitReporter:
    def test_report_writes_valid_xml(self, tmp_path) -> None:
        path = tmp_path / "eval-results.xml"
        reporter = JUnitReporter(path=str(path))
        reporter.report(_sample_results())
        with open(path) as f:
            xml_content = f.read()
        assert "<?xml" in xml_content
        assert "<testsuites" in xml_content or "<testsuite" in xml_content
        assert "test_pass" in xml_content
        assert "test_fail" in xml_content
        assert "<failure" in xml_content  # failed eval has failure tag


# ── 8. InProcessTarget ───────────────────────────────────────────────


class TestInProcessTarget:
    """InProcessTarget assembly (mocked build_xruntime_app)."""

    @pytest.mark.asyncio
    async def test_setup_builds_app(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        # build_xruntime_app is lazy-imported inside setup(); patch the
        # source module so the lazy ``from xruntime._server import ...``
        # resolves to our mock.
        with patch(
            "xruntime._server.build_xruntime_app",
        ) as mock_build, patch(
            "fakeredis.aioredis.FakeRedis",
        ) as mock_redis:
            mock_redis.return_value = MagicMock()
            mock_app = MagicMock()
            mock_app.state.ext = {
                "middleware_state_cache": MagicMock(),
            }
            mock_build.return_value = mock_app
            await target.setup()
            assert mock_build.called
            assert target._app is mock_app

    @pytest.mark.asyncio
    async def test_audit_entries_returns_list(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        target._ext = {
            "middleware_state_cache": MagicMock(),
        }
        target._ext["middleware_state_cache"]._audit_logger.entries = []
        entries = target.audit_entries("tenant-1")
        assert entries == []


# ── 9. Integration smoke (EvalRunner + Console reporter) ────────────


class TestEvalRunnerIntegration:
    """Smoke: a single passing eval flows through Runner → Reporter."""

    @pytest.mark.asyncio
    async def test_passing_eval_produces_passing_result(
        self,
        capsys: pytest.CaptureFixture,
    ) -> None:
        @define_eval("smoke", domain="smoke", tags=("offline",))
        async def _smoke(t: Any) -> None:
            t._results.append(AssertionResult(name="ok", passed=True))

        runner = EvalRunner(
            target="in-process",
            reporters=[ConsoleReporter()],
        )
        runner._setup_target = AsyncMock()

        with patch("xruntime._eval._runner.EvalCollector") as MockCollector:
            MockCollector.return_value.collect.return_value = [_smoke]
            exit_code = await runner.run(evals_dir="dummy")

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "smoke" in out


# ── 10. CLI __main__ coverage ───────────────────────────────────────


class TestEvalCLI:
    """CLI entrypoint: ``python -m xruntime._eval``."""

    def test_list_subcommand_prints_specs(
        self,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from xruntime._eval.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            eval_file = os.path.join(d, "eval_cli_test.py")
            with open(eval_file, "w") as f:
                f.write(
                    "from xruntime._eval import define_eval\n"
                    "@define_eval('cli test', domain='cli', "
                    "tags=('offline',))\n"
                    "async def _cli_eval(t):\n"
                    "    pass\n",
                )
            with patch(
                "sys.argv",
                [
                    "xruntime.eval",
                    "list",
                    "--evals-dir",
                    d,
                    "--tags",
                    "offline",
                ],
            ):
                exit_code = main()
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "cli._cli_eval" in out

    def test_list_empty_dir_returns_zero(
        self,
        capsys: pytest.CaptureFixture,
    ) -> None:
        from xruntime._eval.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            with patch(
                "sys.argv",
                ["xruntime.eval", "list", "--evals-dir", d],
            ):
                exit_code = main()
        assert exit_code == 0

    def test_run_subcommand_returns_exit_code(self) -> None:
        from xruntime._eval.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            eval_file = os.path.join(d, "eval_run_test.py")
            with open(eval_file, "w") as f:
                f.write(
                    "from xruntime._eval import define_eval\n"
                    "from xruntime._eval._models import "
                    "AssertionResult\n"
                    "@define_eval('run test', domain='run', "
                    "tags=('offline',))\n"
                    "async def _run_eval(t):\n"
                    "    t._results.append(AssertionResult("
                    "name='ok', passed=True))\n",
                )
            with patch(
                "xruntime._eval._runner.EvalRunner._setup_target",
            ) as mock_setup:
                mock_setup.return_value = None
                with patch(
                    "sys.argv",
                    [
                        "xruntime.eval",
                        "run",
                        "--evals-dir",
                        d,
                        "--tags",
                        "offline",
                    ],
                ):
                    exit_code = main()
        assert exit_code == 0


# ── 11. RemoteTarget coverage ───────────────────────────────────────


class TestRemoteTarget:
    """RemoteTarget — HTTP-based eval target."""

    @pytest.mark.asyncio
    async def test_setup_creates_client(self) -> None:
        from xruntime._eval._target_remote import RemoteTarget

        target = RemoteTarget(base_url="http://localhost:8900")
        assert target.base_url == "http://localhost:8900"
        await target.setup()
        assert target._client is not None
        await target.teardown()
        assert target._client is None

    @pytest.mark.asyncio
    async def test_send_posts_to_v1_chat(self) -> None:
        from xruntime._eval._target_remote import RemoteTarget

        target = RemoteTarget(base_url="http://eval-remote")
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "reply": "hello",
            "events": [{"type": "TOOL_CALL", "tool_name": "Read"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp
        target._client = mock_client

        reply, events = await target.send(
            tenant_id="t1",
            role="viewer",
            message="hi",
        )
        assert reply == "hello"
        assert len(events) == 1
        mock_client.post.assert_called_once()

    def test_audit_entries_returns_empty(self) -> None:
        from xruntime._eval._target_remote import RemoteTarget

        target = RemoteTarget(base_url="http://eval-remote")
        assert target.audit_entries("t1") == []

    def test_scan_tenant_keys_returns_empty(self) -> None:
        from xruntime._eval._target_remote import RemoteTarget

        target = RemoteTarget(base_url="http://eval-remote")
        assert target.scan_tenant_keys("t1") == []

    def test_approval_state_snapshot_returns_empty(self) -> None:
        from xruntime._eval._target_remote import RemoteTarget

        target = RemoteTarget(base_url="http://eval-remote")
        assert target.approval_state_snapshot("s1") == set()

    @pytest.mark.asyncio
    async def test_teardown_without_setup_is_noop(self) -> None:
        from xruntime._eval._target_remote import RemoteTarget

        target = RemoteTarget(base_url="http://eval-remote")
        # teardown before setup — should not raise
        await target.teardown()


# ── 12. InProcessTarget extended coverage ───────────────────────────


class TestInProcessTargetExtended:
    """InProcessTarget — send, scan, approval snapshot."""

    @pytest.mark.asyncio
    async def test_send_returns_reply_and_events(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        mock_app = MagicMock()
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "reply": "ok",
            "events": [{"type": "TOOL_CALL", "tool_name": "Write"}],
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        target._app = mock_app
        with patch("httpx.AsyncClient") as mock_async_client:
            mock_async_client.return_value.__aenter__.return_value = (
                mock_client
            )
            mock_async_client.return_value.__aexit__.return_value = None
            reply, events = await target.send(
                tenant_id="t1",
                role="viewer",
                message="write file",
            )
        assert reply == "ok"
        assert len(events) == 1

    def test_scan_tenant_keys_no_redis_returns_empty(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        # _fake_redis is None before setup
        assert target.scan_tenant_keys("t1") == []

    def test_approval_snapshot_no_ext_returns_empty(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        assert target.approval_state_snapshot("s1") == set()

    def test_approval_snapshot_no_cache_returns_empty(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        target._ext = {"middleware_state_cache": None}
        assert target.approval_state_snapshot("s1") == set()

    def test_approval_snapshot_no_approval_cache_returns_empty(
        self,
    ) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        mock_cache = MagicMock()
        mock_cache._approval_state_cache = None
        target._ext = {"middleware_state_cache": mock_cache}
        assert target.approval_state_snapshot("s1") == set()

    def test_audit_entries_no_ext_returns_empty(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        assert target.audit_entries("t1") == []

    def test_audit_entries_no_cache_returns_empty(self) -> None:
        from xruntime._eval._target_inproc import InProcessTarget

        target = InProcessTarget()
        target._ext = {"middleware_state_cache": None}
        assert target.audit_entries("t1") == []


# ── 13. EvalContext extended coverage ───────────────────────────────


class TestEvalContextExtended:
    """EvalContext — reply_matches, tool_input_matches, as_session."""

    def test_reply_matches_pass(self) -> None:
        runner = MagicMock()
        ctx = EvalContext(runner, "test.reply_matches")
        ctx.reply = "Error: file not found"
        ctx.reply_matches(r"Error: .+ not found")
        assert ctx.results[-1].passed

    def test_reply_matches_fail(self) -> None:
        runner = MagicMock()
        ctx = EvalContext(runner, "test.reply_matches_fail")
        ctx.reply = "all good"
        ctx.reply_matches(r"Error: ")
        assert not ctx.results[-1].passed

    def test_tool_input_matches_pass(self) -> None:
        runner = MagicMock()
        ctx = EvalContext(runner, "test.tool_input")
        ctx.events = [
            {
                "type": "TOOL_CALL",
                "tool_name": "Write",
                "tool_input": {"file_path": "/etc/passwd"},
            },
        ]
        ctx.tool_input_matches("Write", includes("/etc/passwd"))
        assert ctx.results[-1].passed

    def test_tool_input_matches_tool_not_called(self) -> None:
        runner = MagicMock()
        ctx = EvalContext(runner, "test.tool_not_called")
        ctx.events = []
        ctx.tool_input_matches("Write", includes("x"))
        assert not ctx.results[-1].passed
        assert "never called" in ctx.results[-1].message

    def test_as_session_sets_session_id(self) -> None:
        runner = MagicMock()
        ctx = EvalContext(runner, "test.session")
        ctx.as_session("my-session")
        assert ctx._session_id == "my-session"


# ── 14. EvalRunner delegation coverage ──────────────────────────────


class TestEvalRunnerDelegation:
    """EvalRunner — audit/scan/approval delegation to target."""

    def test_audit_entries_delegates_to_target(self) -> None:
        runner = EvalRunner(target="in-process")
        mock_target = MagicMock()
        mock_target.audit_entries.return_value = ["entry1"]
        runner._target_obj = mock_target
        result = runner.audit_entries("t1")
        assert result == ["entry1"]

    def test_scan_tenant_keys_delegates_to_target(self) -> None:
        runner = EvalRunner(target="in-process")
        mock_target = MagicMock()
        mock_target.scan_tenant_keys.return_value = ["leaked"]
        runner._target_obj = mock_target
        result = runner.scan_tenant_keys("t1")
        assert result == ["leaked"]

    def test_approval_snapshot_delegates_to_target(self) -> None:
        runner = EvalRunner(target="in-process")
        mock_target = MagicMock()
        mock_target.approval_state_snapshot.return_value = {"Write"}
        runner._target_obj = mock_target
        result = runner.approval_state_snapshot("s1")
        assert result == {"Write"}

    def test_audit_entries_no_target_returns_empty(self) -> None:
        runner = EvalRunner(target="in-process")
        runner._target_obj = None
        assert runner.audit_entries("t1") == []

    def test_scan_no_target_returns_empty(self) -> None:
        runner = EvalRunner(target="in-process")
        runner._target_obj = None
        assert runner.scan_tenant_keys("t1") == []

    def test_approval_snapshot_no_target_returns_empty(self) -> None:
        runner = EvalRunner(target="in-process")
        runner._target_obj = None
        assert runner.approval_state_snapshot("s1") == set()
