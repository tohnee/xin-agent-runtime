# -*- coding: utf-8 -*-
"""Knowledge base adapter — protocol bridge for external systems.

An :class:`KnowledgeAdapter` wraps a specific external knowledge
system (llm-wiki, ChromaDB, Pinecone, Neo4j, etc.) and exposes it
through the :class:`KnowledgeBaseBase` contract.

Adapters are registered with :class:`KnowledgeAdapterFactory` so
they can be discovered by backend name from configuration.
"""
from __future__ import annotations

from typing import Callable

from ._base import KnowledgeBaseBase, KnowledgeBaseConfig


class KnowledgeAdapter(KnowledgeBaseBase):
    """Abstract adapter — bridges an external KB system.

    Subclasses implement the backend-specific logic for ingestion,
    compilation, and retrieval against their backing store (a vector
    DB, a wiki compiler, a graph DB, etc.).

    The adapter is responsible for:

    - Translating ``KnowledgeSource`` into the backend's native
      document format.
    - Translating ``KnowledgeQuery`` into the backend's native
      query format.
    - Translating the backend's response into ``KnowledgeChunk``
      and ``KnowledgeResult`` objects.
    - Managing the backend connection lifecycle.

    Args:
        config (`KnowledgeBaseConfig`):
            The knowledge base configuration.
    """

    @classmethod
    def backend_name(cls) -> str:
        """Return the backend name this adapter handles.

        Returns:
            `str`: The backend identifier (e.g. ``"llm_wiki"``).
        """
        raise NotImplementedError


class KnowledgeAdapterFactory:
    """Registry of knowledge base adapter constructors.

    Adapters register themselves by backend name; the factory
    instantiates the correct adapter from a config.

    Usage::

        factory = KnowledgeAdapterFactory()

        @factory.register("llm_wiki")
        class LlmWikiAdapter(KnowledgeAdapter):
            ...

        kb = factory.create(KnowledgeBaseConfig(backend="llm_wiki"))
    """

    def __init__(self) -> None:
        """Initialize the factory."""
        self._builders: dict[
            str,
            Callable[[KnowledgeBaseConfig], KnowledgeAdapter],
        ] = {}

    def register(
        self,
        backend: str,
    ) -> Callable[[type[KnowledgeAdapter]], type[KnowledgeAdapter]]:
        """Decorator to register an adapter class.

        Args:
            backend (`str`):
                The backend name this adapter handles.

        Returns:
            `callable`: Class decorator.
        """

        def decorator(
            cls: type[KnowledgeAdapter],
        ) -> type[KnowledgeAdapter]:
            if not issubclass(cls, KnowledgeAdapter):
                raise TypeError(
                    f"{cls.__name__} must inherit " f"KnowledgeAdapter",
                )
            self._builders[backend] = cls
            return cls

        return decorator

    def create(
        self,
        config: KnowledgeBaseConfig,
    ) -> KnowledgeAdapter:
        """Create an adapter instance from config.

        Args:
            config (`KnowledgeBaseConfig`):
                The config whose ``backend`` field selects the
                adapter.

        Returns:
            `KnowledgeAdapter`: The instantiated adapter.

        Raises:
            ValueError: If no adapter is registered for the backend.
        """
        builder = self._builders.get(config.backend)
        if builder is None:
            raise ValueError(
                f"No knowledge adapter registered for backend "
                f"'{config.backend}'. Registered: "
                f"{list(self._builders.keys())}",
            )
        return builder(config)

    @property
    def registered_backends(self) -> list[str]:
        """List registered backend names.

        Returns:
            `list[str]`: Registered backend names.
        """
        return list(self._builders.keys())


_default_factory = KnowledgeAdapterFactory()


def get_default_factory() -> KnowledgeAdapterFactory:
    """Return the process-wide default factory.

    Returns:
        `KnowledgeAdapterFactory`: The default factory.
    """
    return _default_factory
