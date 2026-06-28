# -*- coding: utf-8 -*-
"""Multi-model router — task complexity-aware model selection.

Routes tasks to different models based on complexity classification
and cost optimization heuristics. Configured via XRuntimeConfig.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable

logger = logging.getLogger("xruntime.model_router")


class ComplexityLevel(Enum):
    """Task complexity classification."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    RESEARCH = "research"


@dataclass
class ModelAssignment:
    """Result of model assignment."""

    model_name: str
    complexity: ComplexityLevel
    confidence: float
    reason: str


class MultiModelRouter:
    """Routes tasks to models based on complexity.

    Uses heuristic classification based on task length, keywords,
    and type. Can be extended with LLM-based classification.

    Args:
        model_tiers (`dict[str, list[str]]`):
            Map of complexity level to list of model names.
            Example: ``{"simple": ["glm-4-flash"], "medium": ["glm-4"],
            "complex": ["glm-5.2"], "research": ["glm-5.2"]}``
        default_level (`ComplexityLevel`):
            Default complexity when classification is uncertain.
    """

    # Keywords indicating higher complexity
    COMPLEX_KEYWORDS = {
        "research",
        "analysis",
        "debug",
        "optimize",
        "architecture",
        "design",
        "review",
        "implement",
        "build",
        "refactor",
        "complex",
        "hard",
        "difficult",
        "multi-step",
        "multi task",
        "deep dive",
        "comprehensive",
    }

    MEDIUM_KEYWORDS = {
        "explain",
        "describe",
        "summarize",
        "translate",
        "convert",
        "format",
        "list",
        "compare",
        "medium",
    }

    SIMPLE_KEYWORDS = {
        "hello",
        "hi",
        "ping",
        "what",
        "when",
        "where",
        "who",
        "simple",
        "easy",
        "quick",
        "yes",
        "no",
        "confirm",
    }

    def __init__(
        self,
        model_tiers: dict[str, list[str]] | None = None,
        default_level: ComplexityLevel = ComplexityLevel.MEDIUM,
    ) -> None:
        """Initialize the router."""
        if model_tiers is None:
            model_tiers = {
                "simple": ["glm-4-flash"],
                "medium": ["glm-4"],
                "complex": ["glm-5.2"],
                "research": ["glm-5.2"],
            }
        self._model_tiers = {
            ComplexityLevel(k): v for k, v in model_tiers.items()
        }
        self._default_level = default_level
        self._callbacks: list[Callable[[ModelAssignment], None]] = []
        logger.debug(
            "MultiModelRouter initialized with tiers: %s",
            {k.value: v for k, v in self._model_tiers.items()},
        )

    def register_callback(
        self,
        callback: Callable[[ModelAssignment], None],
    ) -> None:
        """Register a callback for assignment decisions.

        Args:
            callback: Function called with each assignment.
        """
        self._callbacks.append(callback)

    def classify(self, prompt: str) -> tuple[ComplexityLevel, float]:
        """Classify task complexity.

        Args:
            prompt: The task prompt.

        Returns:
            ``tuple[ComplexityLevel, float]``: Level and confidence.
        """
        prompt_lower = prompt.lower()
        prompt_len = len(prompt)
        scores: dict[ComplexityLevel, float] = {
            ComplexityLevel.SIMPLE: 0.0,
            ComplexityLevel.MEDIUM: 0.0,
            ComplexityLevel.COMPLEX: 0.0,
            ComplexityLevel.RESEARCH: 0.0,
        }

        # Length-based scoring
        if prompt_len < 40:
            scores[ComplexityLevel.SIMPLE] += 0.5
        elif prompt_len < 200:
            scores[ComplexityLevel.MEDIUM] += 0.4
        else:
            scores[ComplexityLevel.COMPLEX] += 0.4

        # Keyword matching
        simple_matches = sum(
            1 for k in self.SIMPLE_KEYWORDS if k in prompt_lower
        )
        medium_matches = sum(
            1 for k in self.MEDIUM_KEYWORDS if k in prompt_lower
        )
        complex_matches = sum(
            1 for k in self.COMPLEX_KEYWORDS if k in prompt_lower
        )

        scores[ComplexityLevel.SIMPLE] += min(simple_matches * 0.1, 0.5)
        scores[ComplexityLevel.MEDIUM] += min(medium_matches * 0.2, 0.7)
        scores[ComplexityLevel.COMPLEX] += min(complex_matches * 0.25, 0.9)

        # Code-related indicators
        if any(
            k in prompt_lower for k in ["```", "def ", "class ", "function"]
        ):
            scores[ComplexityLevel.COMPLEX] += 0.3

        # Research indicators
        if "research" in prompt_lower or "investigate" in prompt_lower:
            scores[ComplexityLevel.RESEARCH] += 0.5

        # Multi-step indicators
        step_matches = len(
            re.findall(r"step\s*\d|first|second|then", prompt_lower)
        )
        if step_matches >= 3:
            scores[ComplexityLevel.COMPLEX] += 0.2

        # Pick highest score
        level = max(scores.keys(), key=lambda k: scores[k])
        confidence = min(scores[level] / max(scores.values()), 1.0)

        logger.debug(
            "Classified task: %s (confidence=%.2f, len=%d)",
            level.value,
            confidence,
            prompt_len,
        )
        return level, confidence

    def assign(
        self,
        prompt: str,
        preferred_model: str | None = None,
    ) -> ModelAssignment:
        """Assign a model to a task.

        Args:
            prompt: The task prompt.
            preferred_model: Optional preferred model override.

        Returns:
            ``ModelAssignment``: Assignment result.
        """
        if preferred_model is not None:
            # User-specified model
            level, conf = self.classify(prompt)
            result = ModelAssignment(
                model_name=preferred_model,
                complexity=level,
                confidence=1.0,
                reason="user-specified",
            )
        else:
            level, conf = self.classify(prompt)
            models = self._model_tiers.get(level, [])
            if not models:
                level = self._default_level
                models = self._model_tiers[level]
            result = ModelAssignment(
                model_name=models[0],
                complexity=level,
                confidence=conf,
                reason=f"classified as {level.value}",
            )

        for cb in self._callbacks:
            cb(result)

        logger.info(
            "Assigned model '%s' (level=%s, confidence=%.2f, reason=%s)",
            result.model_name,
            result.complexity.value,
            result.confidence,
            result.reason,
        )
        return result

    def get_available_models(self) -> list[str]:
        """Get all available model names."""
        result: list[str] = []
        for models in self._model_tiers.values():
            result.extend(models)
        return list(set(result))
