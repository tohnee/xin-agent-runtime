# -*- coding: utf-8 -*-
"""XRuntime server entrypoint for Docker deployment.

XRuntime is an *extension* of AgentScope, not a standalone runtime.
This entrypoint assembles the full stack: AS ``create_app`` +
:func:`create_xruntime_extension` + :func:`mount_protocol_adapters`,
then runs it under uvicorn.
"""
import os
from typing import Any

from xruntime._config import XRuntimeConfig, load_config
from xruntime._gateway._extension import (
    create_xruntime_extension,
    mount_protocol_adapters,
)


def build_xruntime_app(config: XRuntimeConfig | None = None) -> Any:
    """Assemble the AS + XRuntime FastAPI app.

    Wires storage / message bus / workspace manager from the config
    and mounts the three protocol adapter routes.

    Args:
        config (`XRuntimeConfig | None`):
            Pre-built config. ``None`` loads from env / default path.

    Returns:
        `FastAPI`: The configured AS app with XRuntime routes mounted.
    """
    if config is None:
        config_path = os.environ.get("XRUNTIME_CONFIG_PATH")
        config = load_config(config_path)

    # Configure OpenTelemetry tracing when enabled in observability
    # config (consumes otel_enabled / otel_endpoint, previously dead).
    if config.observability.otel_enabled:
        _setup_otel(config.observability.otel_endpoint)

    # Build auth stores from env so AuthMiddleware resolves API keys
    # and JWT tokens to tenant-bound principals (not just anonymous
    # key-matching). Created before ``create_xruntime_extension`` so
    # the middleware factory can use the membership store.
    from xruntime._runtime._tenant._store import (
        ApiKeyRecord,
        ApiKeyStore,
        JwtClaimsParser,
        TenantMembershipStore,
    )
    from xruntime._runtime._tenant import TenantRole

    api_key_store: ApiKeyStore | None = None
    jwt_parser: JwtClaimsParser | None = None
    membership_store = TenantMembershipStore()

    import json as _json

    records_env = os.environ.get("XRUNTIME_API_KEY_RECORDS", "")
    if records_env:
        try:
            records_data = _json.loads(records_env)
            records = [
                ApiKeyRecord(
                    key=r["key"],
                    tenant_id=r["tenant_id"],
                    user_id=r["user_id"],
                    role=TenantRole(r.get("role", "viewer")),
                    kb_ids=r.get("kb_ids", []),
                    key_id=r.get("key_id"),
                    active=r.get("active", True),
                )
                for r in records_data
            ]
            api_key_store = ApiKeyStore(records)
            for r in records:
                membership_store.upsert(
                    tenant_id=r.tenant_id,
                    user_id=r.user_id,
                    role=r.role,
                )
        except (ValueError, KeyError, _json.JSONDecodeError):
            pass

    jwt_secret = os.environ.get("XRUNTIME_JWT_SECRET", "")
    if jwt_secret:
        jwt_parser = JwtClaimsParser(secret=jwt_secret)

    ext = create_xruntime_extension(
        config=config,
        membership_store=membership_store,
    )

    from agentscope.app import create_app
    from agentscope.app.storage import RedisStorage
    from agentscope.app.message_bus import RedisMessageBus

    # Apply the tenant key-prefix so this runtime's Redis keys are
    # namespaced under ``config.storage.tenant_prefix`` (full key-prefix
    # multi-tenant isolation per XRUNTIME-DESIGN). The tenant id comes
    # from the first configured tenant, falling back to "default".
    tenant_id = config.tenants[0].id if config.tenants else "default"

    from xruntime._infra._tenant import build_tenant_key_config

    key_config = build_tenant_key_config(
        tenant_id,
        config.storage.tenant_prefix,
    )

    storage = RedisStorage(
        host=config.storage.redis_host,
        port=config.storage.redis_port,
        db=config.storage.redis_db,
        password=config.storage.redis_password,
        key_config=key_config,
    )
    message_bus = RedisMessageBus(
        host=config.message_bus.redis_host,
        port=config.message_bus.redis_port,
        db=config.message_bus.redis_db,
    )

    # Workspace backend selection via WorkspaceManagerFactory.
    # In production, defaults to docker; local requires explicit
    # ``allow_local_in_production`` override.
    from xruntime._runtime._workspace import (
        WorkspaceConfig,
        WorkspaceManagerFactory,
    )

    ws_backend = os.environ.get(
        "XRUNTIME_WORKSPACE_BACKEND",
        "local",
    )
    ws_production = os.environ.get(
        "XRUNTIME_PRODUCTION",
        "",
    ).lower() in ("1", "true", "yes")
    ws_config = WorkspaceConfig(
        default_backend=ws_backend,
        allow_local_in_production=os.environ.get(
            "XRUNTIME_ALLOW_LOCAL_WORKSPACE",
            "",
        ).lower()
        in ("1", "true", "yes"),
        base_dir=os.environ.get(
            "XRUNTIME_WORKSPACE_DIR",
            "./xruntime-workspaces",
        ),
    )
    ws_factory = WorkspaceManagerFactory(ws_config)
    workspace_manager = ws_factory.create(
        backend=ws_backend,
        production=ws_production,
    )

    app = create_app(
        storage=storage,
        message_bus=message_bus,
        workspace_manager=workspace_manager,
        extra_agent_middlewares=ext["extra_agent_middlewares"],
    )

    # Wire gateway-level ASGI middleware (auth + rate limiting).
    from xruntime._gateway._auth import AuthMiddleware
    from xruntime._gateway._ratelimit import RateLimiter

    if config.server.auth_enabled:
        api_keys = {
            k.strip()
            for k in os.environ.get(
                "XRUNTIME_API_KEYS",
                "",
            ).split(",")
            if k.strip()
        }
        app.add_middleware(
            AuthMiddleware,
            api_keys=api_keys,
            api_key_store=api_key_store,
            jwt_parser=jwt_parser,
        )

    rate_limit = os.environ.get("XRUNTIME_RATE_LIMIT", "")
    if rate_limit:
        from xruntime._gateway._ratelimit import RateLimitMiddleware

        parts = rate_limit.split("/")
        max_req = int(parts[0]) if parts[0] else 100
        window = float(parts[1]) if len(parts) > 1 else 60.0
        limiter = RateLimiter(
            max_requests=max_req,
            window_seconds=window,
        )
        app.state.rate_limiter = limiter
        # Actually enforce the limit via an ASGI middleware (storing the
        # limiter on app.state alone does nothing).
        app.add_middleware(RateLimitMiddleware, limiter=limiter)

    # Expose the metrics collector on app.state for route handlers.
    state_cache = ext.get("middleware_state_cache")
    if state_cache is not None:
        app.state.metrics = state_cache.metrics

    # Retain the plugin registry so its lifecycle is managed and expose
    # it on app.state; register a shutdown handler so ``shutdown_all``
    # runs when the app stops.
    plugin_registry = ext.get("plugin_registry")
    if plugin_registry is not None:
        app.state.plugin_registry = plugin_registry

        @app.on_event("shutdown")
        async def _shutdown_plugins() -> None:
            plugin_registry.shutdown_all()

    # Health / readiness endpoints (the SDK probes GET /health and
    # /ready; AS does not register them, so add them here).
    _mount_health_routes(app)

    mount_protocol_adapters(
        app,
        ext["adapter_registry"],
        config=ext.get("config"),
        model_resolver=ext.get("model_resolver"),
    )
    return app


def _setup_otel(endpoint: str) -> bool:
    """Configure an OpenTelemetry tracer provider for XRuntime.

    Installs a global :class:`TracerProvider`; when ``endpoint`` is set
    an OTLP gRPC span exporter is attached so AS's tracing middleware
    actually exports spans. A missing ``opentelemetry`` install is
    logged and skipped (tracing is optional).

    Args:
        endpoint (`str`): OTLP exporter endpoint (e.g.
            ``"http://localhost:4317"``). Empty means no exporter
            (provider only).

    Returns:
        `bool`: ``True`` if a provider was installed, ``False`` if the
        optional ``opentelemetry`` dependency is unavailable.
    """
    import logging

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        logging.getLogger("xruntime").warning(
            "otel_enabled is set but opentelemetry is not installed; "
            "skipping tracing setup.",
        )
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": "xruntime"}),
    )

    if endpoint:
        try:
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
            )
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: E501
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint),
                ),
            )
        except ImportError:
            logging.getLogger("xruntime").warning(
                "OTLP exporter unavailable; tracing provider installed "
                "without an exporter.",
            )

    trace.set_tracer_provider(provider)
    return True


def _mount_health_routes(app: Any) -> None:
    """Mount ``/health`` and ``/ready`` liveness/readiness routes.

    Args:
        app (`FastAPI`): The app to mount routes onto.
    """

    async def _health() -> dict[str, str]:
        return {"status": "ok"}

    async def _ready() -> dict[str, str]:
        # Ready once the core AS state (storage + chat service) is wired.
        ready = all(
            getattr(app.state, attr, None) is not None
            for attr in ("storage", "chat_service")
        )
        return {"status": "ready" if ready else "not_ready"}

    app.add_api_route("/health", _health, methods=["GET"])
    app.add_api_route("/ready", _ready, methods=["GET"])


def main() -> None:
    """Run the XRuntime server."""
    import uvicorn

    config_path = os.environ.get("XRUNTIME_CONFIG_PATH")
    config = load_config(config_path)

    app = build_xruntime_app(config=config)

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
    )


if __name__ == "__main__":
    main()
