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
        from pantheon.apps.builtin.task import TaskToolSet

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
        from pantheon.apps.builtin.task import TaskToolSet

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
        # No per-project root resolved → fall back to the global settings brain dir.
        team._project_dir = None

        fake_settings = MagicMock()
        fake_settings.brain_dir = "/fake/.pantheon/brain"

        with patch("pantheon.settings.get_settings", return_value=fake_settings):
            await plugin.on_team_created(team)

        primary_instr = team.team_agents[0].instructions
        sub_instr = team.team_agents[1].instructions

        assert "<task_brain_dir>" in primary_instr
        assert "/fake/.pantheon/brain" in primary_instr
        assert "{chat_id}" in primary_instr
        assert "<task_brain_dir>" not in sub_instr  # sub-agent untouched

    @pytest.mark.asyncio
    async def test_anchors_brain_dir_to_per_project_root(self):
        """When the team carries a project root, the brain dir + workspace-root
        prompt point at THAT project — not the global home brain dir."""
        plugin = TaskSystemPlugin()
        team = _make_team(["main"])
        team.team_agents[0].instructions = "base instructions"
        team._project_dir = "/Users/me/Desktop/tmp"

        await plugin.on_team_created(team)

        instr = team.team_agents[0].instructions
        assert "Your workspace root is /Users/me/Desktop/tmp" in instr
        assert "/Users/me/Desktop/tmp/.pantheon/brain" in instr
        # The global home brain dir must NOT leak in.
        assert "/fake/.pantheon/brain" not in instr

    @pytest.mark.asyncio
    async def test_instructs_per_task_folder_organization(self):
        """The brain-dir prompt must tell the agent to put each analysis in its
        OWN folder (not scatter files at the workspace root) — the fix for the
        'everything dumped at root' organization complaint."""
        plugin = TaskSystemPlugin()
        team = _make_team(["main"])
        team.team_agents[0].instructions = "base"
        team._project_dir = "/proj"

        await plugin.on_team_created(team)

        instr = team.team_agents[0].instructions.lower()
        assert "own folder" in instr or "dedicated" in instr
        assert "subfolder" in instr
        assert "scatter" in instr  # explicit "don't scatter at the root" guidance

    @pytest.mark.asyncio
    async def test_injects_decision_points_into_primary_only(self):
        """The leader must get decision-point gating: ask the user at a genuine
        fork (under-specified / expensive / irreversible), proceed otherwise — the
        fix for 'agent stopped confirming consequential choices'."""
        from unittest.mock import patch

        plugin = TaskSystemPlugin()
        team = _make_team(["main", "coder"])
        team.team_agents[0].instructions = "base"
        team.team_agents[1].instructions = "sub"
        team._project_dir = None

        fake_settings = MagicMock()
        fake_settings.brain_dir = "/fake/.pantheon/brain"
        with patch("pantheon.settings.get_settings", return_value=fake_settings):
            await plugin.on_team_created(team)

        primary = team.team_agents[0].instructions.lower()
        assert "<decision_points>" in primary
        assert "blocked_on_user" in primary             # HOW to ask
        assert "pointless confirmations are" in primary  # and when NOT to
        assert "find some data" in primary              # closes the "you pick" loophole
        # Sub-agents don't gate user decisions — they execute delegated work.
        assert "<decision_points>" not in team.team_agents[1].instructions

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


class TestUnfinishedTodosReminder:
    """The ephemeral message is generated from `active_task` (in-memory state), but
    the agent can close its task while task.md still has open items. The reminder
    must read task.md and surface those open items — bridging the active_task ↔
    task.md gap so the agent finishes/closes them instead of leaving them dangling
    (and instead of spinning on a static 'no task is fine' reminder)."""

    def test_reads_only_open_checklist_items(self, tmp_path):
        from pantheon.apps.builtin.task.ephemeral import _read_incomplete_todos
        (tmp_path / "task.md").write_text(
            "- [x] done item\n"
            "- [ ] open todo A\n"
            "- [/] in-progress B — bg running\n"
            "- [-] dropped item\n"
            "plain prose, not a checklist line\n"
        )
        assert _read_incomplete_todos(str(tmp_path)) == [
            "open todo A",
            "in-progress B — bg running",
        ]

    def test_missing_task_md_returns_empty(self, tmp_path):
        from pantheon.apps.builtin.task.ephemeral import _read_incomplete_todos
        assert _read_incomplete_todos(str(tmp_path)) == []

    def test_em_surfaces_open_todos_when_no_active_task(self, tmp_path):
        from pantheon.apps.builtin.task.ephemeral import generate_ephemeral_message
        from pantheon.apps.builtin.task.task_state import ConversationState
        (tmp_path / "task.md").write_text("- [x] done\n- [ ] finish the notebook\n")
        # Fresh state → no active task — exactly the "closed task, open todos" gap.
        em = generate_ephemeral_message(ConversationState(), str(tmp_path))
        assert "<unfinished_todos_reminder>" in em
        assert "finish the notebook" in em

    def test_em_silent_when_all_todos_done(self, tmp_path):
        from pantheon.apps.builtin.task.ephemeral import generate_ephemeral_message
        from pantheon.apps.builtin.task.task_state import ConversationState
        (tmp_path / "task.md").write_text("- [x] done one\n- [x] done two\n")
        em = generate_ephemeral_message(ConversationState(), str(tmp_path))
        assert "<unfinished_todos_reminder>" not in em
