"""Tests for LearningPlugin."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pantheon.internal.learning_system.plugin import LearningPlugin
from pantheon.internal.learning_system.runtime import LearningRuntime


@pytest.fixture
def runtime(runtime_config, tmp_pantheon_dir):
    rt = LearningRuntime(runtime_config)
    rt.initialize(tmp_pantheon_dir)
    return rt


class TestOnRunEnd:
    @pytest.mark.asyncio
    async def test_skips_sub_agent(self, runtime):
        plugin = LearningPlugin(runtime)
        result = {"question": "do task", "messages": [{"role": "user", "content": "x"}], "chat_id": "s1"}
        await plugin.on_run_end(MagicMock(), result)
        # No tasks created for sub-agent runs
        assert len(plugin._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_skips_empty_messages(self, runtime):
        plugin = LearningPlugin(runtime)
        result = {"messages": [], "chat_id": "s1"}
        await plugin.on_run_end(MagicMock(), result)
        assert len(plugin._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_background_task_tracked(self, runtime):
        """Task is kept in _background_tasks to prevent GC, cleaned up on completion."""
        plugin = LearningPlugin(runtime)
        result = {
            "messages": [{"role": "user", "content": "hi"}],
            "chat_id": "s1",
            "memory": MagicMock(_messages=[{"role": "user", "content": "hi"}]),
        }
        await plugin.on_run_end(MagicMock(), result)
        assert len(plugin._background_tasks) >= 1
        await asyncio.gather(*list(plugin._background_tasks), return_exceptions=True)
        assert len(plugin._background_tasks) == 0

    @pytest.mark.asyncio
    async def test_uses_chat_id_as_session(self, runtime):
        """session_id comes from result['chat_id'], not a shared runtime attribute."""
        plugin = LearningPlugin(runtime)
        result = {
            "messages": [{"role": "user", "content": "hi"}],
            "chat_id": "my-chat",
            "memory": MagicMock(_messages=[{"role": "user", "content": "hi"}]),
        }
        await plugin.on_run_end(MagicMock(), result)
        await asyncio.gather(*list(plugin._background_tasks), return_exceptions=True)
        # Counter should be keyed to "my-chat", not "default" or a shared value
        if runtime.extractor:
            assert "my-chat" in runtime.extractor._run_counter
