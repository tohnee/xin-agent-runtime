# -*- coding: utf-8 -*-
"""Audit middleware — logs every tool call to an audit trail.

Captures who (tenant/user/session), what (tool name/input), decision
(allow/deny), result (success/error), and duration.  Supports
in-memory and file-based (JSONL) sinks.

Inherits :class:`agentscope.middleware.MiddlewareBase` so the AS
Agent middleware system correctly detects the ``on_acting`` hook.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase


@dataclass
class AuditEntry:
    """A single audit log entry.

    Args:
        timestamp (`str`):
            ISO 8601 timestamp.
        tenant_id (`str`):
            Tenant identifier.
        user_id (`str`):
            User identifier.
        session_id (`str`):
            Session identifier.
        tool_name (`str`):
            Name of the tool called.
        tool_input (`dict`):
            Tool input arguments.
        decision (`str`):
            Permission decision — ``"ALLOW"`` or ``"DENY"``.
        result (`str`):
            Execution result — ``"success"`` or ``"error"``.
        duration_ms (`int`):
            Execution duration in milliseconds.
    """

    timestamp: str
    tenant_id: str
    user_id: str
    session_id: str
    tool_name: str
    tool_input: dict[str, Any]
    decision: str
    result: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON logging.

        Returns:
            `dict`: The entry as a dict.
        """
        return asdict(self)


class AuditLogger:
    """Audit log sink — in-memory or file-based.

    Args:
        sink (`str`):
            Sink type — ``"memory"`` or ``"file"``.
        file_path (`str | None`):
            File path for ``sink="file"``.
        max_entries (`int`):
            Cap on in-memory entries (memory sink only). Oldest
            entries are dropped first so a long-lived process does not
            accumulate unbounded memory.
    """

    def __init__(
        self,
        sink: str = "memory",
        file_path: str | None = None,
        max_entries: int = 10000,
    ) -> None:
        """Initialize the logger."""
        self.sink = sink
        self.file_path = file_path
        self.max_entries = max_entries
        self.entries: list[AuditEntry] = []

    async def log(self, entry: AuditEntry) -> None:
        """Write an audit entry to the configured sink.

        Args:
            entry (`AuditEntry`):
                The entry to log.
        """
        if self.sink == "memory":
            if len(self.entries) >= self.max_entries:
                self.entries.pop(0)
            self.entries.append(entry)
        elif self.sink == "file" and self.file_path:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")


class AuditMiddleware(MiddlewareBase):
    """Middleware that audits every tool call.

    Wraps ``on_acting`` to capture tool execution metadata and
    write it to an :class:`AuditLogger`.

    Args:
        logger (`AuditLogger`):
            The audit log sink.
        tenant_id (`str`):
            Default tenant id.
        user_id (`str`):
            Default user id.
        redaction_rules (`list[RedactionRule] | None`):
            Rules used to redact secrets from the audited tool input.
            ``None`` uses the default secret-redaction rules so keys /
            tokens / private keys never reach the audit log.
    """

    def __init__(
        self,
        logger: AuditLogger,
        tenant_id: str = "default",
        user_id: str = "anonymous",
        redaction_rules: list[Any] | None = None,
    ) -> None:
        """Initialize the middleware."""
        from ._redaction import _default_rules

        self.logger = logger
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._redaction_rules = (
            redaction_rules
            if redaction_rules is not None
            else _default_rules()
        )

    def _redact_input(self, value: Any) -> dict[str, Any]:
        """Redact secrets from a tool input value for the audit log.

        AS ``ToolCallBlock.input`` is a raw JSON string accumulated
        during streaming, so this first parses it into a dict (the
        ``AuditEntry.tool_input`` type), then redacts every string
        leaf. Non-JSON or non-object inputs are wrapped under a
        ``"_raw"`` key so the audit entry always stores a dict.

        Args:
            value (`Any`): The raw tool input (JSON str / dict / other).

        Returns:
            `dict[str, Any]`: A redacted dict safe to log.
        """
        parsed: Any = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                return {"_raw": self._redact_value(value)}

        if isinstance(parsed, dict):
            return {k: self._redact_value(v) for k, v in parsed.items()}
        return {"_raw": self._redact_value(parsed)}

    def _redact_value(self, value: Any) -> Any:
        """Redact secrets from a single value (recursively for dicts).

        Args:
            value (`Any`): The value to redact.

        Returns:
            `Any`: A redacted representation safe to log.
        """
        from ._redaction import redact_text

        if isinstance(value, str):
            return redact_text(value, self._redaction_rules)
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_value(v) for v in value]
        return value

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Audit a tool call, then delegate to the next handler.

        A denial raised by an inner middleware (e.g. RBAC raising
        :class:`PermissionError`) is recorded as ``decision="DENY"`` /
        ``result="denied"``; any other exception is recorded as
        ``result="error"``; success as ``result="success"``. The tool
        input is redacted before logging.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Contains ``tool_call`` with tool execution details.
            next_handler (`Callable`):
                The next middleware or ``_acting_impl``.

        Yields:
            Tool chunks from the tool execution.
        """
        tool_call = input_kwargs.get("tool_call")
        tool_name = ""
        raw_input: Any = {}
        if tool_call is not None:
            tool_name = getattr(tool_call, "name", "") or getattr(
                tool_call,
                "tool_call_name",
                "",
            )
            raw_input = getattr(tool_call, "input", {}) or {}

        session_id = ""
        if hasattr(agent, "state") and hasattr(
            agent.state,
            "session_id",
        ):
            session_id = agent.state.session_id or ""

        start = time.monotonic()
        decision = "ALLOW"
        result = "success"
        try:
            async for chunk in next_handler():
                yield chunk
        except PermissionError:
            decision = "DENY"
            result = "denied"
            raise
        except Exception:
            result = "error"
            raise
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self.logger.log(
                AuditEntry(
                    timestamp=datetime.now().isoformat(),
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    tool_input=self._redact_input(raw_input),
                    decision=decision,
                    result=result,
                    duration_ms=duration_ms,
                ),
            )
