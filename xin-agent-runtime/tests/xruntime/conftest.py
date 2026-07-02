# -*- coding: utf-8 -*-
"""Shared pytest fixtures for the xruntime test suite."""
import pytest

from xruntime._infra._tenant import current_tenant


@pytest.fixture(autouse=True)
def _seed_tenant_ctx() -> None:
    """Set ``current_tenant`` so the middleware_factory's fail-closed
    defense-in-depth check passes.

    The factory (per Task 1 fix in :mod:`xruntime._gateway._extension`)
    raises :class:`TenantIsolationError` when ``auth_enabled`` is True
    and ``current_tenant`` is unset. Many tests call the factory
    directly (bypassing the gateway handler that normally sets the
    contextvar), so we seed a test tenant here and clear it after to
    prevent cross-test leakage.

    Tests that need ``current_tenant`` unset (e.g. the fail-closed
    tests in ``test_extension_tenant_failclosed.py``) install their
    own autouse fixture that clears the contextvar after this fixture
    sets it — pytest runs higher-scope / conftest fixtures first, then
    test-file fixtures, so the test-file fixture's ``clear()`` wins.
    """
    current_tenant.set("test-tenant")
    yield
    current_tenant.clear()
