# -*- coding: utf-8 -*-
"""Strict fail-closed tests for ``TenantAwareMessageBus.__getattr__``.

These tests pin down the P0 tenant-isolation fix: the wrapper must
NOT silently forward unknown public attributes to the inner bus,
because doing so would let callers bypass the ``tenant:{tid}:`` key
prefix (e.g. ``bus.publish(...)`` / ``bus.acquire_lock(...)`` would
hit the un-prefixed inner bus key).

The fix changes ``__getattr__`` to fail closed: any public method
that was not explicitly wrapped on ``TenantAwareMessageBus`` must
raise :class:`AttributeError` with a developer-facing message
explaining how to add a tenant-prefixed wrapper.  Dunder attributes
and underscore-prefixed internal attributes remain accessible via
normal attribute lookup (``__getattr__`` only fires when regular
lookup fails).
"""
import subprocess
import sys
from pathlib import Path

import pytest


class TestTenantMessageBusStrictGetattr:
    """Fail-closed ``__getattr__`` behaviour."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_bus():
        """Build a TenantAwareMessageBus around a MagicMock inner bus."""
        from unittest.mock import MagicMock

        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        return TenantAwareMessageBus(
            MagicMock(),
            prefix_template="tenant:{tid}:",
        )

    # ------------------------------------------------------------------
    # 1. Wrapped methods still apply the tenant prefix
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_wrapped_methods_have_tenant_prefix(self) -> None:
        """Known wrapped methods must call the inner bus with a
        tenant-prefixed key (spot-check session_publish_event).
        """
        from unittest.mock import AsyncMock, MagicMock

        from xruntime._infra._tenant import current_tenant
        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        mock_bus = MagicMock()
        mock_bus.log_append = AsyncMock(return_value="1234-0")
        mock_bus.publish = AsyncMock()

        bus = TenantAwareMessageBus(
            mock_bus,
            prefix_template="tenant:{tid}:",
        )

        with current_tenant.tenant("t1"):
            await bus.session_publish_event(
                session_id="s1",
                event={"type": "REPLY_END"},
            )

        # log_append must be called with a tenant-prefixed key
        log_key = mock_bus.log_append.call_args[0][0]
        assert log_key.startswith("tenant:t1:"), (
            f"Expected tenant prefix, got {log_key!r}",
        )
        # publish must also use a tenant-prefixed key
        pub_key = mock_bus.publish.call_args[0][0]
        assert pub_key.startswith("tenant:t1:"), (
            f"Expected tenant prefix, got {pub_key!r}",
        )

    # ------------------------------------------------------------------
    # 2. Unwrapped public methods raise AttributeError (fail-closed)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "method_name",
        [
            "publish",
            "acquire_lock",
            "log_append",
            "log_read",
            "log_trim",
            "subscribe",
            "queue_delete",
            "registry_set",
            "registry_del",
            "registry_exists",
            "registry_getall",
            "registry_drop",
            "is_locked",
            "some_other_unwrapped_method",
        ],
    )
    def test_unwrapped_public_method_raises_attribute_error(
        self,
        method_name: str,
    ) -> None:
        """Accessing any inner-bus public method via the wrapper must
        raise AttributeError so callers cannot bypass the tenant
        prefix.
        """
        bus = self._make_bus()

        with pytest.raises(AttributeError) as exc_info:
            getattr(bus, method_name)

        msg = str(exc_info.value)
        # Error must point the developer at adding a wrapper.
        assert "tenant" in msg.lower() or "wrap" in msg.lower(), (
            f"Error message should guide developers to wrap "
            f"{method_name!r} with a tenant prefix; got: {msg!r}",
        )

    # ------------------------------------------------------------------
    # 3. Dunder access still works
    # ------------------------------------------------------------------

    def test_dunder_access_still_works(self) -> None:
        """Dunder attributes resolve through normal lookup and must
        not be blocked by the strict ``__getattr__``.
        """
        bus = self._make_bus()

        # __init__ is defined on the class — must be callable.
        assert callable(bus.__init__)
        # __repr__ falls back to the default object repr (no custom
        # __repr__ defined) — must NOT raise and must mention the
        # class name.
        repr_str = repr(bus)
        assert "TenantAwareMessageBus" in repr_str, (
            f"Expected class name in repr, got {repr_str!r}",
        )
        # __class__ must be the wrapper class, not the inner bus.
        from xruntime._infra._tenant_message_bus import (
            TenantAwareMessageBus,
        )

        assert bus.__class__ is TenantAwareMessageBus

    # ------------------------------------------------------------------
    # 4. Internal underscore-prefixed attributes are accessible
    # ------------------------------------------------------------------

    def test_internal_underscore_attrs_accessible(self) -> None:
        """Underscore-prefixed internal attributes (``_bus``,
        ``_prefix_template``, ``_k``) must remain accessible.
        """
        from unittest.mock import MagicMock

        bus = self._make_bus()

        # Direct attribute access (set in __init__).
        assert bus._bus is not None
        assert bus._prefix_template == "tenant:{tid}:"

        # Internal helper methods are callable.
        assert callable(bus._k)
        assert callable(bus._prefix)

        # Non-existent underscore attribute must still raise a
        # standard AttributeError (not silently forwarded to the
        # inner bus — that would leak the inner bus's private state).
        with pytest.raises(AttributeError):
            bus._does_not_exist

        # Sanity: the inner bus's MagicMock should NOT have been
        # touched for the missing underscore attribute.
        assert not bus._bus._does_not_exist.called  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 5. Regression guard: existing test suite still passes
    # ------------------------------------------------------------------

    def test_existing_test_suite_still_passes(self) -> None:
        """The existing ``test_tenant_aware_message_bus.py`` suite
        (minus the legacy delegation test, which was updated to
        match the new fail-closed behaviour) must still pass.

        Note: ``test_no_tenant_raises_error`` is a pre-existing
        failure caused by the autouse fixture in
        ``tests/xruntime/conftest.py`` setting
        ``current_tenant = "test-tenant"`` — unrelated to the
        ``__getattr__`` fix.  We deselect it here so the regression
        guard focuses on changes introduced by this fix.
        """
        repo_root = Path(__file__).resolve().parents[2]
        test_file = (
            repo_root
            / "tests"
            / "xruntime"
            / "test_tenant_aware_message_bus.py"
        )
        assert test_file.is_file(), (
            f"Cannot find existing test file at {test_file}",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(test_file),
                "-q",
                "-p",
                "no:cacheprovider",
                "--deselect",
                "tests/xruntime/test_tenant_aware_message_bus.py::"
                "TestTenantAwareMessageBus::test_no_tenant_raises_error",
            ],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )

        assert result.returncode == 0, (
            f"Existing test_tenant_aware_message_bus.py failed to "
            f"pass after the strict __getattr__ change.\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
        )
