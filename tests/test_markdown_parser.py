"""Minimal smoke tests for UnifiedMarkdownParser using temp markdown files."""

from __future__ import annotations

from textwrap import dedent

from pantheon.factory.models import AgentConfig, TeamConfig
from pantheon.factory.template_io import (
    UnifiedMarkdownParser,
    FileBasedTemplateManager,
    PromptResolver,
    resolve_prompts,
    get_prompt_resolver,
)
from pantheon.factory.template_manager import TemplateManager


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


# ===== Prompt Resolution Tests =====


def test_prompt_resolver_basic(tmp_path):
    """Test basic prompt resolution."""
    # Create a custom prompts directory
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create a simple prompt
    _write_markdown(
        prompts_dir,
        "greeting.md",
        """
        ---
        id: greeting
        name: Greeting Prompt
        description: A simple greeting
        ---
        Hello, world!
        """,
    )

    resolver = PromptResolver(prompts_dir)
    result = resolver.resolve("Say: {{greeting}}")
    assert "Hello, world!" in result
    assert "{{greeting}}" not in result


def test_prompt_resolver_nested(tmp_path):
    """Test nested prompt resolution."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create nested prompts
    _write_markdown(
        prompts_dir,
        "inner.md",
        """
        ---
        id: inner
        ---
        Inner content
        """,
    )

    _write_markdown(
        prompts_dir,
        "outer.md",
        """
        ---
        id: outer
        ---
        Outer with {{inner}} inside
        """,
    )

    resolver = PromptResolver(prompts_dir)
    result = resolver.resolve("Start: {{outer}}")

    assert "Outer with" in result
    assert "Inner content" in result
    assert "{{outer}}" not in result
    assert "{{inner}}" not in result


def test_prompt_resolver_max_depth(tmp_path):
    """Test that max_depth prevents infinite recursion."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create a self-referential prompt (circular)
    _write_markdown(
        prompts_dir,
        "circular.md",
        """
        ---
        id: circular
        ---
        Before {{circular}} After
        """,
    )

    resolver = PromptResolver(prompts_dir)
    # With max_depth=3, it should stop after 3 levels
    result = resolver.resolve("{{circular}}", max_depth=3)
    # Should have resolved 3 times, leaving one {{circular}} unresolved
    assert "Before" in result


def test_prompt_resolver_missing_prompt(tmp_path):
    """Test that missing prompt raises ValueError."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    resolver = PromptResolver(prompts_dir)

    import pytest
    with pytest.raises(ValueError, match="not found"):
        resolver.resolve("{{nonexistent}}")


def test_prompt_resolver_caching(tmp_path):
    """Test that prompts are cached."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "cached.md",
        """
        ---
        id: cached
        ---
        Cached content
        """,
    )

    resolver = PromptResolver(prompts_dir)

    # First resolution
    resolver.resolve("{{cached}}")
    assert "cached" in resolver._cache

    # Clear cache and verify
    resolver.clear_cache()
    assert "cached" not in resolver._cache


def test_prompt_resolver_list_prompts(tmp_path):
    """Test listing available prompts."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "first.md",
        """
        ---
        id: first
        name: First Prompt
        description: The first prompt
        ---
        Content
        """,
    )

    _write_markdown(
        prompts_dir,
        "second.md",
        """
        ---
        id: second
        name: Second Prompt
        description: The second prompt
        ---
        Content
        """,
    )

    resolver = PromptResolver(prompts_dir)
    prompts = resolver.list_prompts()

    assert len(prompts) == 2
    ids = {p["id"] for p in prompts}
    assert "first" in ids
    assert "second" in ids


def test_builtin_prompts_exist():
    """Test that built-in prompts are available."""
    resolver = get_prompt_resolver()
    prompts = resolver.list_prompts()

    # Check that our built-in prompts exist
    ids = {p["id"] for p in prompts}
    assert "work_strategy" in ids
    assert "output_format" in ids
    assert "work_tracking" in ids
    assert "delegation" in ids
    assert "subagent_strategy" in ids
    assert "skills" in ids


def test_parse_agent_preserves_prompts_until_prepare_team(tmp_path):
    """Parser keeps prompt placeholders; prepare_team resolves them."""
    # Create custom prompts directory
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "custom_strategy.md",
        """
        ---
        id: custom_strategy
        ---
        ## Custom Strategy
        Follow these steps carefully.
        """,
    )

    # Create agent with prompt reference
    agent_path = _write_markdown(
        tmp_path,
        "agent.md",
        """
        ---
        id: test_agent
        name: Test Agent
        model: openai/gpt-5
        ---
        You are a test agent.

        {{custom_strategy}}
        """,
    )

    try:
        parser = UnifiedMarkdownParser()
        agent = parser.parse_file(agent_path)

        # Raw instructions still contain placeholder
        assert "{{custom_strategy}}" in agent.instructions
        assert "## Custom Strategy" not in agent.instructions

        manager = TemplateManager(work_dir=tmp_path)

        # Use custom resolver for this test - must be set AFTER TemplateManager init
        # because TemplateManager.__init__ calls init_prompt_resolver which overwrites it
        from pantheon.factory import template_io
        original_resolver = template_io._prompt_resolver
        template_io._prompt_resolver = PromptResolver(prompts_dir)
        team = TeamConfig(
            id="team",
            name="Team",
            description="",
            agents=[agent],
        )
        manager.prepare_team(team)
        resolved = team.agents[0].instructions
        assert "## Custom Strategy" in resolved
        assert "Follow these steps" in resolved
        assert "{{custom_strategy}}" not in resolved
    finally:
        template_io._prompt_resolver = original_resolver


def test_resolve_prompts_convenience_function():
    """Test the resolve_prompts convenience function."""
    # Use built-in prompts
    result = resolve_prompts("Start {{work_strategy}} End")

    assert "Work Strategy" in result
    assert "{{work_strategy}}" not in result
    assert "Start" in result
    assert "End" in result


# ===== Parameterized Prompt Tests =====


def test_prompt_with_string_param(tmp_path):
    """Test prompt with string parameter."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "greeting.md",
        """
        ---
        id: greeting
        params:
          name:
            type: string
            default: "World"
        ---
        Hello, {name}!
        """,
    )

    resolver = PromptResolver(prompts_dir)

    # Test with default value
    result = resolver.resolve("{{greeting}}")
    assert "Hello, World!" in result

    # Test with passed value
    result = resolver.resolve('{{greeting(name="Alice")}}')
    assert "Hello, Alice!" in result


def test_prompt_with_path_param_default(tmp_path):
    """Test prompt with path parameter using default value."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create a target directory for the default path
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "skills_test.md",
        """
        ---
        id: skills_test
        params:
          root_dir:
            type: path
            default: "../skills"
        ---
        Skills directory: {root_dir}
        """,
    )

    resolver = PromptResolver(prompts_dir)
    result = resolver.resolve("{{skills_test}}")

    # Default path should be resolved relative to prompt file (prompts_dir)
    # ../skills from prompts_dir = tmp_path/skills
    assert str(skills_dir) in result


def test_prompt_with_path_param_passed(tmp_path):
    """Test prompt with path parameter passed from caller."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create caller directory
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Create target directory relative to caller
    custom_skills = agents_dir / "my_skills"
    custom_skills.mkdir()

    _write_markdown(
        prompts_dir,
        "skills_test.md",
        """
        ---
        id: skills_test
        params:
          root_dir:
            type: path
            default: "../skills"
        ---
        Skills directory: {root_dir}
        """,
    )

    resolver = PromptResolver(prompts_dir)

    # Pass path relative to caller (agents_dir)
    result = resolver.resolve(
        '{{skills_test(root_dir="./my_skills")}}',
        base_path=agents_dir
    )

    # Passed path should be resolved relative to base_path (agents_dir)
    assert str(custom_skills) in result


def test_prompt_with_absolute_path_param(tmp_path):
    """Test prompt with absolute path parameter."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Use a real absolute path (cross-platform compatible)
    absolute_path = str(tmp_path / "absolute_skills")

    _write_markdown(
        prompts_dir,
        "skills_test.md",
        """
        ---
        id: skills_test
        params:
          root_dir:
            type: path
            default: "../skills"
        ---
        Skills directory: {root_dir}
        """,
    )

    resolver = PromptResolver(prompts_dir)
    result = resolver.resolve(f'{{{{skills_test(root_dir="{absolute_path}")}}}}')

    # Absolute path should be used as-is
    assert absolute_path in result


def test_prompt_with_integer_param(tmp_path):
    """Test prompt with integer parameter."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "config.md",
        """
        ---
        id: config
        params:
          max_items:
            type: integer
            default: 10
        ---
        Maximum items: {max_items}
        """,
    )

    resolver = PromptResolver(prompts_dir)

    # Test with default
    result = resolver.resolve("{{config}}")
    assert "Maximum items: 10" in result

    # Test with passed value
    result = resolver.resolve("{{config(max_items=25)}}")
    assert "Maximum items: 25" in result


def test_prompt_with_multiple_params(tmp_path):
    """Test prompt with multiple parameters."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "multi.md",
        """
        ---
        id: multi
        params:
          name:
            type: string
            default: "default_name"
          count:
            type: integer
            default: 5
        ---
        Name: {name}, Count: {count}
        """,
    )

    resolver = PromptResolver(prompts_dir)

    # Test with both params
    result = resolver.resolve('{{multi(name="test", count=20)}}')
    assert "Name: test" in result
    assert "Count: 20" in result

    # Test with partial params (use defaults for others)
    result = resolver.resolve('{{multi(name="partial")}}')
    assert "Name: partial" in result
    assert "Count: 5" in result


def test_parse_agent_with_parameterized_prompt(tmp_path):
    """Parser keeps placeholder; prepare_team resolves parameterized prompts."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Create custom skills directory
    custom_skills = agents_dir / "local_skills"
    custom_skills.mkdir()

    _write_markdown(
        prompts_dir,
        "skills_param.md",
        """
        ---
        id: skills_param
        params:
          root_dir:
            type: path
            default: "../skills"
        ---
        Use skills from: {root_dir}
        """,
    )

    # Create agent with parameterized prompt reference
    agent_path = _write_markdown(
        agents_dir,
        "agent.md",
        """
        ---
        id: test_agent
        name: Test Agent
        model: openai/gpt-5
        ---
        You are an agent.

        {{skills_param(root_dir="./local_skills")}}
        """,
    )

    try:
        parser = UnifiedMarkdownParser()
        agent = parser.parse_file(agent_path)

        assert "{{skills_param" in agent.instructions

        manager = TemplateManager(work_dir=tmp_path)

        # Use custom resolver for this test - must be set AFTER TemplateManager init
        from pantheon.factory import template_io
        original_resolver = template_io._prompt_resolver
        template_io._prompt_resolver = PromptResolver(prompts_dir)
        team = TeamConfig(
            id="team",
            name="Team",
            description="",
            agents=[agent],
        )
        manager.prepare_team(team)
        resolved = team.agents[0].instructions

        # Path should be resolved relative to agent file
        assert str(custom_skills) in resolved
        assert "{{skills_param" not in resolved
    finally:
        template_io._prompt_resolver = original_resolver


def test_nested_prompts_with_params(tmp_path):
    """Test nested prompts where outer passes params to inner."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "inner.md",
        """
        ---
        id: inner
        params:
          value:
            type: string
            default: "inner_default"
        ---
        Inner value: {value}
        """,
    )

    _write_markdown(
        prompts_dir,
        "outer.md",
        """
        ---
        id: outer
        ---
        Outer content with {{inner(value="from_outer")}}
        """,
    )

    resolver = PromptResolver(prompts_dir)
    result = resolver.resolve("{{outer}}")

    assert "Outer content with" in result
    assert "Inner value: from_outer" in result


def test_quoted_param_values(tmp_path):
    """Test various quoting styles for parameter values."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    _write_markdown(
        prompts_dir,
        "quoted.md",
        """
        ---
        id: quoted
        params:
          msg:
            type: string
            default: ""
        ---
        Message: {msg}
        """,
    )

    resolver = PromptResolver(prompts_dir)

    # Double quotes
    result = resolver.resolve('{{quoted(msg="hello world")}}')
    assert "Message: hello world" in result

    # Single quotes
    result = resolver.resolve("{{quoted(msg='hello world')}}")
    assert "Message: hello world" in result

    # Unquoted simple value
    result = resolver.resolve("{{quoted(msg=simple)}}")
    assert "Message: simple" in result


# ===== Prompt Path Reference Tests =====


def test_prompt_relative_path_reference(tmp_path):
    """Parser keeps path placeholder; prepare_team resolves it."""
    # Create directory structure
    agents_dir = tmp_path / "agents"
    prompts_dir = tmp_path / "prompts"
    agents_dir.mkdir()
    prompts_dir.mkdir()

    # Create an external prompt
    _write_markdown(
        prompts_dir,
        "external_strategy.md",
        """
        ---
        id: external_strategy
        ---
        ## External Strategy
        This is loaded from an external path.
        """,
    )

    # Create agent that references prompt via relative path
    agent_path = _write_markdown(
        agents_dir,
        "test_agent.md",
        """
        ---
        id: test_agent
        name: Test Agent
        model: openai/gpt-5
        ---
        You are an agent.

        {{../prompts/external_strategy.md}}
        """,
    )

    parser = UnifiedMarkdownParser()
    agent = parser.parse_file(agent_path)

    assert "{{../prompts/external_strategy.md}}" in agent.instructions

    manager = TemplateManager(work_dir=tmp_path)
    team = TeamConfig(id="team", name="Team", description="", agents=[agent])
    manager.prepare_team(team)
    resolved = team.agents[0].instructions

    assert "## External Strategy" in resolved
    assert "external path" in resolved
    assert "{{../prompts/external_strategy.md}}" not in resolved


def test_prompt_nested_relative_paths(tmp_path):
    """Parser keeps placeholders; prepare_team resolves nested paths."""
    # Create directory structure
    agents_dir = tmp_path / "agents"
    shared_dir = tmp_path / "shared"
    common_dir = shared_dir / "common"
    agents_dir.mkdir()
    shared_dir.mkdir()
    common_dir.mkdir()

    # Create innermost prompt
    _write_markdown(
        common_dir,
        "base.md",
        """
        ---
        id: base
        ---
        Base content from common directory.
        """,
    )

    # Create middle prompt that references base
    _write_markdown(
        shared_dir,
        "middle.md",
        """
        ---
        id: middle
        ---
        Middle layer with:
        {{./common/base.md}}
        """,
    )

    # Create agent that references middle prompt
    agent_path = _write_markdown(
        agents_dir,
        "nested_agent.md",
        """
        ---
        id: nested_agent
        name: Nested Agent
        model: openai/gpt-5
        ---
        Agent instructions:

        {{../shared/middle.md}}
        """,
    )

    parser = UnifiedMarkdownParser()
    agent = parser.parse_file(agent_path)

    assert "{{../shared/middle.md}}" in agent.instructions

    manager = TemplateManager(work_dir=tmp_path)
    team = TeamConfig(id="team", name="Team", description="", agents=[agent])
    manager.prepare_team(team)
    resolved = team.agents[0].instructions

    # Both middle and base content should be resolved after prepare_team
    assert "Middle layer" in resolved
    assert "Base content from common directory" in resolved
    assert "{{" not in resolved


def test_prompt_path_with_params(tmp_path):
    """Test prompt path reference with parameters."""
    prompts_dir = tmp_path / "prompts"
    agents_dir = tmp_path / "agents"
    prompts_dir.mkdir()
    agents_dir.mkdir()

    # Create a parameterized external prompt
    _write_markdown(
        prompts_dir,
        "param_prompt.md",
        """
        ---
        id: param_prompt
        params:
          name:
            type: string
            default: "default_name"
          count:
            type: integer
            default: 10
        ---
        Processing {name} with count {count}.
        """,
    )

    # Resolve with parameters via path reference
    from pantheon.factory.template_io import PromptResolver

    resolver = PromptResolver(tmp_path / "builtin_prompts")  # Different dir
    result = resolver.resolve(
        '{{./prompts/param_prompt.md(name="custom", count=42)}}',
        base_path=tmp_path
    )

    assert "Processing custom with count 42" in result


def test_prompt_mixed_id_and_path_references(tmp_path):
    """Test mixing ID references and path references."""
    # Create custom prompts directory for resolver
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()

    # Create a builtin prompt (ID reference)
    _write_markdown(
        builtin_dir,
        "builtin_prompt.md",
        """
        ---
        id: builtin_prompt
        ---
        Builtin content.
        """,
    )

    # Create external prompt (path reference)
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    _write_markdown(
        external_dir,
        "external.md",
        """
        ---
        id: external
        ---
        External content.
        """,
    )

    from pantheon.factory.template_io import PromptResolver

    resolver = PromptResolver(builtin_dir)

    # Mix both references
    text = """
    First: {{builtin_prompt}}
    Second: {{./external/external.md}}
    """

    result = resolver.resolve(text, base_path=tmp_path)

    assert "Builtin content" in result
    assert "External content" in result
    assert "{{" not in result
