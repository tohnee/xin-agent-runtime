# -*- coding: utf-8 -*-
"""XRuntime — AgentScope optional extension for enterprise agent runtime.

XRuntime is NOT a standalone runtime.  It extends AgentScope with:

- **Protocol adapters** — Anthropic Messages API, Claude Code SDK,
  OpenCode SDK wire formats, mounted as additional routes on the
  AS FastAPI app.
- **Enterprise middlewares** — audit logging, quota control, RBAC,
  secret redaction, injected via AS ``extra_agent_middlewares``.
- **Multi-tenant isolation** — tenant key prefixing for Redis storage.
- **DAG orchestrator** — declarative multi-agent workflow engine.
- **Model resolver** — env-var/YAML declarative model provider config.
- **YAML config schema** — declarative configuration with env overrides.
- **Metrics** — Prometheus-compatible metrics collector.

Usage::

    from agentscope.app import create_app
    from xruntime import create_xruntime_extension, mount_protocol_adapters

    ext = create_xruntime_extension()
    app = create_app(
        storage=RedisStorage(),
        message_bus=RedisMessageBus(),
        workspace_manager=LocalWorkspaceManager(),
        extra_agent_middlewares=ext["extra_agent_middlewares"],
    )
    mount_protocol_adapters(app, ext["adapter_registry"])
"""
from ._config import XRuntimeConfig, load_config
from ._version import __version__
from ._gateway._extension import (
    create_xruntime_extension,
    mount_protocol_adapters,
)
from ._runtime._migrator import (
    Migrator,
    MigrationShimMiddleware,
    MigrationResult,
    SCHEMA_VERSION,
)

__all__ = [
    "XRuntimeConfig",
    "load_config",
    "create_xruntime_extension",
    "mount_protocol_adapters",
    "Migrator",
    "MigrationShimMiddleware",
    "MigrationResult",
    "SCHEMA_VERSION",
    "__version__",
]
