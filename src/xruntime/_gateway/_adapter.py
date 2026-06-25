# -*- coding: utf-8 -*-
"""Protocol adapter abstract base class and registry.

Each protocol (Anthropic Messages API, Claude Code SDK, OpenCode SDK)
implements :class:`ProtocolAdapter` to convert between its wire format
and XRuntime's internal :class:`XRuntimeRequest` /
:class:`agentscope.event.AgentEvent` stream.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from ._request import ProtocolType, XRuntimeRequest


class ProtocolAdapter(ABC):
    """Abstract base for protocol adapters.

    Subclasses must define:
        - ``protocol_type``: the :class:`ProtocolType` this adapter
          handles.
        - ``parse_request``: convert a raw protocol-specific request
          into an :class:`XRuntimeRequest`.
        - ``serialize_event_stream``: convert an
          :class:`AgentEvent` async generator into a protocol-specific
          byte stream for the HTTP response body.

    The adapter is stateless — it does not hold session state.  All
    state lives in the runtime core via ``SessionRecord`` /
    ``AgentState``.
    """

    protocol_type: ProtocolType

    @abstractmethod
    async def parse_request(
        self,
        raw: Any,
    ) -> XRuntimeRequest:
        """Parse a raw protocol request into a unified XRuntimeRequest.

        Args:
            raw (`Any`):
                The protocol-specific raw request (e.g. a parsed
                JSON body, a Claude Code SDK message dict, an
                OpenCode config + prompt).

        Returns:
            `XRuntimeRequest`: The unified request.
        """

    @abstractmethod
    async def serialize_event_stream(
        self,
        events: AsyncGenerator[Any, None],
    ) -> AsyncGenerator[bytes, None]:
        """Serialize an AgentEvent stream into protocol-specific bytes.

        Args:
            events (`AsyncGenerator[AgentEvent, None]`):
                The stream of :class:`agentscope.event.AgentEvent`
                objects from the runtime core.

        Yields:
            `bytes`: Chunks of the protocol-specific response body.
        """


class AdapterRegistry:
    """Registry of protocol adapters by :class:`ProtocolType`.

    Adapters are registered at startup and looked up by the gateway
    router when a request arrives.

    Usage::

        registry = AdapterRegistry()
        registry.register(AnthropicMessagesAdapter())
        adapter = registry.get(ProtocolType.ANTHROPIC)
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._adapters: dict[ProtocolType, ProtocolAdapter] = {}

    def register(self, adapter: ProtocolAdapter) -> None:
        """Register an adapter.

        If an adapter for the same protocol type is already
        registered, it is overwritten.

        Args:
            adapter (`ProtocolAdapter`):
                The adapter instance to register.
        """
        self._adapters[adapter.protocol_type] = adapter

    def get(
        self,
        protocol_type: ProtocolType,
    ) -> ProtocolAdapter | None:
        """Look up an adapter by protocol type.

        Args:
            protocol_type (`ProtocolType`):
                The protocol to look up.

        Returns:
            `ProtocolAdapter | None`: The adapter, or ``None`` if
            no adapter is registered for this protocol.
        """
        return self._adapters.get(protocol_type)

    def list_registered(self) -> list[ProtocolType]:
        """List all registered protocol types.

        Returns:
            `list[ProtocolType]`: The registered protocol types.
        """
        return list(self._adapters.keys())
