# -*- coding: utf-8 -*-
"""Online eval — requires a real LLM API key.

This eval is skipped by default.  Run with::

    python3 -m xruntime.eval run --evals-dir tests/evals --tags online

Or via pytest::

    pytest tests/evals/ --run-eval-online
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from xruntime._eval import define_eval
from xruntime._eval._models import AssertionResult

pytestmark = pytest.mark.online


@define_eval(
    "Real LLM responds with non-empty reply (requires ARK_API_KEY)",
    domain="online",
    tags=("online",),
)
async def real_llm_non_empty_reply(t: Any) -> None:
    """Send a simple greeting to the real LLM and verify the reply
    is non-empty and contains expected keywords."""
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        t._results.append(
            AssertionResult(
                name="api_key_available",
                passed=False,
                message="ARK_API_KEY not set; cannot run online eval",
            ),
        )
        return

    # Use the InProcessTarget with real model config
    from xruntime._eval._target_inproc import InProcessTarget

    target = InProcessTarget()
    os.environ.setdefault("XRUNTIME_MODEL_PROVIDER", "dashscope")
    os.environ.setdefault("XRUNTIME_MODEL_API_KEY", api_key)
    os.environ.setdefault("XRUNTIME_MODEL_NAME", "doubao-pro-32k")

    await target.setup()
    t._runner = target
    await t.send("Hello, please respond with the word 'OK'.")
    t.reply_contains("OK")
