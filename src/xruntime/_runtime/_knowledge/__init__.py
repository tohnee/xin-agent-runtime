# -*- coding: utf-8 -*-
"""Knowledge base framework for XRuntime.

Provides a pluggable abstraction for integrating external knowledge
sources (RAG, LLM-Wiki, vector stores, knowledge graphs) into the
agent runtime.

Architecture — three layers:

1. **KnowledgeBaseBase** (ABC) — the storage/retrieval contract.
   Concrete backends implement ``ingest``, ``compile``, ``retrieve``.
2. **KnowledgeAdapter** (ABC) — protocol bridge between a specific
   external system (e.g. llm-wiki, chromadb) and the base contract.
   Adapters handle ingestion, compilation, and retrieval against
   their backing store.
3. **KnowledgeMiddleware** — AS ``MiddlewareBase`` subclass that
   auto-injects retrieved knowledge into agent context before each
   reply (analogous to mem0's ``static_control`` mode).

Usage::

    from xruntime._runtime._knowledge import (
        KnowledgeBaseConfig,
        KnowledgeRegistry,
        KnowledgeMiddleware,
    )

    # 1. Configure
    kb_config = KnowledgeBaseConfig(
        backend="llm_wiki",
        raw_dir="/data/kb-raw",
        compiled_dir="/data/kb-compiled",
    )

    # 2. Register adapter (or use auto-discovery)
    registry = KnowledgeRegistry()
    registry.register(MyLlmWikiAdapter(config=kb_config))

    # 3. Ingest documents
    await registry.ingest("my-doc", content="...")

    # 4. Compile (llm-wiki AOT pattern)
    await registry.compile()

    # 5. Use in middleware (auto-inject into agent context)
    mw = KnowledgeMiddleware(registry=registry)

Design reference:
https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2
"""
from ._base import (
    KnowledgeBaseBase,
    KnowledgeBaseConfig,
    KnowledgeChunk,
    KnowledgeQuery,
    KnowledgeResult,
    KnowledgeSource,
)
from ._adapter import KnowledgeAdapter, KnowledgeAdapterFactory
from ._registry import KnowledgeRegistry
from ._middleware import KnowledgeMiddleware
from ._tools import SearchKnowledgeTool, IngestKnowledgeTool

__all__ = [
    "KnowledgeBaseBase",
    "KnowledgeBaseConfig",
    "KnowledgeChunk",
    "KnowledgeQuery",
    "KnowledgeResult",
    "KnowledgeSource",
    "KnowledgeAdapter",
    "KnowledgeAdapterFactory",
    "KnowledgeRegistry",
    "KnowledgeMiddleware",
    "SearchKnowledgeTool",
    "IngestKnowledgeTool",
]
