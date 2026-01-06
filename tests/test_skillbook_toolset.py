"""
Tests for SkillbookToolSet - the LLM tool interface for skill management.

Tests cover:
1. add_skill (confidence, sources, agent_name, auto-convert)
2. update_skill (content, sources)
3. remove_skill (basic, with sources)
4. tag_skill (all types, accumulation)
5. list_skills (filters)
6. compress_trajectory
7. user-defined skill protection
8. get_skillbook_content

Run with: pytest tests/test_skillbook_toolset.py -v
"""

import json
import tempfile
from pathlib import Path

import pytest

from pantheon.internal.learning.skillbook import Skillbook
from pantheon.toolsets.skillbook import SkillbookToolSet


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def skillbook(temp_dir):
    """Create a fresh Skillbook for testing."""
    skills_dir = temp_dir / "skills"
    skills_dir.mkdir()
    skillbook_path = temp_dir / "skillbook.json"
    sb = Skillbook(
        skills_dir=skills_dir,
        skillbook_path=skillbook_path,
        max_skills_per_section=10,
        max_content_length=500,  # For auto-convert testing
        auto_load=False,
    )
    return sb


@pytest.fixture
def toolset(skillbook):
    """Create a SkillbookToolSet for testing."""
    return SkillbookToolSet(skillbook, min_confidence=0.7)


# ===========================================================================
# add_skill Tests
# ===========================================================================


class TestAddSkill:
    """Tests for add_skill functionality."""

    async def test_add_skill_basic_and_confidence(self, toolset):
        """Test basic addition and confidence threshold."""
        # Basic add
        result = await toolset.add_skill(
            section="strategies",
            content="Use pandas.read_csv() for CSV files > 1MB",
        )
        assert result["success"] is True
        assert result["skill_id"].startswith("str-")
        
        # Low confidence rejected
        result = await toolset.add_skill(
            section="strategies",
            content="Maybe use some tool",
            confidence=0.5,  # Below 0.7 threshold
        )
        assert result["success"] is False
        assert "below threshold" in result["error"]
        
        # Exact threshold accepted
        result = await toolset.add_skill(
            section="patterns",
            content="At threshold skill",
            confidence=0.7,
        )
        assert result["success"] is True

    async def test_add_skill_with_custom_id_and_agent_name(self, toolset):
        """Test custom ID and agent scope."""
        # Custom ID
        result = await toolset.add_skill(
            section="workflows",
            content="API retry workflow",
            skill_id="api-retry-workflow",
        )
        assert result["success"] is True
        assert result["skill_id"] == "api-retry-workflow"
        
        # Agent scope
        result = await toolset.add_skill(
            section="strategies",
            content="Use streaming for large JSON files",
            agent_name="data_analyst",
        )
        assert result["success"] is True
        skill = toolset.skillbook.get_skill(result["skill_id"])
        assert skill.agent_scope == "data_analyst"

    async def test_add_skill_with_sources(self, toolset, temp_dir):
        """Test adding skill with source files."""
        source_file = temp_dir / "workflow.md"
        source_file.write_text("# Workflow details\n\n1. Step one\n2. Step two")
        
        result = await toolset.add_skill(
            section="workflows",
            content="Complex data processing workflow",
            sources=[str(source_file)],
        )
        
        assert result["success"] is True
        skill = toolset.skillbook.get_skill(result["skill_id"])
        assert len(skill.sources) == 1
        assert (toolset.skills_dir / skill.sources[0]).exists()

    async def test_add_skill_auto_convert_long_content(self, toolset):
        """Test that long content (>500 chars) is auto-converted to sources."""
        long_content = "A" * 600  # Exceeds max_content_length=500
        
        result = await toolset.add_skill(
            section="workflows",
            content=long_content,
        )
        
        assert result["success"] is True
        skill = toolset.skillbook.get_skill(result["skill_id"])
        # Long content should be auto-converted to source file
        # and skill.content should be the description (short)
        assert len(skill.content) <= 500 or len(skill.sources) > 0


# ===========================================================================
# update_skill Tests
# ===========================================================================


class TestUpdateSkill:
    """Tests for update_skill functionality."""

    async def test_update_skill_content_and_not_found(self, toolset, temp_dir):
        """Test updating content and handling non-existent skill."""
        # Add a skill first
        add_result = await toolset.add_skill(
            section="strategies",
            content="Original content",
        )
        skill_id = add_result["skill_id"]
        
        # Update content
        result = await toolset.update_skill(
            skill_id=skill_id,
            content="Updated content with better explanation",
        )
        assert result["success"] is True
        skill = toolset.skillbook.get_skill(skill_id)
        assert skill.content == "Updated content with better explanation"
        
        # Not found
        result = await toolset.update_skill(
            skill_id="non-existent-id",
            content="New content",
        )
        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_update_skill_with_sources(self, toolset, temp_dir):
        """Test updating skill sources."""
        add_result = await toolset.add_skill(
            section="workflows",
            content="A workflow",
        )
        skill_id = add_result["skill_id"]
        
        # Create new source file
        new_source = temp_dir / "new_workflow.md"
        new_source.write_text("# New workflow content")
        
        result = await toolset.update_skill(
            skill_id=skill_id,
            sources=[str(new_source)],
        )
        
        assert result["success"] is True
        skill = toolset.skillbook.get_skill(skill_id)
        assert len(skill.sources) == 1


# ===========================================================================
# remove_skill Tests
# ===========================================================================


class TestRemoveSkill:
    """Tests for remove_skill functionality."""

    async def test_remove_skill_basic_and_with_sources(self, toolset, temp_dir):
        """Test removing skills with and without sources."""
        # Add and remove basic skill
        add_result = await toolset.add_skill(
            section="strategies",
            content="Temporary skill to remove",
        )
        skill_id = add_result["skill_id"]
        
        result = await toolset.remove_skill(skill_id)
        assert result["success"] is True
        assert toolset.skillbook.get_skill(skill_id) is None
        
        # Not found
        result = await toolset.remove_skill("non-existent-id")
        assert result["success"] is False
        assert "not found" in result["error"]
        
        # Remove skill with sources
        source_file = temp_dir / "to_delete.md"
        source_file.write_text("# Content")
        
        add_result = await toolset.add_skill(
            section="workflows",
            content="Skill with source",
            sources=[str(source_file)],
        )
        skill_id = add_result["skill_id"]
        skill = toolset.skillbook.get_skill(skill_id)
        copied_source = toolset.skills_dir / skill.sources[0]
        assert copied_source.exists()
        
        result = await toolset.remove_skill(skill_id)
        assert result["success"] is True
        assert not copied_source.exists()


# ===========================================================================
# tag_skill Tests
# ===========================================================================


class TestTagSkill:
    """Tests for tag_skill functionality."""

    async def test_tag_skill_all_types_and_accumulation(self, toolset):
        """Test all tag types and accumulation."""
        add_result = await toolset.add_skill(
            section="strategies",
            content="Skill for tagging",
        )
        skill_id = add_result["skill_id"]
        
        # Test helpful
        result = await toolset.tag_skill(skill_id, "helpful")
        assert result["success"] is True
        assert "+1" in result["stats"]
        
        # Test accumulation
        await toolset.tag_skill(skill_id, "helpful")
        result = await toolset.tag_skill(skill_id, "helpful")
        assert "+3" in result["stats"]
        
        # Test harmful
        add2 = await toolset.add_skill("strategies", "Another skill")
        result = await toolset.tag_skill(add2["skill_id"], "harmful")
        assert result["success"] is True
        assert "-1" in result["stats"]
        
        # Test neutral
        add3 = await toolset.add_skill("strategies", "Third skill")
        result = await toolset.tag_skill(add3["skill_id"], "neutral")
        assert result["success"] is True
        assert "~1" in result["stats"]
        
        # Not found
        result = await toolset.tag_skill("non-existent-id", "helpful")
        assert result["success"] is False
        assert "not found" in result["error"]


# ===========================================================================
# list_skills Tests
# ===========================================================================


class TestListSkills:
    """Tests for list_skills functionality."""

    async def test_list_skills_filters(self, toolset):
        """Test list_skills with various filters."""
        # Setup: add test skills
        await toolset.add_skill("strategies", "Use polars for large CSV files")
        await toolset.add_skill("strategies", "Use pandas for small data")
        add3 = await toolset.add_skill("patterns", "Retry with exponential backoff")
        long_content = "A" * 150 + " end"
        await toolset.add_skill("workflows", long_content)
        
        # Tag one as helpful
        await toolset.tag_skill(add3["skill_id"], "helpful")
        await toolset.tag_skill(add3["skill_id"], "helpful")
        
        # All skills
        result = await toolset.list_skills()
        assert result["total"] == 4
        
        # Empty case
        toolset.skillbook._skills.clear()
        result = await toolset.list_skills()
        assert result["total"] == 0
        
        # Re-add for more tests
        await toolset.add_skill("strategies", "Use polars for streaming")
        await toolset.add_skill("strategies", "Use POLARS uppercase")
        
        # By section (using query with semantic=False)
        result = await toolset.list_skills(query="strategies", semantic=False)
        assert result["total"] == 2
        assert all(s["section"] == "strategies" for s in result["skills"])
        
        # By keyword (case-insensitive, semantic=False)
        result = await toolset.list_skills(query="polars", semantic=False)
        assert result["total"] == 2
        
        # Include full content
        await toolset.add_skill("workflows", "X" * 150)
        result = await toolset.list_skills(include_full_content=False)
        long_skill = [s for s in result["skills"] if len(s["content"]) > 100]
        if long_skill:
            assert long_skill[0]["content"].endswith("...")
        
        result = await toolset.list_skills(include_full_content=True)
        long_skill = [s for s in result["skills"] if len(s["content"]) > 100]
        if long_skill:
            assert not long_skill[0]["content"].endswith("...")


# ===========================================================================
# compress_trajectory Tests
# ===========================================================================


class TestCompressTrajectory:
    """Tests for compress_trajectory functionality."""

    async def test_compress_trajectory(self, toolset, temp_dir):
        """Test trajectory compression and skill extraction."""
        # Create a sample memory file
        memory_path = temp_dir / "memory.json"
        memory_data = {
            "messages": [
                {"role": "user", "content": "Help me with data analysis"},
                {
                    "role": "assistant",
                    "content": "I'll help you. Using [str-001] skill.",
                    "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}]
                },
                {"role": "tool", "content": "File written successfully"},
            ]
        }
        memory_path.write_text(json.dumps(memory_data))
        
        # Basic compression
        result = await toolset.compress_trajectory(str(memory_path))
        
        assert result["success"] is True
        assert "trajectory_path" in result
        assert "details_path" in result
        assert Path(result["trajectory_path"]).exists()
        
        # Skill extraction
        memory_data["messages"][1]["content"] = "Using skill [pat-002] for this"
        memory_path.write_text(json.dumps(memory_data))
        
        result = await toolset.compress_trajectory(str(memory_path))
        assert "skill_ids_cited" in result
        
        # Invalid path
        result = await toolset.compress_trajectory("/non/existent/path.json")
        assert result["success"] is False
        assert "error" in result


# ===========================================================================
# User-defined Skill Protection Tests
# ===========================================================================


class TestUserDefinedProtection:
    """Tests for user-defined skill protection."""

    async def test_user_defined_update_and_remove_rejected(self, toolset, temp_dir):
        """Test that user-defined skills cannot be modified or removed."""
        # Create a user-defined skill file
        skill_file = toolset.skills_dir / "my-custom-skill.md"
        skill_file.write_text("""---
id: user-custom-001
description: My custom skill
section: strategies
type: user
---

This is a user-defined skill with custom content.
""")
        
        # Load the skill
        from pantheon.internal.learning.skill_loader import SkillLoader
        loader = SkillLoader(toolset.skills_dir, toolset.skillbook)
        loader.load_and_merge()
        
        # Try to update - should be rejected
        result = await toolset.update_skill(
            skill_id="user-custom-001",
            content="Trying to override user content",
        )
        assert result["success"] is False
        assert "user-defined" in result["error"].lower()
        
        # Try to remove - should be rejected
        result = await toolset.remove_skill("user-custom-001")
        assert result["success"] is False
        assert "user-defined" in result["error"].lower()


# ===========================================================================
# get_skillbook_content Tests
# ===========================================================================


class TestGetSkillbookContent:
    """Tests for get_skillbook_content functionality."""

    async def test_get_skillbook_content(self, toolset):
        """Test getting formatted skillbook content."""
        # Add some skills
        await toolset.add_skill("strategies", "Strategy one")
        await toolset.add_skill("patterns", "Pattern one")
        
        result = await toolset.get_skillbook_content()
        
        assert result["success"] is True
        assert "content" in result
        assert result["skill_count"] == 2
        assert "Strategy one" in result["content"]
        assert "Pattern one" in result["content"]
        
        # With agent name
        await toolset.add_skill(
            "strategies",
            "Agent-specific skill",
            agent_name="my_agent"
        )
        
        result = await toolset.get_skillbook_content(agent_name="my_agent")
        assert "Agent-specific skill" in result["content"]


# ===========================================================================
# Integration Tests
# ===========================================================================


class TestIntegration:
    """Integration tests for full workflows."""

    async def test_full_learning_workflow(self, toolset, temp_dir):
        """Test complete learning workflow: add, tag, update, list."""
        # Add skill
        add_result = await toolset.add_skill(
            section="strategies",
            content="Use polars for lazy evaluation",
            confidence=0.9,
        )
        skill_id = add_result["skill_id"]
        
        # Tag as helpful
        await toolset.tag_skill(skill_id, "helpful")
        
        # Update with better content
        await toolset.update_skill(
            skill_id=skill_id,
            content="Use polars lazy evaluation for memory-efficient processing of large datasets",
        )
        
        # Verify via list (using query with semantic=False for keyword search)
        result = await toolset.list_skills(query="polars", semantic=False)
        assert result["total"] == 1
        assert "memory-efficient" in result["skills"][0]["content"]
        assert "+1" in result["skills"][0]["stats"]

    async def test_deduplication_workflow(self, toolset):
        """Test checking for duplicates before adding."""
        # Add first skill
        await toolset.add_skill(
            section="strategies",
            content="Use streaming for large files",
        )
        
        # Check for similar before adding (using query with semantic=False)
        existing = await toolset.list_skills(query="streaming", semantic=False)
        assert existing["total"] == 1
        
        # Instead of adding duplicate, update existing
        skill_id = existing["skills"][0]["id"]
        await toolset.update_skill(
            skill_id=skill_id,
            content="Use streaming APIs for files > 1GB to avoid memory issues",
        )
        
        # Still only one skill
        result = await toolset.list_skills(query="streaming", semantic=False)
        assert result["total"] == 1
        assert "1GB" in result["skills"][0]["content"]
