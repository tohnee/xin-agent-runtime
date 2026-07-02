# -*- coding: utf-8 -*-
"""E2E test configuration — markers and fixtures.

E2E tests require real API keys and are skipped by default.
Run with: ``pytest --run-e2e`` to enable.
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --run-e2e option."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run E2E tests that require real API keys.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip E2E tests unless --run-e2e is passed."""
    if config.getoption("--run-e2e"):
        return
    skip_e2e = pytest.mark.skip(
        reason="Need --run-e2e option to run (requires API key)",
    )
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


@pytest.fixture
def ark_api_key() -> str:
    """Ark API key from environment.

    Fail-closed: no hardcoded fallback. E2E tests that need a real
    key must set the ``ARK_API_KEY`` environment variable; otherwise
    the fixture returns an empty string and tests should skip
    themselves.
    """
    import os

    key = os.environ.get("ARK_API_KEY", "")
    if not key:
        pytest.skip(
            "ARK_API_KEY not set; skipping E2E test that requires "
            "a real Ark API key.",
        )
    return key
