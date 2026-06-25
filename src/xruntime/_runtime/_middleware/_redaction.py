# -*- coding: utf-8 -*-
"""Secret redaction middleware — strips sensitive data from tool IO.

Applies regex-based redaction rules to tool inputs and outputs,
replacing secrets with ``[REDACTED_*]`` placeholders before they
enter the agent's context or audit log.

Inherits :class:`agentscope.middleware.MiddlewareBase` and implements
``on_acting`` to redact tool result text before it enters the agent
context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

from agentscope.middleware import MiddlewareBase


@dataclass
class RedactionRule:
    """A single redaction rule.

    Args:
        name (`str`):
            Rule name.
        pattern (`str`):
            Regex pattern to match.
        replacement (`str`):
            Replacement text.
    """

    name: str
    pattern: str
    replacement: str


def redact_text(
    text: str,
    rules: list[RedactionRule],
) -> str:
    """Apply all redaction rules to a text string.

    Args:
        text (`str`):
            The input text.
        rules (`list[RedactionRule]`):
            Redaction rules to apply.

    Returns:
        `str`: The redacted text.
    """
    result = text
    for rule in rules:
        result = re.sub(
            rule.pattern,
            rule.replacement,
            result,
            flags=re.MULTILINE | re.DOTALL,
        )
    return result


def _default_rules() -> list[RedactionRule]:
    """Return the default set of redaction rules.

    Returns:
        `list[RedactionRule]`: Default rules.
    """
    return [
        RedactionRule(
            name="api_key",
            pattern=r"sk-[a-zA-Z0-9]{20,}",
            replacement="[REDACTED_API_KEY]",
        ),
        RedactionRule(
            name="bearer_token",
            pattern=r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*",
            replacement="Bearer [REDACTED_TOKEN]",
        ),
        RedactionRule(
            name="private_key",
            pattern=(
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
                r".*?-----END [A-Z ]*PRIVATE KEY-----"
            ),
            replacement="[REDACTED_PRIVATE_KEY]",
        ),
        RedactionRule(
            name="password_assignment",
            pattern=r"password\s*[=:]\s*\S+",
            replacement="password=[REDACTED]",
        ),
    ]


class SecretRedactionMiddleware(MiddlewareBase):
    """Middleware that redacts secrets from tool IO.

    Args:
        rules (`list[RedactionRule] | None`):
            Custom redaction rules.  ``None`` uses defaults.
    """

    def __init__(
        self,
        rules: list[RedactionRule] | None = None,
    ) -> None:
        """Initialize the middleware."""
        self.rules = rules or _default_rules()

    def redact(self, text: str) -> str:
        """Redact secrets from a text string.

        Args:
            text (`str`):
                The input text.

        Returns:
            `str`: The redacted text.
        """
        return redact_text(text, self.rules)

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Redact secrets from tool input and results.

        The tool call's input (a raw JSON string on AS
        ``ToolCallBlock``) is redacted in place before execution so
        secrets in tool arguments never reach the audit trail or the
        agent context. Result chunk text is then redacted as it streams
        back.

        Args:
            agent (`Agent`):
                The agent instance.
            input_kwargs (`dict`):
                Contains ``tool_call``.
            next_handler (`Callable`):
                The next middleware or ``_acting_impl``.

        Yields:
            Tool chunks with redacted text content.
        """
        tool_call = input_kwargs.get("tool_call")
        if tool_call is not None:
            raw_input = getattr(tool_call, "input", None)
            if isinstance(raw_input, str) and raw_input:
                redacted = self.redact(raw_input)
                if redacted != raw_input:
                    try:
                        tool_call.input = redacted
                    except (AttributeError, ValueError):
                        pass

        async for chunk in next_handler():
            if hasattr(chunk, "content"):
                for block in chunk.content:
                    if hasattr(block, "text") and block.text:
                        block.text = self.redact(block.text)
            yield chunk
