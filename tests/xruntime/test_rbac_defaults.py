# -*- coding: utf-8 -*-
"""Tests for XRuntime default enterprise RBAC wiring."""

import pytest

from xruntime._config import XRuntimeConfig
from xruntime._gateway._mw_state import MiddlewareStateCache

pytestmark = pytest.mark.anyio


async def test_default_enterprise_role_is_viewer_not_admin() -> None:
    """The shared RBAC middleware should default to least privilege."""
    cache = MiddlewareStateCache(XRuntimeConfig(), tenant_id="acme")
    rbac = await cache.get_rbac_middleware()

    assert "viewer" in rbac.roles
    assert rbac.default_role == "viewer"
    assert rbac.check_tool("unassigned-session", "Read") == "allow"
    assert rbac.check_tool("unassigned-session", "Bash") == "deny"


async def test_configured_default_role_can_be_contributor() -> None:
    """Operators may opt into a broader default role explicitly."""
    cfg = XRuntimeConfig.model_validate(
        {
            "permission": {
                "default_role": "contributor",
            },
        },
    )
    cache = MiddlewareStateCache(cfg, tenant_id="acme")
    rbac = await cache.get_rbac_middleware()

    assert rbac.default_role == "contributor"
    assert rbac.check_tool("unassigned-session", "Read") == "allow"
    assert rbac.check_tool("unassigned-session", "Write") == "allow"
    assert rbac.check_tool("unassigned-session", "Bash") == "deny"
