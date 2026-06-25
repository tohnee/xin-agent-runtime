# ADR-001: XRuntime as AgentScope Extension (not standalone)

**Date**: 2026-06-25
**Status**: Accepted

## Context

XRuntime was originally conceived as a standalone runtime. After
analysis, it became clear that AgentScope already provides the core
Agent execution kernel (ReAct loop, model providers, tools, middleware,
storage, sessions). Building a parallel runtime would duplicate
maintenance and diverge from upstream.

## Decision

XRuntime is an **extension** of AgentScope, not a replacement:

- AgentScope remains the execution kernel (protocol-agnostic,
  enterprise-policy-agnostic).
- XRuntime provides the enterprise shell: protocol adapters,
  gateway auth, RBAC, knowledge base, multi-tenant isolation,
  observability.

The entry point is `create_xruntime_extension()` which returns
middleware factories and adapter registries that plug into AS
`create_app()`.

## Consequences

- XRuntime cannot run without AgentScope installed.
- Protocol adapters mount as additional FastAPI routes on the AS app.
- Enterprise middlewares are injected via AS `extra_agent_middlewares`.
- The standalone `create_xruntime_app` was removed.
