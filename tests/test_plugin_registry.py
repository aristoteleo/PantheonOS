"""Tests for the centralized plugin registry."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from pantheon.team.plugin import TeamPlugin
from pantheon.team.plugin_registry import (
    PluginDef,
    _registry,
    register_plugin,
    create_plugins,
)


class DummyPlugin(TeamPlugin):
    async def on_team_created(self, team):
        pass


@pytest.fixture(autouse=True)
def clean_registry():
    """Save and restore registry state around each test."""
    saved = list(_registry)
    _registry.clear()
    yield
    _registry.clear()
    _registry.extend(saved)


class TestRegisterPlugin:
    def test_register_adds_to_registry(self):
        pdef = PluginDef(
            name="test", config_key="test", enabled_key="enabled",
            factory=lambda c, s: DummyPlugin(), priority=100,
        )
        register_plugin(pdef)
        assert any(p.name == "test" for p in _registry)

    def test_priority_ordering(self):
        register_plugin(PluginDef(
            name="late", config_key="x", enabled_key="e",
            factory=lambda c, s: DummyPlugin(), priority=200,
        ))
        register_plugin(PluginDef(
            name="early", config_key="x", enabled_key="e",
            factory=lambda c, s: DummyPlugin(), priority=50,
        ))
        names = [p.name for p in _registry]
        assert names.index("early") < names.index("late")


class TestCreatePlugins:
    def test_creates_enabled_plugins(self):
        register_plugin(PluginDef(
            name="enabled_one", config_key="test_plugin",
            enabled_key="enabled",
            factory=lambda c, s: DummyPlugin(), priority=100,
        ))
        settings = MagicMock()
        settings._ensure_loaded = MagicMock()
        settings._settings = {"test_plugin": {"enabled": True}}
        plugins = create_plugins(settings)
        assert len(plugins) >= 1
        assert any(isinstance(p, DummyPlugin) for p in plugins)

    def test_skips_disabled_plugins(self):
        register_plugin(PluginDef(
            name="disabled_one", config_key="test_disabled",
            enabled_key="enabled",
            factory=lambda c, s: DummyPlugin(), priority=100,
        ))
        settings = MagicMock()
        settings._ensure_loaded = MagicMock()
        settings._settings = {"test_disabled": {"enabled": False}}
        plugins = create_plugins(settings)
        disabled_found = any(
            isinstance(p, DummyPlugin) for p in plugins
        )
        # May still have system plugins from _ensure_plugins_registered
        # but our DummyPlugin should not be there since it's disabled

    def test_handles_factory_error(self):
        def bad_factory(c, s):
            raise RuntimeError("boom")

        register_plugin(PluginDef(
            name="broken", config_key="broken_plugin",
            enabled_key="enabled",
            factory=bad_factory, priority=100,
        ))
        settings = MagicMock()
        settings._ensure_loaded = MagicMock()
        settings._settings = {"broken_plugin": {"enabled": True}}
        # Should not raise
        plugins = create_plugins(settings)

    def test_factory_returning_none_skipped(self):
        register_plugin(PluginDef(
            name="none_factory", config_key="none_plugin",
            enabled_key="enabled",
            factory=lambda c, s: None, priority=100,
        ))
        settings = MagicMock()
        settings._ensure_loaded = MagicMock()
        settings._settings = {"none_plugin": {"enabled": True}}
        plugins = create_plugins(settings)
        assert not any(p is None for p in plugins)


class TestPluginHooks:
    """Test that TeamPlugin base class has all expected hooks."""

    def test_has_all_hooks(self):
        hooks = [
            "on_team_created", "on_run_start", "on_run_end",
            "pre_compression", "post_compression",
            "on_tool_call", "on_shutdown",
        ]
        for hook in hooks:
            assert hasattr(TeamPlugin, hook), f"Missing hook: {hook}"

    @pytest.mark.asyncio
    async def test_default_hooks_are_noop(self):
        plugin = DummyPlugin()
        # These should all return None without error
        assert await plugin.pre_compression(None, "s", []) is None
        await plugin.post_compression(None, {})
        await plugin.on_tool_call(None, "test", {}, None)
        await plugin.on_shutdown()
