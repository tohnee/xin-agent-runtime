# -*- coding: utf-8 -*-
"""Tests for XRuntimeRequest and ProtocolAdapter ABC."""
import inspect
from abc import ABC

import pytest

from xruntime._gateway._request import (
    XRuntimeRequest,
    ProtocolType,
    ToolExecutionMode,
)
from xruntime._gateway._adapter import (
    ProtocolAdapter,
    AdapterRegistry,
)


class TestXRuntimeRequest:
    """Tests for the unified internal request model."""

    def test_defaults(self) -> None:
        """Default request should have minimal fields set."""
        req = XRuntimeRequest(
            protocol=ProtocolType.ANTHROPIC,
            prompt="hello",
        )
        assert req.protocol == ProtocolType.ANTHROPIC
        assert req.prompt == "hello"
        assert req.session_id is None
        assert req.user_id == "anonymous"
        assert req.tenant_id == "default"
        assert req.tool_mode == ToolExecutionMode.SERVER
        assert req.system_prompt is None
        assert req.allowed_tools == []
        assert req.disallowed_tools == []
        assert req.permission_mode == "default"
        assert req.max_turns is None
        assert req.metadata == {}

    def test_with_session(self) -> None:
        """Request with session_id should carry it."""
        req = XRuntimeRequest(
            protocol=ProtocolType.CLAUDE_CODE,
            prompt="continue",
            session_id="sess-123",
        )
        assert req.session_id == "sess-123"
        assert req.protocol == ProtocolType.CLAUDE_CODE

    def test_with_tenant(self) -> None:
        """Request should support tenant isolation."""
        req = XRuntimeRequest(
            protocol=ProtocolType.OPENCODE,
            prompt="run task",
            tenant_id="acme-corp",
            user_id="user-456",
        )
        assert req.tenant_id == "acme-corp"
        assert req.user_id == "user-456"

    def test_tool_mode_external(self) -> None:
        """Request should support external tool execution mode."""
        req = XRuntimeRequest(
            protocol=ProtocolType.ANTHROPIC,
            prompt="use external tools",
            tool_mode=ToolExecutionMode.EXTERNAL,
        )
        assert req.tool_mode == ToolExecutionMode.EXTERNAL

    def test_with_permission_mode(self) -> None:
        """Request should carry permission_mode."""
        req = XRuntimeRequest(
            protocol=ProtocolType.CLAUDE_CODE,
            prompt="edit files",
            permission_mode="accept_edits",
        )
        assert req.permission_mode == "accept_edits"

    def test_with_tools_config(self) -> None:
        """Request should carry allowed/disallowed tools."""
        req = XRuntimeRequest(
            protocol=ProtocolType.CLAUDE_CODE,
            prompt="review code",
            allowed_tools=["Read", "Glob", "Grep"],
            disallowed_tools=["Bash"],
        )
        assert req.allowed_tools == ["Read", "Glob", "Grep"]
        assert req.disallowed_tools == ["Bash"]

    def test_with_max_turns(self) -> None:
        """Request should carry max_turns."""
        req = XRuntimeRequest(
            protocol=ProtocolType.CLAUDE_CODE,
            prompt="limited task",
            max_turns=5,
        )
        assert req.max_turns == 5


class TestProtocolType:
    """Tests for the ProtocolType enum."""

    def test_values(self) -> None:
        """ProtocolType should have the three target protocols."""
        assert ProtocolType.ANTHROPIC == "anthropic"
        assert ProtocolType.CLAUDE_CODE == "claude_code"
        assert ProtocolType.OPENCODE == "opencode"


class TestToolExecutionMode:
    """Tests for the ToolExecutionMode enum."""

    def test_values(self) -> None:
        """ToolExecutionMode should have SERVER and EXTERNAL."""
        assert ToolExecutionMode.SERVER == "server"
        assert ToolExecutionMode.EXTERNAL == "external"


class TestProtocolAdapter:
    """Tests for the ProtocolAdapter ABC."""

    def test_is_abc(self) -> None:
        """ProtocolAdapter should be an ABC."""
        assert issubclass(ProtocolAdapter, ABC)

    def test_has_parse_request(self) -> None:
        """ProtocolAdapter should define parse_request as abstract."""
        assert hasattr(ProtocolAdapter, "parse_request")
        assert getattr(
            ProtocolAdapter.parse_request,
            "__isabstractmethod__",
            False,
        )

    def test_has_serialize_event_stream(self) -> None:
        """ProtocolAdapter should define serialize_event_stream as abstract."""
        assert hasattr(ProtocolAdapter, "serialize_event_stream")
        assert getattr(
            ProtocolAdapter.serialize_event_stream,
            "__isabstractmethod__",
            False,
        )

    def test_has_protocol_type(self) -> None:
        """ProtocolAdapter should define protocol_type attribute."""
        annotations = getattr(ProtocolAdapter, "__annotations__", {})
        assert "protocol_type" in annotations

    def test_cannot_instantiate_directly(self) -> None:
        """ProtocolAdapter should not be directly instantiable."""
        with pytest.raises(TypeError):
            ProtocolAdapter()  # type: ignore[abstract]


class TestAdapterRegistry:
    """Tests for the adapter registry."""

    def test_register_and_get(self) -> None:
        """Should register and retrieve an adapter by protocol type."""

        class DummyAdapter(ProtocolAdapter):
            protocol_type = ProtocolType.ANTHROPIC

            async def parse_request(self, raw: object) -> XRuntimeRequest:
                return XRuntimeRequest(
                    protocol=ProtocolType.ANTHROPIC,
                    prompt="dummy",
                )

            async def serialize_event_stream(self, events: object) -> object:
                yield b""

        registry = AdapterRegistry()
        adapter = DummyAdapter()
        registry.register(adapter)
        assert registry.get(ProtocolType.ANTHROPIC) is adapter

    def test_get_nonexistent_returns_none(self) -> None:
        """Getting an unregistered protocol should return None."""
        registry = AdapterRegistry()
        assert registry.get(ProtocolType.OPENCODE) is None

    def test_list_registered(self) -> None:
        """Should list all registered protocol types."""

        class DummyA(ProtocolAdapter):
            protocol_type = ProtocolType.ANTHROPIC

            async def parse_request(self, raw: object) -> XRuntimeRequest:
                ...

            async def serialize_event_stream(self, events: object) -> object:
                ...

        class DummyB(ProtocolAdapter):
            protocol_type = ProtocolType.CLAUDE_CODE

            async def parse_request(self, raw: object) -> XRuntimeRequest:
                ...

            async def serialize_event_stream(self, events: object) -> object:
                ...

        registry = AdapterRegistry()
        registry.register(DummyA())
        registry.register(DummyB())
        registered = registry.list_registered()
        assert ProtocolType.ANTHROPIC in registered
        assert ProtocolType.CLAUDE_CODE in registered

    def test_duplicate_register_overwrites(self) -> None:
        """Registering the same protocol twice should overwrite."""

        class DummyA(ProtocolAdapter):
            protocol_type = ProtocolType.ANTHROPIC

            async def parse_request(self, raw: object) -> XRuntimeRequest:
                ...

            async def serialize_event_stream(self, events: object) -> object:
                ...

        class DummyB(ProtocolAdapter):
            protocol_type = ProtocolType.ANTHROPIC

            async def parse_request(self, raw: object) -> XRuntimeRequest:
                ...

            async def serialize_event_stream(self, events: object) -> object:
                ...

        registry = AdapterRegistry()
        a = DummyA()
        b = DummyB()
        registry.register(a)
        registry.register(b)
        assert registry.get(ProtocolType.ANTHROPIC) is b
