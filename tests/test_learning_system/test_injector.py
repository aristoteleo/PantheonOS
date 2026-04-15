"""Tests for SkillInjector."""

import pytest

from pantheon.internal.learning_system.injector import SkillInjector

from .conftest import SAMPLE_SKILL_CONTENT, MINIMAL_SKILL


class TestBuildSkillIndex:
    def test_empty_store(self, store):
        injector = SkillInjector(store)
        assert injector.build_skill_index() == ""

    def test_with_skills(self, store_with_skill):
        injector = SkillInjector(store_with_skill)
        index = injector.build_skill_index()
        assert "test-skill" in index
        assert "A test skill" in index

    def test_disabled_skills(self, store_with_skill):
        injector = SkillInjector(store_with_skill, disabled_skills=["test-skill"])
        assert injector.build_skill_index() == ""

    def test_agent_scope_filter(self, store):
        content = "---\nname: scoped\ndescription: Scoped skill\nagent_scope: [researcher]\n---\n\nBody."
        store.create_skill("scoped", content)
        injector = SkillInjector(store)

        # Should show for researcher
        index = injector.build_skill_index(agent_name="researcher")
        assert "scoped" in index

        # Should not show for leader
        index = injector.build_skill_index(agent_name="leader")
        assert "scoped" not in index

    def test_caching(self, store_with_skill):
        injector = SkillInjector(store_with_skill)
        index1 = injector.build_skill_index()
        index2 = injector.build_skill_index()
        assert index1 == index2

    def test_invalidate_cache(self, store_with_skill):
        injector = SkillInjector(store_with_skill)
        injector.build_skill_index()
        assert injector._cache is not None
        injector.invalidate_cache()
        assert injector._cache is None


class TestRuntimeIntegration:
    def test_build_guidance(self, store_with_skill, runtime_config, tmp_pantheon_dir):
        from pantheon.internal.learning_system.runtime import LearningRuntime

        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)

        # Create skill in the runtime's store
        rt.store.create_skill("demo", MINIMAL_SKILL.replace("minimal", "demo"))

        guidance = rt.build_skill_guidance()
        assert "demo" in guidance
        assert "## Skills" in guidance
        assert "skill_manage" in guidance

    def test_no_guidance_when_empty(self, runtime_config, tmp_pantheon_dir):
        from pantheon.internal.learning_system.runtime import LearningRuntime

        rt = LearningRuntime(runtime_config)
        rt.initialize(tmp_pantheon_dir)
        assert rt.build_skill_guidance() == ""
