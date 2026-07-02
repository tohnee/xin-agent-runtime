# -*- coding: utf-8 -*-
"""Tests for XRuntime enterprise middlewares.

Tests cover:
- AuditMiddleware: tool call audit logging
- QuotaMiddleware: tenant-level quota enforcement
- RbacMiddleware: role-based tool access control
- SecretRedactionMiddleware: sensitive data redaction in tool IO
"""
from unittest.mock import MagicMock

import pytest

from xruntime._runtime._middleware._audit import (
    AuditMiddleware,
    AuditEntry,
    AuditLogger,
)
from xruntime._runtime._middleware._quota import (
    QuotaMiddleware,
    QuotaConfig,
    QuotaExceededError,
    QuotaTracker,
)
from xruntime._runtime._middleware._rbac import (
    RbacMiddleware,
    RoleDefinition,
    RbacRule,
)
from xruntime._runtime._middleware._redaction import (
    SecretRedactionMiddleware,
    RedactionRule,
    redact_text,
)


class TestAuditEntry:
    """Tests for AuditEntry data model."""

    def test_creation(self) -> None:
        """Audit entry should capture all fields."""
        entry = AuditEntry(
            timestamp="2026-01-01T00:00:00",
            tenant_id="acme",
            user_id="user-1",
            session_id="sess-1",
            tool_name="Bash",
            tool_input={"command": "ls"},
            decision="ALLOW",
            result="success",
            duration_ms=42,
        )
        assert entry.tenant_id == "acme"
        assert entry.tool_name == "Bash"
        assert entry.decision == "ALLOW"
        assert entry.duration_ms == 42

    def test_to_dict(self) -> None:
        """Audit entry should serialize to dict."""
        entry = AuditEntry(
            timestamp="2026-01-01T00:00:00",
            tenant_id="acme",
            user_id="user-1",
            session_id="sess-1",
            tool_name="Read",
            tool_input={"path": "/etc/passwd"},
            decision="ALLOW",
            result="success",
            duration_ms=10,
        )
        d = entry.to_dict()
        assert d["tool_name"] == "Read"
        assert d["tenant_id"] == "acme"
        assert "timestamp" in d


class TestAuditLogger:
    """Tests for AuditLogger sink."""

    async def test_log_to_list(self) -> None:
        """In-memory logger should collect entries."""
        logger = AuditLogger(sink="memory")
        entry = AuditEntry(
            timestamp="2026-01-01",
            tenant_id="t1",
            user_id="u1",
            session_id="s1",
            tool_name="Bash",
            tool_input={},
            decision="ALLOW",
            result="success",
            duration_ms=5,
        )
        await logger.log(entry)
        assert len(logger.entries) == 1

    async def test_log_to_file(self, tmp_path) -> None:
        """File logger should write JSONL."""
        log_file = tmp_path / "audit.jsonl"
        logger = AuditLogger(sink="file", file_path=str(log_file))
        entry = AuditEntry(
            timestamp="2026-01-01",
            tenant_id="t1",
            user_id="u1",
            session_id="s1",
            tool_name="Read",
            tool_input={"path": "foo.py"},
            decision="ALLOW",
            result="success",
            duration_ms=3,
        )
        await logger.log(entry)
        content = log_file.read_text()
        assert "Read" in content
        assert "foo.py" in content


class TestAuditMiddleware:
    """Tests for AuditMiddleware."""

    def test_inherits_middleware_base(self) -> None:
        """Should be importable as a middleware."""
        mw = AuditMiddleware(AuditLogger(sink="memory"))
        assert mw is not None

    async def test_logs_tool_call(self) -> None:
        """on_acting should produce an audit entry."""
        logger = AuditLogger(sink="memory")
        mw = AuditMiddleware(logger, tenant_id="acme", user_id="u1")

        tool_call = MagicMock()
        tool_call.name = "Bash"
        tool_call.tool_call_id = "tc-1"
        tool_call.input = {"command": "echo hi"}

        agent = MagicMock()
        agent.name = "agent-1"
        agent.state = MagicMock()
        agent.state.session_id = "sess-1"

        async def mock_next():
            yield MagicMock()
            yield MagicMock()

        gen = mw.on_acting(
            agent,
            {"tool_call": tool_call},
            mock_next,
        )
        async for _ in gen:
            pass

        assert len(logger.entries) == 1
        assert logger.entries[0].tool_name == "Bash"
        assert logger.entries[0].tenant_id == "acme"


class TestQuotaConfig:
    """Tests for QuotaConfig."""

    def test_defaults(self) -> None:
        """Default config should have unlimited quota."""
        cfg = QuotaConfig()
        assert cfg.max_tokens is None
        assert cfg.max_tool_calls is None
        assert cfg.max_cost_usd is None

    def test_custom_limits(self) -> None:
        """Custom limits should be set."""
        cfg = QuotaConfig(
            max_tokens=10000,
            max_tool_calls=50,
            max_cost_usd=5.0,
        )
        assert cfg.max_tokens == 10000
        assert cfg.max_tool_calls == 50
        assert cfg.max_cost_usd == 5.0


class TestQuotaTracker:
    """Tests for QuotaTracker."""

    def test_initial_usage(self) -> None:
        """Fresh tracker should have zero usage."""
        tracker = QuotaTracker(QuotaConfig(max_tokens=1000))
        assert tracker.token_usage == 0
        assert tracker.tool_call_count == 0
        assert tracker.cost_usd == 0.0

    def test_consume_tokens(self) -> None:
        """Consuming tokens should update usage."""
        tracker = QuotaTracker(QuotaConfig(max_tokens=1000))
        tracker.consume_tokens(500)
        assert tracker.token_usage == 500

    def test_consume_tokens_exceeds(self) -> None:
        """Exceeding token limit should raise."""
        tracker = QuotaTracker(QuotaConfig(max_tokens=100))
        tracker.consume_tokens(80)
        with pytest.raises(QuotaExceededError):
            tracker.consume_tokens(50)

    def test_consume_tool_calls(self) -> None:
        """Consuming tool calls should update count."""
        tracker = QuotaTracker(QuotaConfig(max_tool_calls=5))
        tracker.consume_tool_call()
        tracker.consume_tool_call()
        assert tracker.tool_call_count == 2

    def test_consume_tool_calls_exceeds(self) -> None:
        """Exceeding tool call limit should raise."""
        tracker = QuotaTracker(QuotaConfig(max_tool_calls=2))
        tracker.consume_tool_call()
        tracker.consume_tool_call()
        with pytest.raises(QuotaExceededError):
            tracker.consume_tool_call()

    def test_consume_cost(self) -> None:
        """Consuming cost should update total."""
        tracker = QuotaTracker(QuotaConfig(max_cost_usd=10.0))
        tracker.consume_cost(3.5)
        assert tracker.cost_usd == 3.5

    def test_consume_cost_exceeds(self) -> None:
        """Exceeding cost limit should raise."""
        tracker = QuotaTracker(QuotaConfig(max_cost_usd=1.0))
        tracker.consume_cost(0.8)
        with pytest.raises(QuotaExceededError):
            tracker.consume_cost(0.5)

    def test_unlimited_no_raise(self) -> None:
        """Unlimited config should never raise."""
        tracker = QuotaTracker(QuotaConfig())
        tracker.consume_tokens(999999)
        tracker.consume_tool_call()
        tracker.consume_cost(999999.0)
        assert tracker.token_usage == 999999


class TestQuotaMiddleware:
    """Tests for QuotaMiddleware."""

    def test_creation(self) -> None:
        """Middleware should be creatable with config."""
        mw = QuotaMiddleware(
            QuotaConfig(max_tool_calls=10, max_tokens=1000),
        )
        assert mw is not None

    async def test_blocks_on_tool_call_limit(self) -> None:
        """Should block when tool call quota is exceeded."""
        mw = QuotaMiddleware(
            QuotaConfig(max_tool_calls=1),
        )

        tool_call = MagicMock()
        tool_call.tool_call_name = "Bash"
        tool_call.tool_call_id = "tc-1"

        agent = MagicMock()
        agent.name = "agent"

        async def mock_next():
            yield MagicMock()

        gen = mw.on_acting(
            agent,
            {"tool_call": tool_call},
            mock_next,
        )
        async for _ in gen:
            pass

        gen2 = mw.on_acting(
            agent,
            {"tool_call": tool_call},
            mock_next,
        )
        with pytest.raises(QuotaExceededError):
            async for _ in gen2:
                pass

    def test_accepts_shared_tracker(self) -> None:
        """Middleware should accept an externally-managed tracker."""
        shared = QuotaTracker(
            QuotaConfig(max_tool_calls=5),
        )
        mw = QuotaMiddleware(
            QuotaConfig(max_tool_calls=1),
            tracker=shared,
        )
        assert mw.tracker is shared

    async def test_shared_tracker_accumulates_across_mws(self) -> None:
        """Two middlewares sharing a tracker should see combined usage."""
        shared = QuotaTracker(
            QuotaConfig(max_tool_calls=2),
        )
        mw1 = QuotaMiddleware(
            QuotaConfig(max_tool_calls=99),
            tracker=shared,
        )
        mw2 = QuotaMiddleware(
            QuotaConfig(max_tool_calls=99),
            tracker=shared,
        )

        tool_call = MagicMock()
        tool_call.tool_call_name = "Bash"
        tool_call.tool_call_id = "tc-1"
        agent = MagicMock()
        agent.name = "agent"

        async def mock_next():
            yield MagicMock()

        for mw in (mw1, mw2):
            gen = mw.on_acting(
                agent,
                {"tool_call": tool_call},
                mock_next,
            )
            async for _ in gen:
                pass

        assert shared.tool_call_count == 2


class TestRbacRule:
    """Tests for RbacRule."""

    def test_creation(self) -> None:
        """Rule should capture tool and action."""
        rule = RbacRule(tool_pattern="Bash", action="allow")
        assert rule.tool_pattern == "Bash"
        assert rule.action == "allow"

    def test_matches_exact(self) -> None:
        """Exact tool name should match."""
        rule = RbacRule(tool_pattern="Read", action="allow")
        assert rule.matches("Read")
        assert not rule.matches("Bash")

    def test_matches_glob(self) -> None:
        """Glob pattern should match."""
        rule = RbacRule(tool_pattern="mcp__github__*", action="allow")
        assert rule.matches("mcp__github__get_issue")
        assert not rule.matches("mcp__playwright__screenshot")


class TestRoleDefinition:
    """Tests for RoleDefinition."""

    def test_creation(self) -> None:
        """Role should capture name and rules."""
        role = RoleDefinition(
            name="viewer",
            rules=[
                RbacRule("Read", "allow"),
                RbacRule("Write", "deny"),
                RbacRule("Bash", "deny"),
            ],
        )
        assert role.name == "viewer"
        assert len(role.rules) == 3

    def test_check_tool_allowed(self) -> None:
        """Role should allow matching tools."""
        role = RoleDefinition(
            name="coder",
            rules=[
                RbacRule("Read", "allow"),
                RbacRule("Write", "allow"),
                RbacRule("Edit", "allow"),
                RbacRule("Bash", "deny"),
            ],
        )
        assert role.check_tool("Read") == "allow"
        assert role.check_tool("Write") == "allow"
        assert role.check_tool("Bash") == "deny"

    def test_check_tool_default_deny(self) -> None:
        """Unmatched tools should default to deny."""
        role = RoleDefinition(
            name="restricted",
            rules=[RbacRule("Read", "allow")],
        )
        assert role.check_tool("Bash") == "deny"


class TestRbacMiddleware:
    """Tests for RbacMiddleware."""

    def test_creation(self) -> None:
        """Middleware should be creatable with roles."""
        mw = RbacMiddleware(
            roles={
                "viewer": RoleDefinition(
                    "viewer",
                    [RbacRule("Read", "allow")],
                ),
            },
        )
        assert mw is not None

    def test_assign_role(self) -> None:
        """Should be able to assign a role to a session."""
        mw = RbacMiddleware(
            roles={
                "viewer": RoleDefinition(
                    "viewer",
                    [RbacRule("Read", "allow")],
                ),
            },
        )
        mw.assign_role("sess-1", "viewer")
        assert mw.get_role("sess-1") == "viewer"

    def test_check_tool_allowed(self) -> None:
        """Allowed tool should pass."""
        mw = RbacMiddleware(
            roles={
                "viewer": RoleDefinition(
                    "viewer",
                    [RbacRule("Read", "allow")],
                ),
            },
        )
        mw.assign_role("sess-1", "viewer")
        assert mw.check_tool("sess-1", "Read") == "allow"

    def test_check_tool_denied(self) -> None:
        """Denied tool should be blocked."""
        mw = RbacMiddleware(
            roles={
                "viewer": RoleDefinition(
                    "viewer",
                    [
                        RbacRule("Read", "allow"),
                        RbacRule("Bash", "deny"),
                    ],
                ),
            },
        )
        mw.assign_role("sess-1", "viewer")
        assert mw.check_tool("sess-1", "Bash") == "deny"

    def test_check_tool_no_role(self) -> None:
        """Session without role should default deny."""
        mw = RbacMiddleware(roles={})
        assert mw.check_tool("sess-1", "Read") == "deny"


class TestSecretRedaction:
    """Tests for SecretRedactionMiddleware."""

    def test_redact_text_api_key(self) -> None:
        """API keys should be redacted."""
        rule = RedactionRule(
            name="api_key",
            pattern=r"sk-[a-zA-Z0-9]{20,}",
            replacement="[REDACTED_API_KEY]",
        )
        text = "The key is sk-abc123def456ghi789jkl012mno345"
        redacted = redact_text(text, [rule])
        assert "sk-abc" not in redacted
        assert "[REDACTED_API_KEY]" in redacted

    def test_redact_text_multiple_rules(self) -> None:
        """Multiple redaction rules should all apply."""
        rules = [
            RedactionRule(
                name="api_key",
                pattern=r"sk-[a-zA-Z0-9]{20,}",
                replacement="[REDACTED]",
            ),
            RedactionRule(
                name="password",
                pattern=r"password\s*[=:]\s*\S+",
                replacement="password=[REDACTED]",
            ),
        ]
        text = "api_key=sk-abc123def456ghi789jkl012mno345 password=secret123"
        redacted = redact_text(text, rules)
        assert "sk-abc" not in redacted
        assert "secret123" not in redacted

    def test_redact_text_no_match(self) -> None:
        """Text without secrets should pass through."""
        rule = RedactionRule(
            name="api_key",
            pattern=r"sk-[a-zA-Z0-9]{20,}",
            replacement="[REDACTED]",
        )
        text = "This is a normal message"
        assert redact_text(text, [rule]) == text

    def test_default_rules_include_common_secrets(self) -> None:
        """Default rules should cover common secret patterns."""
        mw = SecretRedactionMiddleware()
        assert len(mw.rules) >= 3
        patterns = [r.name for r in mw.rules]
        assert "api_key" in patterns
        assert "bearer_token" in patterns
        assert "private_key" in patterns

    def test_redact_private_key(self) -> None:
        """Private key blocks should be redacted."""
        mw = SecretRedactionMiddleware()
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA\n"
            "-----END RSA PRIVATE KEY-----"
        )
        redacted = redact_text(text, mw.rules)
        assert "MIIEpAIBAAKCAQEA" not in redacted


class _FakeUsage:
    """A stand-in for ChatUsage with input/output token counts."""

    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    """A stand-in for ChatResponse exposing usage + metadata."""

    def __init__(
        self,
        usage: _FakeUsage | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.usage = usage
        self.metadata = metadata or {}


class TestQuotaModelCall:
    """Tests for QuotaMiddleware.on_model_call token/cost metering."""

    async def test_consumes_tokens_non_streaming(self) -> None:
        """A non-streaming response should meter total tokens."""
        tracker = QuotaTracker(QuotaConfig(max_tokens=1000))
        mw = QuotaMiddleware(QuotaConfig(), tracker=tracker)
        response = _FakeResponse(usage=_FakeUsage(100, 50))

        async def next_handler(**kwargs):
            return response

        result = await mw.on_model_call(MagicMock(), {}, next_handler)
        assert result is response
        assert tracker.token_usage == 150

    async def test_token_limit_exceeded(self) -> None:
        """Exceeding the token limit should raise."""
        tracker = QuotaTracker(QuotaConfig(max_tokens=100))
        mw = QuotaMiddleware(QuotaConfig(), tracker=tracker)

        async def next_handler(**kwargs):
            return _FakeResponse(usage=_FakeUsage(80, 50))

        with pytest.raises(QuotaExceededError):
            await mw.on_model_call(MagicMock(), {}, next_handler)

    async def test_consumes_tokens_streaming(self) -> None:
        """A streaming response should meter tokens from the last chunk."""
        tracker = QuotaTracker(QuotaConfig(max_tokens=1000))
        mw = QuotaMiddleware(QuotaConfig(), tracker=tracker)

        async def stream():
            yield _FakeResponse(usage=_FakeUsage(10, 5))
            yield _FakeResponse(usage=_FakeUsage(100, 40))

        async def next_handler(**kwargs):
            return stream()

        result = await mw.on_model_call(MagicMock(), {}, next_handler)
        chunks = [c async for c in result]
        assert len(chunks) == 2
        # Only the final chunk's usage is counted (140), not summed.
        assert tracker.token_usage == 140

    async def test_consumes_cost_metadata(self) -> None:
        """A response with cost metadata should meter USD cost."""
        tracker = QuotaTracker(QuotaConfig(max_cost_usd=1.0))
        mw = QuotaMiddleware(QuotaConfig(), tracker=tracker)

        async def next_handler(**kwargs):
            return _FakeResponse(
                usage=_FakeUsage(1, 1),
                metadata={"cost": 0.25},
            )

        await mw.on_model_call(MagicMock(), {}, next_handler)
        assert tracker.cost_usd == 0.25

    def test_on_model_call_is_implemented(self) -> None:
        """The AS middleware system must detect on_model_call."""
        mw = QuotaMiddleware(QuotaConfig())
        assert mw.is_implemented("on_model_call") is True


class TestAuditInputType:
    """Tests that audit tool_input is always a dict (issue #6)."""

    async def test_json_string_input_parsed_to_dict(self) -> None:
        """A JSON-string tool input is parsed into a dict for the log."""
        logger = AuditLogger(sink="memory")
        mw = AuditMiddleware(logger)

        tool_call = MagicMock()
        tool_call.name = "Bash"
        tool_call.input = '{"command": "ls", "cwd": "/tmp"}'

        agent = MagicMock()
        agent.state = MagicMock()
        agent.state.session_id = "s1"

        async def mock_next():
            yield MagicMock()

        async for _ in mw.on_acting(
            agent,
            {"tool_call": tool_call},
            mock_next,
        ):
            pass

        entry = logger.entries[0]
        assert isinstance(entry.tool_input, dict)
        assert entry.tool_input["command"] == "ls"
        assert entry.tool_input["cwd"] == "/tmp"

    async def test_secrets_redacted_in_input(self) -> None:
        """Secrets in the JSON tool input are redacted in the log."""
        logger = AuditLogger(sink="memory")
        mw = AuditMiddleware(logger)

        tool_call = MagicMock()
        tool_call.name = "Bash"
        tool_call.input = '{"command": "curl -H Bearer sk-' + "a" * 30 + '"}'

        agent = MagicMock()
        agent.state = MagicMock()
        agent.state.session_id = "s1"

        async def mock_next():
            yield MagicMock()

        async for _ in mw.on_acting(
            agent,
            {"tool_call": tool_call},
            mock_next,
        ):
            pass

        entry = logger.entries[0]
        assert isinstance(entry.tool_input, dict)
        assert "sk-" + "a" * 30 not in entry.tool_input["command"]

    async def test_non_json_input_wrapped(self) -> None:
        """A non-JSON string input is wrapped under _raw as a dict."""
        logger = AuditLogger(sink="memory")
        mw = AuditMiddleware(logger)

        tool_call = MagicMock()
        tool_call.name = "Bash"
        tool_call.input = "not json"

        agent = MagicMock()
        agent.state = MagicMock()
        agent.state.session_id = "s1"

        async def mock_next():
            yield MagicMock()

        async for _ in mw.on_acting(
            agent,
            {"tool_call": tool_call},
            mock_next,
        ):
            pass

        entry = logger.entries[0]
        assert isinstance(entry.tool_input, dict)
        assert entry.tool_input["_raw"] == "not json"


class TestRedactionToolInput:
    """Tests that redaction scrubs tool_call.input (issue #7)."""

    async def test_redacts_tool_input_before_execution(self) -> None:
        """A secret in tool_call.input is redacted in place."""
        mw = SecretRedactionMiddleware()

        class _ToolCall:
            def __init__(self) -> None:
                self.input = '{"command": "echo sk-' + "b" * 30 + '"}'

        tool_call = _ToolCall()

        async def mock_next():
            if False:
                yield None

        async for _ in mw.on_acting(
            MagicMock(),
            {"tool_call": tool_call},
            mock_next,
        ):
            pass

        assert "sk-" + "b" * 30 not in tool_call.input
        assert "[REDACTED_API_KEY]" in tool_call.input

    async def test_clean_input_unchanged(self) -> None:
        """Input without secrets is left untouched."""
        mw = SecretRedactionMiddleware()

        class _ToolCall:
            def __init__(self) -> None:
                self.input = '{"command": "ls -la"}'

        tool_call = _ToolCall()

        async def mock_next():
            if False:
                yield None

        async for _ in mw.on_acting(
            MagicMock(),
            {"tool_call": tool_call},
            mock_next,
        ):
            pass

        assert tool_call.input == '{"command": "ls -la"}'
