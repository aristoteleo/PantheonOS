"""Tests for LearningRuntime."""

import pytest

from pantheon.internal.learning_system.runtime import LearningRuntime

from .conftest import MINIMAL_SKILL


class TestInitialization:
    def test_init(self, runtime_config, tmp_pantheon_dir):
        rt = LearningRuntime(runtime_config)
        assert not rt.is_initialized
        rt.initialize(tmp_pantheon_dir)
        assert rt.is_initialized
        assert rt.store is not None
        assert rt.injector is not None
        assert rt.extractor is not None

    def test_init_without_extract(self, tmp_pantheon_dir):
        config = {"enabled": True, "model": "gpt-4o-mini", "extract_enabled": False}
        rt = LearningRuntime(config)
        rt.initialize(tmp_pantheon_dir)
        assert rt.extractor is None


class TestBuildGuidance:
    def test_empty(self, runtime_config, tmp_pantheon_dir):
        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)
        assert rt.build_skill_guidance() == ""

    def test_with_skills(self, runtime_config, tmp_pantheon_dir):
        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)
        rt.store.create_skill("test", MINIMAL_SKILL.replace("minimal", "test"))
        guidance = rt.build_skill_guidance()
        assert "test" in guidance


class TestMaybeExtractSkillsCounter:
    @pytest.mark.asyncio
    async def test_increments_by_tool_call_count(self, runtime_config, tmp_pantheon_dir):
        """Counter increments by number of tool calls, not just 1 per run."""
        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)

        messages = [
            {"role": "user", "content": "do stuff"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"function": {"name": "read"}},
                {"function": {"name": "write"}},
                {"function": {"name": "search"}},
            ]},
        ]
        await rt.maybe_extract_skills("s1", messages)
        assert rt.extractor._run_counter["s1"] == 3

    @pytest.mark.asyncio
    async def test_increments_by_one_when_no_tool_calls(self, runtime_config, tmp_pantheon_dir):
        """Minimum increment is 1 even when no tool calls."""
        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)

        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        await rt.maybe_extract_skills("s1", messages)
        assert rt.extractor._run_counter["s1"] == 1


class TestOnSkillToolUsed:
    def test_invalidates_cache(self, runtime_config, tmp_pantheon_dir):
        """Test that on_skill_tool_used invalidates injector cache."""
        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)
        # Create a skill and build guidance (populates cache)
        rt.store.create_skill("test", MINIMAL_SKILL.replace("minimal", "test"))
        guidance1 = rt.build_skill_guidance()
        assert "test" in guidance1
        # Call on_skill_tool_used (should invalidate cache)
        rt.on_skill_tool_used("s1")
        # Create another skill
        rt.store.create_skill("test2", MINIMAL_SKILL.replace("minimal", "test2"))
        # Build guidance again (should pick up new skill)
        guidance2 = rt.build_skill_guidance()
        assert "test2" in guidance2
