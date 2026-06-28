# ADR-006: Model Governance with Capability Registry

## Status

Accepted

## Context

With multiple model providers and varying tenant policies, XRuntime needs:
1. A way to track which models support which capabilities (tools, vision)
2. Tenant-specific model allowlists to restrict usage
3. Fallback mechanisms when preferred models are unavailable
4. Cost and token budget enforcement

## Decision

### 1. ModelCapability Registry
- `ModelCapability` dataclass tracks: `supports_tools`, `supports_vision`, `max_tokens`, costs
- `ModelCapabilityRegistry` stores capability metadata keyed by model name
- Registry is populated at startup; new models can be registered dynamically

### 2. Capability-Based Model Router
- `ModelRouter.select()` filters candidates by capability requirements
- Takes `requires_tools` and `requires_vision` flags
- Returns first matching candidate from the ordered preference list
- Falls back to a designated model if no candidates match

### 3. Tenant Allowlists
- `tenant_allowlist` parameter enforces per-tenant model restrictions
- Unauthorized models raise `ValueError: Model 'X' is not allowed by tenant allowlist`
- Fallback models are also checked against the allowlist

### 4. Cost Enforcement Hook
- Cost metadata (`cost_per_1k_input`, `cost_per_1k_output`) is available for:
  - Quota middleware budget tracking
  - Usage analytics
  - Budget exhaustion blocking

## Consequences

### Positive
- **Capability safety**: Agents won't try to use tools on models that don't support them
- **Tenant control**: Explicit allowlists prevent unauthorized model usage
- **Resilient**: Fallback models ensure continuity when primary is unavailable
- **Auditable**: Capability registry enables cost analysis and capacity planning

### Negative
- **Maintenance**: Model capabilities need to be kept up-to-date
- **Dual-source**: Capabilities are defined in both model cards and the registry

### Neutral
- Models not in the registry are skipped during selection
- Cost enforcement is optional; middleware can choose to track or block
