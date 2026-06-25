# -*- coding: utf-8 -*-
"""XRuntime configuration schema and loading.

All runtime behaviour is configurable through a YAML file plus
environment-variable overrides.  The schema is a tree of pydantic
models rooted at :class:`XRuntimeConfig`.

Environment variables use the ``XRUNTIME_`` prefix with a single
underscore separating the section name from the field name, e.g.
``XRUNTIME_SERVER_PORT=7777`` overrides ``server.port``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """HTTP server configuration.

    Args:
        host (`str`):
            Bind address.
        port (`int`):
            Bind port.
        auth_enabled (`bool`):
            Whether JWT/API-key auth is enforced at the gateway.
    """

    host: str = "0.0.0.0"
    port: int = 8900
    auth_enabled: bool = True


class StorageConfig(BaseModel):
    """Storage backend configuration.

    Args:
        backend (`str`):
            Backend type — ``"redis"`` (default) or ``"postgres"``.
        redis_host (`str`):
            Redis host when ``backend == "redis"``.
        redis_port (`int`):
            Redis port.
        redis_db (`int`):
            Redis database index.
        redis_password (`str | None`):
            Redis password.
        tenant_prefix (`str`):
            Key prefix for multi-tenant isolation.  ``{tid}`` is
            replaced with the tenant id at runtime.
    """

    backend: str = "redis"
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None
    tenant_prefix: str = "tenant:{tid}:"


class MessageBusConfig(BaseModel):
    """Message bus backend configuration.

    Args:
        backend (`str`):
            Backend type — ``"redis"`` (default).
        redis_host (`str`):
            Redis host.
        redis_port (`int`):
            Redis port.
        redis_db (`int`):
            Redis database index.
        tenant_prefix (`str`):
            Key prefix for multi-tenant isolation.
    """

    backend: str = "redis"
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    tenant_prefix: str = "tenant:{tid}:"


class TenantConfig(BaseModel):
    """A single tenant definition.

    Args:
        id (`str`):
            Unique tenant identifier.
        name (`str`):
            Display name.
        credentials (`list[str]`):
            Credential ids available to this tenant.
    """

    id: str
    name: str = ""
    credentials: list[str] = Field(default_factory=list)


class AgentBlueprintConfig(BaseModel):
    """An agent blueprint for declarative agent creation.

    Args:
        name (`str`):
            Agent display name.
        system_prompt (`str`):
            System prompt text.
        model_config_name (`str`):
            Reference to a model config by name.
        allowed_tools (`list[str]`):
            Tools to auto-approve.
        disallowed_tools (`list[str]`):
            Tools to deny.
        max_iters (`int | None`):
            Maximum reasoning-acting iterations for this agent.
            ``None`` falls back to the runtime default.
    """

    name: str
    system_prompt: str = ""
    model_config_name: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    max_iters: int | None = None


class McpServerConfig(BaseModel):
    """An MCP server declaration.

    Args:
        name (`str`):
            Server name.
        transport (`str`):
            Transport type — ``"stdio"`` or ``"http"``.
        command (`str | None`):
            Command for stdio transport.
        args (`list[str]`):
            Arguments for stdio transport.
        url (`str | None`):
            URL for http transport.
        env (`dict[str, str]`):
            Environment variables for stdio transport.
    """

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class SkillConfig(BaseModel):
    """A skill directory declaration.

    Args:
        path (`str`):
            Filesystem path to the skill directory.
        scan_subdir (`bool`):
            Whether to scan subdirectories for skills.
    """

    path: str
    scan_subdir: bool = False


class PermissionConfig(BaseModel):
    """Permission configuration.

    Args:
        mode (`str`):
            Default permission mode — ``"default"``,
            ``"accept_edits"``, ``"explore"``, ``"bypass"``,
            ``"dont_ask"``.
        rules (`list[dict[str, Any]]`):
            Declarative permission rules.
    """

    mode: str = "default"
    rules: list[dict[str, Any]] = Field(default_factory=list)


class PluginConfig(BaseModel):
    """A plugin declaration.

    Args:
        name (`str`):
            Plugin name (entry-point or module path).
        enabled (`bool`):
            Whether the plugin is active.
        config (`dict[str, Any]`):
            Plugin-specific configuration.
    """

    name: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ObservabilityConfig(BaseModel):
    """Observability configuration.

    Args:
        otel_enabled (`bool`):
            Whether OpenTelemetry tracing is enabled.
        otel_endpoint (`str`):
            OTLP exporter endpoint.
        audit_enabled (`bool`):
            Whether audit logging is enabled.
        audit_storage (`str`):
            Audit log storage — ``"file"`` or ``"redis"``.
    """

    otel_enabled: bool = False
    otel_endpoint: str = ""
    audit_enabled: bool = True
    audit_storage: str = "file"


class KnowledgeConfig(BaseModel):
    """Knowledge base configuration.

    Args:
        enabled (`bool`):
            Whether the knowledge base is active.
        backend (`str`):
            Backend type — ``"llm_wiki"``, ``"vector_store"``,
            ``"knowledge_graph"``, or ``"custom"``.
        mode (`str`):
            Retrieval mode — ``"static_control"`` (auto-inject),
            ``"agent_control"`` (tools only), or ``"both"``.
        raw_dir (`str`):
            Directory for raw source documents.
        compiled_dir (`str`):
            Directory for compiled knowledge pages.
        retrieval_top_k (`int`):
            Number of chunks to retrieve per query.
        auto_compile (`bool`):
            Whether to auto-compile after ingestion.
        embedding_model (`dict`):
            Embedding model config (provider, api_key, model).
        llm_model (`dict`):
            LLM config for compilation steps.
        extra (`dict`):
            Backend-specific configuration.
    """

    enabled: bool = False
    backend: str = "llm_wiki"
    mode: str = "static_control"
    raw_dir: str = "/var/lib/xruntime/kb-raw"
    compiled_dir: str = "/var/lib/xruntime/kb-compiled"
    retrieval_top_k: int = 5
    auto_compile: bool = False
    embedding_model: dict[str, Any] = Field(default_factory=dict)
    llm_model: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class XRuntimeConfig(BaseModel):
    """The top-level XRuntime configuration.

    Args:
        server (`ServerConfig`):
            HTTP server settings.
        storage (`StorageConfig`):
            Storage backend settings.
        message_bus (`MessageBusConfig`):
            Message bus settings.
        tenants (`list[TenantConfig]`):
            Tenant definitions for multi-tenant isolation.
        agents (`list[AgentBlueprintConfig]`):
            Agent blueprints for declarative agent creation.
        mcps (`list[McpServerConfig]`):
            Global MCP server declarations.
        skills (`list[SkillConfig]`):
            Skill directory declarations.
        permission (`PermissionConfig`):
            Permission settings.
        plugins (`list[PluginConfig]`):
            Plugin declarations.
        observability (`ObservabilityConfig`):
            Observability settings.
        knowledge (`KnowledgeConfig`):
            Knowledge base module configuration.
    """

    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    message_bus: MessageBusConfig = Field(default_factory=MessageBusConfig)
    tenants: list[TenantConfig] = Field(default_factory=list)
    agents: list[AgentBlueprintConfig] = Field(default_factory=list)
    mcps: list[McpServerConfig] = Field(default_factory=list)
    skills: list[SkillConfig] = Field(default_factory=list)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    plugins: list[PluginConfig] = Field(default_factory=list)
    observability: ObservabilityConfig = Field(
        default_factory=ObservabilityConfig,
    )
    knowledge: KnowledgeConfig = Field(
        default_factory=KnowledgeConfig,
    )
    """Knowledge base configuration for RAG / LLM-Wiki integration.
    When ``enabled``, a :class:`KnowledgeMiddleware` is injected into
    the agent middleware chain to auto-retrieve and inject relevant
    knowledge before each reply."""
    model_providers: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
    )
    """Model provider declarations keyed by config name.
    Each value: ``{"name": "anthropic", "api_key": "sk-...",
    "model": "claude-sonnet-4-20250514", "base_url": null}``.
    """
    enable_enterprise_middlewares: bool = True
    """Whether to attach audit/quota/rbac/redaction middlewares
    to every agent created by the runtime."""


def _decode_env_value(value: str) -> Any:
    """Decode an env-var value, JSON-parsing when possible.

    ``"false"`` → ``False``, ``"7777"`` → ``7777``,
    ``'[{"a":1}]'`` → a list; values that are not valid JSON
    (e.g. ``"redis"``, ``"0.0.0.0"``) are returned as plain strings.

    Args:
        value (`str`):
            The raw env-var string.

    Returns:
        `Any`: The decoded value.
    """
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _apply_env_overrides(cfg: XRuntimeConfig) -> XRuntimeConfig:
    """Apply ``XRUNTIME_`` env-var overrides to a config instance.

    Env vars follow the pattern ``XRUNTIME_<SECTION>_<FIELD>``. The
    section is matched against the top-level fields of
    :class:`XRuntimeConfig` (longest match first, so
    ``message_bus`` is not split into ``message`` + ``bus``). Values
    are JSON-decoded when possible, falling back to plain strings.

    Args:
        cfg (`XRuntimeConfig`):
            The config instance to override.

    Returns:
        `XRuntimeConfig`: The updated config.
    """
    prefix = "XRUNTIME_"
    # Top-level field names are the valid sections. Sort by length
    # descending so multi-word sections (``message_bus``,
    # ``enable_enterprise_middlewares``) match before their prefixes.
    sections = sorted(
        (f.lower() for f in XRuntimeConfig.model_fields),
        key=len,
        reverse=True,
    )
    overrides: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        remaining = key[len(prefix) :].lower()
        section: str | None = None
        field: str | None = None
        for s in sections:
            if remaining == s:
                section = s
                field = None
                break
            if remaining.startswith(s + "_"):
                section = s
                field = remaining[len(s) + 1 :]
                break
        if section is None:
            continue  # unknown section — ignore rather than misroute
        path = section if field is None else f"{section}.{field}"
        overrides[path] = _decode_env_value(value)

    if not overrides:
        return cfg

    data = cfg.model_dump()
    for path, value in overrides.items():
        parts = path.split(".")
        current = data
        for part in parts[:-1]:
            if not isinstance(current.get(part), dict):
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

    return XRuntimeConfig(**data)


def load_config(
    config_path: str | None = None,
) -> XRuntimeConfig:
    """Load XRuntime configuration from YAML and env vars.

    If ``config_path`` is ``None``, returns defaults (still applies
    env-var overrides).  If the file does not exist, raises
    :class:`FileNotFoundError`.

    Args:
        config_path (`str | None`):
            Path to a YAML config file.  ``None`` for defaults only.

    Returns:
        `XRuntimeConfig`: The loaded and overridden configuration.
    """
    if config_path is None:
        return _apply_env_overrides(XRuntimeConfig())

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {config_path}",
        )

    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = XRuntimeConfig(**raw)
    return _apply_env_overrides(cfg)
