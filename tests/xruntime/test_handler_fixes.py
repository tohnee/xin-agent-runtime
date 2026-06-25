# -*- coding: utf-8 -*-
"""Regression guard for the P0 gateway request-path refactor.

The gateway used to expose a per-event ``_serialize_event`` (which
spanned a fresh event loop per event and reset adapter state) and a
no-op ``_ensure_session`` (which never persisted a session). Both were
replaced by the continuous-stream ``_serialize_stream`` and the
materializing ``_materialize_session``. These tests lock in that the
old broken symbols are gone and the new ones exist with the right
shape, so the regression cannot silently return.

The end-to-end behaviour (materialization writes storage; the stream
accumulates adapter state across events) is covered by
``test_e2e_request_path.py`` against the real AS stack.
"""
import inspect

import pytest

from xruntime._gateway import _extension


class TestRemovedBrokenSymbols:
    """The old per-event / no-op helpers must be gone."""

    def test_serialize_event_removed(self) -> None:
        """The per-event ``_serialize_event`` must no longer exist."""
        assert not hasattr(_extension, "_serialize_event")

    def test_ensure_session_removed(self) -> None:
        """The no-op ``_ensure_session`` must no longer exist."""
        assert not hasattr(_extension, "_ensure_session")

    def test_resolve_agent_id_removed(self) -> None:
        """The storage-ignoring ``_resolve_agent_id`` must be gone."""
        assert not hasattr(_extension, "_resolve_agent_id")


class TestNewSymbols:
    """The replacement helpers exist and are well-shaped."""

    def test_serialize_stream_is_async_generator_function(
        self,
    ) -> None:
        """``_serialize_stream`` must be an async generator function."""
        assert inspect.isasyncgenfunction(_extension._serialize_stream)

    def test_materialize_session_is_coroutine(self) -> None:
        """``_materialize_session`` must be a coroutine function."""
        assert inspect.iscoroutinefunction(_extension._materialize_session)

    def test_gateway_state_caches(self) -> None:
        """``_GatewayState`` exposes credential/agent caches."""
        state = _extension._GatewayState(
            _extension.XRuntimeConfig(),
            _extension.ModelResolver(),
        )
        assert state.credential_id("u", "p", "k") is None
        assert state.agent_cache("u", "name") is None


@pytest.mark.parametrize(
    "symbol",
    ["create_xruntime_extension", "mount_protocol_adapters"],
)
def test_public_api_present(symbol: str) -> None:
    """The public extension entrypoints remain exported."""
    assert hasattr(_extension, symbol)
