"""Interface-level tests for TemplateManager."""

from __future__ import annotations

from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_manager import TemplateManager


def _make_manager(tmp_path):
    return TemplateManager(work_dir=tmp_path)


def test_validate_template_dict_expands_all_sub_agents(tmp_path):
    manager = _make_manager(tmp_path)

    agent_a = AgentConfig(
        id="alpha",
        name="Alpha",
        model="openai/gpt-4o-mini",
        toolsets=["python"],
    )
    agent_b = AgentConfig(
        id="beta",
        name="Beta",
        model="openai/gpt-4o-mini",
        mcp_servers=["search"],
    )
    manager.file_manager.create_agent(agent_a)
    manager.file_manager.create_agent(agent_b)

    template_dict = {
        "id": "research_room",
        "name": "Research Room",
        "description": "Collect and summarize",
        "agents": [agent_a.to_dict()],
        "sub_agents": ["all"],
        "skills": ["alpha"],
    }

    result = manager.validate_template_dict(template_dict)
    assert result["success"] is True
    assert result["compatible"] is True
    assert result["missing_sub_agents"] == []
    assert {"alpha", "beta"}.issubset(result["sub_agents"].keys())
    assert "python" in result["required_toolsets"]
    assert "search" in result["required_mcp_servers"]
    # Alpha and Beta both end up as sub-agents when "all" is used.
    assert result["sub_agents"]["alpha"]["enable_skills"] is True
    assert result["sub_agents"]["beta"]["enable_skills"] is False


def test_template_file_crud_roundtrip(tmp_path):
    manager = _make_manager(tmp_path)

    agent_payload = {
        "id": "scribe",
        "name": "Scribe",
        "model": "openai/gpt-4o-mini",
        "instructions": "Write summaries",
    }
    write_resp = manager.write_template_file("agents/scribe.md", agent_payload)
    assert write_resp["success"] is True
    assert write_resp["operation"] == "create"

    read_agent = manager.read_template_file("agents/scribe.md")
    assert read_agent["success"] is True
    assert read_agent["content"]["name"] == "Scribe"

    chatroom_payload = TeamConfig(
        id="room1",
        name="Room One",
        description="demo",
        agents=[AgentConfig.from_dict(agent_payload)],
    ).to_dict()
    chatroom_payload["type"] = "chatroom"
    write_room_resp = manager.write_template_file("chatrooms/room1.md", chatroom_payload)
    assert write_room_resp["success"] is True

    listing = manager.list_template_files("all")
    assert listing["success"] is True
    paths = {entry["path"] for entry in listing["files"]}
    assert "agents/scribe.md" in paths
    assert "chatrooms/room1.md" in paths

    delete_resp = manager.delete_template_file("chatrooms/room1.md")
    assert delete_resp["success"] is True
    list_after_delete = manager.list_template_files("chatrooms")
    remaining_paths = {entry["path"] for entry in list_after_delete["files"]}
    assert "chatrooms/room1.md" not in remaining_paths
