# -*- coding: utf-8 -*-
"""Plugin system — XRuntimePlugin interface and registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginContext:
    """Context passed to plugins during initialization.

    Args:
        config (`dict[str, Any]`):
            The XRuntime configuration dict.
        adapter_registry (Any):
            The protocol adapter registry (for registering custom
            adapters).
        middleware_registry (Any):
            The middleware registry (for registering custom
            middlewares).
    """

    config: dict[str, Any] = field(default_factory=dict)
    adapter_registry: Any = None
    middleware_registry: Any = None


class XRuntimePlugin(ABC):
    """Abstract base for XRuntime plugins.

    Subclasses define:
        - ``name``: unique plugin name.
        - ``version``: plugin version string.
        - ``initialize``: called at startup with a
          :class:`PluginContext`.
        - ``shutdown``: called at shutdown for cleanup.
    """

    name: str = ""
    version: str = "0.0.0"

    @abstractmethod
    def initialize(self, context: PluginContext) -> None:
        """Initialize the plugin.

        Args:
            context (`PluginContext`):
                The plugin context with config and registries.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """Shut down the plugin and release resources."""


class PluginRegistry:
    """Registry for XRuntime plugins.

    Plugins are registered at startup, initialized with a
    :class:`PluginContext`, and shut down in reverse order on
    shutdown.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._plugins: dict[str, XRuntimePlugin] = {}
        self._initialized: bool = False

    def register(self, plugin: XRuntimePlugin) -> None:
        """Register a plugin.

        Args:
            plugin (`XRuntimePlugin`):
                The plugin instance to register.
        """
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> XRuntimePlugin | None:
        """Look up a plugin by name.

        Args:
            name (`str`):
                The plugin name.

        Returns:
            `XRuntimePlugin | None`: The plugin, or ``None``.
        """
        return self._plugins.get(name)

    def list_plugins(self) -> list[str]:
        """List all registered plugin names.

        Returns:
            `list[str]`: Registered plugin names.
        """
        return list(self._plugins.keys())

    def initialize_all(self, context: PluginContext) -> None:
        """Initialize all registered plugins.

        Args:
            context (`PluginContext`):
                The context to pass to each plugin.
        """
        for plugin in self._plugins.values():
            plugin.initialize(context)
        self._initialized = True

    def shutdown_all(self) -> None:
        """Shut down all registered plugins in reverse order."""
        for plugin in reversed(list(self._plugins.values())):
            plugin.shutdown()
        self._initialized = False
