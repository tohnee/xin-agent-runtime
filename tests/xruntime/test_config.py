# -*- coding: utf-8 -*-
"""Tests for XRuntimeConfig schema, loading, and env-var override."""
import os
import tempfile
from pathlib import Path

import pytest

from xruntime._config import (
    ServerConfig,
    StorageConfig,
    MessageBusConfig,
    TenantConfig,
    PermissionConfig,
    ObservabilityConfig,
    XRuntimeConfig,
    load_config,
)


class TestServerConfig:
    """Tests for ServerConfig."""

    def test_defaults(self) -> None:
        """Default server config should bind to 0.0.0.0:8900."""
        cfg = ServerConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8900
        assert cfg.auth_enabled is True

    def test_custom_values(self) -> None:
        """Custom values should be respected."""
        cfg = ServerConfig(host="127.0.0.1", port=9999, auth_enabled=False)
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9999
        assert cfg.auth_enabled is False


class TestStorageConfig:
    """Tests for StorageConfig."""

    def test_defaults(self) -> None:
        """Default storage backend should be redis."""
        cfg = StorageConfig()
        assert cfg.backend == "redis"
        assert cfg.redis_host == "localhost"
        assert cfg.redis_port == 6379
        assert cfg.redis_db == 0

    def test_tenant_prefix(self) -> None:
        """Tenant isolation prefix should be applied."""
        cfg = StorageConfig(tenant_prefix="tenant:{tid}:")
        assert cfg.tenant_prefix == "tenant:{tid}:"


class TestMessageBusConfig:
    """Tests for MessageBusConfig."""

    def test_defaults(self) -> None:
        """Default message bus backend should be redis."""
        cfg = MessageBusConfig()
        assert cfg.backend == "redis"
        assert cfg.redis_host == "localhost"
        assert cfg.redis_port == 6379


class TestPermissionConfig:
    """Tests for PermissionConfig."""

    def test_defaults(self) -> None:
        """Default permission mode should be default."""
        cfg = PermissionConfig()
        assert cfg.mode == "default"
        assert cfg.rules == []


class TestXRuntimeConfig:
    """Tests for the top-level XRuntimeConfig."""

    def test_defaults(self) -> None:
        """Default config should have all sections populated."""
        cfg = XRuntimeConfig()
        assert cfg.server.port == 8900
        assert cfg.storage.backend == "redis"
        assert cfg.message_bus.backend == "redis"
        assert cfg.tenants == []
        assert cfg.permission.mode == "default"
        assert cfg.agents == []
        assert cfg.mcps == []
        assert cfg.skills == []
        assert cfg.plugins == []

    def test_with_tenants(self) -> None:
        """Config with tenants should parse correctly."""
        cfg = XRuntimeConfig(
            tenants=[
                TenantConfig(id="acme", name="ACME Corp"),
            ],
        )
        assert len(cfg.tenants) == 1
        assert cfg.tenants[0].id == "acme"


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_from_yaml_file(self, tmp_path: Path) -> None:
        """Config should load from a YAML file."""
        yaml_content = (
            "server:\n"
            "  host: 127.0.0.1\n"
            "  port: 9999\n"
            "storage:\n"
            "  backend: redis\n"
            "  redis_host: redis.example.com\n"
            "tenants:\n"
            "  - id: acme\n"
            "    name: ACME Corp\n"
        )
        config_file = tmp_path / "xruntime.yaml"
        config_file.write_text(yaml_content)

        cfg = load_config(str(config_file))
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9999
        assert cfg.storage.redis_host == "redis.example.com"
        assert len(cfg.tenants) == 1
        assert cfg.tenants[0].id == "acme"

    def test_env_var_override(self, tmp_path: Path) -> None:
        """Env vars with XRUNTIME_ prefix should override file values."""
        yaml_content = "server:\n  port: 8900\n"
        config_file = tmp_path / "xruntime.yaml"
        config_file.write_text(yaml_content)

        old_val = os.environ.get("XRUNTIME_SERVER_PORT")
        os.environ["XRUNTIME_SERVER_PORT"] = "7777"
        try:
            cfg = load_config(str(config_file))
            assert cfg.server.port == 7777
        finally:
            if old_val is not None:
                os.environ["XRUNTIME_SERVER_PORT"] = old_val
            else:
                del os.environ["XRUNTIME_SERVER_PORT"]

    def test_env_var_override_storage(self, tmp_path: Path) -> None:
        """Env var should override storage redis host."""
        config_file = tmp_path / "xruntime.yaml"
        config_file.write_text("storage:\n  redis_host: localhost\n")

        old_val = os.environ.get("XRUNTIME_STORAGE_REDIS_HOST")
        os.environ["XRUNTIME_STORAGE_REDIS_HOST"] = "redis.prod.internal"
        try:
            cfg = load_config(str(config_file))
            assert cfg.storage.redis_host == "redis.prod.internal"
        finally:
            if old_val is not None:
                os.environ["XRUNTIME_STORAGE_REDIS_HOST"] = old_val
            else:
                del os.environ["XRUNTIME_STORAGE_REDIS_HOST"]

    def test_load_nonexistent_file_raises(self) -> None:
        """Loading a nonexistent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/xruntime.yaml")

    def test_load_no_file_returns_defaults(self) -> None:
        """load_config with no file should return defaults."""
        cfg = load_config()
        assert cfg.server.port == 8900
        assert cfg.storage.backend == "redis"
