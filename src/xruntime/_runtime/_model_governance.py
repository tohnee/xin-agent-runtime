# -*- coding: utf-8 -*-
"""Model governance — capability registry and router (M6).

Provides tenant model allowlists, capability-based selection,
fallback models, and cost/token budget enforcement hooks.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapability:
    """Capabilities of a model.

    Args:
        supports_tools (`bool`):
            Whether the model supports function/tool calling.
        supports_vision (`bool`):
            Whether the model supports image inputs.
        max_tokens (`int`):
            Maximum context window.
        cost_per_1k_input (`float`):
            Cost per 1K input tokens (USD).
        cost_per_1k_output (`float`):
            Cost per 1K output tokens (USD).
    """

    supports_tools: bool = False
    supports_vision: bool = False
    max_tokens: int = 0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0


class ModelCapabilityRegistry:
    """Registry of model capabilities.

    Models are registered with their capabilities so the
    :class:`ModelRouter` can select the right model for a task.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._capabilities: dict[str, ModelCapability] = {}

    def register(
        self,
        model_name: str,
        capability: ModelCapability,
    ) -> None:
        """Register a model's capabilities.

        Args:
            model_name (`str`): The model identifier.
            capability (`ModelCapability`): The model's capabilities.
        """
        self._capabilities[model_name] = capability

    def get(self, model_name: str) -> ModelCapability | None:
        """Return capabilities for a model, or None.

        Args:
            model_name (`str`): The model identifier.

        Returns:
            `ModelCapability | None`: The capabilities, or None.
        """
        return self._capabilities.get(model_name)

    def all_models(self) -> list[str]:
        """Return all registered model names."""
        return list(self._capabilities.keys())


class ModelRouter:
    """Selects a model based on capabilities and tenant allowlist.

    Args:
        registry (`ModelCapabilityRegistry`):
            The capability registry to consult.
    """

    def __init__(self, registry: ModelCapabilityRegistry) -> None:
        """Initialize the router."""
        self._registry = registry

    def select(
        self,
        candidates: list[str],
        requires_tools: bool = False,
        requires_vision: bool = False,
        tenant_allowlist: set[str] | None = None,
        fallback: str | None = None,
    ) -> str:
        """Select the best model from candidates.

        Args:
            candidates (`list[str]`):
                Ordered list of model preferences.
            requires_tools (`bool`):
                Whether the task requires tool calling.
            requires_vision (`bool`):
                Whether the task requires vision.
            tenant_allowlist (`set[str] | None`):
                Models the tenant is allowed to use. When provided,
                candidates not in this set are rejected.
            fallback (`str | None`):
                Fallback model when no candidate is available.

        Returns:
            `str`: The selected model name.

        Raises:
            `ValueError`: If no candidate is available and no
                fallback is provided.
        """
        for model in candidates:
            # Check tenant allowlist
            if tenant_allowlist is not None and model not in tenant_allowlist:
                raise ValueError(
                    f"Model '{model}' is not allowed by tenant " f"allowlist",
                )

            cap = self._registry.get(model)
            if cap is None:
                continue

            if requires_tools and not cap.supports_tools:
                continue
            if requires_vision and not cap.supports_vision:
                continue

            return model

        # Try fallback
        if fallback is not None:
            if (
                tenant_allowlist is not None
                and fallback not in tenant_allowlist
            ):
                raise ValueError(
                    f"Fallback model '{fallback}' is not allowed by "
                    f"tenant allowlist",
                )
            return fallback

        raise ValueError(
            f"No suitable model found among {candidates}",
        )
