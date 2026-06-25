# AGENTS.md

Compact guide for OpenCode sessions working in the AgentScope repo.
Authoritative detail lives in `CONTRIBUTING.md` and `.github/copilot-instructions.md`; this file captures only what an agent would otherwise guess wrong.

## Commands

```bash
# Setup (Python 3.11+ required)
uv pip install -e ".[dev]"          # dev extra pulls in [full] + pytest + pre-commit
pre-commit install

# Verify (run in this order before opening a PR)
pre-commit run --all-files          # black --line-length=79, flake8, mypy, pylint, pyroma --min=10
pytest tests                        # CI runs: coverage run -m pytest tests, with GRPC_VERBOSITY=ERROR
pytest tests/<file>::<test> -p no:cacheprovider   # single test; pytest-forked is installed

```

No `pytest.ini`/`addopts`/markers exist — plain `pytest tests`. Tests rely on
`fakeredis` and the `MockModel`/`MockCredential` in `tests/utils.py`; provider
tests that need real API keys are expected to fail without credentials.

## Layout

- Source is `src/agentscope/` (src-layout). Top-level public surface is tiny:
  `from agentscope import logger, setup_logger, set_id_factory, __version__`.
  Everything else is reached via subpackage `__init__.py` exports.
- Single core agent class: `agentscope.agent.Agent`. Do **not** add new
  top-level agent classes — specialized agents go in `examples/`. Open an
  issue first if a new class seems unavoidable.
- `agentscope.app` is the FastAPI service layer (`create_app`, routers,
  message bus, storage, workspace manager).
- Model providers live under `agentscope/model/<provider>/` with sibling
  `_models/*.yaml` cards shipped as package data.

## Conventions that differ from defaults

- **Lazy imports are mandatory** for anything not in `[project.dependencies]`
  (i.e. the `gemini`/`ollama`/`xai`/`service`/`storage`/`workspace`/`mem0`
  extras). Import at point of use, never at module top. This keeps
  `import agentscope` lightweight.
- **`_` prefix everywhere under `src/agentscope`**: files are `_name.py`,
  internal classes/functions are `_name`. Public API is controlled via
  `__init__.py`.
- **Black line length is 79**, not the default 88. flake8 ignores `E203`.
- Docstrings are **English, reStructuredText**, with the `Args:`/`Returns:`
  template using backticked types (see `.github/copilot-instructions.md`).
- Comments: English only.
- Pre-commit skipping is prohibited at file level. The only tolerated skip
  is on agent system-prompt parameters (to avoid `\n` reformatting).

## Contributing a chat model (all four pieces required, or PR is rejected)

1. Credential class under `agentscope.credential` (subclass `CredentialBase`).
2. Model class under `agentscope.model.<provider>/` (subclass `ChatModelBase`)
   covering streaming + non-streaming, tools, `tool_choice`, reasoning models.
3. Model-card YAML(s) under `agentscope.model.<provider>._models/` with
   `name`, `label`, `status`, `input_types`, `output_types`, `context_size`,
   `output_size`.
4. Two formatters under `agentscope.formatter`: `<Provider>ChatFormatter`
   and `<Provider>MultiAgentFormatter`.

Reference: `agentscope/model/_anthropic/` + `agentscope/formatter/_anthropic_formatter.py`.

## Contributing a workspace backend

Workspace class (subclass `WorkspaceBase`) under `agentscope.workspace` **and**
a matching `WorkspaceManagerBase` in `agentscope/app/_manager/_workspace_manager.py`,
plus a docs PR in the separate `agentscope-ai/docs` repo. Reference:
`_local_workspace.py` / `LocalWorkspaceManager`.

## Dependency quirks

- `mem0` extra is pinned to `mem0ai>=2.0.0`; `Mem0Middleware` targets v2.x
  architecture and degrades on v1.x.
- New deps: add to the right optional group in `pyproject.toml`, not to
  `[project.dependencies]` unless truly core. Discuss in an issue first.
- User-facing docs live in a separate repo (`agentscope-ai/docs`); this
  repo only holds inline docstrings, `README.md`, and `examples/`.

## Git / PR

- Conventional Commits, enforced by `amannn/action-semantic-pull-request`.
  Allowed types: `feat fix docs ci refactor test chore perf style build revert`.
  Scope optional but, if present, must match `^[a-z0-9_-]+$` (lowercase only).
  Subject must start with a lowercase letter.
- Branch naming: `feat/<desc>`, `fix/<desc>`, `docs/<desc>`.
- Keep PRs atomic; AI-generated PRs must be reviewed line-by-line by the
  author (see `CONTRIBUTING.md` §2).
