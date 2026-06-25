# -*- coding: utf-8 -*-
"""Tests for the XRuntime server entrypoint (Bug 1 fix).

Verifies that ``xruntime._server`` no longer imports the deleted
``_gateway._app`` module, and that ``build_xruntime_app`` assembles
the AS + XRuntime stack correctly via the extension pattern.
"""
from unittest.mock import patch, MagicMock


class TestServerEntrypoint:
    """Tests for the fixed server entrypoint."""

    def test_server_module_imports_without_error(self) -> None:
        """_server.py should import cleanly (no missing _app module)."""
        import xruntime._server  # noqa: F401

    def test_build_xruntime_app_exists(self) -> None:
        """build_xruntime_app should be exported from _server."""
        from xruntime._server import build_xruntime_app

        assert callable(build_xruntime_app)

    def test_server_does_not_import_deleted_app(self) -> None:
        """_server must not reference xruntime._gateway._app."""
        import inspect
        from xruntime import _server

        source = inspect.getsource(_server)
        assert "_gateway._app" not in source
        assert "create_xruntime_app" not in source

    def test_main_exists_and_is_callable(self) -> None:
        """main() entrypoint should exist."""
        from xruntime._server import main

        assert callable(main)

    def test_build_app_uses_extension_pattern(self) -> None:
        """build_xruntime_app should call create_app + mount adapters."""
        import sys
        from types import ModuleType
        from xruntime._config import XRuntimeConfig
        from xruntime._server import build_xruntime_app

        config = XRuntimeConfig()

        mock_as_app = ModuleType("agentscope.app")
        mock_as_app.create_app = MagicMock(return_value=MagicMock())
        mock_as_storage = ModuleType("agentscope.app.storage")
        mock_as_storage.RedisStorage = MagicMock(
            return_value=MagicMock(),
        )
        mock_as_bus = ModuleType("agentscope.app.message_bus")
        mock_as_bus.RedisMessageBus = MagicMock(
            return_value=MagicMock(),
        )
        mock_as_wm = ModuleType("agentscope.app.workspace_manager")
        mock_as_wm.LocalWorkspaceManager = MagicMock(
            return_value=MagicMock(),
        )

        fake_modules = {
            "agentscope.app": mock_as_app,
            "agentscope.app.storage": mock_as_storage,
            "agentscope.app.message_bus": mock_as_bus,
            "agentscope.app.workspace_manager": mock_as_wm,
        }

        with patch.dict(sys.modules, fake_modules), patch(
            "xruntime._server.create_xruntime_extension"
        ) as mock_ext, patch(
            "xruntime._server.mount_protocol_adapters"
        ) as mock_mount:
            mock_ext.return_value = {
                "extra_agent_middlewares": MagicMock(),
                "adapter_registry": MagicMock(),
                "config": config,
            }

            build_xruntime_app(config=config)

            mock_ext.assert_called_once()
            mock_as_app.create_app.assert_called_once()
            mock_mount.assert_called_once()


class TestRateLimitMiddleware:
    """Tests for the RateLimitMiddleware ASGI enforcement (issue #11)."""

    async def test_allows_within_limit(self) -> None:
        """Requests within the limit pass through."""
        from httpx import ASGITransport, AsyncClient
        from fastapi import FastAPI
        from xruntime._gateway._ratelimit import (
            RateLimiter,
            RateLimitMiddleware,
        )

        app = FastAPI()

        @app.get("/v1/ping")
        async def _ping() -> dict:
            return {"ok": True}

        app.add_middleware(
            RateLimitMiddleware,
            limiter=RateLimiter(max_requests=2, window_seconds=60.0),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            r1 = await client.get("/v1/ping", headers={"x-api-key": "k1"})
            r2 = await client.get("/v1/ping", headers={"x-api-key": "k1"})
        assert r1.status_code == 200
        assert r2.status_code == 200

    async def test_blocks_over_limit(self) -> None:
        """Requests over the limit get a 429."""
        from httpx import ASGITransport, AsyncClient
        from fastapi import FastAPI
        from xruntime._gateway._ratelimit import (
            RateLimiter,
            RateLimitMiddleware,
        )

        app = FastAPI()

        @app.get("/v1/ping")
        async def _ping() -> dict:
            return {"ok": True}

        app.add_middleware(
            RateLimitMiddleware,
            limiter=RateLimiter(max_requests=1, window_seconds=60.0),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            ok = await client.get("/v1/ping", headers={"x-api-key": "k1"})
            blocked = await client.get("/v1/ping", headers={"x-api-key": "k1"})
        assert ok.status_code == 200
        assert blocked.status_code == 429

    async def test_health_route_exempt(self) -> None:
        """Health probes are never throttled."""
        from httpx import ASGITransport, AsyncClient
        from fastapi import FastAPI
        from xruntime._gateway._ratelimit import (
            RateLimiter,
            RateLimitMiddleware,
        )

        app = FastAPI()

        @app.get("/health")
        async def _health() -> dict:
            return {"status": "ok"}

        app.add_middleware(
            RateLimitMiddleware,
            limiter=RateLimiter(max_requests=1, window_seconds=60.0),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            for _ in range(3):
                resp = await client.get("/health")
                assert resp.status_code == 200

    async def test_separate_clients_independent(self) -> None:
        """Different api keys have independent limits."""
        from httpx import ASGITransport, AsyncClient
        from fastapi import FastAPI
        from xruntime._gateway._ratelimit import (
            RateLimiter,
            RateLimitMiddleware,
        )

        app = FastAPI()

        @app.get("/v1/ping")
        async def _ping() -> dict:
            return {"ok": True}

        app.add_middleware(
            RateLimitMiddleware,
            limiter=RateLimiter(max_requests=1, window_seconds=60.0),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            a = await client.get("/v1/ping", headers={"x-api-key": "ka"})
            b = await client.get("/v1/ping", headers={"x-api-key": "kb"})
        assert a.status_code == 200
        assert b.status_code == 200


class TestHealthRoutes:
    """Tests for /health and /ready routes (issue #19)."""

    def test_mount_health_routes_adds_both(self) -> None:
        """_mount_health_routes registers /health and /ready."""
        from unittest.mock import MagicMock
        from xruntime._server import _mount_health_routes

        app = MagicMock()
        _mount_health_routes(app)
        paths = [c.args[0] for c in app.add_api_route.call_args_list]
        assert "/health" in paths
        assert "/ready" in paths

    async def test_health_and_ready_respond(self) -> None:
        """/health and /ready return JSON status."""
        from httpx import ASGITransport, AsyncClient
        from fastapi import FastAPI
        from xruntime._server import _mount_health_routes

        app = FastAPI()
        app.state.storage = object()
        app.state.chat_service = object()
        _mount_health_routes(app)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            health = await client.get("/health")
            ready = await client.get("/ready")
        assert health.json() == {"status": "ok"}
        assert ready.json() == {"status": "ready"}


class TestLazyImports:
    """Lazy-import conformance (issue #21)."""

    def test_server_no_top_level_uvicorn(self) -> None:
        """_server must not import uvicorn at module top."""
        import inspect
        from xruntime import _server

        src = inspect.getsource(_server)
        head = src.split("def build_xruntime_app")[0]
        assert "import uvicorn" not in head

    def test_orchestrator_no_top_level_yaml(self) -> None:
        """_orchestrator must not import yaml at module top."""
        import inspect
        from xruntime._runtime import _orchestrator

        src = inspect.getsource(_orchestrator)
        head = src.split("class WorkflowStatus")[0]
        assert "import yaml" not in head


class TestBuildAppTypeAnnotation:
    """build_xruntime_app must be type-annotated (issue #20)."""

    def test_param_and_return_annotated(self) -> None:
        """The config param and return are annotated."""
        import inspect
        from xruntime._server import build_xruntime_app

        sig = inspect.signature(build_xruntime_app)
        assert sig.parameters["config"].annotation is not inspect._empty
        assert sig.return_annotation is not inspect._empty


class TestOtelSetup:
    """Tests for OTel tracing setup (issue #17)."""

    def test_setup_otel_installs_provider(self) -> None:
        """_setup_otel installs a TracerProvider when otel is present."""
        from xruntime._server import _setup_otel

        result = _setup_otel("")
        # opentelemetry is a dependency in this repo, so it installs.
        assert result is True

    def test_observability_config_reads_otel_fields(self) -> None:
        """build_xruntime_app calls _setup_otel when otel_enabled."""
        import inspect
        from xruntime import _server

        src = inspect.getsource(_server.build_xruntime_app)
        assert "otel_enabled" in src
        assert "_setup_otel" in src
