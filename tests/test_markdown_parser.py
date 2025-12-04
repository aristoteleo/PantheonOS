"""Minimal smoke tests for UnifiedMarkdownParser using temp markdown files."""

from __future__ import annotations

from textwrap import dedent

from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_io import UnifiedMarkdownParser, FileBasedTemplateManager


def _write_markdown(tmp_path, filename, content) -> str:
    text = content
    if not text.startswith("\n"):
        text = "\n" + text
    path = tmp_path / filename
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")
    return path


def test_parse_agent_markdown(tmp_path):
    parser = UnifiedMarkdownParser()
    path = _write_markdown(
        tmp_path,
        "inline_agent.md",
        """
        ---
        id: researcher
        name: Researcher
        model: openai/gpt-4o-mini
        icon: 🤖
        toolsets:
          - python
        tags:
          - analysis
        ---
        Collect data and summarize findings.
        """,
    )

    agent = parser.parse_file(path)
    assert isinstance(agent, AgentConfig)
    assert agent.id == "researcher"
    assert agent.name == "Researcher"
    assert agent.model == "openai/gpt-4o-mini"
    assert agent.toolsets == ["python"]
    assert agent.tags == ["analysis"]
    assert "summarize" in agent.instructions


def test_parse_multi_agent_team_markdown(tmp_path):
    parser = UnifiedMarkdownParser()
    path = _write_markdown(
        tmp_path,
        "team_config.md",
        """
        ---
        id: research_room
        name: Research Room
        type: team
        icon: 💬
        category: research
        version: 1.2.3
        agents:
          - analyst
          - writer
        analyst:
          id: analyst
          name: Analyst
          model: openai/gpt-4.1-mini
          icon: 🧠
          tags:
            - gather
        writer:
          id: writer
          name: Writer
          model: openai/gpt-4.1-mini
          icon: ✍️
        tags:
          - markdown
        ---
        Team overview instructions.
        ---
        Gather intelligence and note sources.
        ---
        Draft final response with citations.
        """,
    )

    team = parser.parse_file(path)
    assert isinstance(team, TeamConfig)
    assert team.id == "research_room"
    assert team.name == "Research Room"
    assert team.category == "research"
    assert team.version == "1.2.3"
    assert team.tags == ["markdown"]
    assert len(team.agents) == 2

    analyst, writer = team.agents
    assert analyst.id == "analyst"
    assert analyst.tags == ["gather"]
    assert "intelligence" in analyst.instructions

    assert writer.id == "writer"
    assert writer.instructions.startswith("Draft final response")


def test_parse_team_with_id_references(tmp_path):
    """Test parsing a team that references agents by ID (no inline metadata)."""
    parser = UnifiedMarkdownParser()

    # Team references 'helper' agent by ID without inline metadata
    path = _write_markdown(
        tmp_path,
        "team_with_refs.md",
        """
        ---
        id: ref_team
        name: Reference Team
        type: team
        agents:
          - coordinator
          - helper
        coordinator:
          id: coordinator
          name: Coordinator
          model: openai/gpt-5
          icon: 🎯
        ---
        Coordinator instructions here.
        """,
    )

    team = parser.parse_file(path)
    assert isinstance(team, TeamConfig)
    assert len(team.agents) == 2

    # First agent is inline (has model)
    coordinator = team.agents[0]
    assert coordinator.id == "coordinator"
    assert coordinator.model == "openai/gpt-5"
    assert "Coordinator instructions" in coordinator.instructions

    # Second agent is a reference (empty model)
    helper = team.agents[1]
    assert helper.id == "helper"
    assert helper.model == ""  # Unresolved reference


def test_parse_team_with_path_references(tmp_path):
    """Test parsing a team that references agents by file path."""
    parser = UnifiedMarkdownParser()

    # Team references an agent by relative path
    path = _write_markdown(
        tmp_path,
        "team_with_path_refs.md",
        """
        ---
        id: path_team
        name: Path Reference Team
        type: team
        agents:
          - coordinator
          - ./custom/specialist.md
          - ../shared/common.md
        coordinator:
          id: coordinator
          name: Coordinator
          model: openai/gpt-5
        ---
        Coordinator instructions.
        """,
    )

    team = parser.parse_file(path)
    assert isinstance(team, TeamConfig)
    assert len(team.agents) == 3

    # First agent is inline
    assert team.agents[0].id == "coordinator"
    assert team.agents[0].model == "openai/gpt-5"

    # Path references stored in id field, empty model
    assert team.agents[1].id == "./custom/specialist.md"
    assert team.agents[1].model == ""

    assert team.agents[2].id == "../shared/common.md"
    assert team.agents[2].model == ""


def test_resolve_agent_references(tmp_path):
    """Test that FileBasedTemplateManager resolves agent references."""
    # Setup: create directory structure
    pantheon_dir = tmp_path / ".pantheon"
    agents_dir = pantheon_dir / "agents"
    teams_dir = pantheon_dir / "teams"
    agents_dir.mkdir(parents=True)
    teams_dir.mkdir(parents=True)

    # Create a referenced agent
    _write_markdown(
        agents_dir,
        "helper.md",
        """
        ---
        id: helper
        name: Helper Agent
        model: openai/gpt-4
        icon: 🤝
        toolsets:
          - file_manager
        ---
        I am a helpful assistant.
        """,
    )

    # Create a team that references the agent
    _write_markdown(
        teams_dir,
        "my_team.md",
        """
        ---
        id: my_team
        name: My Team
        type: team
        agents:
          - coordinator
          - helper
        coordinator:
          id: coordinator
          name: Coordinator
          model: openai/gpt-5
        ---
        Coordinator instructions.
        """,
    )

    # Read team with reference resolution
    manager = FileBasedTemplateManager(work_dir=tmp_path)
    team = manager.read_team("my_team", resolve_refs=True)

    assert len(team.agents) == 2

    # Coordinator is inline
    assert team.agents[0].id == "coordinator"
    assert team.agents[0].model == "openai/gpt-5"

    # Helper is resolved from file
    assert team.agents[1].id == "helper"
    assert team.agents[1].name == "Helper Agent"
    assert team.agents[1].model == "openai/gpt-4"
    assert team.agents[1].toolsets == ["file_manager"]
    assert "helpful assistant" in team.agents[1].instructions


def test_resolve_path_references(tmp_path):
    """Test that FileBasedTemplateManager resolves path references."""
    # Setup: create directory structure
    pantheon_dir = tmp_path / ".pantheon"
    teams_dir = pantheon_dir / "teams"
    custom_agents_dir = teams_dir / "custom"
    teams_dir.mkdir(parents=True)
    custom_agents_dir.mkdir(parents=True)

    # Create a custom agent in a subdirectory
    _write_markdown(
        custom_agents_dir,
        "specialist.md",
        """
        ---
        id: specialist
        name: Custom Specialist
        model: openai/gpt-4-turbo
        icon: 🔬
        ---
        I am a specialist.
        """,
    )

    # Create a team that references the agent by relative path
    _write_markdown(
        teams_dir,
        "path_team.md",
        """
        ---
        id: path_team
        name: Path Team
        type: team
        agents:
          - coordinator
          - ./custom/specialist.md
        coordinator:
          id: coordinator
          name: Coordinator
          model: openai/gpt-5
        ---
        Coordinator instructions.
        """,
    )

    # Read team with reference resolution
    manager = FileBasedTemplateManager(work_dir=tmp_path)
    team = manager.read_team("path_team", resolve_refs=True)

    assert len(team.agents) == 2

    # Coordinator is inline
    assert team.agents[0].id == "coordinator"

    # Specialist is resolved from path
    assert team.agents[1].id == "specialist"
    assert team.agents[1].name == "Custom Specialist"
    assert team.agents[1].model == "openai/gpt-4-turbo"
    assert "specialist" in team.agents[1].instructions


def test_mixed_inline_and_references(tmp_path):
    """Test team with mixed inline definitions and references."""
    parser = UnifiedMarkdownParser()

    path = _write_markdown(
        tmp_path,
        "mixed_team.md",
        """
        ---
        id: mixed_team
        name: Mixed Team
        type: team
        agents:
          - inline_agent
          - referenced_by_id
          - ./path/to/agent.md
          - another_inline
        inline_agent:
          id: inline_agent
          name: Inline Agent
          model: openai/gpt-5
        another_inline:
          id: another_inline
          name: Another Inline
          model: openai/gpt-4
        ---
        Instructions for inline_agent.
        ---
        Instructions for another_inline.
        """,
    )

    team = parser.parse_file(path)
    assert len(team.agents) == 4

    # Inline agents have model and instructions
    assert team.agents[0].id == "inline_agent"
    assert team.agents[0].model == "openai/gpt-5"
    assert "inline_agent" in team.agents[0].instructions

    # ID reference
    assert team.agents[1].id == "referenced_by_id"
    assert team.agents[1].model == ""

    # Path reference
    assert team.agents[2].id == "./path/to/agent.md"
    assert team.agents[2].model == ""

    # Another inline
    assert team.agents[3].id == "another_inline"
    assert team.agents[3].model == "openai/gpt-4"
    assert "another_inline" in team.agents[3].instructions
