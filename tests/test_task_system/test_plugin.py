"""Tests for TaskSystemPlugin."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from pantheon.internal.task_system.plugin import TaskSystemPlugin, _create_task_plugin


def _make_team(agent_names: list[str]):
    """Build a minimal mock PantheonTeam with named agents."""
    agents = []
    for name in agent_names:
        a = MagicMock()
        a.name = name
        a._ephemeral_hooks = []
        a._tool_tracking_hooks = []
        agents.append(a)
    team = MagicMock()
    team.team_agents = agents
    return team


def _make_settings(configs: dict):
    class _Settings:
        def get_section(self, key):
            return configs.get(key, {})
    return _Settings()


class TestGetToolsets:
    @pytest.mark.asyncio
    async def test_injects_into_primary_agent_only(self):
        plugin = TaskSystemPlugin()
        team = _make_team(["main", "coder", "reviewer"])

        specs = await plugin.get_toolsets(team)

        assert len(specs) == 1
        _, agent_names = specs[0]
        assert agent_names == ["main"]

    @pytest.mark.asyncio
    async def test_returns_task_toolset_instance(self):
        from pantheon.toolsets.task import TaskToolSet

        plugin = TaskSystemPlugin()
        team = _make_team(["main"])

        specs = await plugin.get_toolsets(team)

        toolset, _ = specs[0]
        assert isinstance(toolset, TaskToolSet)

    @pytest.mark.asyncio
    async def test_empty_team_returns_empty(self):
        plugin = TaskSystemPlugin()
        team = _make_team([])

        specs = await plugin.get_toolsets(team)

        assert specs == []

    @pytest.mark.asyncio
    async def test_registers_ephemeral_hook_on_primary_only(self):
        plugin = TaskSystemPlugin()
        team = _make_team(["main", "coder"])

        await plugin.get_toolsets(team)

        assert len(team.team_agents[0]._ephemeral_hooks) == 1   # primary
        assert len(team.team_agents[1]._ephemeral_hooks) == 0   # sub-agent untouched

    @pytest.mark.asyncio
    async def test_registers_tool_tracking_hook_on_primary_only(self):
        plugin = TaskSystemPlugin()
        team = _make_team(["main", "coder"])

        await plugin.get_toolsets(team)

        assert len(team.team_agents[0]._tool_tracking_hooks) == 1
        assert len(team.team_agents[1]._tool_tracking_hooks) == 0


class TestEphemeralHook:
    @pytest.mark.asyncio
    async def test_hook_returns_eu_message(self):
        plugin = TaskSystemPlugin()
        team = _make_team(["main"])
        await plugin.get_toolsets(team)

        hook = team.team_agents[0]._ephemeral_hooks[0]
        msgs = await hook([], {"client_id": "test"})

        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "EPHEMERAL_MESSAGE" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_hook_closure_captures_toolset(self):
        """Two plugin instances each get their own independent toolset."""
        plugin_a = TaskSystemPlugin()
        plugin_b = TaskSystemPlugin()
        team_a = _make_team(["main"])
        team_b = _make_team(["main"])

        specs_a = await plugin_a.get_toolsets(team_a)
        specs_b = await plugin_b.get_toolsets(team_b)

        toolset_a = specs_a[0][0]
        toolset_b = specs_b[0][0]
        assert toolset_a is not toolset_b


class TestToolTrackingHook:
    @pytest.mark.asyncio
    async def test_hook_delegates_to_process_tool_messages(self):
        from unittest.mock import patch
        from pantheon.toolsets.task import TaskToolSet

        plugin = TaskSystemPlugin()
        team = _make_team(["main"])
        specs = await plugin.get_toolsets(team)
        task_toolset = specs[0][0]

        hook = team.team_agents[0]._tool_tracking_hooks[0]

        tool_calls = [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]
        tool_messages = [{"tool_call_id": "c1", "tool_name": "read_file"}]
        ctx = {"client_id": "x"}

        with patch.object(task_toolset, "process_tool_messages") as mock_proc:
            await hook(tool_calls, tool_messages, ctx)
            mock_proc.assert_called_once_with(
                tool_calls=tool_calls,
                tool_messages=tool_messages,
                context_variables=ctx,
            )


class TestOnTeamCreated:
    @pytest.mark.asyncio
    async def test_injects_task_brain_dir_into_primary_only(self):
        from unittest.mock import patch

        plugin = TaskSystemPlugin()
        team = _make_team(["main", "coder"])
        team.team_agents[0].instructions = "base instructions"
        team.team_agents[1].instructions = "sub instructions"

        fake_settings = MagicMock()
        fake_settings.brain_dir = "/fake/.pantheon/brain"

        with patch("pantheon.settings.get_settings", return_value=fake_settings):
            await plugin.on_team_created(team)

        primary_instr = team.team_agents[0].instructions
        sub_instr = team.team_agents[1].instructions

        assert "<task_brain_dir>" in primary_instr
        assert "/fake/.pantheon/brain" in primary_instr
        assert "{client_id}" in primary_instr
        assert "<task_brain_dir>" not in sub_instr  # sub-agent untouched

    @pytest.mark.asyncio
    async def test_noop_when_no_agents(self):
        plugin = TaskSystemPlugin()
        team = _make_team([])
        # Should not raise
        await plugin.on_team_created(team)

    @pytest.mark.asyncio
    async def test_noop_when_no_instructions(self):
        from unittest.mock import patch

        plugin = TaskSystemPlugin()
        team = _make_team(["main"])
        team.team_agents[0].instructions = None

        fake_settings = MagicMock()
        fake_settings.brain_dir = "/fake/.pantheon/brain"

        with patch("pantheon.settings.get_settings", return_value=fake_settings):
            await plugin.on_team_created(team)
        assert team.team_agents[0].instructions is None


class TestFactory:
    def test_returns_instance(self):
        assert isinstance(_create_task_plugin({}, MagicMock()), TaskSystemPlugin)


class TestRegistration:
    def test_registered_in_registry(self):
        import pantheon.internal.task_system.plugin  # noqa: F401
        from pantheon.team.plugin_registry import _registry

        assert any(p.name == "task_system" for p in _registry)

    def test_priority_before_memory(self):
        import pantheon.internal.task_system.plugin  # noqa: F401
        import pantheon.internal.memory_system.plugin  # noqa: F401
        from pantheon.team.plugin_registry import _registry

        task_prio = next(p.priority for p in _registry if p.name == "task_system")
        mem_prio = next(p.priority for p in _registry if p.name == "memory_system")
        assert task_prio < mem_prio

    def test_enabled_creates_plugin(self):
        from pantheon.team.plugin_registry import create_plugins

        plugins = create_plugins(_make_settings({
            "task_system": {"enabled": True},
            "memory_system": {"enabled": False},
            "learning_system": {"enabled": False},
            "compression": {"enabled": False},
        }))
        assert any(isinstance(p, TaskSystemPlugin) for p in plugins)

    def test_disabled_skips_plugin(self):
        from pantheon.team.plugin_registry import create_plugins

        plugins = create_plugins(_make_settings({
            "task_system": {"enabled": False},
            "memory_system": {"enabled": False},
            "learning_system": {"enabled": False},
            "compression": {"enabled": False},
        }))
        assert not any(isinstance(p, TaskSystemPlugin) for p in plugins)


class TestPluginBaseClass:
    """Verify TeamPlugin no longer exposes the removed hooks."""

    def test_no_get_ephemeral_messages(self):
        from pantheon.team.plugin import TeamPlugin
        assert not hasattr(TeamPlugin, "get_ephemeral_messages")

    def test_no_on_tool_calls_batch(self):
        from pantheon.team.plugin import TeamPlugin
        assert not hasattr(TeamPlugin, "on_tool_calls_batch")
