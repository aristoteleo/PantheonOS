"""Tests for SkillExtractor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.learning_system.extractor import SkillExtractor

from .conftest import SAMPLE_SKILL_CONTENT


class TestMaybeExtract:
    @pytest.mark.asyncio
    async def test_skips_below_threshold(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini", nudge_interval=5)
        extractor._run_counter["s1"] = 3
        result = await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_empty_messages(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini")
        extractor._run_counter["s1"] = 10
        result = await extractor.maybe_extract("s1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_in_progress(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini")
        extractor._run_counter["s1"] = 10
        lock = extractor._get_lock("s1")
        await lock.acquire()
        try:
            result = await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])
            assert result is None
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_skips_agent_writes(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini")
        extractor._run_counter["s1"] = 10
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "skill_create"}}
            ]}
        ]
        result = await extractor.maybe_extract("s1", messages)
        assert result is None

    @pytest.mark.asyncio
    async def test_extracts_skills(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini", nudge_interval=1)
        extractor._run_counter["s1"] = 5
        messages = [
            {"role": "user", "content": "Deploy the app to Fly.io"},
            {"role": "assistant", "content": "Done! Here's what I did..."},
        ]

        # Mock agent that simulates writing a skill via file_manager
        async def fake_run(*args, **kwargs):
            store.create_skill("deploy-flyio", "---\nname: deploy-flyio\ndescription: Deploy to Fly.io\n---\n\n# Deploy\n\n1. Run flyctl deploy\n")
            return MagicMock(content="Created skill deploy-flyio.")

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=fake_run)

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await extractor.maybe_extract("s1", messages)

        assert result is not None
        assert "deploy-flyio" in result

        entry = store.load_skill("deploy-flyio")
        assert entry is not None

    @pytest.mark.asyncio
    async def test_returns_only_changed_skills(self, store):
        """Verify _extract returns only new/updated skills, not all."""
        # Pre-create an existing skill
        store.create_skill("existing", "---\nname: existing\ndescription: Old skill\n---\n\nOld content\n")

        extractor = SkillExtractor(store, model="gpt-4o-mini", nudge_interval=1)
        extractor._run_counter["s1"] = 5
        messages = [
            {"role": "user", "content": "Deploy the app"},
            {"role": "assistant", "content": "Done!"},
        ]

        async def fake_run(*args, **kwargs):
            store.create_skill("new-skill", "---\nname: new-skill\ndescription: New\n---\n\nNew content\n")
            return MagicMock(content="Created new-skill.")

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=fake_run)

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await extractor.maybe_extract("s1", messages)

        assert result is not None
        assert "new-skill" in result
        assert "existing" not in result  # Should NOT include pre-existing unchanged skill

    @pytest.mark.asyncio
    async def test_resets_counter(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini", nudge_interval=1)
        extractor._run_counter["s1"] = 5

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(content="Nothing to extract."))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])

        assert extractor._run_counter["s1"] == 0

    @pytest.mark.asyncio
    async def test_handles_llm_error(self, store):
        extractor = SkillExtractor(store, model="gpt-4o-mini", nudge_interval=1)
        extractor._run_counter["s1"] = 5

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(side_effect=Exception("API err"))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            result = await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])
        assert result is None


class TestIncrementAndReset:
    def test_increment_default_by_one(self, store):
        extractor = SkillExtractor(store, model="test")
        extractor.increment_run("s1")
        extractor.increment_run("s1")
        assert extractor._run_counter["s1"] == 2

    def test_increment_by_n(self, store):
        """increment_run(by=N) accumulates N — used for tool-call count."""
        extractor = SkillExtractor(store, model="test")
        extractor.increment_run("s1", by=7)
        extractor.increment_run("s1", by=3)
        assert extractor._run_counter["s1"] == 10

    def test_reset(self, store):
        extractor = SkillExtractor(store, model="test")
        extractor._run_counter["s1"] = 5
        extractor.reset_counter("s1")
        assert extractor._run_counter["s1"] == 0


class TestPending:
    @pytest.mark.asyncio
    async def test_sets_pending_when_locked(self, store):
        """When lock is held, maybe_extract sets _pending instead of silently dropping."""
        extractor = SkillExtractor(store, model="test", nudge_interval=1)
        extractor._run_counter["s1"] = 5
        lock = extractor._get_lock("s1")
        await lock.acquire()
        try:
            await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])
            assert extractor._pending.get("s1") is True
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_clears_pending_on_extraction_start(self, store):
        """When extraction starts, _pending is cleared."""
        extractor = SkillExtractor(store, model="test", nudge_interval=1)
        extractor._run_counter["s1"] = 5
        extractor._pending["s1"] = True

        mock_agent = AsyncMock()
        mock_agent.run = AsyncMock(return_value=MagicMock(content="Nothing."))

        with patch("pantheon.internal.background_agent.create_background_agent", new_callable=AsyncMock, return_value=mock_agent):
            await extractor.maybe_extract("s1", [{"role": "user", "content": "hi"}])

        assert extractor._pending.get("s1") is False


class TestMutualExclusion:
    def test_detects_skill_create(self, store):
        extractor = SkillExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "skill_manage", "arguments": '{"action": "create", "name": "test"}'}}
            ]}
        ]
        assert extractor._has_agent_skill_writes(messages) is True

    def test_detects_skill_update(self, store):
        extractor = SkillExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "skill_manage", "arguments": '{"action": "update", "name": "test"}'}}
            ]}
        ]
        assert extractor._has_agent_skill_writes(messages) is True

    def test_detects_skill_patch(self, store):
        extractor = SkillExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "skill_manage", "arguments": '{"action": "patch", "name": "test"}'}}
            ]}
        ]
        assert extractor._has_agent_skill_writes(messages) is True

    def test_ignores_skill_delete(self, store):
        extractor = SkillExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "skill_manage", "arguments": '{"action": "delete", "name": "test"}'}}
            ]}
        ]
        assert extractor._has_agent_skill_writes(messages) is False

    def test_no_skill_writes(self, store):
        extractor = SkillExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "memory_write"}}
            ]}
        ]
        assert extractor._has_agent_skill_writes(messages) is False

    def test_no_false_positive_on_name_containing_action(self, store):
        """Skill named 'patch-test' with delete action should NOT be detected as write."""
        extractor = SkillExtractor(store, model="test")
        messages = [
            {"role": "assistant", "content": "done", "tool_calls": [
                {"function": {"name": "skill_manage", "arguments": '{"action": "delete", "name": "patch-test"}'}}
            ]}
        ]
        assert extractor._has_agent_skill_writes(messages) is False
