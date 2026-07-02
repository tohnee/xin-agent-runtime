# ADR-004: RuntimeExecutionPlan as Unified Protocol Abstraction

**Date**: 2026-06-25
**Status**: Accepted

## Context

Three protocol adapters (Anthropic, Claude Code, OpenCode) each
produce an `XRuntimeRequest`. However, protocol-specific metadata
(sandbox, budget, model, tools) was stored in an opaque `metadata`
dict and never resolved into actionable execution fields. Downstream
code had no unified way to make governance decisions.

## Decision

Introduce `RuntimeExecutionPlan` as the unified execution abstraction:

1. Each adapter produces `XRuntimeRequest` (parse only).
2. `build_plan_from_request()` converts it to a
   `RuntimeExecutionPlan`, extracting metadata into typed fields
   (model, budget, workspace, tools, knowledge scope).
3. Downstream code (gateway, middleware, workspace) reads from the
   plan, not raw metadata.
4. Client-supplied `allowed_tools` are intersected with the tenant
   tool allowlist — permissions can only tighten, never widen.

## Consequences

- Protocol adapters remain thin (parse + serialize only).
- Governance logic operates on a single unified type.
- Claude Code `sandbox`/`max_budget_usd`/`model`/`fallback_model`
  are now first-class fields.
- OpenCode `permissions` cannot bypass tenant-level policy.
