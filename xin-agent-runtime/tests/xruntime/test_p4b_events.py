# -*- coding: utf-8 -*-
"""TDD tests for P4-B: EventBus + WorkflowRegistry + Canary."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xruntime._runtime._orchestrator import (
    Workflow,
    WorkflowStep,
)
from xruntime._runtime._workflow._events import (
    EventBus,
    EventTrigger,
)
from xruntime._runtime._workflow._registry import WorkflowRegistry
from xruntime._runtime._workflow._canary import CanaryDeployment


def _wf(wf_id: str = "wf") -> Workflow:
    return Workflow(
        id=wf_id,
        name=wf_id,
        steps=[WorkflowStep(id="s1", name="S1", agent="a", prompt="p")],
    )


class TestEventBus:
    """EventBus — publish/subscribe."""

    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self) -> None:
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe("evt", lambda d: _append(received, d))
        await bus.publish("evt", {"x": 1})
        assert len(received) == 1
        assert received[0]["x"] == 1

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self) -> None:
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe("evt", lambda d: _append(r1, d))
        bus.subscribe("evt", lambda d: _append(r2, d))
        count = await bus.publish("evt", {"v": 42})
        assert count == 2
        assert len(r1) == 1
        assert len(r2) == 1

    @pytest.mark.asyncio
    async def test_handler_exception_isolation(self) -> None:
        bus = EventBus()
        received: list[dict] = []

        async def bad(d: dict) -> None:
            raise RuntimeError("boom")

        bus.subscribe("evt", bad)
        bus.subscribe("evt", lambda d: _append(received, d))
        count = await bus.publish("evt", {})
        assert count == 2
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self) -> None:
        bus = EventBus()
        received: list[dict] = []
        handler = lambda d: _append(received, d)  # noqa: E731
        bus.subscribe("evt", handler)
        assert bus.unsubscribe("evt", handler) is True
        await bus.publish("evt", {})
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_returns_false(self) -> None:
        bus = EventBus()
        assert bus.unsubscribe("nope", lambda d: None) is False

    @pytest.mark.asyncio
    async def test_no_subscribers_returns_zero(self) -> None:
        bus = EventBus()
        count = await bus.publish("nope", {})
        assert count == 0

    def test_subscriber_count(self) -> None:
        bus = EventBus()
        bus.subscribe("a", lambda d: None)
        bus.subscribe("a", lambda d: None)
        bus.subscribe("b", lambda d: None)
        assert bus.subscriber_count == 3


class TestEventTrigger:
    """EventTrigger — fires on event."""

    @pytest.mark.asyncio
    async def test_trigger_fires(self) -> None:
        bus = EventBus()
        triggered: list[dict] = []

        async def on_fire(data: dict) -> None:
            triggered.append(data)

        trigger = EventTrigger(
            "github.pr.opened",
            "review-pr",
            bus,
            on_fire,
        )
        await bus.publish("github.pr.opened", {"pr": 123})
        assert trigger.fired_count == 1
        assert triggered[0]["pr"] == 123

    @pytest.mark.asyncio
    async def test_trigger_workflow_id(self) -> None:
        bus = EventBus()
        trigger = EventTrigger("evt", "wf-1", bus)
        assert trigger.workflow_id == "wf-1"
        await bus.publish("evt", {})
        assert trigger.fired_count == 1


class TestWorkflowRegistry:
    """WorkflowRegistry — version management."""

    def test_register_and_get(self) -> None:
        reg = WorkflowRegistry()
        wf = _wf("review-pr")
        reg.register(wf, version="v1")
        got = reg.get("review-pr")
        assert got is not None
        assert got.id == "review-pr"

    def test_get_specific_version(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1")
        reg.register(_wf("wf"), version="v2")
        assert reg.get("wf", "v1") is not None
        assert reg.get("wf", "v2") is not None

    def test_get_default_version(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        assert reg.get_default_version("wf") == "v1"

    def test_set_default(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        assert reg.set_default("wf", "v2") is True
        assert reg.get_default_version("wf") == "v2"

    def test_set_default_unknown_version(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1")
        assert reg.set_default("wf", "v99") is False

    def test_list_versions(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1")
        reg.register(_wf("wf"), version="v2")
        versions = reg.list_versions("wf")
        assert set(versions) == {"v1", "v2"}

    def test_get_unknown_workflow(self) -> None:
        reg = WorkflowRegistry()
        assert reg.get("nonexistent") is None

    def test_workflow_count(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf1"), version="v1")
        reg.register(_wf("wf2"), version="v1")
        assert reg.workflow_count == 2


class TestCanaryDeployment:
    """CanaryDeployment — canary routing."""

    def test_zero_percent_returns_stable(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        canary = CanaryDeployment(
            reg,
            "wf",
            canary_version="v2",
            stable_version="v1",
            canary_percent=0,
        )
        assert canary.select_version() == "v1"

    def test_hundred_percent_returns_canary(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        canary = CanaryDeployment(
            reg,
            "wf",
            canary_version="v2",
            stable_version="v1",
            canary_percent=100,
        )
        assert canary.select_version() == "v2"

    def test_set_canary_percent(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        canary = CanaryDeployment(
            reg,
            "wf",
            canary_version="v2",
            stable_version="v1",
        )
        assert canary.canary_percent == 0.0
        canary.set_canary_percent(50)
        assert canary.canary_percent == 50.0

    def test_fifty_percent_routes_to_either(self) -> None:
        """50% canary routes to both versions over many calls."""
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        canary = CanaryDeployment(
            reg,
            "wf",
            canary_version="v2",
            stable_version="v1",
            canary_percent=50,
        )
        results = {canary.select_version() for _ in range(100)}
        assert "v1" in results
        assert "v2" in results

    def test_promote_to_stable(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        canary = CanaryDeployment(
            reg,
            "wf",
            canary_version="v2",
            stable_version="v1",
            canary_percent=50,
        )
        assert canary.promote_to_stable() is True
        assert reg.get_default_version("wf") == "v2"
        assert canary.canary_percent == 0

    def test_rollback(self) -> None:
        reg = WorkflowRegistry()
        reg.register(_wf("wf"), version="v1", default=True)
        reg.register(_wf("wf"), version="v2")
        canary = CanaryDeployment(
            reg,
            "wf",
            canary_version="v2",
            stable_version="v1",
            canary_percent=50,
        )
        canary.rollback()
        assert canary.canary_percent == 0
        assert reg.get_default_version("wf") == "v1"


async def _append(lst: list, item: Any) -> None:
    lst.append(item)
