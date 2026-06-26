# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is **AgentScope** (PyPI package `agentscope`, Python ≥3.11) plus an enterprise
extension called **XRuntime** built on top of it. The repo folder is named
`xin-agent-runtime`, but the installed package is `agentscope`. XRuntime is an
*upgrade/rewrite target* of the older `xin-agent-runtime` — see `XRUNTIME-DESIGN.md`
for the authoritative design (draft v0.1, with full protocol/permission mapping tables).

**Authoritative agent guides already live in this repo** — read them, don't duplicate:
`AGENTS.md` (compact), `CONTRIBUTING.md` (full), `.github/copilot-instructions.md`
(strict code-review checklist, also mirrored at `.gemini/styleguide.md`).

## Commands

```bash
# Setup (Python 3.11+). Two install profiles:
uv pip install -e ".[dev]"          # AS dev: [full] + pytest + pre-commit
uv pip install -e ".[xruntime-dev]" # adds claude-agent-sdk, pyyaml, pytest-asyncio, httpx
pre-commit install

# Lint (run before PRs) — black --line-length=79, flake8 (ignores E203), mypy, pylint, pyroma --min=10
pre-commit run --all-files

# Tests — plain pytest, src-layout auto-added via conftest.py + pyproject pythonpath=["src"]
pytest tests
pytest tests/xruntime                              # XRuntime tests only
pytest tests/<file>::<test> -p no:cacheprovider    # single test (pytest-forked is installed)
# CI runs: GRPC_VERBOSITY=ERROR coverage run -m pytest tests

# Run model-provider smoke scripts (need real API keys in env):
python scripts/model_examples/<provider>_call.py

# Run the XRuntime server (see Gotchas — entrypoint is currently stale):
python -m xruntime._server            # reads XRUNTIME_CONFIG_PATH / XRUNTIME_* env
docker compose -f deploy/docker-compose.yml up   # xruntime + redis on :8900/:6379
```

Tests rely on `fakeredis` and the `MockModel`/`MockCredential` in `tests/utils.py`.
Provider tests that need real API keys are expected to fail without credentials.

## Architecture

**src-layout, three packages** under `src/`:
- `agentscope/` — the base library. **Do not modify AS core** when working on XRuntime;
  XRuntime depends only on AS's public/stable surface (`Agent`, `create_app`, the
  `*Base` ABCs, `AgentEvent`, `Msg`).
- `xruntime/` — the enterprise extension (implemented, has tests).
- `xruntime_sdk/` — client SDK with `XRuntimeClient` and `AdminClient` (209 lines,
  covers chat, session management, and admin operations). TypeScript SDK deferred to v2.

**AgentScope base (big picture):**
- Top-level public surface is tiny (`from agentscope import logger, setup_logger,
  set_id_factory, __version__`); everything else is reached via subpackage
  `__init__.py` exports.
- One core agent class: `agentscope.agent.Agent`. Don't add new top-level agent
  classes — specialized agents go in `examples/`.
- `agentscope.app` is the FastAPI service layer: `create_app(...)`, routers, message
  bus, storage, workspace manager. `create_app` accepts extension hooks like
  `extra_agent_middlewares`, `extra_agent_tools`, `custom_subagent_templates`.
- Model providers live under `agentscope/model/<provider>/` with sibling
  `_models/*.yaml` "model cards" shipped as package data.

**XRuntime is an *extension*, not a standalone runtime.** The integration pattern is:
```python
from agentscope.app import create_app
from xruntime import create_xruntime_extension, mount_protocol_adapters
ext = create_xruntime_extension(config=...)     # or config_path=...
app = create_app(storage=..., message_bus=..., workspace_manager=...,
                 extra_agent_middlewares=ext["extra_agent_middlewares"])
mount_protocol_adapters(app, ext["adapter_registry"])
```
- `create_xruntime_extension()` (`src/xruntime/_gateway/_extension.py`) returns
  `{extra_agent_middlewares, adapter_registry, config, middleware_state_cache}`. The
  middleware value is an async **factory** `(user_id, agent_id, session_id) -> list[MiddlewareBase]`
  that produces the enterprise middlewares per agent turn. State (quota trackers, audit
  logger, RBAC roles) is cached in `MiddlewareStateCache` so it persists across turns.
- `mount_protocol_adapters()` adds three POST routes to an existing AS FastAPI app:
  `/v1/messages` (Anthropic Messages API), `/v1/claude-code/query` (Claude Code SDK),
  `/v1/opencode` (OpenCode). Each route parses its wire format into an
  `XRuntimeRequest`, then delegates to AS `ChatService.run()` and streams events back.
- **Protocol adapters** (`src/xruntime/_gateway/_adapter.py`): each implements the
  `ProtocolAdapter` ABC — `parse_request(raw) -> XRuntimeRequest` and
  `serialize_event_stream(events) -> bytes`. Adapters are stateless; all session state
  lives in AS `SessionRecord`/`AgentState`. The internal event standard is AS
  `AgentEvent` (25 `EventType`s); adapters do bidirectional wire-format conversion.
- **Enterprise middlewares** (`src/xruntime/_runtime/_middleware/`): `AuditMiddleware`,
  `QuotaMiddleware`, `RbacMiddleware`, `SecretRedactionMiddleware` — all four are
  injected via the factory above. Quota state is shared per-session; audit logger is
  shared per-tenant; RBAC defaults to an "admin" role (allow all) with a "viewer" role
  available. `MigrationShimMiddleware` also inherits `MiddlewareBase` for optional use.
- **ModelResolver** (`src/xruntime/_runtime/_model_resolver.py`): resolves an agent
  blueprint's `model_config_name` to a real AS `CredentialBase`+`ChatModelBase` pair.
  Resolution order: runtime registry → env vars (`XRUNTIME_MODEL_PROVIDER`,
  `XRUNTIME_MODEL_API_KEY`, `XRUNTIME_MODEL_NAME`, `XRUNTIME_MODEL_BASE_URL`) →
  `model_providers` in the YAML config. Supported providers: anthropic, openai,
  dashscope, deepseek, moonshot, ollama, gemini, xai.
- **Config** (`src/xruntime/_config.py`): a pydantic tree rooted at `XRuntimeConfig`,
  loaded from YAML + `XRUNTIME_*` env overrides (`XRUNTIME_<SECTION>_<FIELD>`, values
  JSON-decoded when possible). Example: `examples/xruntime/xruntime.yaml`.
- **Multi-tenant isolation:** Redis key prefix `tenant:{tid}:` on Storage + MessageBus
  (full isolation, confirmed in the design doc).
- **Sandbox/Workspace:** XRuntime delegates all agent execution to AS's `WorkspaceManager`.
  Three backends: `LocalWorkspace` (no sandbox, dev only), `DockerWorkspace` (container
  sandbox), `E2BWorkspace` (cloud VM sandbox). `build_xruntime_app()` currently hardcodes
  `LocalWorkspaceManager`; to switch, pass `DockerWorkspaceManager` or
  `E2BWorkspaceManager` to `create_app(workspace_manager=...)`. See
  `docs/xruntime/SANDBOX-ARCHITECTURE.md` for the full security analysis.
- **Knowledge base** (`src/xruntime/_runtime/_knowledge/`): pluggable RAG / LLM-Wiki
  framework. `KnowledgeBaseBase` ABC defines ingest/compile/retrieve; `KnowledgeAdapter`
  bridges external systems; `KnowledgeRegistry` manages multiple backends;
  `KnowledgeMiddleware` auto-injects context (AS `MiddlewareBase` subclass). Default
  `LlmWikiAdapter` implements the compiler pattern (AOT retrieval). Enabled via
  `config.knowledge.enabled`; wired in `create_xruntime_extension()`. `TenantContext` uses `contextvars.ContextVar`
  for async-safe per-request tenant scoping.
- **Workspace / sandbox** (AS-owned, not XRuntime): `build_xruntime_app()` currently
  hardcodes `LocalWorkspaceManager` (no sandbox — tools run on host). For production,
  swap to `DockerWorkspaceManager` (container sandbox) or `E2BWorkspaceManager` (cloud
  VM sandbox) by passing a different `workspace_manager` to `create_app()`. See
  `docs/xruntime/SANDBOX-ARCHITECTURE.md` for full architecture, security analysis, and
  switching instructions.
- **Knowledge base framework** (`src/xruntime/_runtime/_knowledge/`): pluggable RAG /
  LLM-Wiki integration. `KnowledgeBaseBase` ABC defines ingest→compile→retrieve contract.
  `LlmWikiAdapter` implements the compiler-pattern (AOT) from
  https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2.
  `KnowledgeMiddleware` auto-injects retrieved knowledge into agent context.
  `KnowledgeRegistry` manages multiple backends. Configured via `knowledge` section in
  `XRuntimeConfig`. Disabled by default. `TenantContext` uses
  `contextvars.ContextVar` for async-safe tenant scoping.
- **Sandbox/Workspace** (AS-owned, not XRuntime): `WorkspaceBase` ABC has three backends —
  `LocalWorkspace` (no sandbox, tools run on host), `DockerWorkspace` (container sandbox via
  in-container MCP gateway), `E2BWorkspace` (cloud VM sandbox). `build_xruntime_app()`
  currently hardcodes `LocalWorkspaceManager`; switch to Docker/E2B by passing a different
  manager to `create_app()`. See `docs/xruntime/SANDBOX-ARCHITECTURE.md` for full analysis.
- **Knowledge base** (`src/xruntime/_runtime/_knowledge/`): pluggable RAG / LLM-Wiki framework.
  `KnowledgeBaseBase` ABC defines ingest → compile → retrieve contract.
  `LlmWikiAdapter` implements the compiler pattern (AOT: pre-compile raw docs into wiki pages,
  retrieve from compiled layer). `KnowledgeMiddleware` auto-injects retrieved context before
  each reply (static_control) or exposes search/ingest tools (agent_control).
  Configured via `knowledge:` section in YAML. See `docs/xruntime/KB-GUIDE.md`.
- **Workspace / sandbox:** XRuntime does NOT create sandboxes — it delegates to AS's
  `WorkspaceManager`. The default `build_xruntime_app()` uses `LocalWorkspaceManager`
  (no sandbox, tools run on host). For production, switch to `DockerWorkspaceManager`
  (container sandbox) or `E2BWorkspaceManager` (cloud VM sandbox). See
  `docs/xruntime/SANDBOX-ARCHITECTURE.md` for the full architecture, security analysis,
  and resource model.
- **Knowledge base** (`src/xruntime/_runtime/_knowledge/`): pluggable KB framework with
  `KnowledgeBaseBase` ABC, `KnowledgeAdapter` + `KnowledgeAdapterFactory` for backend
  registration, `KnowledgeRegistry` for multi-backend management, and
  `KnowledgeMiddleware` (AS `MiddlewareBase`) for auto-injecting retrieved knowledge
  into agent context. The LLM-Wiki adapter (`_llm_wiki_adapter.py`) implements the
  compiler-pattern (AOT retrieval) from https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2
  with raw→compiled→index layers. Configured via `knowledge:` section in YAML.
  Disabled by default.

## Conventions that differ from defaults

- **Lazy imports are mandatory** for anything not in `[project.dependencies]` (i.e. the
  `gemini`/`ollama`/`xai`/`service`/`storage`/`workspace`/`mem0`/`xruntime` extras).
  Import at point of use, never at module top — keeps `import agentscope` lightweight.
  For base-class imports use the factory pattern (`def get_xxx_cls() -> "MyClass": ...`).
- **`_` prefix everywhere** under `src/`: files are `_name.py`, internal
  classes/functions are `_name`. Public API is controlled via `__init__.py`.
- **Black line length is 79** (not 88). flake8 ignores `E203`.
- **Docstrings:** English, reStructuredText, with the `Args:`/`Returns:` template using
  backticked types (see `.github/copilot-instructions.md` for the exact template).
  Comments: English only.
- **Pre-commit:** no file-level skips. The only tolerated skip is on agent
  system-prompt parameters (to avoid `\n` reformatting). Modify code rather than skip.
- `mem0` extra is pinned to `mem0ai>=2.0.0` (`Mem0Middleware` targets v2.x and
  degrades on v1.x). New deps go in the right optional group in `pyproject.toml`, not
  in `[project.dependencies]` unless truly core — discuss in an issue first.
- User-facing docs live in this repo under `docs/xruntime/`; inline docstrings,
  `README.md`, `examples/`, and phase reports are all here.

## Contributing a chat model (all four pieces required, or PR rejected)

1. Credential class under `agentscope.credential` (subclass `CredentialBase`).
2. Model class under `agentscope.model.<provider>/` (subclass `ChatModelBase`) —
   streaming + non-streaming, tools, `tool_choice`, reasoning models.
3. Model-card YAML(s) under `agentscope.model.<provider>._models/` with `name`,
   `label`, `status`, `input_types`, `output_types`, `context_size`, `output_size`.
4. Two formatters under `agentscope.formatter`: `<Provider>ChatFormatter` and
   `<Provider>MultiAgentFormatter`.

Reference: `agentscope/model/_anthropic/` + `agentscope/formatter/_anthropic_formatter.py`.

Contributing a workspace backend: subclass `WorkspaceBase` under `agentscope.workspace`
**and** a matching `WorkspaceManagerBase` in `agentscope/app/_manager/_workspace_manager.py`
(reference: `_local_workspace.py` / `LocalWorkspaceManager`).

## Git / PR (per AGENTS.md)

- Conventional Commits, enforced by `amannn/action-semantic-pull-request`. Allowed
  types: `feat fix docs ci refactor test chore perf style build revert`. Scope optional
  but, if present, must match `^[a-z0-9_-]+$`. Subject starts with a lowercase letter.
- Branch naming: `feat/<desc>`, `fix/<desc>`, `docs/<desc>`. Keep PRs atomic.

## Gotchas

- **Server entrypoint:** `src/xruntime/_server.py` exposes `build_xruntime_app(config)`
  which assembles the full stack (AS `create_app` + `create_xruntime_extension` +
  `mount_protocol_adapters` + gateway auth/rate-limit middleware). The old
  `create_xruntime_app` from the deleted `_gateway/_app.py` is gone;
  `tests/xruntime/test_server.py` verifies the extension pattern is used.
- **Middleware state caching:** `create_xruntime_extension` returns a
  `MiddlewareStateCache` in `ext["middleware_state_cache"]`. The AS middleware factory
  is called per-turn, but quota trackers, audit loggers, and RBAC roles are cached and
  shared across turns within a session (see `_gateway/_mw_state.py`).
- **Gateway middleware:** `AuthMiddleware` (API-key) and `RateLimiter` (sliding-window)
  are wired in `build_xruntime_app` when `config.server.auth_enabled` is true or
  `XRUNTIME_RATE_LIMIT` env var is set. `MetricsCollector` is exposed on
  `app.state.metrics`.
- `XRUNTIME-DESIGN.md` is a design doc (some sections marked "待确认/to confirm"); the
  implemented code in `src/xruntime/` is the source of truth where they disagree.
  `docs/xruntime/` holds per-phase module docs and test reports (P0–P5).
- AS event/permission/session internals are referenced by stable path in the design doc
  (e.g. `AS/permission/_engine.py`); verify a symbol still exists before relying on it.
