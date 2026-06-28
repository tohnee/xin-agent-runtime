# ADR-007: Langfuse Observability with Secret Redaction

## Status

Accepted

## Context

XRuntime needs production-grade observability for:
1. Tracking LLM usage per tenant and user
2. Debugging tool call performance
3. Auditing knowledge retrieval patterns
4. Billing and cost attribution

However, traces can contain sensitive data like API keys, bearer tokens, and PII.

## Decision

### 1. Optional Langfuse Integration
- Exporter is **disabled by default**; opt-in via `LangfuseConfig.enabled=True`
- Graceful degradation: if `langfuse` package is not installed, falls back to `NoopExporter`
- `is_noop` property allows callers to skip work when tracing is disabled

### 2. Automatic Secret Redaction
- `_redact_payload()` recursively processes all trace data
- Two regex patterns are applied:
  - `sk-[a-zA-Z0-9]{20,}` → `[REDACTED_API_KEY]`
  - `Bearer\s+[a-zA-Z0-9\-._~+/]+=*` → `Bearer [REDACTED_TOKEN]`
- Handles strings, dicts, and lists recursively

### 3. Typed Trace Methods
- `trace_generation()`: model calls with token counts, tenant/user/session scope
- `trace_tool_call()`: tool execution with metadata
- `trace_knowledge_retrieve()`: knowledge retrieval with query and result count

## Consequences

### Positive
- **Safe by default**: No tracing happens unless explicitly enabled
- **No secrets leak**: Redaction runs before data leaves the process
- **Zero overhead**: No-op exporter is effectively free when disabled
- **Multi-tenant ready**: Tenant scope is included in every trace

### Negative
- **Redaction is best-effort**: Custom secret formats may leak
- **No PII detection**: Only API key patterns are caught; not general PII

### Neutral
- Langfuse-specific; could be abstracted to support multiple observability backends
- Trace payloads are deliberately minimal to minimize data exposure
