# ADR-005: Workspace Production Safety

## Status

Accepted

## Context

AgentScope's workspace subsystem allows agents to execute code and manipulate files. In production deployments:
1. The `local` backend runs code directly on the host machine, which is unsafe
2. Path traversal attacks could allow agents to access sensitive files outside their workspace
3. Tenant and session isolation is required for multi-tenant deployments

## Decision

### 1. Production Default: Docker Backend
- **Default backend** is `docker` (not `local`) in production mode
- `local` backend is **rejected by default** in production
- Explicit override `allow_local_in_production=True` is required to use local

### 2. Path Traversal Guard
- Workspace paths are validated to reject `..`, `/`, and OS-specific separators
- `tenant_id` and `session_id` are both checked before path construction
- Tenant-scoped path structure: `{base_dir}/tenants/{tenant_id}/sessions/{session_id}`

### 3. Backend Selection via Factory
- `WorkspaceManagerFactory` encapsulates backend selection logic
- Factory validates production vs. backend combination before creation
- Three backends supported: `local`, `docker`, `e2b`

## Consequences

### Positive
- **Defense in depth**: Default-deny for unsafe backends
- **Multi-tenant safe**: Physical isolation via tenant/session directories
- **Clear intent**: Explicit override required for dangerous configurations
- **Single point**: All workspace creation goes through the factory

### Negative
- **Configuration complexity**: Production deployments need Docker/E2B setup
- **Local development friction**: Must set `production=False` for local testing

### Neutral
- `WorkspaceConfig` is the single source of workspace policy
- Runtime errors are clear: "Local workspace backend is not allowed in production"
