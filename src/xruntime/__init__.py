# -*- coding: utf-8 -*-
"""Xin Agent Runtime — enterprise agent runtime platform.

Xin Agent Runtime is built on the AgentScope execution kernel and the
XRuntime enterprise extension layer. It provides a complete enterprise
agent runtime with protocol adapters, multi-tenant isolation, RBAC,
knowledge base governance, workspace sandboxing, and observability.

Core capabilities:

- **Protocol adapters** — Anthropic Messages API, Claude Code SDK,
  OpenCode SDK wire formats, mounted as additional routes on the
  FastAPI app.
- **RuntimeExecutionPlan** — unified execution plan across all three
  protocols with permissions tightening and metadata landing.
- **Enterprise middlewares** — audit logging, quota control, RBAC,
  secret redaction, knowledge injection.
- **Multi-tenant isolation** — tenant key prefixing, per-request
  tenant resolution, anti-spoofing via authenticated principal.
- **RBAC** — Owner/Admin/Contributor/Viewer four-tier roles with
  16 fine-grained actions, default deny.
- **Knowledge base** — LLM-Wiki AOT compiler with BM25 retrieval,
  per-KB ACL, audit log, secret redaction.
- **Workspace** — Local/Docker/E2B backends with production safety
  guard (rejects LocalWorkspace in production).
- **Model governance** — ModelCapabilityRegistry + ModelRouter with
  tenant allowlist and fallback.
- **Observability** — OTel tracing, Prometheus metrics, Langfuse
  exporter (optional, no-op by default).

Usage::

    from xruntime import create_xruntime_extension, mount_protocol_adapters
    from agentscope.app import create_app

    ext = create_xruntime_extension()
    app = create_app(
        storage=storage,
        message_bus=message_bus,
        workspace_manager=workspace_manager,
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
