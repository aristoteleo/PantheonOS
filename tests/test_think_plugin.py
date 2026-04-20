"""Tests for ThinkPlugin (leader-only think injection)."""

import pytest
from unittest.mock import MagicMock


from pantheon.internal.think_plugin import ThinkPlugin, _create_think_plugin



def _make_team(primary_toolsets=None, secondary_toolsets=None):
    primary = MagicMock()
    primary.name = "leader"
    primary.instructions = "base instructions"
    primary._declared_toolsets = list(primary_toolsets or [])

    secondary = MagicMock()
    secondary.name = "researcher"
    secondary.instructions = "sub instructions"
    secondary._declared_toolsets = list(secondary_toolsets or [])

    team = MagicMock()
    team.team_agents = [primary, secondary]
    return team



def _make_settings(configs: dict):
    class _Settings:
        def get_section(self, key):
            return configs.get(key, {})

    return _Settings()


class TestGetToolsets:
    @pytest.mark.asyncio
    async def test_injects_think_tool_only_when_primary_declares_think(self):
        plugin = ThinkPlugin()
        team = _make_team(primary_toolsets=["file_manager", "think"])

        specs = await plugin.get_toolsets(team)

        assert specs == []
        team.team_agents[0].tool.assert_called_once()
        _, kwargs = team.team_agents[0].tool.call_args
        assert kwargs["key"] == "think"
        team.team_agents[1].tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_inject_when_primary_has_no_think(self):
        plugin = ThinkPlugin()
        team = _make_team(primary_toolsets=["file_manager"], secondary_toolsets=["think"])

        specs = await plugin.get_toolsets(team)

        assert specs == []
        team.team_agents[0].tool.assert_not_called()
        team.team_agents[1].tool.assert_not_called()


class TestOnTeamCreated:
    @pytest.mark.asyncio
    async def test_injects_prompt_only_for_primary_when_think_enabled(self):
        plugin = ThinkPlugin()
        team = _make_team(primary_toolsets=["think"])

        await plugin.on_team_created(team)

        assert "## Think Tool Usage" in team.team_agents[0].instructions
        assert "## Think Tool Usage" not in team.team_agents[1].instructions

    @pytest.mark.asyncio
    async def test_prompt_injection_is_idempotent(self):
        plugin = ThinkPlugin()
        team = _make_team(primary_toolsets=["think"])

        await plugin.on_team_created(team)
        first = team.team_agents[0].instructions
        await plugin.on_team_created(team)

        assert team.team_agents[0].instructions == first


class TestFactoryAndRegistry:
    def test_factory_returns_instance(self):
        assert isinstance(_create_think_plugin({}, MagicMock()), ThinkPlugin)

    def test_enabled_creates_plugin(self):
        from pantheon.team.plugin_registry import create_plugins

        plugins = create_plugins(
            _make_settings(
                {
                    "think_system": {"enabled": True},
                    "task_system": {"enabled": False},
                    "memory_system": {"enabled": False},
                    "learning_system": {"enabled": False},
                    "compression": {"enabled": False},
                }
            )
        )

        assert any(isinstance(p, ThinkPlugin) for p in plugins)

    def test_disabled_skips_plugin(self):
        from pantheon.team.plugin_registry import create_plugins

        plugins = create_plugins(
            _make_settings(
                {
                    "think_system": {"enabled": False},
                    "task_system": {"enabled": False},
                    "memory_system": {"enabled": False},
                    "learning_system": {"enabled": False},
                    "compression": {"enabled": False},
                }
            )
        )

        assert not any(isinstance(p, ThinkPlugin) for p in plugins)
