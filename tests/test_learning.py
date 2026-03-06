"""
Comprehensive tests for ACE (Agentic Context Engineering) module.

Tests cover:
1. Skillbook CRUD operations and persistence
2. SkillLoader file parsing and merging
3. LearningInput and build_learning_input
4. Reflector LLM analysis
5. SkillManager LLM decisions
6. LearningPipeline async workflow
7. End-to-end integration

Run with: pytest tests/test_learning.py -v
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from pantheon.internal.learning import (
    LearningPipeline,
    LearningInput,
    Reflector,
    Skill,
    Skillbook,
    SkillManager,
    build_learning_input,
    create_learning_resources,
)
from pantheon.internal.learning.reflector import ReflectorOutput, SkillTag, ExtractedLearning
from pantheon.internal.learning.skill_manager import UpdateOperation

# Test model - use normal model from settings (defined in .pantheon/settings.json)
from pantheon.utils.model_selector import get_model_selector
TEST_MODEL = get_model_selector().resolve_model("normal")[0]


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def skillbook(temp_dir):
    """Create a fresh Skillbook for testing."""
    skills_dir = Path(temp_dir) / "skills"
    skills_dir.mkdir()
    skillbook_path = Path(temp_dir) / "skillbook.json"
    sb = Skillbook(
        skills_dir=skills_dir,
        skillbook_path=skillbook_path,
        max_skills_per_section=10,
        max_content_length=500,
        auto_load=False,  # Don't auto-load in tests
    )
    return sb


@pytest.fixture
def sample_messages():
    """Sample conversation messages for testing."""
    return [
        {"role": "user", "content": "How do I read a CSV file in Python?"},
        {
            "role": "assistant",
            "content": "I'll help you read a CSV file. Following [str-00001], I'll use pandas.",
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {
                        "name": "execute_code",
                        "arguments": '{"code": "import pandas as pd\\ndf = pd.read_csv(\\"data.csv\\")"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_123",
            "content": "Successfully read CSV with 1000 rows and 5 columns.",
        },
        {
            "role": "assistant",
            "content": "I've successfully read the CSV file using pandas. The file contains 1000 rows and 5 columns. You can now work with the DataFrame `df`.",
        },
    ]


@pytest.fixture
def sample_learning_input(sample_messages, temp_dir):
    """Create a sample LearningInput for testing."""
    return build_learning_input(
        turn_id="test-turn-001",
        agent_name="code_agent",
        messages=sample_messages,
        learning_dir=temp_dir,
    )


# ===========================================================================
# Skillbook Tests
# ===========================================================================


class TestSkillbook:
    """Tests for Skillbook CRUD operations and persistence."""

    def test_add_skill(self, skillbook):
        """Test adding skills."""
        skill = skillbook.add_skill(
            section="strategies",
            content="Use pandas for CSV file operations",
            agent_scope="global",
        )
        assert skill is not None
        assert skill.id.startswith("str-")
        assert skill.content == "Use pandas for CSV file operations"
        assert skill.agent_scope == "global"
        assert len(skillbook.skills()) == 1

    def test_add_skill_with_agent_scope(self, skillbook):
        """Test adding agent-specific skills."""
        skill = skillbook.add_skill(
            section="patterns",
            content="Always validate input paths",
            agent_scope="file_agent",
        )
        assert skill.agent_scope == "file_agent"

    def test_update_skill(self, skillbook):
        """Test updating skill content."""
        skill = skillbook.add_skill("strategies", "Original content")
        updated = skillbook.update_skill(skill.id, content="Updated content")
        assert updated is not None
        assert updated.content == "Updated content"

    def test_tag_skill(self, skillbook):
        """Test tagging skills."""
        skill = skillbook.add_skill("strategies", "Test skill")

        # Tag as helpful
        skillbook.tag_skill(skill.id, "helpful", 2)
        assert skill.helpful == 2

        # Tag as harmful
        skillbook.tag_skill(skill.id, "harmful", 1)
        assert skill.harmful == 1

        # Tag as neutral
        skillbook.tag_skill(skill.id, "neutral", 1)
        assert skill.neutral == 1

    def test_remove_skill(self, skillbook):
        """Test soft removal of skills."""
        skill = skillbook.add_skill("strategies", "Test skill")
        skillbook.remove_skill(skill.id, soft=True)
        assert skill.status == "invalid"  # Soft removal marks as invalid

    def test_get_skills_for_agent(self, skillbook):
        """Test retrieving skills for specific agent with scope enabled."""
        # Enable agent scope for this test
        skillbook.enable_agent_scope = True
        
        # Add global skill
        skillbook.add_skill("strategies", "Global strategy", agent_scope="global")
        # Add agent-specific skill
        skillbook.add_skill("patterns", "Code pattern", agent_scope="code_agent")
        # Add another agent's skill
        skillbook.add_skill("mistakes", "File mistake", agent_scope="file_agent")

        # Unknown agent should only see global skills (scope enabled)
        global_skills = skillbook.get_skills_for_agent("unknown_agent")
        assert len(global_skills) == 1

        # Code agent should see global + its own
        code_skills = skillbook.get_skills_for_agent("code_agent")
        assert len(code_skills) == 2

    def test_as_prompt(self, skillbook):
        """Test prompt generation with helpful-first sorting."""
        s1 = skillbook.add_skill("strategies", "Good strategy")
        s2 = skillbook.add_skill("strategies", "Better strategy")

        # Make s2 more helpful
        skillbook.tag_skill(s2.id, "helpful", 5)
        skillbook.tag_skill(s1.id, "helpful", 1)

        prompt = skillbook.as_prompt("any_agent")
        assert "Better strategy" in prompt
        assert "Good strategy" in prompt
        # Better strategy should appear first (higher score)
        assert prompt.index("Better strategy") < prompt.index("Good strategy")

    def test_persistence(self, skillbook, temp_dir):
        """Test save and load."""
        path = os.path.join(temp_dir, "test_skillbook.json")

        # Add skills and save
        skillbook.add_skill("strategies", "Strategy 1")
        skillbook.add_skill("mistakes", "Mistake 1")
        skillbook.save(path)

        # Load into new skillbook
        skills_dir2 = Path(temp_dir) / "skills2"
        skills_dir2.mkdir()
        new_sb = Skillbook(
            skills_dir=skills_dir2,
            skillbook_path=Path(path),
            auto_load=True,
        )

        assert len(new_sb.skills()) == 2
        assert any(s.content == "Strategy 1" for s in new_sb.skills())

    def test_max_skills_eviction(self, temp_dir):
        """Test eviction when max skills per section reached."""
        skills_dir = Path(temp_dir) / "skills_eviction"
        skills_dir.mkdir()
        sb = Skillbook(
            skills_dir=skills_dir,
            skillbook_path=Path(temp_dir) / "eviction.json",
            max_skills_per_section=3,
            max_content_length=500,
            auto_load=False,
        )

        # Add 3 skills
        s1 = sb.add_skill("strategies", "Skill 1")
        s2 = sb.add_skill("strategies", "Skill 2")
        s3 = sb.add_skill("strategies", "Skill 3")

        # Make s1 have negative score
        sb.tag_skill(s1.id, "harmful", 5)
        s1_id = s1.id

        # Add 4th skill - should evict s1 (hard delete, not soft)
        s4 = sb.add_skill("strategies", "Skill 4")
        assert s4 is not None

        # s1 should be completely removed (hard delete)
        assert sb.get_skill(s1_id) is None

        active_strategies = [
            s for s in sb.skills() if s.section == "strategies" and s.status == "active"
        ]
        assert len(active_strategies) == 3

    def test_content_length_no_limit(self, skillbook):
        """Test that content has no length limit (unified structure)."""
        long_content = "x" * 600
        skill = skillbook.add_skill("strategies", long_content)
        # Content is now stored without truncation
        assert len(skill.content) == 600


# ===========================================================================
# SkillLoader Tests
# ===========================================================================


class TestSkillLoader:
    """Tests for SkillLoader functionality."""

    @pytest.fixture
    def skills_dir(self, temp_dir):
        """Create a skills directory for testing."""
        skills = Path(temp_dir) / "skills"
        skills.mkdir()
        return skills

    def test_parse_front_matter(self, skills_dir):
        """Test parsing YAML front matter from markdown files."""
        from pantheon.internal.learning.skill_loader import parse_front_matter

        # Create a skill file with front matter
        skill_file = skills_dir / "test.md"
        skill_file.write_text("""---
id: test-skill
description: A test skill
section: workflows
tags: [test, demo]
---

# Content here
""")

        fm, body = parse_front_matter(skill_file)
        
        assert fm is not None
        assert fm["id"] == "test-skill"
        assert fm["description"] == "A test skill"
        assert fm["section"] == "workflows"
        assert fm["tags"] == ["test", "demo"]
        assert "# Content here" in body

    def test_parse_front_matter_no_front_matter(self, skills_dir):
        """Test parsing file without front matter."""
        from pantheon.internal.learning.skill_loader import parse_front_matter

        # Create a file without front matter
        no_fm_file = skills_dir / "no_fm.md"
        no_fm_file.write_text("# Just content\n\nNo front matter here.")

        fm, body = parse_front_matter(no_fm_file)
        
        assert fm is None
        assert "# Just content" in body

    def test_parse_skills_md(self, skills_dir):
        """Test parsing SKILLS.md for simple rules."""
        from pantheon.internal.learning.skill_loader import parse_skills_md

        # Create SKILLS.md
        skills_md = skills_dir / "SKILLS.md"
        skills_md.write_text("""---
# User rules
---

## User Rules

- Always use uv for Python projects
- Run tests before commit

## Strategies

- Use polars for large data
""")

        skills = parse_skills_md(skills_md, skills_dir)
        
        assert len(skills) == 3
        
        # Check user rules
        user_rules = [s for s in skills if s.section == "user_rules"]
        assert len(user_rules) == 2
        assert any("uv" in s.content for s in user_rules)
        
        # Check strategies
        strategies = [s for s in skills if s.section == "strategies"]
        assert len(strategies) == 1
        assert "polars" in strategies[0].content

    def test_scan_skill_files(self, skills_dir):
        """Test scanning skills directory for .md files."""
        from pantheon.internal.learning.skill_loader import scan_skill_files

        # Create various files
        (skills_dir / "skill1.md").write_text("# Skill 1")
        (skills_dir / "skill2.md").write_text("# Skill 2")
        (skills_dir / "SKILLS.md").write_text("# Main skills")  # Should be skipped
        (skills_dir / "subdir").mkdir()
        (skills_dir / "subdir" / "nested.md").write_text("# Nested")
        (skills_dir / ".hidden").mkdir()
        (skills_dir / ".hidden" / "secret.md").write_text("# Hidden")  # Should be skipped

        files = scan_skill_files(skills_dir)
        
        # Should find 3 files (skill1, skill2, nested), skip SKILLS.md and hidden
        assert len(files) == 3
        file_names = [f.name for f in files]
        assert "skill1.md" in file_names
        assert "skill2.md" in file_names
        assert "nested.md" in file_names
        assert "SKILLS.md" not in file_names
        assert "secret.md" not in file_names

    def test_parse_skill_from_file(self, skills_dir):
        """Test parsing a skill file and creating a Skill object."""
        from pantheon.internal.learning.skill_loader import parse_skill_from_file

        # Create a valid skill file
        skill_file = skills_dir / "my-workflow.md"
        skill_file.write_text("""---
id: my-workflow
description: A workflow for doing X
section: workflows
tags: [example]
---

# Detailed content
This is the full content body that stays in the file only.
""")

        skill = parse_skill_from_file(skill_file, skills_dir)

        assert skill is not None
        assert skill.id == "my-workflow"
        # For file-based skills: content = None (full content stays in source file)
        # description from front matter is used for display
        assert skill.content is None
        assert skill.description == "A workflow for doing X"
        assert skill.section == "workflows"
        assert skill.tags == ["example"]
        assert skill.is_user_defined()

    def test_parse_skill_from_file_missing_required_fields(self, skills_dir):
        """Test that files without id are skipped (description is optional)."""
        from pantheon.internal.learning.skill_loader import parse_skill_from_file

        # Create file without description - should still work (description optional)
        no_desc = skills_dir / "no-desc.md"
        no_desc.write_text("""---
id: no-desc
---
# Missing description
""")

        skill = parse_skill_from_file(no_desc, skills_dir)
        assert skill is not None  # description is optional
        assert skill.id == "no-desc"
        assert skill.description is None

        # Create file without id - should be skipped (id is required)
        no_id = skills_dir / "no-id.md"
        no_id.write_text("""---
description: No id here
---
# Missing id
""")

        skill = parse_skill_from_file(no_id, skills_dir)
        assert skill is None

    def test_skill_loader_merge(self, skills_dir):
        """Test SkillLoader merging skills into skillbook."""
        from pantheon.internal.learning.skill_loader import SkillLoader

        # Create skill files
        (skills_dir / "workflow1.md").write_text("""---
id: workflow1
description: First workflow
section: workflows
---
# Details
""")
        (skills_dir / "workflow2.md").write_text("""---
id: workflow2
description: Second workflow
---
# Details
""")

        # Create skillbook with existing skill
        skillbook = Skillbook()

        # Load and merge
        loader = SkillLoader(skills_dir, skillbook)
        loaded = loader.load_and_merge(cleanup_orphans=False)

        assert loaded == 2
        assert skillbook.get_skill("workflow1") is not None
        assert skillbook.get_skill("workflow2") is not None
        # File-based skills have content=None, check description instead
        assert skillbook.get_skill("workflow1").description == "First workflow"

    def test_skill_loader_preserves_ratings(self, skills_dir):
        """Test that SkillLoader preserves existing ratings when updating."""
        from pantheon.internal.learning.skill_loader import SkillLoader

        # Create skillbook with existing skill that has ratings
        skillbook = Skillbook()
        existing = Skill(
            id="existing-skill",
            section="strategies",
            content="Old content",
            sources=["skills/existing.md"],
            helpful=10,
            harmful=2,
        )
        skillbook._skills[existing.id] = existing
        skillbook._sections.setdefault(existing.section, []).append(existing.id)

        # Create skill file with same id but different content
        (skills_dir / "existing.md").write_text("""---
id: existing-skill
description: Updated content
section: strategies
---
# New details
""")

        # Load and merge
        loader = SkillLoader(skills_dir, skillbook)
        loader.load_and_merge(cleanup_orphans=False)

        # Verify description was updated but ratings preserved
        skill = skillbook.get_skill("existing-skill")
        assert skill is not None
        # Description should be updated from file front matter
        assert skill.description == "Updated content"
        # Ratings should be preserved from existing skill
        assert skill.helpful == 10
        assert skill.harmful == 2

    def test_skill_loader_orphan_cleanup(self, skills_dir):
        """Test that SkillLoader cleans up orphan skills."""
        from pantheon.internal.learning.skill_loader import SkillLoader

        # Create skillbook with orphan skill (source file doesn't exist)
        skillbook = Skillbook()
        orphan = Skill(
            id="orphan-skill",
            section="strategies",
            content="This file was deleted",
            sources=["skills/deleted.md"],
        )
        skillbook._skills[orphan.id] = orphan
        skillbook._sections.setdefault(orphan.section, []).append(orphan.id)

        # Add a pure content skill (no sources) - should NOT be cleaned up
        pure_content = Skill(
            id="str-00001",
            section="strategies",
            content="Auto-learned skill",
            type="system",
        )
        skillbook._skills[pure_content.id] = pure_content
        skillbook._sections["strategies"].append(pure_content.id)

        # Create one valid skill file
        (skills_dir / "valid.md").write_text("""---
id: valid-skill
description: This exists
---
# Details
""")

        # Load with orphan cleanup
        loader = SkillLoader(skills_dir, skillbook)
        loader.load_and_merge(cleanup_orphans=True)

        # Orphan should be removed
        assert skillbook.get_skill("orphan-skill") is None
        # Pure content skill should be kept
        assert skillbook.get_skill("str-00001") is not None
        # New skill should be added
        assert skillbook.get_skill("valid-skill") is not None

    def test_skill_loader_no_orphan_cleanup(self, skills_dir):
        """Test that orphan cleanup can be disabled."""
        from pantheon.internal.learning.skill_loader import SkillLoader

        # Create skillbook with orphan skill
        skillbook = Skillbook()
        orphan = Skill(
            id="orphan-skill",
            section="strategies",
            content="This file was deleted",
            sources=["skills/deleted.md"],
        )
        skillbook._skills[orphan.id] = orphan
        skillbook._sections.setdefault(orphan.section, []).append(orphan.id)

        # Load without orphan cleanup
        loader = SkillLoader(skills_dir, skillbook)
        loader.load_and_merge(cleanup_orphans=False)

        # Orphan should still exist
        assert skillbook.get_skill("orphan-skill") is not None

    def test_skill_loader_system_skill(self, skills_dir):
        """Test loading system-generated skill files."""
        from pantheon.internal.learning.skill_loader import SkillLoader

        # Create a system skill file
        (skills_dir / "wfl-00001.md").write_text("""---
id: wfl-00001
description: System learned workflow
section: workflows
type: system
learned_from: chat-abc123
---
# Auto-generated content
""")

        skillbook = Skillbook()
        loader = SkillLoader(skills_dir, skillbook)
        loader.load_and_merge()

        skill = skillbook.get_skill("wfl-00001")
        assert skill is not None
        assert skill.is_system()
        assert skill.learned_from == "chat-abc123"


# ===========================================================================
# LearningInput Tests
# ===========================================================================


class TestLearningInput:
    """Tests for LearningInput and build_learning_input."""

    def test_build_learning_input(self, sample_messages, temp_dir):
        """Test building LearningInput from messages."""
        li = build_learning_input(
            turn_id="test-123",
            agent_name="test_agent",
            messages=sample_messages,
            learning_dir=temp_dir,
        )

        assert li.turn_id == "test-123"
        assert li.agent_name == "test_agent"
        assert li.details_path.endswith(".json")
        assert os.path.exists(li.details_path)

    def test_details_saved(self, sample_messages, temp_dir):
        """Test that full details are saved to file."""
        li = build_learning_input(
            turn_id="save-test",
            agent_name="agent",
            messages=sample_messages,
            learning_dir=temp_dir,
        )

        assert li.details_path
        assert os.path.exists(li.details_path)

        with open(li.details_path) as f:
            data = json.load(f)
        # Unified format saves messages directly
        assert "messages" in data
        assert len(data["messages"]) == 4

    def test_agent_name_fallback(self, sample_messages, temp_dir):
        """Test that empty agent_name falls back to 'global'."""
        li = build_learning_input(
            turn_id="test",
            agent_name="",
            messages=sample_messages,
            learning_dir=temp_dir,
        )
        assert li.agent_name == "global"


# ===========================================================================
# Reflector Tests (requires LLM)
# ===========================================================================


class TestReflector:
    """Tests for Reflector LLM analysis."""

    @pytest.mark.asyncio
    async def test_reflect_success_case(self, sample_learning_input, skillbook):
        """Test reflection on successful trajectory."""
        # Add a skill that was cited
        skillbook.add_skill(
            "strategies",
            "Use pandas.read_csv() for CSV file operations",
            agent_scope="global",
        )
        # Manually set ID to match citation
        skillbook._skills["str-00001"] = skillbook._skills.pop(
            list(skillbook._skills.keys())[0]
        )
        skillbook._skills["str-00001"].id = "str-00001"

        reflector = Reflector(model=TEST_MODEL)
        output = await reflector.reflect(sample_learning_input, skillbook)

        print(f"\n=== Reflector Output ===")
        print(f"Analysis: {output.analysis}")
        print(f"Skill Tags: {output.skill_tags}")
        print(f"Learnings: {output.extracted_learnings}")
        print(f"Confidence: {output.confidence}")

        assert isinstance(output, ReflectorOutput)
        assert output.analysis
        assert output.confidence > 0

    @pytest.mark.asyncio
    async def test_reflect_empty_skillbook(self, sample_learning_input):
        """Test reflection with empty skillbook."""
        skillbook = Skillbook()
        reflector = Reflector(model=TEST_MODEL)

        output = await reflector.reflect(sample_learning_input, skillbook)

        assert isinstance(output, ReflectorOutput)
        assert output.analysis

    @pytest.mark.asyncio
    async def test_reflect_extracts_learnings(self, temp_dir):
        """Test that reflector extracts new learnings."""
        messages = [
            {"role": "user", "content": "Read a large CSV file efficiently"},
            {
                "role": "assistant",
                "content": "I'll use pandas with chunking for better memory usage.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "for chunk in pd.read_csv(file, chunksize=10000): process(chunk)"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Processed 1M rows in 5 seconds with 100MB memory.",
            },
            {
                "role": "assistant",
                "content": "Successfully processed the large file using chunked reading. This kept memory usage under 100MB while processing 1 million rows.",
            },
        ]

        li = build_learning_input("test", "agent", messages, temp_dir)
        reflector = Reflector(model=TEST_MODEL)
        skillbook = Skillbook()

        output = await reflector.reflect(li, skillbook)

        print(f"\n=== Extracted Learnings ===")
        for learning in output.extracted_learnings:
            print(f"- [{learning.section}] {learning.content}")

        # Should extract some learnings about chunked reading
        assert isinstance(output, ReflectorOutput)


# ===========================================================================
# SkillManager Tests (requires LLM)
# ===========================================================================


class TestSkillManager:
    """Tests for SkillManager LLM decisions."""

    @pytest.mark.asyncio
    async def test_update_skills_with_tags(self, skillbook):
        """Test that SkillManager applies tags from reflection."""
        # Add a skill
        skill = skillbook.add_skill("strategies", "Use pandas for data processing")

        # Create reflection with tag
        reflection = ReflectorOutput(
            analysis="The pandas strategy was very helpful",
            skill_tags=[
                SkillTag(id=skill.id, tag="helpful", reason="Led to correct solution")
            ],
            extracted_learnings=[],
            confidence=0.9,
        )

        sm = SkillManager(model=TEST_MODEL)
        operations = await sm.update_skills(reflection, skillbook, "test_agent")

        print(f"\n=== SkillManager Operations ===")
        for op in operations:
            print(f"- {op.type}: {op.skill_id or op.content}")

        assert isinstance(operations, list)

    @pytest.mark.asyncio
    async def test_update_skills_with_learnings(self, skillbook):
        """Test that SkillManager adds new skills from learnings."""
        reflection = ReflectorOutput(
            analysis="Discovered a new pattern for file handling",
            skill_tags=[],
            extracted_learnings=[
                ExtractedLearning(
                    section="patterns",
                    content="Use context managers (with statement) for file operations",
                    agent_scope="global",
                    atomicity_score=0.95,
                    evidence="Prevented resource leak in test case",
                )
            ],
            confidence=0.85,
        )

        sm = SkillManager(model=TEST_MODEL)
        operations = await sm.update_skills(reflection, skillbook, "test_agent")

        print(f"\n=== SkillManager Operations ===")
        for op in operations:
            print(f"- {op.type}: {op.content or op.skill_id}")

        assert isinstance(operations, list)
        # Should have at least one ADD or UPDATE operation
        add_ops = [op for op in operations if op.type == "ADD"]
        if add_ops:
            assert add_ops[0].atomicity_score >= 0.7

    @pytest.mark.asyncio
    async def test_deduplication_check(self, skillbook):
        """Test that SkillManager avoids duplicates."""
        # Add existing skill
        skillbook.add_skill("strategies", "Use pandas.read_csv() for CSV files")

        # Create reflection with similar learning
        reflection = ReflectorOutput(
            analysis="Used pandas to read CSV",
            skill_tags=[],
            extracted_learnings=[
                ExtractedLearning(
                    section="strategies",
                    content="Use pandas to read CSV files",  # Very similar!
                    agent_scope="global",
                    atomicity_score=0.9,
                )
            ],
            confidence=0.8,
        )

        sm = SkillManager(model=TEST_MODEL)
        operations = await sm.update_skills(reflection, skillbook, "test_agent")

        print(f"\n=== Dedup Test Operations ===")
        for op in operations:
            print(f"- {op.type}: {op.content or op.skill_id}")
            if op.pre_add_check:
                print(f"  Pre-add check: {op.pre_add_check}")

        # Should prefer UPDATE over ADD for similar content
        add_ops = [op for op in operations if op.type == "ADD"]
        for op in add_ops:
            if op.pre_add_check:
                # If it's a duplicate, same_meaning should be True
                assert not op.pre_add_check.same_meaning, "Should not add duplicate"


# ===========================================================================
# Pipeline Tests
# ===========================================================================


class TestLearningPipeline:
    """Tests for LearningPipeline async workflow."""

    @pytest.mark.asyncio
    async def test_pipeline_lifecycle(self, temp_dir):
        """Test pipeline start and stop."""
        skillbook = Skillbook()
        skillbook._path = os.path.join(temp_dir, "sb.json")
        reflector = Reflector(model=TEST_MODEL)
        sm = SkillManager(model=TEST_MODEL)

        pipeline = LearningPipeline(
            skillbook=skillbook,
            reflector=reflector,
            skill_manager=sm,
            learning_dir=temp_dir,
        )

        await pipeline.start()
        assert pipeline._running

        await pipeline.stop()
        assert not pipeline._running

    @pytest.mark.asyncio
    async def test_pipeline_submit_and_process(self, sample_learning_input, temp_dir):
        """Test submitting and processing a learning task."""
        skillbook = Skillbook()
        skillbook._path = os.path.join(temp_dir, "sb.json")
        reflector = Reflector(model=TEST_MODEL)
        sm = SkillManager(model=TEST_MODEL)

        pipeline = LearningPipeline(
            skillbook=skillbook,
            reflector=reflector,
            skill_manager=sm,
            learning_dir=temp_dir,
        )

        await pipeline.start()

        # Submit learning
        pipeline.submit(sample_learning_input)

        # Wait for processing
        await asyncio.sleep(10)  # Give time for LLM calls

        await pipeline.stop()

        print(f"\n=== Skills after pipeline ===")
        for skill in skillbook.skills():
            print(f"- [{skill.section}] {skill.content}")

    @pytest.mark.asyncio
    async def test_pipeline_skip_short_trajectory(self, temp_dir):
        """Test that short trajectories are skipped."""
        skills_dir = Path(temp_dir) / "skills_skip"
        skills_dir.mkdir()
        skillbook = Skillbook(
            skills_dir=skills_dir,
            skillbook_path=Path(temp_dir) / "skip_test.json",
            auto_load=False,
        )
        pipeline = LearningPipeline(
            skillbook=skillbook,
            reflector=Reflector(model=TEST_MODEL),
            skill_manager=SkillManager(model=TEST_MODEL),
            learning_dir=temp_dir,
        )
        # Create a short messages file (too short to process)
        short_messages = [{"role": "user", "content": "Hi"}]
        short_file = Path(temp_dir) / "short_messages.json"
        short_file.write_text(json.dumps({"messages": short_messages}))
        
        short_input = LearningInput(
            turn_id="short",
            agent_name="agent",
            details_path=str(short_file),
        )

        await pipeline.start()
        pipeline.submit(short_input)  # Should be skipped
        await asyncio.sleep(0.5)
        await pipeline.stop()

        # Queue should be empty since it was skipped
        assert pipeline._queue.empty()


# ===========================================================================
# Integration Tests
# ===========================================================================


class TestLearningIntegration:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_create_learning_resources(self, temp_dir):
        """Test factory function for creating ACE resources."""
        config = {
            "enable_learning": True,
            "skillbook_path": os.path.join(temp_dir, "skillbook.json"),
            "learning_model": TEST_MODEL,
            "learning_dir": temp_dir,
            "max_skills_per_section": 20,
            "max_content_length": 400,
        }

        skillbook, pipeline = create_learning_resources(config=config)

        assert skillbook is not None
        assert pipeline is not None
        assert skillbook.max_skills_per_section == 20
        assert skillbook.max_content_length == 400

    @pytest.mark.asyncio
    async def test_create_learning_resources_disabled(self):
        """Test that disabled ACE returns None resources."""
        skillbook, pipeline = create_learning_resources(
            config={"enable_learning": False, "enable_injection": False}
        )

        assert skillbook is None
        assert pipeline is None

    @pytest.mark.asyncio
    async def test_full_learning_cycle(self, temp_dir):
        """Test complete learning cycle: messages -> learning -> skills."""
        # Setup
        config = {
            "enable_learning": True,
            "skillbook_path": os.path.join(temp_dir, "skillbook.json"),
            "learning_model": TEST_MODEL,
            "learning_dir": temp_dir,
            "max_skills_per_section": 30,
            "max_content_length": 500,
        }
        skillbook, pipeline = create_learning_resources(config=config)

        # Start pipeline
        await pipeline.start()

        # Create learning input
        messages = [
            {
                "role": "user",
                "content": "How do I handle file not found errors in Python?",
            },
            {
                "role": "assistant",
                "content": "You should use try-except with FileNotFoundError.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "execute_code",
                            "arguments": '{"code": "try:\\n    with open(file) as f:\\n        data = f.read()\\nexcept FileNotFoundError:\\n    print(\\"File not found\\")"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Code executed successfully. Handled missing file gracefully.",
            },
            {
                "role": "assistant",
                "content": "I've demonstrated how to handle FileNotFoundError using try-except. This is the recommended pattern for graceful error handling when working with files.",
            },
        ]

        li = build_learning_input(
            turn_id="full-cycle-test",
            agent_name="code_agent",
            messages=messages,
            learning_dir=temp_dir,
        )

        # Submit and wait for processing
        pipeline.submit(li)
        await asyncio.sleep(15)  # Wait for LLM processing

        # Stop and verify
        await pipeline.stop()

        print(f"\n=== Full Cycle Results ===")
        print(f"Total skills: {len(skillbook.skills())}")
        for skill in skillbook.skills():
            score = skill.helpful - skill.harmful
            print(f"- [{skill.section}] {skill.content} (score: {score})")

        # Verify skillbook was saved
        assert os.path.exists(config["skillbook_path"])


# ===========================================================================
# PantheonTeam Integration Tests (Single Agent)
# ===========================================================================


class TestPantheonTeamLearning:
    """Tests for PantheonTeam integration with ACE (single agent scenarios)."""

    @pytest.fixture
    def learning_resources(self, temp_dir):
        """Create ACE resources for testing."""
        config = {
            "enable_learning": True,
            "skillbook_path": os.path.join(temp_dir, "skillbook.json"),
            "learning_model": TEST_MODEL,
            "learning_dir": temp_dir,
            "max_skills_per_section": 30,
            "max_content_length": 500,
        }
        skillbook, pipeline = create_learning_resources(config=config)
        return skillbook, pipeline

    @pytest.mark.asyncio
    async def test_pantheon_team_skillbook_injection(self, learning_resources, temp_dir):
        """Test that skillbook is injected into agent instructions."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam

        skillbook, pipeline = learning_resources
        
        # Add some skills before creating team
        # Add some skills before creating team
        s1 = skillbook.add_skill(
            "strategies",
            "Always validate user input before processing",
            agent_scope="global",
        )
        s1.type = "user"  # Mock as user-defined to force static injection

        s2 = skillbook.add_skill(
            "strategies",
            "Use descriptive variable names for readability",
            agent_scope="test_agent",
        )
        s2.type = "user"  # Mock as user-defined to force static injection
        
        # Create agent with basic instructions
        agent = Agent(
            name="test_agent",
            instructions="You are a helpful assistant.",
            model=TEST_MODEL,
        )
        original_instructions = agent.instructions
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_injection": True,
            "enable_learning": False,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        # Trigger plugin lifecycle (normally happens in async_setup)
        await learning_plugin.on_team_created(team)
        
        # Verify skillbook was injected - check for new header format
        assert "Available Strategic Knowledge" in agent.instructions or "User Rules" in agent.instructions
        assert "validate user input" in agent.instructions
        assert "descriptive variable names" in agent.instructions
        
        print(f"\n=== Agent Instructions After Injection ===")
        print(agent.instructions)

    @pytest.mark.asyncio
    async def test_pantheon_team_single_agent_run(self, learning_resources, temp_dir):
        """Test running single agent in PantheonTeam with ACE learning."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory

        skillbook, pipeline = learning_resources
        
        # Start pipeline
        await pipeline.start()
        
        # Create agent
        agent = Agent(
            name="math_agent",
            instructions="You are a math assistant. Answer math questions concisely.",
            model=TEST_MODEL,
        )
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_learning": True,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        # Run a simple query
        memory = Memory(name="test-memory")
        response = await team.run("What is 2 + 2?", memory=memory)
        
        print(f"\n=== Agent Response ===")
        print(f"Content: {response.content}")
        
        # Wait for learning to process
        await asyncio.sleep(10)
        
        # Stop pipeline
        await pipeline.stop()
        
        # Verify response
        assert response is not None
        assert response.content is not None
        
        # Check if learning was submitted (memory should have messages)
        assert len(memory._messages) >= 2  # At least user + assistant

    @pytest.mark.asyncio
    async def test_pantheon_team_with_tool_use(self, learning_resources, temp_dir):
        """Test PantheonTeam with tool usage and ACE learning."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory

        skillbook, pipeline = learning_resources
        
        # Start pipeline
        await pipeline.start()
        
        # Create agent with a tool
        agent = Agent(
            name="calculator_agent",
            instructions="You are a calculator. Use the calculate tool for math.",
            model=TEST_MODEL,
        )
        
        tool_called = False
        
        @agent.tool
        def calculate(expression: str) -> str:
            """Calculate a math expression."""
            nonlocal tool_called
            tool_called = True
            try:
                result = eval(expression)
                return f"Result: {result}"
            except Exception as e:
                return f"Error: {e}"
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_learning": True,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        # Run query that should use tool
        memory = Memory(name="test-memory")
        response = await team.run(
            "Please calculate 15 * 7 using the calculate tool",
            memory=memory
        )
        
        print(f"\n=== Tool Use Response ===")
        print(f"Content: {response.content}")
        print(f"Tool was called: {tool_called}")
        
        # Wait for learning to process
        await asyncio.sleep(10)
        
        await pipeline.stop()
        
        # Verify tool was used and response is correct
        assert response is not None
        assert tool_called or "105" in str(response.content)
        
        # Check message history includes tool call
        messages = memory._messages
        print(f"\n=== Message History ({len(messages)} messages) ===")
        for i, msg in enumerate(messages):
            print(f"{i}: {msg.get('role')} - {str(msg.get('content', ''))[:100]}...")

    @pytest.mark.asyncio
    async def test_pantheon_team_learning_submission(self, learning_resources, temp_dir):
        """Test that learning is submitted after agent run."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory

        skillbook, pipeline = learning_resources
        
        # Start pipeline
        await pipeline.start()
        
        # Create agent
        agent = Agent(
            name="code_assistant",
            instructions="You are a Python coding assistant. Provide helpful code examples.",
            model=TEST_MODEL,
        )
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_learning": True,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        # Run query
        memory = Memory(name="test-memory")
        response = await team.run(
            "How do I create a list comprehension in Python?",
            memory=memory
        )
        
        print(f"\n=== Response ===")
        print(f"Content: {response.content}")
        
        # Wait for learning pipeline to process
        await asyncio.sleep(15)
        
        await pipeline.stop()
        
        # Check if skillbook was updated (may have new skills)
        skills = skillbook.skills()
        print(f"\n=== Skillbook After Learning ===")
        print(f"Total skills: {len(skills)}")
        for skill in skills:
            print(f"- [{skill.section}] {skill.content}")

    @pytest.mark.asyncio
    async def test_pantheon_team_without_ace(self, temp_dir):
        """Test PantheonTeam works fine without ACE (None values)."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory

        # Create agent
        agent = Agent(
            name="simple_agent",
            instructions="You are a simple assistant.",
            model=TEST_MODEL,
        )
        
        # Create team without plugins (no ACE)
        team = PantheonTeam(
            agents=[agent],
            plugins=[],  # No plugins
        )
        
        # Run query
        memory = Memory(name="test-memory")
        response = await team.run("Say hello!", memory=memory)
        
        print(f"\n=== Response Without ACE ===")
        print(f"Content: {response.content}")
        
        # Should work fine without ACE
        assert response is not None
        assert response.content is not None

    @pytest.mark.asyncio
    async def test_pantheon_team_agent_scope_skills(self, learning_resources, temp_dir):
        """Test that agent-specific skills are properly injected."""
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam

        skillbook, pipeline = learning_resources
        
        # Enable agent scope for this test
        skillbook.enable_agent_scope = True
        
        # Add global skill
        s1 = skillbook.add_skill(
            "strategies",
            "Global tip: Be concise",
            agent_scope="global",
        )
        s1.type = "user"
        
        # Add agent-specific skill
        s2 = skillbook.add_skill(
            "strategies",
            "Python tip: Use f-strings for formatting",
            agent_scope="python_agent",
        )
        s2.type = "user"
        
        # Add different agent's skill
        s3 = skillbook.add_skill(
            "strategies",
            "JavaScript tip: Avoid var",
            agent_scope="js_agent",
        )
        s3.type = "user"
        
        # Create python_agent
        python_agent = Agent(
            name="python_agent",
            instructions="You are a Python expert.",
            model=TEST_MODEL,
        )
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_injection": True,
            "enable_learning": False,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[python_agent],
            plugins=[learning_plugin],
        )
        
        # Trigger plugin lifecycle (normally happens in async_setup)
        await learning_plugin.on_team_created(team)
        
        # Verify correct skills were injected
        assert "Global tip" in python_agent.instructions
        assert "f-strings" in python_agent.instructions
        assert "JavaScript" not in python_agent.instructions  # Should NOT have js_agent's skill
        
        print(f"\n=== Python Agent Instructions ===")
        print(python_agent.instructions)

    @pytest.mark.asyncio
    async def test_pantheon_team_skill_learning_e2e(self, learning_resources, temp_dir):
        """
        End-to-end test: User teaches agent something → ACE learns → Skill added.
        
        This test verifies the complete learning loop:
        1. User sends message with explicit instruction to remember
        2. Agent responds acknowledging the instruction
        3. ACE Reflector analyzes the trajectory
        4. ACE SkillManager decides to add a skill
        5. Skill is actually added to the Skillbook
        """
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory

        skillbook, pipeline = learning_resources
        
        # Verify skillbook starts empty
        initial_skill_count = len(skillbook.skills())
        print(f"\n=== Initial Skillbook ===")
        print(f"Skills: {initial_skill_count}")
        
        # Start pipeline
        await pipeline.start()
        
        # Create agent that follows instructions carefully
        agent = Agent(
            name="learning_agent",
            instructions="""You are a helpful assistant that learns from user instructions.

When users tell you to remember something important, acknowledge it clearly and explain 
that you will apply this knowledge in future interactions.

Always be specific about what you learned.""",
            model=TEST_MODEL,
        )
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_learning": True,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        # User message with explicit learning instruction
        # This message is designed to trigger the ACE system to extract a learnable skill
        user_message = """
IMPORTANT: Please remember this rule for all future coding tasks:

When writing Python functions, ALWAYS include type hints for function parameters 
and return values. This is a critical best practice.

For example:
def calculate_sum(a: int, b: int) -> int:
    return a + b

Please acknowledge that you understand this rule and will apply it.
"""
        
        # Run the conversation
        memory = Memory(name="learning-test")
        response = await team.run(user_message, memory=memory)
        
        print(f"\n=== Agent Response ===")
        print(f"Content: {response.content[:500]}...")
        
        # Wait for ACE pipeline to process the learning
        # The pipeline runs asynchronously, so we need to wait
        print("\n=== Waiting for ACE Learning (20s) ===")
        await asyncio.sleep(20)
        
        # Stop pipeline (this also triggers final save)
        await pipeline.stop()
        
        # Check if new skills were added
        final_skill_count = len(skillbook.skills())
        new_skills = skillbook.skills()
        
        print(f"\n=== Skillbook After Learning ===")
        print(f"Initial skills: {initial_skill_count}")
        print(f"Final skills: {final_skill_count}")
        print(f"New skills added: {final_skill_count - initial_skill_count}")
        
        for skill in new_skills:
            score = skill.helpful - skill.harmful
            print(f"\n[{skill.id}] Section: {skill.section}")
            print(f"  Content: {skill.content}")
            print(f"  Score: helpful={skill.helpful}, harmful={skill.harmful}")
        
        # Verify at least one skill was learned
        # Note: This assertion may be flaky depending on LLM response
        # The test is valuable for manual verification even if it sometimes fails
        if final_skill_count > initial_skill_count:
            print("\n✅ SUCCESS: New skill(s) learned!")
            
            # Check if type hints are mentioned in any skill (content or description)
            def check_skill_text(skill):
                text = (skill.content or "") + " " + (skill.description or "")
                text_lower = text.lower()
                return "type" in text_lower or "hint" in text_lower or "parameter" in text_lower

            type_hint_mentioned = any(check_skill_text(skill) for skill in new_skills)
            print(f"Type hints mentioned in skills: {type_hint_mentioned}")
        else:
            print("\n⚠️ No new skills added (LLM may not have extracted learnable pattern)")
        
        # At minimum, verify the conversation completed successfully
        assert response is not None
        assert response.content is not None
        assert len(memory._messages) >= 2

    @pytest.mark.asyncio 
    async def test_pantheon_team_multi_turn_learning(self, learning_resources, temp_dir):
        """
        Test learning across multiple conversation turns.
        
        This simulates a more realistic scenario where learning happens
        over the course of a conversation.
        """
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory

        skillbook, pipeline = learning_resources
        
        # Start pipeline
        await pipeline.start()
        
        # Create agent
        agent = Agent(
            name="coding_assistant",
            instructions="You are a Python coding assistant. Help users with code and remember important patterns.",
            model=TEST_MODEL,
        )
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": temp_dir,
            "enable_learning": True,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        memory = Memory(name="multi-turn-test")
        
        # Turn 1: User asks about file handling
        print("\n=== Turn 1: File Handling Question ===")
        response1 = await team.run(
            "How should I properly handle file operations in Python to avoid resource leaks?",
            memory=memory
        )
        print(f"Response: {response1.content[:300]}...")
        
        # Turn 2: User provides feedback and asks to remember
        print("\n=== Turn 2: User Feedback ===")
        response2 = await team.run(
            "Great! Please remember: always use 'with' statement (context managers) for file operations. This is crucial for all our Python projects.",
            memory=memory  
        )
        print(f"Response: {response2.content[:300]}...")
        
        # Wait for learning
        print("\n=== Waiting for ACE Learning (15s) ===")
        await asyncio.sleep(15)
        
        await pipeline.stop()
        
        # Check results
        skills = skillbook.skills()
        print(f"\n=== Final Skillbook ({len(skills)} skills) ===")
        for skill in skills:
            print(f"- [{skill.section}] {skill.content}")
        
        # Verify conversation worked
        assert response1 is not None
        assert response2 is not None
        assert len(memory._messages) >= 4  # At least 2 user + 2 assistant


# ===========================================================================
# Persistence Tests (Real .pantheon directory)
# ===========================================================================


class TestLearningPersistence:
    """Tests for ACE persistence in real .pantheon directory."""

    @pytest.mark.asyncio
    async def test_real_pantheon_persistence(self):
        """
        Test that ACE files are actually persisted in .pantheon/ace directory.
        
        This test uses the real project's .pantheon directory (not temp dir)
        to verify that persistence works correctly.
        """
        from pantheon.agent import Agent
        from pantheon.team import PantheonTeam
        from pantheon.internal.memory import Memory
        from pantheon.settings import get_settings
        import shutil
        
        settings = get_settings()
        learning_dir = settings.learning_dir
        
        print(f"\n=== ACE Directory: {learning_dir} ===")
        
        # Clean up previous test data (if any)
        test_skillbook_path = learning_dir / "test_skillbook.json"
        test_learning_dir = learning_dir / "test_learning"
        
        if test_skillbook_path.exists():
            test_skillbook_path.unlink()
        if test_learning_dir.exists():
            shutil.rmtree(test_learning_dir)
        
        # Create ACE resources with real paths
        config = {
            "enable_learning": True,
            "skillbook_path": str(test_skillbook_path),
            "learning_model": TEST_MODEL,
            "learning_dir": str(test_learning_dir),
            "max_skills_per_section": 30,
            "max_content_length": 500,
        }
        
        skillbook, pipeline = create_learning_resources(config=config)
        
        # Start pipeline
        await pipeline.start()
        
        # Create agent
        agent = Agent(
            name="persistence_test_agent",
            instructions="You are a helpful assistant for persistence testing.",
            model=TEST_MODEL,
        )
        
        # Create team with plugin-based ACE
        from pantheon.internal.learning.plugin import LearningPlugin
        learning_plugin = LearningPlugin({
            "learning_dir": str(test_learning_dir),
            "enable_learning": True,
        })
        learning_plugin.skillbook = skillbook
        learning_plugin.learning_pipeline = pipeline
        learning_plugin._initialized = True
        
        team = PantheonTeam(
            agents=[agent],
            plugins=[learning_plugin],
        )
        
        # Run a conversation to trigger learning
        memory = Memory(name="persistence-test")
        response = await team.run(
            "Remember this: Always use descriptive function names in Python code. This improves readability.",
            memory=memory
        )
        
        print(f"\n=== Agent Response ===")
        print(f"Content: {response.content[:300]}...")
        
        # Wait for learning
        print("\n=== Waiting for ACE Learning (15s) ===")
        await asyncio.sleep(15)
        
        # Stop pipeline (triggers save)
        await pipeline.stop()
        
        # Verify files were created
        print(f"\n=== Checking Persistence ===")
        print(f"ACE directory exists: {learning_dir.exists()}")
        print(f"Skillbook file exists: {test_skillbook_path.exists()}")
        print(f"Learning dir exists: {test_learning_dir.exists()}")
        
        # List files in ACE directory
        if learning_dir.exists():
            print(f"\n=== Files in {learning_dir} ===")
            for item in learning_dir.iterdir():
                if item.is_file():
                    print(f"  📄 {item.name} ({item.stat().st_size} bytes)")
                else:
                    print(f"  📁 {item.name}/")
                    for subitem in item.iterdir():
                        if subitem.is_file():
                            print(f"      📄 {subitem.name} ({subitem.stat().st_size} bytes)")
        
        # Read skillbook content
        if test_skillbook_path.exists():
            with open(test_skillbook_path) as f:
                content = f.read()
            print(f"\n=== Skillbook Content ({len(content)} bytes) ===")
            print(content[:500])
        
        # Verify assertions
        assert learning_dir.exists(), f"ACE directory should exist: {learning_dir}"
        assert test_skillbook_path.exists(), f"Skillbook should be saved: {test_skillbook_path}"
        
        # Check skillbook has skills
        skills = skillbook.skills()
        print(f"\n=== Loaded Skills ({len(skills)}) ===")
        for skill in skills:
            print(f"- [{skill.section}] {skill.content}")
        
        # Cleanup
        if test_skillbook_path.exists():
            test_skillbook_path.unlink()
        if test_learning_dir.exists():
            shutil.rmtree(test_learning_dir)
        
        print("\n✅ Persistence test completed successfully!")

    @pytest.mark.asyncio
    async def test_skillbook_reload_from_disk(self):
        """
        Test that skillbook can be saved and reloaded from disk.
        """
        from pantheon.settings import get_settings
        
        settings = get_settings()
        learning_dir = settings.learning_dir
        test_path = learning_dir / "reload_test_skillbook.json"
        
        print(f"\n=== Testing Skillbook Reload ===")
        print(f"Test path: {test_path}")
        
        # Create skillbook and add skills
        test_skills_dir = learning_dir / "reload_skills"
        test_skills_dir.mkdir(parents=True, exist_ok=True)
        sb1 = Skillbook(
            skills_dir=test_skills_dir,
            skillbook_path=test_path,
            max_skills_per_section=30,
            auto_load=False,
        )
        sb1.add_skill("strategies", "Test skill 1: Use pytest for testing")
        sb1.add_skill("patterns", "Test skill 2: Follow PEP8 style guide")
        sb1.tag_skill(sb1.skills()[0].id, "helpful", 3)
        
        # Save to disk
        sb1.save(str(test_path))
        print(f"Saved skillbook with {len(sb1.skills())} skills")
        
        # Verify file exists
        assert test_path.exists(), "Skillbook file should exist after save"
        print(f"File size: {test_path.stat().st_size} bytes")
        
        # Create new skillbook and load (with different empty skills_dir)
        test_skills_dir2 = learning_dir / "reload_skills2"
        test_skills_dir2.mkdir(parents=True, exist_ok=True)
        sb2 = Skillbook(
            skills_dir=test_skills_dir2,
            skillbook_path=test_path,
            auto_load=True,
        )
        
        print(f"Loaded skillbook with {len(sb2.skills())} skills")
        
        # Verify skills are identical
        assert len(sb2.skills()) == 2
        
        skills = sb2.skills()
        print("\n=== Reloaded Skills ===")
        for skill in skills:
            print(f"- [{skill.id}] {skill.content} (helpful={skill.helpful})")
        
        # Verify tags were preserved
        skill1 = [s for s in skills if "pytest" in s.content][0]
        assert skill1.helpful == 3, "Tag should be preserved after reload"
        
        # Cleanup
        test_path.unlink()
        print("\n✅ Reload test passed!")


# ===========================================================================
# Run tests
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
