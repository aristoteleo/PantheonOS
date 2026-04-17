"""
Centralized plugin registry for Pantheon.

All system plugins register here. ChatRoom and Factory both call
create_plugins() to get the same set of enabled plugins.

To add a new plugin:
1. Create your plugin class inheriting from TeamPlugin
2. Add a _create_xxx_plugin(config, settings) factory function
3. Call register_plugin() at module level
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from pantheon.team.plugin import TeamPlugin

logger = logging.getLogger(__name__)


@dataclass
class PluginDef:
    """Declarative plugin definition for the registry."""

    name: str
    config_key: str  # settings.json section key (e.g., "memory_system")
    enabled_key: str  # key within config to check (e.g., "enabled")
    factory: Callable[[dict, Any], "TeamPlugin | None"]
    priority: int = 100  # lower = earlier in hook execution order


_registry: list[PluginDef] = []


def register_plugin(plugin_def: PluginDef) -> None:
    """Register a plugin definition. Called at module import time."""
    _registry.append(plugin_def)
    _registry.sort(key=lambda p: p.priority)


def create_plugins(settings: Any) -> list["TeamPlugin"]:
    """Create all enabled plugins from settings.

    Used by both ChatRoom._ensure_plugins() and factory.create_team_from_template().
    Returns plugins sorted by priority (lower priority number = earlier execution).
    """
    _ensure_plugins_registered()

    plugins: list["TeamPlugin"] = []
    for pdef in _registry:
        try:
            config = _get_config(settings, pdef.config_key)
            if not config.get(pdef.enabled_key):
                continue
            plugin = pdef.factory(config, settings)
            if plugin is not None:
                plugins.append(plugin)
                logger.debug(f"Plugin '{pdef.name}' created (priority={pdef.priority})")
        except Exception as e:
            logger.warning(f"Failed to create plugin '{pdef.name}': {e}")
    return plugins


def _get_config(settings: Any, config_key: str) -> dict:
    """Get plugin config from settings by key."""
    # Try get_<config_key>_config() first (e.g., get_compression_config)
    getter_name = f"get_{config_key}_config"
    getter = getattr(settings, getter_name, None)
    if getter and callable(getter):
        return getter()
    # Fallback: generic get_section (works for any plugin, including third-party)
    if hasattr(settings, "get_section"):
        return settings.get_section(config_key)
    settings._ensure_loaded()
    return settings._settings.get(config_key, {})


def _ensure_plugins_registered() -> None:
    """Import plugin modules to trigger their register_plugin() calls.

    This is idempotent — modules are only imported once by Python.
    """
    import pantheon.internal.task_system.plugin  # noqa: F401
    import pantheon.internal.compression.plugin  # noqa: F401
    import pantheon.internal.memory_system.plugin  # noqa: F401
    import pantheon.internal.learning_system.plugin  # noqa: F401
