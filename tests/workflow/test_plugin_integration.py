"""Task 10 — WorkflowTeamPlugin + ChatRoom wiring integration tests.

These are integration-level but FAST: no real LLM, no endpoint run loop. They
cover three layers:

  1. **Plugin unit** — WorkflowTeamPlugin.get_toolsets targets the LEADER ONLY
     (team_agents[0]) and wraps the injected engine; sub-agents are not targeted.
  2. **Team injection** — drive PantheonTeam.async_setup with a real (no-LLM)
     2-agent team and assert the leader's agent gets the 5 workflow tools as a
     provider while the sub-agent does not (proves leader-only scoping end to
     end through the real toolset-injection path).
  3. **Room wiring** — a real ChatRoom (embedded, no run loop) constructs a
     ``_workflow_engine``; ``_ensure_plugins`` includes the WorkflowTeamPlugin;
     and the engine's ``on_key_event`` callback schedules a ``room.chat`` with
     the workflow notification for the right chat_id (decision 14 wake path).
"""

from __future__ import annotations

import asyncio

import pytest

from pantheon.workflow.plugin import WorkflowTeamPlugin
from pantheon.workflow.toolset import WorkflowToolSet


# --- fakes ---------------------------------------------------------------- #


class _FakeAgent:
    def __init__(self, name: str):
        self.name = name


class _FakeTeam:
    """Minimal stand-in exposing the ``team_agents`` accessor the plugin uses."""

    def __init__(self, agents):
        self.team_agents = agents


class _FakeEngine:
    """Sentinel engine — the plugin only stores and forwards it."""


# --- 1. plugin unit ------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_toolsets_targets_leader_only():
    engine = _FakeEngine()
    plugin = WorkflowTeamPlugin(engine)
    team = _FakeTeam([_FakeAgent("leader"), _FakeAgent("worker-a"), _FakeAgent("worker-b")])

    specs = await plugin.get_toolsets(team)

    assert len(specs) == 1
    toolset, agent_names = specs[0]
    # Leader-only injection (decision 9/13): the agent_names list is exactly the
    # entry agent and NOT the sub-agents.
    assert agent_names == ["leader"]
    assert "worker-a" not in agent_names
    assert "worker-b" not in agent_names
    # The toolset is a WorkflowToolSet wrapping the injected engine (not a fresh
    # one).
    assert isinstance(toolset, WorkflowToolSet)
    assert toolset._engine is engine


@pytest.mark.asyncio
async def test_get_toolsets_empty_when_no_leader():
    plugin = WorkflowTeamPlugin(_FakeEngine())
    assert await plugin.get_toolsets(_FakeTeam([])) == []


@pytest.mark.asyncio
async def test_on_team_created_is_noop():
    plugin = WorkflowTeamPlugin(_FakeEngine())
    # ABC-required hook; must not raise.
    assert await plugin.on_team_created(_FakeTeam([_FakeAgent("leader")])) is None


# --- 2. real team injection (no LLM) -------------------------------------- #


@pytest.mark.asyncio
async def test_team_setup_injects_workflow_tools_into_leader_only():
    """End-to-end through PantheonTeam.async_setup: leader gets the workflow
    provider, sub-agent does not."""
    from pantheon.agent import Agent
    from pantheon.team import PantheonTeam

    leader = Agent(name="leader", instructions="lead")
    worker = Agent(name="worker", instructions="work")
    team = PantheonTeam(
        agents=[leader, worker],
        plugins=[WorkflowTeamPlugin(_FakeEngine())],
        allow_transfer=False,
    )
    await team.async_setup()

    # The WorkflowToolSet registers as a provider named "workflow".
    assert "workflow" in leader.providers
    assert "workflow" not in worker.providers

    # The leader's workflow provider exposes the 5 tools.
    ts = WorkflowToolSet(_FakeEngine())
    assert set(ts.tool_functions.keys()) == {
        "workflow_create",
        "workflow_status",
        "workflow_get_output",
        "workflow_edit",
        "workflow_control",
    }


# --- 3. room wiring ------------------------------------------------------- #


@pytest.fixture
def room(tmp_path):
    """A real embedded ChatRoom with no run loop started.

    Mirrors the lightweight fixture style used by the chatroom memory tests
    (ChatRoom(memory_dir=..., workspace_path=...)). No endpoint run() is invoked,
    so construction is fast and LLM-free.
    """
    from pantheon.chatroom.room import ChatRoom

    return ChatRoom(
        memory_dir=str(tmp_path / "memory"),
        workspace_path=str(tmp_path / "ws"),
    )


def test_room_constructs_workflow_engine(room, tmp_path):
    from pantheon.workflow.engine import WorkflowEngine

    assert isinstance(room._workflow_engine, WorkflowEngine)
    # base_dir reuses the room workspace resolution (_ws == workspace_path).
    assert str(room._workflow_engine.base_dir) == str(tmp_path / "ws")
    # on_key_event is wired to the room's wake callback.
    assert room._workflow_engine._on_key_event == room._on_workflow_key_event


@pytest.mark.asyncio
async def test_ensure_plugins_includes_workflow_plugin(room):
    plugins = await room._ensure_plugins()
    assert any(isinstance(p, WorkflowTeamPlugin) for p in plugins)
    # idempotent: a second call must not duplicate it.
    plugins2 = await room._ensure_plugins()
    assert sum(isinstance(p, WorkflowTeamPlugin) for p in plugins2) == 1
    # and the plugin wraps the room's singleton engine.
    wf = next(p for p in plugins2 if isinstance(p, WorkflowTeamPlugin))
    assert wf._engine is room._workflow_engine


@pytest.mark.asyncio
async def test_on_key_event_schedules_chat_wake(room):
    """Decision 14: a settled workflow wakes the Leader via self.chat."""
    calls = []

    async def _fake_chat(chat_id, message):
        calls.append((chat_id, message))

    room.chat = _fake_chat  # monkeypatch the wake target

    # Fire the callback exactly as the engine would (synchronous; it schedules
    # an asyncio task on the running loop).
    room._workflow_engine._on_key_event("wf-7", "chat-xyz", "completed", "all good")

    # Let the scheduled _auto_chat task run.
    await asyncio.sleep(0)

    assert len(calls) == 1
    chat_id, message = calls[0]
    assert chat_id == "chat-xyz"
    assert message[0]["role"] == "user"
    content = message[0]["content"]
    assert "<workflow_notification>" in content
    assert "wf-7" in content
    assert "completed" in content
    assert "all good" in content


@pytest.mark.asyncio
async def test_on_key_event_no_running_loop_is_safe(room):
    """The callback must swallow the no-running-loop case (RuntimeError guard).

    When fired outside an event loop, ``asyncio.get_running_loop()`` raises
    RuntimeError; the callback must catch it and return without raising. We
    invoke the synchronous callback from a worker thread (no loop) to exercise
    that branch.
    """
    room.chat = lambda *a, **k: None  # never reached on the no-loop path

    err = []

    def _call_without_loop():
        try:
            room._workflow_engine._on_key_event("wf-1", "c", "failed", "boom")
        except Exception as e:  # pragma: no cover - failure path
            err.append(e)

    await asyncio.to_thread(_call_without_loop)
    assert err == []
