"""Interface-level tests for TemplateManager."""

from __future__ import annotations

from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_manager import TemplateManager


def _make_manager(tmp_path):
    return TemplateManager(work_dir=tmp_path)


def test_validate_template_dict_with_inline_agents(tmp_path):
    """Test that validate_template_dict works with inline agents only."""
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

    template_dict = {
        "id": "research_room",
        "name": "Research Room",
        "description": "Collect and summarize",
        "agents": [agent_a.to_dict(), agent_b.to_dict()],
    }

    result = manager.validate_template_dict(template_dict)
    assert result["success"] is True
    assert result["compatible"] is True
    assert {"alpha", "beta"}.issubset(result["agents"].keys())
    assert "python" in result["required_toolsets"]
    assert "search" in result["required_mcp_servers"]


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

    team_payload = TeamConfig(
        id="room1",
        name="Room One",
        description="demo",
        agents=[AgentConfig.from_dict(agent_payload)],
    ).to_dict()
    team_payload["type"] = "team"
    write_room_resp = manager.write_template_file("teams/room1.md", team_payload)
    assert write_room_resp["success"] is True

    listing = manager.list_template_files("all")
    assert listing["success"] is True
    paths = {entry["path"] for entry in listing["files"]}
    assert "agents/scribe.md" in paths
    assert "teams/room1.md" in paths

    delete_resp = manager.delete_template_file("teams/room1.md")
    assert delete_resp["success"] is True
    list_after_delete = manager.list_template_files("teams")
    remaining_paths = {entry["path"] for entry in list_after_delete["files"]}
    assert "teams/room1.md" not in remaining_paths
