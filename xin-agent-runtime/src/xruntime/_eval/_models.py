# -*- coding: utf-8 -*-
"""Data models for the Evals framework.

Pure dataclasses with no business logic — the Runner / Context /
Reporter modules operate on these.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class EvalStatus(str, enum.Enum):
    """Final status of a single eval run.

    Values:
        PASSED: All assertions passed.
        FAILED: One or more assertions failed (no uncaught exception).
        ERROR: Uncaught exception during eval execution.
        SKIPPED: Eval was filtered out / skipped.
    """

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class AssertionResult:
    """One assertion outcome inside an eval.

    Args:
        name (`str`): Human-readable assertion name.
        passed (`bool`): Whether the assertion held.
        message (`str`): Failure message (empty when passed).
        evidence (`dict`): Captured evidence for debugging.
    """

    name: str
    passed: bool
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Aggregate result of one eval.

    Args:
        eval_id (`str`): Unique id (``domain.name``).
        description (`str`): Human-readable spec.
        status (`EvalStatus`): Final status.
        assertions (`list`): Per-assertion outcomes.
        trace (`dict`): AgentEvent stream + middleware snapshot.
        duration_ms (`int`): Wall-clock duration.
    """

    eval_id: str
    description: str
    status: EvalStatus
    assertions: list[AssertionResult] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class EvalSpec:
    """Static spec of an eval, collected before running.

    Args:
        eval_id (`str`): ``domain.name`` identifier.
        description (`str`): Spec text shown in reports.
        domain (`str`): Grouping key (e.g. ``security``).
        tags (`list`): ``offline`` / ``online`` / ``security``.
        fn (`Callable`): The decorated async function.
    """

    eval_id: str
    description: str
    domain: str
    tags: list[str]
    fn: Callable[[Any], Awaitable[None]]
