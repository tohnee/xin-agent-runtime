# -*- coding: utf-8 -*-
"""XRuntime extension for AgentScope — mounts protocol adapters and
enterprise middlewares onto an existing AS ``create_app`` FastAPI app.

XRuntime is an *extension* of AgentScope, not a replacement. Instead of
building a parallel FastAPI app, this module provides:

- :func:`create_xruntime_extension` — returns a config dict to spread
  into :func:`agentscope.app.create_app` (``extra_agent_middlewares``)
  plus the adapter registry, config, and model resolver.
- :func:`mount_protocol_adapters` — mounts the three protocol adapter
  routes (``/v1/messages``, ``/v1/claude-code/query``,
  ``/v1/opencode``) onto an existing AS FastAPI app.

Each route parses its wire format into an :class:`XRuntimeRequest`,
materializes a credential + agent + session in AS storage (so
``ChatService.run`` does not 404), triggers the run via
``chat_run_registry.spawn`` (the same primitive AS's own ``/chat/``
route uses), and streams the session's ``AgentEvent`` stream back
through the protocol adapter as a single request-response stream.

The stream follows AS's canonical pattern (see
``agentscope/app/_router/_session.py``): subscribe live via
``session_subscribe_events`` with an ``on_ready`` hook that spawns the
run (so no events are missed), feed the continuous event stream to the
adapter, and close on the terminal event (``REPLY_END`` /
``EXCEED_MAX_ITERS``).

Usage::

    from agentscope.app import create_app
    from agentscope.app.storage import RedisStorage
    from agentscope.app.message_bus import RedisMessageBus
    from agentscope.app.workspace_manager import LocalWorkspaceManager
    from xruntime._gateway._extension import (
        create_xruntime_extension,
        mount_protocol_adapters,
    )

    ext = create_xruntime_extension(config=...)
    app = create_app(
        storage=RedisStorage(),
        message_bus=RedisMessageBus(),
        workspace_manager=LocalWorkspaceManager(),
        extra_agent_middlewares=ext["extra_agent_middlewares"],
    )
    mount_protocol_adapters(
        app,
        ext["adapter_registry"],
        config=ext["config"],
        model_resolver=ext["model_resolver"],
    )
"""
# NOTE: ``from __future__ import annotations`` is deliberately omitted so
# that route-handler annotations like ``request: Request`` are resolved to
# real classes at definition time (FastAPI needs the actual ``Request``
# type, not a string, to inject the request object instead of treating it
# as a query parameter).
import asyncio
from typing import Any, AsyncGenerator

from .._config import XRuntimeConfig, load_config
from .._runtime._model_resolver import ModelResolver
from ._adapter import AdapterRegistry, ProtocolAdapter
from ._mw_state import MiddlewareStateCache
from ._request import ProtocolType, XRuntimeRequest
from ._anthropic_adapter import AnthropicMessagesAdapter
from ._claude_code_adapter import ClaudeCodeAdapter
from ._opencode_adapter import OpenCodeAdapter


_ROUTE_PROTOCOL_MAP: dict[str, ProtocolType] = {
    "/v1/messages": ProtocolType.ANTHROPIC,
    "/v1/claude-code/query": ProtocolType.CLAUDE_CODE,
    "/v1/opencode": ProtocolType.OPENCODE,
}

# AgentEvent types that terminate a reply stream. The gateway closes the
# HTTP response after the adapter emits its frames for one of these.
_TERMINAL_EVENT_TYPES: set[str] = {"REPLY_END", "EXCEED_MAX_ITERS"}

_DEFAULT_AGENT_NAME = "xruntime-agent"
_DEFAULT_SYSTEM_PROMPT = "You're a helpful assistant."
_DEFAULT_MAX_ITERS = 20
# Poll interval (seconds) for noticing a run that finished without a
# terminal event (e.g. an internal error swallowed by ChatService.run).
_RUN_DONE_POLL_SECS = 0.5


def _default_adapters() -> AdapterRegistry:
    """Build a registry with all three protocol adapters.

    Returns:
        `AdapterRegistry`: Registry with all adapters.
    """
    registry = AdapterRegistry()
    registry.register(AnthropicMessagesAdapter())
    registry.register(ClaudeCodeAdapter())
    registry.register(OpenCodeAdapter())
    return registry


def create_xruntime_extension(
    config: XRuntimeConfig | None = None,
    config_path: str | None = None,
    adapter_registry: AdapterRegistry | None = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Create the XRuntime extension config for AS ``create_app``.

    Returns a dict of arguments to spread into
    :func:`agentscope.app.create_app`:

    - ``extra_agent_middlewares`` — async factory that produces
      audit/quota/RBAC/redaction middlewares per agent turn, sharing
      state via :class:`MiddlewareStateCache`.
    - ``adapter_registry`` — for use with
      :func:`mount_protocol_adapters`.
    - ``config`` — the resolved :class:`XRuntimeConfig`.
    - ``model_resolver`` — a :class:`ModelResolver` for the gateway.
    - ``middleware_state_cache`` — the shared
      :class:`MiddlewareStateCache` (metrics, quota trackers, audit
      logger, RBAC roles).

    Args:
        config (`XRuntimeConfig | None`):
            Pre-built config.
        config_path (`str | None`):
            Path to YAML config file.
        adapter_registry (`AdapterRegistry | None`):
            Custom adapter registry.
        tenant_id (`str`):
            Default tenant id for middlewares / audit sink.

    Returns:
        `dict[str, Any]`: Extension config dict.
    """
    if config is None:
        config = load_config(config_path)

    registry = adapter_registry or _default_adapters()
    model_resolver = ModelResolver()
    state_cache = MiddlewareStateCache(config, tenant_id=tenant_id)

    async def middleware_factory(
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> list[Any]:
        """Produce enterprise middlewares per agent turn.

        Uses the shared :class:`MiddlewareStateCache` so quota state
        accumulates per session, the audit logger is shared per tenant,
        and RBAC roles persist across turns.

        Args:
            user_id (`str`): The authenticated user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.

        Returns:
            `list[MiddlewareBase]`: Enterprise middlewares.
        """
        from .._runtime._middleware._audit import AuditMiddleware
        from .._runtime._middleware._quota import (
            QuotaConfig,
            QuotaMiddleware,
        )
        from .._runtime._middleware._redaction import (
            SecretRedactionMiddleware,
        )

        middlewares: list[Any] = []

        if config.observability.audit_enabled:
            audit_logger = await state_cache.get_audit_logger()
            middlewares.append(
                AuditMiddleware(
                    logger=audit_logger,
                    tenant_id=tenant_id,
                    user_id=user_id,
                )
            )

        # The enterprise middlewares (quota / RBAC / secret redaction)
        # are gated by ``enable_enterprise_middlewares`` so an operator
        # can disable them wholesale (e.g. for a lightweight embed).
        if config.enable_enterprise_middlewares:
            quota_tracker = await state_cache.get_quota_tracker(
                session_id,
            )
            middlewares.append(
                QuotaMiddleware(QuotaConfig(), tracker=quota_tracker)
            )

            # RBAC defaults to the "admin" role (allow all) so tool
            # calls are not blocked unless an app assigns a stricter
            # role.
            rbac = await state_cache.get_rbac_middleware()
            rbac.assign_role(session_id, "admin")
            middlewares.append(rbac)

            middlewares.append(SecretRedactionMiddleware())

        # Knowledge middleware (RAG / LLM-Wiki auto-injection). Created
        # lazily and shared per tenant via the state cache; returns
        # ``None`` when ``config.knowledge.enabled`` is false.
        knowledge_mw = await state_cache.get_knowledge_middleware()
        if knowledge_mw is not None:
            middlewares.append(knowledge_mw)

        return middlewares

    plugin_registry = _load_plugins(config, registry)

    return {
        "extra_agent_middlewares": middleware_factory,
        "adapter_registry": registry,
        "config": config,
        "model_resolver": model_resolver,
        "middleware_state_cache": state_cache,
        "plugin_registry": plugin_registry,
    }


def _load_plugins(
    config: XRuntimeConfig,
    registry: AdapterRegistry,
) -> Any:
    """Load and initialize plugins declared in ``config.plugins``.

    Each plugin's ``name`` is a ``"module.path:ClassName"`` import spec
    (or a bare module exposing a top-level plugin instance). Only
    enabled plugins are loaded. A plugin that fails to import or
    initialize is logged and skipped so one bad plugin cannot break
    startup.

    Args:
        config (`XRuntimeConfig`): The runtime config.
        registry (`AdapterRegistry`): The adapter registry, passed to
            each plugin via :class:`PluginContext`.

    Returns:
        `PluginRegistry`: The initialized plugin registry.
    """
    import logging

    from .._runtime._plugin import PluginContext, PluginRegistry

    plugin_registry = PluginRegistry()
    if not config.plugins:
        return plugin_registry

    log = logging.getLogger("xruntime.plugins")
    for plugin_cfg in config.plugins:
        if not plugin_cfg.enabled:
            continue
        try:
            plugin = _import_plugin(plugin_cfg.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to load plugin %s: %s", plugin_cfg.name, exc)
            continue
        try:
            plugin_registry.register(plugin)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Failed to register plugin %s: %s",
                plugin_cfg.name,
                exc,
            )

    context = PluginContext(
        config=config.model_dump(),
        adapter_registry=registry,
    )
    try:
        plugin_registry.initialize_all(context)
    except Exception as exc:  # noqa: BLE001
        log.warning("Plugin initialization error: %s", exc)
    return plugin_registry


def _import_plugin(spec: str) -> Any:
    """Import a plugin from a ``"module:attr"`` or ``"module"`` spec.

    Args:
        spec (`str`): The import spec.

    Returns:
        `XRuntimePlugin`: The plugin instance.

    Raises:
        ImportError: If the module or attribute cannot be imported.
        TypeError: If the resolved object is not a plugin instance.
    """
    import importlib

    from .._runtime._plugin import XRuntimePlugin

    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    if attr:
        obj = getattr(module, attr)
    else:
        # Bare module: expect a top-level ``plugin`` instance.
        obj = getattr(module, "plugin", module)
    if isinstance(obj, XRuntimePlugin):
        return obj
    if isinstance(obj, type) and issubclass(obj, XRuntimePlugin):
        return obj()
    raise TypeError(
        f"{spec!r} did not resolve to an XRuntimePlugin instance",
    )


class _GatewayState:
    """Per-app gateway state: caches + config + resolver.

    Held on ``app.state.xruntime_gateway`` so route handlers can reach
    it. Credential and agent ids are cached per (user, key) so repeated
    requests reuse persisted records instead of creating duplicates.
    """

    def __init__(
        self,
        config: XRuntimeConfig,
        model_resolver: ModelResolver,
    ) -> None:
        """Initialize the gateway state.

        Args:
            config (`XRuntimeConfig`): The resolved config.
            model_resolver (`ModelResolver`): The model resolver.
        """
        self.config = config
        self.model_resolver = model_resolver
        # (user_id, provider_name, api_key) -> credential_id
        self._credential_cache: dict[tuple[str, str, str], str] = {}
        # (user_id, agent_name) -> (agent_id, system_prompt, max_iters)
        self._agent_cache: dict[tuple[str, str], tuple[str, str, int]] = {}

    def credential_id(
        self,
        user_id: str,
        provider_name: str,
        api_key: str,
    ) -> str | None:
        """Return a cached credential id, if any.

        Args:
            user_id (`str`): The user id.
            provider_name (`str`): The provider name.
            api_key (`str`): The api key.

        Returns:
            `str | None`: The cached credential id, or ``None``.
        """
        return self._credential_cache.get(
            (user_id, provider_name, api_key),
        )

    def cache_credential_id(
        self,
        user_id: str,
        provider_name: str,
        api_key: str,
        credential_id: str,
    ) -> None:
        """Cache a credential id.

        Args:
            user_id (`str`): The user id.
            provider_name (`str`): The provider name.
            api_key (`str`): The api key.
            credential_id (`str`): The credential id.
        """
        self._credential_cache[
            (user_id, provider_name, api_key)
        ] = credential_id

    def agent_cache(
        self,
        user_id: str,
        agent_name: str,
    ) -> tuple[str, str, int] | None:
        """Return cached (agent_id, system_prompt, max_iters), if any.

        Args:
            user_id (`str`): The user id.
            agent_name (`str`): The agent name.

        Returns:
            `tuple[str, str, int] | None`: The cached tuple, or ``None``.
        """
        return self._agent_cache.get((user_id, agent_name))

    def cache_agent(
        self,
        user_id: str,
        agent_name: str,
        agent_id: str,
        system_prompt: str,
        max_iters: int,
    ) -> None:
        """Cache an agent id with its config.

        Args:
            user_id (`str`): The user id.
            agent_name (`str`): The agent name.
            agent_id (`str`): The agent id.
            system_prompt (`str`): The system prompt.
            max_iters (`int`): The max iterations.
        """
        self._agent_cache[(user_id, agent_name)] = (
            agent_id,
            system_prompt,
            max_iters,
        )


class _MaterializeError(Exception):
    """Raised when credential/agent/session materialization fails."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
    ) -> None:
        """Initialize the error.

        Args:
            message (`str`): The error message.
            status_code (`int`): The HTTP status code.
        """
        super().__init__(message)
        self.status_code = status_code


def mount_protocol_adapters(
    app: Any,
    registry: AdapterRegistry,
    *,
    config: XRuntimeConfig | None = None,
    model_resolver: ModelResolver | None = None,
) -> None:
    """Mount protocol adapter routes onto an existing AS FastAPI app.

    Each route parses the protocol-specific request into an
    :class:`XRuntimeRequest`, materializes a credential + agent +
    session, triggers ``ChatService.run`` via
    ``chat_run_registry.spawn``, and streams the session's
    ``AgentEvent`` stream back through the adapter as one
    request-response stream.

    Args:
        app (`FastAPI`):
            The AS FastAPI app (from ``create_app``). Must have
            ``chat_service``, ``chat_run_registry``, ``storage``,
            ``message_bus`` on ``app.state`` (set by AS lifespan).
        registry (`AdapterRegistry`):
            The protocol adapter registry.
        config (`XRuntimeConfig | None`):
            The XRuntime config. Defaults to a fresh
            :class:`XRuntimeConfig`.
        model_resolver (`ModelResolver | None`):
            The model resolver. Defaults to a new one.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse, StreamingResponse

    state = _GatewayState(
        config or XRuntimeConfig(),
        model_resolver or ModelResolver(),
    )
    app.state.xruntime_gateway = state

    def _make_handler(
        route_path: str,
        protocol_type: ProtocolType,
    ) -> Any:
        """Create a route handler for one protocol."""

        async def _handler(request: Request) -> Any:
            adapter = registry.get(protocol_type)
            if adapter is None:
                return JSONResponse(
                    {
                        "status": "not_implemented",
                        "message": f"{protocol_type.value} adapter "
                        f"not registered",
                    },
                    status_code=404,
                )

            raw = await request.json()
            headers = {k.lower(): v for k, v in request.headers.items()}
            xrt_request = await adapter.parse_request(
                raw,
                headers=headers,
            )

            # Scope the request to its tenant so downstream code can read
            # the active tenant via ``current_tenant`` without threading
            # it through every call (async-safe via contextvars).
            from .._infra._tenant import current_tenant

            current_tenant.set(xrt_request.tenant_id)

            storage = app.state.storage
            chat_service = app.state.chat_service
            chat_run_registry = app.state.chat_run_registry
            message_bus = app.state.message_bus

            user_id = xrt_request.user_id

            try:
                agent_id, session_id = await _materialize_session(
                    state,
                    storage,
                    xrt_request,
                    user_id,
                    xrt_request.tenant_id,
                )
            except _MaterializeError as exc:
                return JSONResponse(
                    {"detail": str(exc)},
                    status_code=exc.status_code,
                )

            from agentscope.message import UserMsg

            input_msg = UserMsg(
                name=user_id,
                content=xrt_request.prompt,
            )

            async def _stream() -> AsyncGenerator[bytes, None]:
                async for chunk in _serialize_stream(
                    adapter,
                    message_bus,
                    chat_run_registry,
                    chat_service,
                    session_id,
                    user_id,
                    agent_id,
                    input_msg,
                ):
                    yield chunk

            return StreamingResponse(
                _stream(),
                media_type="application/x-ndjson",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        return _handler

    for route_path, protocol_type in _ROUTE_PROTOCOL_MAP.items():
        app.add_api_route(
            route_path,
            _make_handler(route_path, protocol_type),
            methods=["POST"],
        )

    # Prometheus scrape endpoint. The metrics collector is set on
    # app.state.metrics by the server entrypoint (or the gateway state
    # cache); expose it as text for a Prometheus scraper.
    from fastapi import Response

    async def _metrics_handler() -> Response:
        collector = getattr(app.state, "metrics", None)
        if collector is None:
            return Response(
                "",
                media_type="text/plain; version=0.0.4",
                status_code=404,
            )
        return Response(
            collector.export_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app.add_api_route("/metrics", _metrics_handler, methods=["GET"])


async def _serialize_stream(
    adapter: ProtocolAdapter,
    message_bus: Any,
    chat_run_registry: Any,
    chat_service: Any,
    session_id: str,
    user_id: str,
    agent_id: str,
    input_msg: Any,
) -> AsyncGenerator[bytes, None]:
    """Stream a session's AgentEvent stream through the adapter.

    Subscribes live via ``session_subscribe_events`` with an
    ``on_ready`` hook that spawns ``ChatService.run`` (so the
    subscription is established before the run publishes, matching AS's
    canonical pattern and avoiding missed events). The live event
    stream is fed to the adapter as one continuous stream so the
    adapter's cross-event state accumulates correctly. The stream
    closes on the terminal event or when the run finishes without one.

    Args:
        adapter (`ProtocolAdapter`): The protocol adapter.
        message_bus (`MessageBus`): The AS message bus.
        chat_run_registry (`ChatRunRegistry`): The run registry.
        chat_service (`ChatService`): The chat service.
        session_id (`str`): The session id.
        user_id (`str`): The user id.
        agent_id (`str`): The agent id.
        input_msg (`Msg`): The input message.

    Yields:
        `bytes`: Protocol-specific stream chunks from the adapter.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    run_task: list[asyncio.Task] = []

    def _on_ready() -> None:
        """Spawn the chat run once the subscription is live."""
        try:
            run_task.append(
                chat_run_registry.spawn(
                    chat_service.run(
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                        input_msg=input_msg,
                    ),
                    session_id=session_id,
                ),
            )
        except Exception:  # noqa: BLE001
            # e.g. a run is already active for this session — unblock
            # the stream so it closes cleanly instead of hanging.
            queue.put_nowait(None)

    async def _feeder() -> None:
        """Forward live session events into the queue."""
        try:
            async for evt in message_bus.session_subscribe_events(
                session_id,
                on_ready=_on_ready,
            ):
                await queue.put(evt)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            await queue.put(None)

    feeder_task = asyncio.create_task(
        _feeder(),
        name=f"xruntime-feeder:{session_id}",
    )

    async def _events() -> AsyncGenerator[dict, None]:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=_RUN_DONE_POLL_SECS,
                )
            except asyncio.TimeoutError:
                # No event this window. If the run finished and the
                # queue is drained, no more events will arrive.
                if run_task and run_task[0].done() and queue.empty():
                    return
                continue
            if item is None:
                return
            yield item
            if item.get("type") in _TERMINAL_EVENT_TYPES:
                return

    adapter_stream = adapter.serialize_event_stream(_events())
    try:
        async for chunk in adapter_stream:
            yield chunk
    finally:
        # Explicitly close the adapter generator so its ``finally`` /
        # cleanup runs even on the exception / early-exit path (a normal
        # ``async for`` completion already closes it, but a client
        # disconnect mid-stream does not).
        await adapter_stream.aclose()
        feeder_task.cancel()
        try:
            await feeder_task
        except asyncio.CancelledError:
            pass
        if run_task:
            _observe_task(run_task[0])


def _observe_task(task: asyncio.Task) -> None:
    """Surface an unobserved task exception to the log.

    ``ChatService.run`` swallows its own exceptions internally, so this
    usually no-ops; it guards against exceptions raised before the run
    body (e.g. spawn errors) going unobserved.

    Args:
        task (`asyncio.Task`): The task to observe.
    """
    if not task.done():
        return
    exc = task.exception()
    if exc is not None:
        try:
            from agentscope._logging import logger

            logger.error("Chat run task raised: %s", exc)
        except Exception:  # noqa: BLE001
            pass


async def _materialize_session(
    state: _GatewayState,
    storage: Any,
    request: XRuntimeRequest,
    user_id: str,
    tenant_id: str,
) -> tuple[str, str]:
    """Materialize a credential + agent + session for a request.

    Ensures the persisted records exist so ``ChatService.run`` does not
    404. Credentials and agents are cached per (user, key) for reuse;
    sessions are created fresh unless the request resumes an existing
    one (``session_id`` or Claude Code ``continue_conversation``).

    Args:
        state (`_GatewayState`): The gateway state.
        storage (`StorageBase`): The AS storage backend.
        request (`XRuntimeRequest`): The unified request.
        user_id (`str`): The user id.
        tenant_id (`str`): The tenant id.

    Returns:
        `tuple[str, str]`: The ``(agent_id, session_id)``.

    Raises:
        `_MaterializeError`: If the model provider cannot be resolved
            or a resumed session does not exist.
    """
    provider = state.model_resolver.resolve_provider(
        _resolve_model_config_name(state, request),
        state.config,
    )
    if provider is None:
        raise _MaterializeError(
            "No model provider configured. Set XRUNTIME_MODEL_PROVIDER "
            "and XRUNTIME_MODEL_API_KEY, or model_providers in config.",
            status_code=400,
        )

    # 1. Credential (cached per user + provider + api_key)
    credential_id = state.credential_id(
        user_id,
        provider.name,
        provider.api_key,
    )
    if credential_id is None:
        try:
            credential = state.model_resolver.build_credential(provider)
        except ValueError as exc:
            raise _MaterializeError(str(exc), status_code=400) from exc
        credential_id = await storage.upsert_credential(
            user_id,
            credential,
        )
        state.cache_credential_id(
            user_id,
            provider.name,
            provider.api_key,
            credential_id,
        )
    cred_type = state.model_resolver.credential_type(provider.name)

    # 2. Agent (cached per user + name; re-upsert when config changes)
    agent_name = request.metadata.get("agent_name") or _DEFAULT_AGENT_NAME
    system_prompt = (
        request.system_prompt
        or _blueprint_system_prompt(state, agent_name)
        or _DEFAULT_SYSTEM_PROMPT
    )
    max_iters = (
        request.max_turns
        or _blueprint_max_iters(state, agent_name)
        or _DEFAULT_MAX_ITERS
    )
    agent_id = await _ensure_agent(
        state,
        storage,
        user_id,
        agent_name,
        system_prompt,
        max_iters,
    )

    # 3. Session
    chat_model_config = _build_chat_model_config(
        cred_type,
        credential_id,
        provider.model,
    )
    permission_context = _build_permission_context(request)

    session_id = request.session_id
    if session_id == "__continue__":
        sessions = await storage.list_sessions(user_id, agent_id)
        session_id = sessions[0].id if sessions else None

    if session_id:
        existing = await storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if existing is None:
            raise _MaterializeError(
                f"Session '{session_id}' not found.",
                status_code=404,
            )
        updated_state = existing.state.model_copy(
            update={"permission_context": permission_context},
        )
        await storage.upsert_session(
            user_id=user_id,
            agent_id=agent_id,
            config=existing.config,
            state=updated_state,
            session_id=session_id,
        )
    else:
        from agentscope.app.storage import SessionConfig
        from agentscope.state import AgentState

        workspace_id = f"xruntime:{tenant_id}"
        session_record = await storage.upsert_session(
            user_id=user_id,
            agent_id=agent_id,
            config=SessionConfig(
                workspace_id=workspace_id,
                chat_model_config=chat_model_config,
            ),
            state=AgentState(permission_context=permission_context),
        )
        session_id = session_record.id

    return agent_id, session_id


def _resolve_model_config_name(
    state: _GatewayState,
    request: XRuntimeRequest,
) -> str | None:
    """Resolve a model_config_name for a request.

    For OpenCode (which carries an ``agent_name``), look up the
    matching agent blueprint's ``model_config_name``. Otherwise return
    ``None`` so the resolver falls back to env vars.

    Args:
        state (`_GatewayState`): The gateway state.
        request (`XRuntimeRequest`): The unified request.

    Returns:
        `str | None`: The model config name, or ``None``.
    """
    agent_name = request.metadata.get("agent_name")
    if not agent_name:
        return None
    for blueprint in state.config.agents:
        if blueprint.name == agent_name:
            return blueprint.model_config_name or None
    return None


def _blueprint_system_prompt(
    state: _GatewayState,
    agent_name: str,
) -> str | None:
    """Return a blueprint's system prompt, if any.

    Args:
        state (`_GatewayState`): The gateway state.
        agent_name (`str`): The agent name.

    Returns:
        `str | None`: The blueprint system prompt, or ``None``.
    """
    for blueprint in state.config.agents:
        if blueprint.name == agent_name:
            return blueprint.system_prompt or None
    return None


def _blueprint_max_iters(
    state: _GatewayState,
    agent_name: str,
) -> int | None:
    """Return a blueprint's max iterations, if configured.

    Args:
        state (`_GatewayState`): The gateway state.
        agent_name (`str`): The agent name.

    Returns:
        `int | None`: The max iterations, or ``None``.
    """
    for blueprint in state.config.agents:
        if blueprint.name == agent_name:
            return blueprint.max_iters
    return None


async def _ensure_agent(
    state: _GatewayState,
    storage: Any,
    user_id: str,
    agent_name: str,
    system_prompt: str,
    max_iters: int,
) -> str:
    """Ensure an agent record exists (cached), re-upserting on change.

    Args:
        state (`_GatewayState`): The gateway state.
        storage (`StorageBase`): The AS storage backend.
        user_id (`str`): The user id.
        agent_name (`str`): The agent name.
        system_prompt (`str`): The system prompt.
        max_iters (`int`): The max iterations.

    Returns:
        `str`: The agent id.
    """
    from agentscope.agent import ContextConfig, ReActConfig
    from agentscope.app.storage import AgentData, AgentRecord

    cached = state.agent_cache(user_id, agent_name)
    if (
        cached is not None
        and cached[1] == system_prompt
        and cached[2] == max_iters
    ):
        return cached[0]

    agent_id = cached[0] if cached is not None else None
    data_kwargs: dict[str, Any] = {
        "name": agent_name,
        "system_prompt": system_prompt,
        "context_config": ContextConfig(),
        "react_config": ReActConfig(max_iters=max_iters),
    }
    if agent_id is not None:
        data_kwargs["id"] = agent_id
    agent_record = AgentRecord(
        user_id=user_id,
        data=AgentData(**data_kwargs),
    )
    agent_id = await storage.upsert_agent(user_id, agent_record)
    state.cache_agent(
        user_id,
        agent_name,
        agent_id,
        system_prompt,
        max_iters,
    )
    return agent_id


def _build_chat_model_config(
    cred_type: str,
    credential_id: str,
    model: str,
) -> Any:
    """Build a ``ChatModelConfig`` referencing a persisted credential.

    Args:
        cred_type (`str`): The credential type discriminator.
        credential_id (`str`): The persisted credential id.
        model (`str`): The model name.

    Returns:
        `ChatModelConfig`: The model config.
    """
    from agentscope.app.storage import ChatModelConfig

    return ChatModelConfig(
        type=cred_type,
        credential_id=credential_id,
        model=model,
        parameters={},
    )


def _build_permission_context(request: XRuntimeRequest) -> Any:
    """Build a ``PermissionContext`` from a request's permission fields.

    Maps ``permission_mode`` to an AS :class:`PermissionMode`,
    ``allowed_tools`` to allow rules, and ``disallowed_tools`` to deny
    rules (deny applies to all calls of that tool name).

    Args:
        request (`XRuntimeRequest`): The unified request.

    Returns:
        `PermissionContext`: The permission context.
    """
    from agentscope.permission import (
        PermissionBehavior,
        PermissionContext,
        PermissionMode,
        PermissionRule,
    )

    try:
        mode = PermissionMode(request.permission_mode)
    except ValueError:
        mode = PermissionMode.DEFAULT

    allow_rules: dict[str, list[Any]] = {
        tool: [
            PermissionRule(
                tool_name=tool,
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="xruntime",
            ),
        ]
        for tool in request.allowed_tools
    }
    deny_rules: dict[str, list[Any]] = {
        tool: [
            PermissionRule(
                tool_name=tool,
                rule_content=None,
                behavior=PermissionBehavior.DENY,
                source="xruntime",
            ),
        ]
        for tool in request.disallowed_tools
    }
    return PermissionContext(
        mode=mode,
        allow_rules=allow_rules,
        deny_rules=deny_rules,
    )
