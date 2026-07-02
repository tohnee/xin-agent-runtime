# -*- coding: utf-8 -*-
"""Pytest configuration — ensures src/ is on sys.path."""
import sys
from pathlib import Path

src = Path(__file__).parent.parent / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))


def pytest_addoption(parser):
    """Add --run-eval-online option for online evals."""
    parser.addoption(
        "--run-eval-online",
        action="store_true",
        default=False,
        help="Run online evals that require real API keys.",
    )


def pytest_collection_modifyitems(config, items):
    """Skip online evals unless --run-eval-online is passed."""
    if config.getoption("--run-eval-online"):
        return
    import pytest

    skip_online = pytest.mark.skip(
        reason="Need --run-eval-online option (requires API key)",
    )
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)
