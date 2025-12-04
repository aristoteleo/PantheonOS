"""Minimal smoke tests for UnifiedMarkdownParser using temp markdown files."""

from __future__ import annotations

from textwrap import dedent

from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_io import UnifiedMarkdownParser


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


def test_parse_multi_agent_chatroom_markdown(tmp_path):
    parser = UnifiedMarkdownParser()
    path = _write_markdown(
        tmp_path,
        "team_chatroom.md",
        """
        ---
        id: research_room
        name: Research Room
        type: chatroom
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
        sub_agents:
          - helper
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

    chatroom = parser.parse_file(path)
    assert isinstance(chatroom, TeamConfig)
    assert chatroom.id == "research_room"
    assert chatroom.name == "Research Room"
    assert chatroom.category == "research"
    assert chatroom.version == "1.2.3"
    assert chatroom.sub_agents == ["helper"]
    assert chatroom.tags == ["markdown"]
    assert len(chatroom.agents) == 2

    analyst, writer = chatroom.agents
    assert analyst.id == "analyst"
    assert analyst.tags == ["gather"]
    assert "intelligence" in analyst.instructions

    assert writer.id == "writer"
    assert writer.instructions.startswith("Draft final response")
