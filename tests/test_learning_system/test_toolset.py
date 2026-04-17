"""Tests for SkillToolSet (3 tools: list, view, manage)."""

import json
import pytest

from pantheon.internal.learning_system.runtime import LearningRuntime
from pantheon.internal.learning_system.toolset import SkillToolSet

from .conftest import SAMPLE_SKILL_CONTENT, MINIMAL_SKILL


@pytest.fixture
def toolset(runtime_config, tmp_pantheon_dir):
    rt = LearningRuntime(runtime_config)
    rt.initialize(tmp_pantheon_dir)
    return SkillToolSet(rt)


class TestSkillList:
    @pytest.mark.asyncio
    async def test_empty(self, toolset):
        result = json.loads(await toolset.skill_list())
        assert result["success"]
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_with_skills(self, toolset):
        toolset._runtime.store.create_skill("demo", MINIMAL_SKILL.replace("minimal", "demo"))
        result = json.loads(await toolset.skill_list())
        assert result["count"] == 1
        assert result["skills"][0]["name"] == "demo"


class TestSkillView:
    @pytest.mark.asyncio
    async def test_view_skill(self, toolset):
        toolset._runtime.store.create_skill("test-skill", SAMPLE_SKILL_CONTENT)
        result = json.loads(await toolset.skill_view("test-skill"))
        assert result["success"]
        assert result["name"] == "test-skill"
        assert "When running unit tests" in result["content"]

    @pytest.mark.asyncio
    async def test_view_not_found(self, toolset):
        result = json.loads(await toolset.skill_view("nonexistent"))
        assert not result["success"]
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_view_supporting_file(self, toolset):
        toolset._runtime.store.create_skill("test-skill", SAMPLE_SKILL_CONTENT)
        toolset._runtime.store.write_supporting_file("test-skill", "references/api.md", "API docs")
        result = json.loads(await toolset.skill_view("test-skill", "references/api.md"))
        assert result["success"]
        assert result["content"] == "API docs"


class TestSkillManageCreate:
    @pytest.mark.asyncio
    async def test_create(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="create",
            name="my-skill",
            content=MINIMAL_SKILL.replace("minimal", "my-skill"),
        ))
        assert result["success"]
        assert result["name"] == "my-skill"

    @pytest.mark.asyncio
    async def test_create_invalid(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="create", name="INVALID", content="bad"
        ))
        assert not result["success"]

    @pytest.mark.asyncio
    async def test_create_no_content(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="create", name="test"
        ))
        assert not result["success"]
        assert "content" in result["error"].lower()


class TestSkillManageUpdate:
    @pytest.mark.asyncio
    async def test_update(self, toolset):
        toolset._runtime.store.create_skill("test-skill", SAMPLE_SKILL_CONTENT)
        new_content = MINIMAL_SKILL.replace("minimal", "test-skill").replace("Minimal skill", "Rewritten")
        result = json.loads(await toolset.skill_manage(
            action="update", name="test-skill", content=new_content
        ))
        assert result["success"]

    @pytest.mark.asyncio
    async def test_update_no_content(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="update", name="test"
        ))
        assert not result["success"]


class TestSkillManagePatch:
    @pytest.mark.asyncio
    async def test_patch(self, toolset):
        toolset._runtime.store.create_skill("test-skill", SAMPLE_SKILL_CONTENT)
        result = json.loads(await toolset.skill_manage(
            action="patch",
            name="test-skill",
            old_string="When running unit tests.",
            new_string="Always.",
        ))
        assert result["success"]
        entry = toolset._runtime.store.load_skill("test-skill")
        assert "Always." in entry.content

    @pytest.mark.asyncio
    async def test_patch_missing_args(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="patch", name="test", old_string="foo"
        ))
        assert not result["success"]


class TestSkillManageDelete:
    @pytest.mark.asyncio
    async def test_delete(self, toolset):
        toolset._runtime.store.create_skill("test-skill", SAMPLE_SKILL_CONTENT)
        result = json.loads(await toolset.skill_manage(
            action="delete", name="test-skill"
        ))
        assert result["success"]

    @pytest.mark.asyncio
    async def test_delete_not_found(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="delete", name="nonexistent"
        ))
        assert not result["success"]


class TestSkillManageUnknownAction:
    @pytest.mark.asyncio
    async def test_unknown(self, toolset):
        result = json.loads(await toolset.skill_manage(
            action="fly", name="test"
        ))
        assert not result["success"]
        assert "Unknown action" in result["error"]
