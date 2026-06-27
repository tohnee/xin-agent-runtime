# -*- coding: utf-8 -*-
"""Shared middleware state cache for XRuntime.

The AS middleware factory ``(user_id, agent_id, session_id) -> list``
is invoked once per agent turn.  Without a shared state cache, every
turn would rebuild fresh middleware instances — resetting quota
counters and discarding audit log sinks.

This module provides :class:`MiddlewareStateCache`, which keeps
quota trackers, audit loggers, and RBAC middleware instances alive
across turns, keyed by ``(tenant_id, user_id, session_id)``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .._config import XRuntimeConfig


class MiddlewareStateCache:
    """Cache that keeps middleware state alive across turns.

    Quota trackers are keyed by session id so that token / tool-call
    / cost usage accumulates across all turns within a session.  The
    audit logger is a singleton scoped to the cache (one per tenant).
    The RBAC middleware is also a singleton so role assignments
    persist.

    Args:
        config (`XRuntimeConfig`):
            The runtime config — drives audit sink selection and
            quota defaults.
        tenant_id (`str`):
            The tenant this cache belongs to.
    """

    def __init__(
        self,
        config: XRuntimeConfig,
        tenant_id: str = "default",
    ) -> None:
        """Initialize the cache."""
        self._config = config
        self._tenant_id = tenant_id
        self._quota_trackers: dict[str, Any] = {}
        self._audit_logger: Any | None = None
        self._rbac_mw: Any | None = None
        self._knowledge_mw: Any | None = None
        self._knowledge_registry: Any | None = None
        self._metrics: Any | None = None
        self._langfuse_exporter: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def metrics(self) -> Any:
        """Return the shared :class:`MetricsCollector`.

        Lazily created on first access.

        Returns:
            `MetricsCollector`: The shared metrics collector.
        """
        if self._metrics is None:
            from .._infra._metrics import MetricsCollector

            self._metrics = MetricsCollector()
        return self._metrics

    async def get_langfuse_exporter(self) -> Any:
        """Return the shared Langfuse exporter, creating it on first use.

        When Langfuse is disabled or not installed, returns a
        no-op exporter so the middleware chain is unaffected.

        Returns:
            `LangfuseExporter`: The shared exporter.
        """
        if self._langfuse_exporter is not None:
            return self._langfuse_exporter
        async with self._lock:
            if self._langfuse_exporter is not None:
                return self._langfuse_exporter
            from .._runtime._langfuse import LangfuseConfig, LangfuseExporter

            lf_cfg = LangfuseConfig(
                enabled=self._config.observability.langfuse_enabled,
                host=self._config.observability.langfuse_host,
                public_key=self._config.observability.langfuse_public_key,
                secret_key=self._config.observability.langfuse_secret_key,
            )
            self._langfuse_exporter = LangfuseExporter(lf_cfg)
            return self._langfuse_exporter

    async def get_audit_logger(self) -> Any:
        """Return the shared audit logger, creating it on first use.

        The sink is determined by ``config.observability.audit_storage``:
        ``"file"`` writes JSONL to ``audit_{tenant}.jsonl``;
        ``"memory"`` keeps an in-process list.

        Returns:
            `AuditLogger`: The shared audit logger.
        """
        if self._audit_logger is not None:
            return self._audit_logger
        async with self._lock:
            if self._audit_logger is not None:
                return self._audit_logger
            from .._runtime._middleware._audit import AuditLogger

            obs = self._config.observability
            if obs.audit_storage == "file":
                import os
                import logging

                log_dir = os.environ.get(
                    "XRUNTIME_AUDIT_DIR",
                    "/var/log/xruntime",
                )
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    file_path = os.path.join(
                        log_dir,
                        f"audit_{self._tenant_id}.jsonl",
                    )
                    self._audit_logger = AuditLogger(
                        sink="file",
                        file_path=file_path,
                    )
                except OSError:
                    logging.getLogger(__name__).warning(
                        "Cannot create audit log dir %s; "
                        "falling back to memory sink",
                        log_dir,
                    )
                    self._audit_logger = AuditLogger(sink="memory")
            else:
                self._audit_logger = AuditLogger(sink="memory")
        return self._audit_logger

    async def get_quota_tracker(
        self,
        session_id: str,
    ) -> Any:
        """Return the quota tracker for a session.

        Creates a new tracker on first access for the session, then
        reuses it on subsequent turns so usage accumulates.

        Args:
            session_id (`str`):
                The session id.

        Returns:
            `QuotaTracker`: The session's quota tracker.
        """
        if session_id in self._quota_trackers:
            return self._quota_trackers[session_id]
        async with self._lock:
            if session_id in self._quota_trackers:
                return self._quota_trackers[session_id]
            from .._runtime._middleware._quota import (
                QuotaConfig,
                QuotaTracker,
            )

            self._quota_trackers[session_id] = QuotaTracker(
                QuotaConfig(),
            )
        return self._quota_trackers[session_id]

    async def get_rbac_middleware(self) -> Any:
        """Return the shared RBAC middleware.

        The default role is configured by ``permission.default_role`` and
        defaults to ``"viewer"`` for least privilege. Applications can
        assign different roles per session via
        :meth:`RbacMiddleware.assign_role`.

        Returns:
            `RbacMiddleware`: The shared RBAC middleware.
        """
        if self._rbac_mw is not None:
            return self._rbac_mw
        async with self._lock:
            if self._rbac_mw is not None:
                return self._rbac_mw
            from .._runtime._middleware._rbac import (
                RbacMiddleware,
                RoleDefinition,
                RbacRule,
            )

            owner_role = RoleDefinition(
                "owner",
                [RbacRule("*", "allow")],
            )
            admin_role = RoleDefinition(
                "admin",
                [RbacRule("*", "allow")],
            )
            contributor_role = RoleDefinition(
                "contributor",
                [
                    RbacRule("Read", "allow"),
                    RbacRule("Glob", "allow"),
                    RbacRule("Grep", "allow"),
                    RbacRule("Write", "allow"),
                    RbacRule("Edit", "allow"),
                    RbacRule("search_knowledge", "allow"),
                    RbacRule("ingest_knowledge", "allow"),
                    RbacRule("Bash", "deny"),
                    RbacRule("*", "deny"),
                ],
            )
            viewer_role = RoleDefinition(
                "viewer",
                [
                    RbacRule("Read", "allow"),
                    RbacRule("Glob", "allow"),
                    RbacRule("Grep", "allow"),
                    RbacRule("search_knowledge", "allow"),
                    RbacRule("ingest_knowledge", "deny"),
                    RbacRule("Write", "deny"),
                    RbacRule("Edit", "deny"),
                    RbacRule("Bash", "deny"),
                    RbacRule("*", "deny"),
                ],
            )
            default_role = self._config.permission.default_role
            self._rbac_mw = RbacMiddleware(
                roles={
                    "owner": owner_role,
                    "admin": admin_role,
                    "contributor": contributor_role,
                    "viewer": viewer_role,
                },
                default_role=default_role,
            )
        return self._rbac_mw

    async def get_knowledge_middleware(
        self,
        user_id: str = "",
        kb_ids: list[str] | None = None,
        role: str = "viewer",
    ) -> Any:
        """Return the shared knowledge middleware, or None.

        Lazily creates the knowledge registry and middleware from
        the config. Returns ``None`` if the knowledge base is not
        enabled.

        Returns:
            `KnowledgeMiddleware | None`: The middleware, or None.
        """
        if not self._config.knowledge.enabled:
            return None
        async with self._lock:
            from .._runtime._knowledge import (
                KnowledgeBaseConfig,
                KnowledgeRegistry,
                KnowledgeMiddleware,
            )

            if self._knowledge_registry is not None:
                return KnowledgeMiddleware(
                    registry=self._knowledge_registry,
                    mode=self._config.knowledge.mode,
                    top_k=self._config.knowledge.retrieval_top_k,
                    tenant_id=self._tenant_id,
                    user_id=user_id,
                    kb_ids=kb_ids or [],
                    role=role,
                )
            from .._runtime._knowledge._adapter import get_default_factory
            from .._runtime._knowledge._llm_wiki_adapter import (
                _register_default_adapter,
            )

            _register_default_adapter()

            kb_cfg = self._config.knowledge
            base_config = KnowledgeBaseConfig(
                backend=kb_cfg.backend,
                raw_dir=kb_cfg.raw_dir,
                compiled_dir=kb_cfg.compiled_dir,
                retrieval_top_k=kb_cfg.retrieval_top_k,
                auto_compile=kb_cfg.auto_compile,
                extra=kb_cfg.extra,
            )
            # Use the process-wide default factory so the adapter
            # registered by ``_register_default_adapter`` is visible to
            # this registry (a fresh ``KnowledgeAdapterFactory`` would be
            # empty and fail to resolve the ``llm_wiki`` backend).
            registry = KnowledgeRegistry(factory=get_default_factory())
            registry.register_from_config(base_config)
            await registry.initialize()

            self._knowledge_registry = registry

        return KnowledgeMiddleware(
            registry=self._knowledge_registry,
            mode=self._config.knowledge.mode,
            top_k=self._config.knowledge.retrieval_top_k,
            tenant_id=self._tenant_id,
            user_id=user_id,
            kb_ids=kb_ids or [],
            role=role,
        )
