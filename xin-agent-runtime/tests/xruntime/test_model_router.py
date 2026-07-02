# -*- coding: utf-8 -*-
"""Tests for MultiModelRouter."""
from __future__ import annotations

from xruntime._runtime._model_router import (
    ComplexityLevel,
    MultiModelRouter,
)


class TestMultiModelRouter:
    """MultiModelRouter tests."""

    def test_init_default_tiers(self) -> None:
        """Default tiers are set up correctly."""
        router = MultiModelRouter()
        models = router.get_available_models()
        assert "glm-4-flash" in models
        assert "glm-5.2" in models

    def test_classify_simple_hello(self) -> None:
        """Short hello is classified as simple."""
        router = MultiModelRouter()
        level, conf = router.classify("Hello, how are you?")
        assert level == ComplexityLevel.SIMPLE
        assert conf > 0.3

    def test_classify_medium_explain(self) -> None:
        """Explain is medium."""
        router = MultiModelRouter()
        level, conf = router.classify(
            "Explain how a neural network works in detail",
        )
        assert level in (ComplexityLevel.MEDIUM, ComplexityLevel.COMPLEX)

    def test_classify_complex_research(self) -> None:
        """Research task is complex/research."""
        router = MultiModelRouter()
        level, conf = router.classify(
            "Research and implement a distributed database "
            "system with sharding and replication support",
        )
        assert level in (ComplexityLevel.COMPLEX, ComplexityLevel.RESEARCH)

    def test_classify_code_task(self) -> None:
        """Code-related is complex."""
        router = MultiModelRouter()
        level, conf = router.classify(
            "Write a Python class that implements a "
            "thread-safe cache with TTL support```",
        )
        assert level == ComplexityLevel.COMPLEX

    def test_assign_simple(self) -> None:
        """Simple task gets simple model."""
        router = MultiModelRouter(
            model_tiers={
                "simple": ["cheap-model"],
                "medium": ["medium-model"],
                "complex": ["expensive-model"],
                "research": ["research-model"],
            },
        )
        result = router.assign("Hello")
        assert result.model_name == "cheap-model"
        assert result.complexity == ComplexityLevel.SIMPLE

    def test_assign_preferred_model(self) -> None:
        """Preferred model overrides classification."""
        router = MultiModelRouter()
        result = router.assign("Hello", preferred_model="custom-model")
        assert result.model_name == "custom-model"

    def test_callback_called(self) -> None:
        """Callback is called on assignment."""
        results = []

        def callback(assignment):
            results.append(assignment)

        router = MultiModelRouter()
        router.register_callback(callback)
        router.assign("Hello")
        assert len(results) == 1
        assert results[0].complexity == ComplexityLevel.SIMPLE

    def test_get_available_models(self) -> None:
        """get_available_models returns unique set."""
        router = MultiModelRouter(
            model_tiers={
                "simple": ["a", "b"],
                "medium": ["b", "c"],
                "complex": ["d"],
                "research": ["d"],
            },
        )
        models = set(router.get_available_models())
        assert models == {"a", "b", "c", "d"}

    def test_assignment_has_reason(self) -> None:
        """Assignment has reason field."""
        router = MultiModelRouter()
        result = router.assign("Hello")
        assert len(result.reason) > 0
        assert "classified" in result.reason

    def test_assignment_confidence(self) -> None:
        """Assignment has confidence between 0 and 1."""
        router = MultiModelRouter()
        result = router.assign("Hello")
        assert 0.0 <= result.confidence <= 1.0
