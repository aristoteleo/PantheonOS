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
        model="low",
        toolsets=["python"],
    )
    agent_b = AgentConfig(
        id="beta",
        name="Beta",
        model="low",
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


def test_list_template_files_summary_returns_lightweight_team_metadata(tmp_path):
    manager = _make_manager(tmp_path)

    agent_payload = {
        "id": "scribe",
        "name": "Scribe",
        "description": "Writes concise notes",
        "model": "openai/gpt-4o-mini",
        "icon": "✍️",
        "instructions": "Write summaries",
        "toolsets": ["python"],
    }
    assert manager.write_template_file("agents/scribe.md", agent_payload)["success"] is True

    team_payload = TeamConfig(
        id="room1",
        name="Room One",
        description="A fast-loading room",
        icon="🏠",
        category="research",
        tags=["fast", "summary"],
        agents=[AgentConfig(id="scribe", name="", model="")],
    ).to_dict()
    team_payload["type"] = "team"
    assert manager.write_template_file("teams/room1.md", team_payload)["success"] is True

    listing = manager.list_template_files("teams", view="summary")

    assert listing["success"] is True
    room = next(entry for entry in listing["files"] if entry["id"] == "room1")
    assert room["name"] == "Room One"
    assert room["description"] == "A fast-loading room"
    assert room["icon"] == "🏠"
    assert room["category"] == "research"
    assert room["tags"] == ["fast", "summary"]
    assert room["agent_count"] == 1
    assert room["agent_refs"] == [
        {
            "id": "scribe",
            "name": "Scribe",
            "icon": "✍️",
            "source_path": str(tmp_path / ".pantheon" / "agents" / "scribe.md"),
            "is_reference": True,
        }
    ]
    assert "instructions" not in room["agent_refs"][0]
    assert "model" not in room["agent_refs"][0]
    assert "toolsets" not in room["agent_refs"][0]


def test_single_cell_team_has_no_fm_router(tmp_path):
    # fm_router routed for the SCFM toolset, removed in the 2026-08 toolset
    # cleanup; the team must not reference an agent that no longer ships.
    manager = _make_manager(tmp_path)
    team = manager.get_template("single_cell_team")
    assert team is not None
    agent_ids = [a.id for a in team.agents]
    assert "fm_router" not in agent_ids


def test_graph_maker_team_and_dedicated_agents_are_not_factory_defaults(tmp_path):
    manager = _make_manager(tmp_path)

    factory_templates = manager.system_templates_dir
    assert not (factory_templates / "teams" / "graph_maker_team.md").exists()
    assert not (factory_templates / "agents" / "graph_maker").exists()
    figure_styling = factory_templates / "skills" / "figure_styling"
    figure_styling_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(figure_styling.rglob("*.md"))
    )
    for legacy_coupling in [
        "Graph Maker",
        "graph_maker",
        "data_plotter",
        "illustrator",
        "leader",
        "multi-agent",
        "workdir",
        "style_card",
    ]:
        assert legacy_coupling not in figure_styling_text

    listing = manager.list_template_files("all")
    assert listing["success"] is True
    paths = {
        entry["path"]
        for entry in listing["files"]
        if entry["source_path"].startswith(str(tmp_path))
    }
    assert "teams/graph_maker_team.md" not in paths
    assert not any(path.startswith("agents/graph_maker/") for path in paths)
