# ADR-002: Tenant Key-Prefix Isolation for Redis

**Date**: 2026-06-25
**Status**: Accepted

## Context

Multiple tenants share a single Redis instance. Without isolation,
one tenant's keys (agents, sessions, credentials) could collide with
another's, causing data leakage and corruption.

## Decision

Use **key-prefix isolation** via `TenantKeyPrefixer`:

- Every Redis key template is prefixed with `tenant:{tid}:`.
- `build_tenant_key_config()` produces a tenant-scoped
  `RedisStorage.KeyConfig` applied at app startup.
- `TenantContext` (contextvars-based) provides async-safe per-request
  tenant scoping.
- The gateway handler sets `current_tenant` from the authenticated
  principal (not client-supplied headers — anti-spoofing).

## Consequences

- Two tenants with the same `user_id` have different Redis keys.
- Key prefix is resolved once at startup; runtime overhead is zero.
- `TenantContext` is async-safe (contextvars) and thread-safe.
- Header tenant spoofing is rejected: the authenticated principal's
  `tenant_id` always overrides client-supplied values.
