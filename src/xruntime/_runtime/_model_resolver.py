# -*- coding: utf-8 -*-
"""Model resolver — resolves model_config_name to real
CredentialBase + ChatModelBase instances.

This is the bridge that connects XRuntime's declarative config to
AgentScope's actual model providers.  It supports three resolution
sources (in priority order):

1. **Runtime registry** — pre-registered (model_name, credential)
   pairs passed at app construction time.
2. **Environment variables** — ``XRUNTIME_MODEL_PROVIDER`` +
   ``XRUNTIME_MODEL_API_KEY`` + ``XRUNTIME_MODEL_NAME``.
3. **Config file** — ``model_providers`` section in ``xruntime.yaml``.

Supported providers: ``anthropic``, ``openai``, ``dashscope``,
``deepseek``, ``moonshot``, ``ollama``, ``gemini``, ``xai``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Type

from .._config import XRuntimeConfig


@dataclass
class ModelProviderConfig:
    """A model provider declaration.

    Args:
        name (`str`):
            Provider name — ``"anthropic"``, ``"openai"``,
            ``"dashscope"``, etc.
        api_key (`str`):
            API key for this provider.
        model (`str`):
            Model name (e.g. ``"claude-sonnet-4-20250514"``).
        base_url (`str | None`):
            Optional custom base URL.
    """

    name: str
    api_key: str
    model: str
    base_url: str | None = None


@dataclass
class ModelResolution:
    """Result of resolving a model config name.

    Args:
        credential (`Any`):
            A ``CredentialBase`` instance.
        model_class (`type`):
            The ``ChatModelBase`` subclass.
        model_name (`str`):
            The model name string.
    """

    credential: Any
    model_class: type
    model_name: str


def _provider_registry() -> dict[str, tuple[Type[Any], Type[Any]]]:
    """Map provider names to (CredentialClass, ModelClass) pairs.

    Lazy-imports AS components to keep the module importable
    without optional deps.

    Returns:
        `dict[str, tuple[type, type]]`: Provider name →
        (credential class, model class).
    """
    from agentscope.credential import (
        AnthropicCredential,
        DashScopeCredential,
        DeepSeekCredential,
        GeminiCredential,
        MoonshotCredential,
        OllamaCredential,
        OpenAICredential,
        XAICredential,
    )
    from agentscope.model import (
        AnthropicChatModel,
        DashScopeChatModel,
        DeepSeekChatModel,
        GeminiChatModel,
        MoonshotChatModel,
        OllamaChatModel,
        OpenAIChatModel,
        XAIChatModel,
    )

    return {
        "anthropic": (AnthropicCredential, AnthropicChatModel),
        "openai": (OpenAICredential, OpenAIChatModel),
        "dashscope": (DashScopeCredential, DashScopeChatModel),
        "deepseek": (DeepSeekCredential, DeepSeekChatModel),
        "gemini": (GeminiCredential, GeminiChatModel),
        "moonshot": (MoonshotCredential, MoonshotChatModel),
        "ollama": (OllamaCredential, OllamaChatModel),
        "xai": (XAICredential, XAIChatModel),
    }


# Extensible provider → credential-class registry. Seeded with the
# built-in providers; :func:`register_provider` adds custom ones (used
# by tests and by deployments with custom ``CredentialBase`` subclasses).
_extra_credential_classes: dict[str, Type[Any]] = {}


def _credential_classes() -> dict[str, Type[Any]]:
    """Map provider names to ``CredentialBase`` subclasses.

    Merges the built-in providers with any registered via
    :func:`register_provider`. Lazy-imports the built-in credential
    classes to keep the module importable without optional deps.

    Returns:
        `dict[str, type]`: Provider name → credential class.
    """
    from agentscope.credential import (
        AnthropicCredential,
        DashScopeCredential,
        DeepSeekCredential,
        GeminiCredential,
        MoonshotCredential,
        OllamaCredential,
        OpenAICredential,
        XAICredential,
    )

    classes: dict[str, Type[Any]] = {
        "anthropic": AnthropicCredential,
        "openai": OpenAICredential,
        "dashscope": DashScopeCredential,
        "deepseek": DeepSeekCredential,
        "gemini": GeminiCredential,
        "moonshot": MoonshotCredential,
        "ollama": OllamaCredential,
        "xai": XAICredential,
    }
    classes.update(_extra_credential_classes)
    return classes


def register_provider(
    name: str,
    credential_cls: Type[Any],
) -> None:
    """Register a custom provider name → credential class mapping.

    Lets the gateway materialize credentials for custom
    :class:`~agentscope.credential.CredentialBase` subclasses (e.g. a
    mock for tests, or a private provider). The credential class must
    define a ``type`` discriminator field and implement
    ``get_chat_model_class``. Also register it with
    :class:`~agentscope.credential.CredentialFactory` so it can be
    rehydrated from storage.

    Args:
        name (`str`):
            Provider name (e.g. ``"mock_v2"``).
        credential_cls (`type`):
            The ``CredentialBase`` subclass.
    """
    _extra_credential_classes[name] = credential_cls


class ModelResolver:
    """Resolves model config names to real AS model + credential.

    Args:
        registry (`dict[str, ModelProviderConfig] | None`):
            Pre-registered providers, keyed by model config name.
    """

    def __init__(
        self,
        registry: dict[str, ModelProviderConfig] | None = None,
    ) -> None:
        """Initialize the resolver."""
        self._registry: dict[str, ModelProviderConfig] = registry or {}

    def register(
        self,
        config_name: str,
        provider: ModelProviderConfig,
    ) -> None:
        """Register a model provider under a config name.

        Args:
            config_name (`str`):
                The name used to reference this provider in
                agent blueprints.
            provider (`ModelProviderConfig`):
                The provider config.
        """
        self._registry[config_name] = provider

    def resolve(
        self,
        model_config_name: str | None,
        config: XRuntimeConfig | None = None,
    ) -> ModelResolution | None:
        """Resolve a model config name to credential + model class.

        Resolution order:
            1. Runtime registry
            2. Environment variables
            3. Config file model_providers

        Args:
            model_config_name (`str | None`):
                The config name to resolve.  ``None`` falls back
                to env vars.
            config (`XRuntimeConfig | None`):
                The XRuntime config for fallback resolution.

        Returns:
            `ModelResolution | None`: The resolution, or ``None``
            if no provider could be resolved.
        """
        provider_config = self._resolve_source(
            model_config_name,
            config,
        )
        if provider_config is None:
            return None

        return self._build_resolution(provider_config)

    def resolve_provider(
        self,
        model_config_name: str | None,
        config: XRuntimeConfig | None = None,
    ) -> ModelProviderConfig | None:
        """Resolve a model config name to a provider config.

        Same resolution order as :meth:`resolve` (runtime registry →
        env vars → config file) but returns the raw
        :class:`ModelProviderConfig` rather than a built model. Used by
        the gateway to persist a credential and build a
        ``ChatModelConfig``.

        Args:
            model_config_name (`str | None`):
                The config name to resolve. ``None`` falls back to env
                vars.
            config (`XRuntimeConfig | None`):
                The XRuntime config for fallback resolution.

        Returns:
            `ModelProviderConfig | None`: The provider config, or
            ``None`` if no provider could be resolved.
        """
        return self._resolve_source(model_config_name, config)

    def build_credential(
        self,
        provider: ModelProviderConfig,
    ) -> Any:
        """Build a ``CredentialBase`` instance for a provider config.

        Only fields the credential class declares are passed, so
        providers without ``base_url`` / ``api_key`` (e.g. Ollama's
        ``host``) do not raise. The returned instance carries its
        ``type`` discriminator and a generated ``id``; the gateway
        persists it via ``storage.upsert_credential``.

        Args:
            provider (`ModelProviderConfig`):
                The provider config.

        Returns:
            `CredentialBase`: A credential instance.

        Raises:
            ValueError: If the provider name is unsupported.
        """
        classes = _credential_classes()
        cred_cls = classes.get(provider.name)
        if cred_cls is None:
            raise ValueError(
                f"Unsupported model provider: {provider.name}. "
                f"Supported: {list(classes.keys())}",
            )
        kwargs = self._credential_kwargs(cred_cls, provider)
        return cred_cls(**kwargs)

    @staticmethod
    def credential_type(provider_name: str) -> str:
        """Return the credential ``type`` discriminator for a provider.

        Used by the gateway to build a ``ChatModelConfig.type`` without
        instantiating the credential (e.g. on the cached-credential
        path).

        Args:
            provider_name (`str`):
                The provider name (e.g. ``"openai"``).

        Returns:
            `str`: The credential type discriminator (e.g.
            ``"openai_credential"``).

        Raises:
            ValueError: If the provider name is unsupported.
        """
        classes = _credential_classes()
        cred_cls = classes.get(provider_name)
        if cred_cls is None:
            raise ValueError(
                f"Unsupported model provider: {provider_name}. "
                f"Supported: {list(classes.keys())}",
            )
        return cred_cls.model_fields["type"].default

    @staticmethod
    def _credential_kwargs(
        cred_cls: Type[Any],
        provider: ModelProviderConfig,
    ) -> dict[str, Any]:
        """Build constructor kwargs for a credential class.

        Maps the provider config's ``api_key`` / ``base_url`` onto the
        fields the credential class actually declares (Ollama uses
        ``host``, XAI uses ``api_host``).

        Args:
            cred_cls (`type`):
                The credential class.
            provider (`ModelProviderConfig`):
                The provider config.

        Returns:
            `dict[str, Any]`: Constructor kwargs.
        """
        fields = set(getattr(cred_cls, "model_fields", {}))
        kwargs: dict[str, Any] = {}
        if provider.api_key and "api_key" in fields:
            kwargs["api_key"] = provider.api_key
        if provider.base_url:
            if "base_url" in fields:
                kwargs["base_url"] = provider.base_url
            elif "host" in fields:
                kwargs["host"] = provider.base_url
            elif "api_host" in fields:
                kwargs["api_host"] = provider.base_url
        return kwargs

    def _resolve_source(
        self,
        model_config_name: str | None,
        config: XRuntimeConfig | None,
    ) -> ModelProviderConfig | None:
        """Find the provider config from registry, env, or file.

        Args:
            model_config_name (`str | None`):
                The config name.
            config (`XRuntimeConfig | None`):
                The XRuntime config.

        Returns:
            `ModelProviderConfig | None`: The provider config.
        """
        # 1. Runtime registry
        if model_config_name and model_config_name in self._registry:
            return self._registry[model_config_name]

        # 2. Environment variables
        env_provider = os.environ.get("XRUNTIME_MODEL_PROVIDER", "")
        env_key = os.environ.get("XRUNTIME_MODEL_API_KEY", "")
        env_model = os.environ.get(
            "XRUNTIME_MODEL_NAME",
            model_config_name or "",
        )
        if env_provider and env_key:
            return ModelProviderConfig(
                name=env_provider,
                api_key=env_key,
                model=env_model,
                base_url=os.environ.get("XRUNTIME_MODEL_BASE_URL") or None,
            )

        # 3. Config file
        if config and config.model_providers:
            for name, prov in config.model_providers.items():
                if model_config_name and model_config_name == name:
                    return ModelProviderConfig(
                        name=prov.get("name", name),
                        api_key=prov.get("api_key", ""),
                        model=prov.get(
                            "model",
                            model_config_name,
                        ),
                        base_url=prov.get("base_url"),
                    )

        return None

    def _build_resolution(
        self,
        provider: ModelProviderConfig,
    ) -> ModelResolution:
        """Build a ModelResolution from a provider config.

        Args:
            provider (`ModelProviderConfig`):
                The provider config.

        Returns:
            `ModelResolution`: The resolution with credential
            and model class instances.

        Raises:
            ValueError: If the provider name is not supported.
        """
        registry = _provider_registry()
        pair = registry.get(provider.name)
        if pair is None:
            raise ValueError(
                f"Unsupported model provider: {provider.name}. "
                f"Supported: {list(registry.keys())}",
            )

        cred_cls, model_cls = pair

        credential = self._build_credential(
            cred_cls,
            provider,
        )

        return ModelResolution(
            credential=credential,
            model_class=model_cls,
            model_name=provider.model,
        )

    def _build_credential(
        self,
        cred_cls: Type[Any],
        provider: ModelProviderConfig,
    ) -> Any:
        """Build a credential instance from a provider config.

        Args:
            cred_cls (`type`):
                The credential class.
            provider (`ModelProviderConfig`):
                The provider config.

        Returns:
            `Any`: A credential instance.
        """
        kwargs: dict[str, Any] = {
            "api_key": provider.api_key,
        }
        if provider.base_url:
            kwargs["base_url"] = provider.base_url

        return cred_cls(**kwargs)

    def build_model(
        self,
        resolution: ModelResolution,
        stream: bool = True,
    ) -> Any:
        """Build a ChatModelBase instance from a resolution.

        Args:
            resolution (`ModelResolution`):
                The model resolution.
            stream (`bool`):
                Whether to enable streaming.

        Returns:
            `Any`: A ChatModelBase instance.
        """
        Parameters = resolution.model_class.Parameters

        model = resolution.model_class(
            credential=resolution.credential,
            model=resolution.model_name,
            parameters=Parameters(),
            stream=stream,
        )
        return model
