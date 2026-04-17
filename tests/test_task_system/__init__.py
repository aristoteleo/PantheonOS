"""Tests for TaskSystemPlugin."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from pantheon.internal.task_system.plugin import TaskSystemPlugin, _create_task_plugin


def _make_team(agent_names: list[str]):
    """Build a minimal mock PantheonTeam with named agents."""
    agents = []
    for name in agent_names:
        a = MagicMock()
        a.name = name
        agents.append(a)
    team = MagicMock()
    team.team_agents = agents
    return team


class TestGetToolsets:
    @pytest.mark.asyncio
    async def test_injects_into_primary_agent_only(self):
        plugin = TaskSystemPlugin()
        team = _make_team(["main", "coder", "reviewer"])

        specs = await plugin.get_toolsets(team)

        assert len(specs) == 1
        toolset, agent_names = specs[0]
        assert agent_names == ["main"]

    @pytest.mark.asyncio
    async def test_single_agent_team(self):
        plugin = TaskSystemPlugin()
        team = _make_team(["solo"])

        specs = await plugin.get_toolsets(team)

        assert len(specs) == 1
        _, agent_names = specs[0]
        assert agent_names == ["solo"]

    @pytest.mark.asyncio
    async def test_empty_team_returns_empty(self):
        plugin = TaskSystemPlugin()
        team = _make_team([])

        specs = await plugin.get_toolsets(team)

        assert specs == []

    @pytest.mark.asyncio
    async def test_returns_task_toolset_instance(self):
        from pantheon.toolsets.task import TaskToolSet

        plugin = TaskSystemPlugin()
        team = _make_team(["main"])

        specs = await plugin.get_toolsets(team)

        toolset, _ = specs[0]
        assert isinstance(toolset, TaskToolSet)


class TestOnTeamCreated:
    @pytest.mark.asyncio
    async def test_is_noop(self):
        """on_team_created should not raise and not modify agents."""
        plugin = TaskSystemPlugin()
        team = _make_team(["main"])
        team.team_agents[0].instructions = "original"

        await plugin.on_team_created(team)

        assert team.team_agents[0].instructions == "original"


class TestFactory:
    def test_create_task_plugin_returns_instance(self):
        plugin = _create_task_plugin({}, MagicMock())
        assert isinstance(plugin, TaskSystemPlugin)


class TestRegistration:
    def test_task_system_registered_in_registry(self):
        """Importing the plugin module should register it."""
        import pantheon.internal.task_system.plugin  # noqa: F401
        from pantheon.team.plugin_registry import _registry

        names = [p.name for p in _registry]
        assert "task_system" in names

    def test_task_system_priority_before_memory(self):
        """task_system (priority=10) should run before memory_system (priority=50)."""
        import pantheon.internal.task_system.plugin  # noqa: F401
        import pantheon.internal.memory_system.plugin  # noqa: F401
        from pantheon.team.plugin_registry import _registry

        task_prio = next(p.priority for p in _registry if p.name == "task_system")
        mem_prio = next(p.priority for p in _registry if p.name == "memory_system")
        assert task_prio < mem_prio

    def test_create_plugins_includes_task_system(self):
        """create_plugins() should create TaskSystemPlugin when enabled."""
        from pantheon.team.plugin_registry import create_plugins

        settings = MagicMock()
        settings._ensure_loaded = MagicMock()
        settings._settings = {
            "task_system": {"enabled": True},
            "memory_system": {"enabled": False},
            "learning_system": {"enabled": False},
            "compression": {"enabled": False},
        }

        plugins = create_plugins(settings)
        assert any(isinstance(p, TaskSystemPlugin) for p in plugins)

    def test_create_plugins_skips_disabled_task_system(self):
        """create_plugins() should skip TaskSystemPlugin when disabled."""
        from pantheon.team.plugin_registry import create_plugins

        settings = MagicMock()
        settings._ensure_loaded = MagicMock()
        settings._settings = {
            "task_system": {"enabled": False},
            "memory_system": {"enabled": False},
            "learning_system": {"enabled": False},
            "compression": {"enabled": False},
        }

        plugins = create_plugins(settings)
        assert not any(isinstance(p, TaskSystemPlugin) for p in plugins)
