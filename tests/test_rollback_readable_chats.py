"""Chats this runtime writes stay readable by the runtime before Agent App.

After a rollback the previous agent image reads the same Volume. Its chat
list sorts ``last_activity_date`` as naive datetimes, so one value with an
offset left users with an empty chat list. And it builds a chat's agents
from the team stored in the chat, reading their tools from ``toolsets``
only, so an agent that selects Apps would have no tools.
"""

import json
from datetime import datetime, timedelta, timezone

from pantheon.chatroom.runtime import _activity_date
from pantheon.factory.models import AgentConfig, PRE_APP_TOOLSETS, rollback_readable_team
from pantheon.internal.memory.storage import (
    JSONBackend,
    JSONLBackend,
    activity_timestamp,
    rollback_readable_metadata,
)


def _previous_runtime_sort(values):
    """The previous runtime's list_chats ordering, verbatim."""
    chats = [{"last_activity_date": v} for v in values]
    chats.sort(
        key=lambda x: datetime.fromisoformat(x["last_activity_date"])
        if x["last_activity_date"]
        else datetime.min,
        reverse=True,
    )
    return chats


def test_activity_timestamp_is_the_previous_runtimes_form():
    stamp = activity_timestamp()
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is None
    assert abs(parsed - datetime.now()) < timedelta(seconds=5)
    _previous_runtime_sort([stamp, datetime.now().isoformat(), None])


def test_metadata_with_an_offset_is_written_naive(tmp_path):
    aware = "2026-11-26T08:00:00.123456+00:00"
    extra = {"last_activity_date": aware, "running": False, "project": {"name": "p"}}
    for backend in (JSONLBackend(tmp_path / "jsonl"), JSONBackend(tmp_path / "json")):
        backend.save_metadata("c1", "Chat", extra)
        _, name, stored = backend.load_metadata("c1")
        assert name == "Chat" and stored["running"] is False and stored["project"] == {"name": "p"}
        written = datetime.fromisoformat(stored["last_activity_date"])
        assert written.tzinfo is None
        # The same instant, as local time.
        assert written.astimezone(timezone.utc) == datetime.fromisoformat(aware)
        _previous_runtime_sort([stored["last_activity_date"], "2026-01-01T00:00:00", None])
    assert extra["last_activity_date"] == aware  # what is held in memory is unchanged


def test_naive_and_unknown_values_are_written_as_they_are():
    for value in ("2026-11-26T08:00:00", None, "", "not a date", 5):
        extra = {"last_activity_date": value}
        assert rollback_readable_metadata(extra) is extra
    assert rollback_readable_metadata({"name": "x"}) == {"name": "x"}
    assert rollback_readable_metadata(None) is None


def test_this_runtime_reads_both_forms_as_the_same_instant():
    naive_local = datetime(2026, 11, 26, 8, 0, 0)
    aware = naive_local.astimezone().astimezone(timezone.utc).isoformat()
    assert _activity_date(naive_local.isoformat()) == _activity_date(aware)
    assert _activity_date(aware.replace("+00:00", "Z")) == _activity_date(aware)
    # Round trip through the disk form.
    written = rollback_readable_metadata({"last_activity_date": aware})["last_activity_date"]
    assert _activity_date(written) == _activity_date(aware)


def test_a_jsonl_chat_round_trips(tmp_path):
    from pantheon.internal.memory.memory import Memory

    memory = Memory("Chat")
    memory.set_metadata("last_activity_date", datetime.now(timezone.utc).isoformat())
    memory.add_messages([{"role": "user", "content": "hi"}])
    JSONLBackend(tmp_path).persist(memory)
    path = tmp_path / f"{memory.id}.jsonl"
    meta = json.loads((tmp_path / f"{memory.id}.meta.json").read_text())
    assert set(meta) == {"id", "name", "extra_data"}
    assert datetime.fromisoformat(meta["extra_data"]["last_activity_date"]).tzinfo is None
    loaded = Memory.load(str(path))
    assert _activity_date(loaded.extra_data["last_activity_date"]) is not None


# --- the team a chat stores --------------------------------------------------

def _previous_runtime_toolsets(agent: dict) -> list:
    """The previous runtime's AgentConfig.from_dict: toolsets only."""
    return list(agent.get("toolsets", []) or [])


def _team(*agents):
    return {"id": "default", "name": "General Team", "mode": "agent",
            "agents": [dict(a) for a in agents]}


LEADER = {"id": "leader", "name": "Leader", "model": "normal", "toolsets": [], "apps": None}
RESEARCHER = {"id": "researcher", "name": "Researcher", "model": "normal", "toolsets": [],
              "apps": {"allow": ["web", "file_manager"]}}
PRE_APP_AGENT = {"id": "analyst", "name": "Analyst", "model": "normal",
                 "toolsets": ["file_manager", "shell", "task"], "apps": None}
MCP_AGENT = {"id": "m", "name": "M", "model": "normal", "toolsets": ["mcp:context7", "think"],
             "apps": {"deny": ["desktop", "shell"]}}


def test_an_agent_that_selects_apps_keeps_its_tools_after_a_rollback():
    stored = rollback_readable_team(_team(LEADER, RESEARCHER, PRE_APP_AGENT, MCP_AGENT))
    leader, researcher, analyst, mcp = stored["agents"]
    assert _previous_runtime_toolsets(leader) == list(PRE_APP_TOOLSETS)
    assert leader["apps"] == {}
    assert _previous_runtime_toolsets(researcher) == ["file_manager", "web"]
    assert analyst == PRE_APP_AGENT  # already a pre-App agent: untouched
    assert mcp["toolsets"] == ["mcp:context7", "think", "file_manager", "package",
                               "integrated_notebook", "web", "evolution"]
    assert stored["mode"] == "agent" and stored["id"] == "default"


def test_this_runtime_gives_each_agent_the_same_tools_as_before():
    original = _team(LEADER, RESEARCHER, PRE_APP_AGENT, MCP_AGENT)
    stored = rollback_readable_team(original)
    for before, after in zip(original["agents"], stored["agents"]):
        assert (AgentConfig.from_dict(after).to_creation_payload()
                == AgentConfig.from_dict(before).to_creation_payload())
    # Storing twice changes nothing more.
    assert rollback_readable_team(stored) == stored


def test_a_malformed_team_is_stored_as_it_is():
    assert rollback_readable_team(None) is None
    assert rollback_readable_team({"agents": "x"}) == {"agents": "x"}
    bad = {"agents": [{"id": "a", "apps": {"allow": "nope"}}, "not an agent"]}
    assert rollback_readable_team(bad) == bad


def test_required_toolsets_are_what_the_agents_bind():
    from pantheon.factory.models import TeamConfig
    from pantheon.factory.template_manager import TemplateManager

    team = TeamConfig.from_dict(rollback_readable_team(_team(LEADER, PRE_APP_AGENT)))
    manager = TemplateManager.__new__(TemplateManager)
    manager._scoped_settings = None
    _, required, _ = manager.prepare_team(team)
    assert required == {"file_manager", "shell"}  # the pre-App agent's, minus task
