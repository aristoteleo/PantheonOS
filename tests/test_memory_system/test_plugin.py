"""Tests for MemorySystemPlugin (adapter) lifecycle."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pantheon.internal.memory_system.plugin import MemorySystemPlugin


class TestPluginAsAdapter:
    @pytest.mark.asyncio
    async def test_uninitialized_runtime_skips(self, runtime_config, tmp_pantheon_dir, tmp_runtime_dir):
        from pantheon.internal.memory_system.runtime import MemoryRuntime
        rt = MemoryRuntime(runtime_config)  # not initialized
        plugin = MemorySystemPlugin(rt)
        team = MagicMock()
        await plugin.on_team_created(team)  # should not crash

    @pytest.mark.asyncio
    async def test_injects_memory_index(self, runtime):
        # Write some index content
        runtime.store.write_index("- [Test](memory/test.md) — A test memory\n")

        plugin = MemorySystemPlugin(runtime)
        mock_agent = MagicMock()
        mock_agent.name = "test_agent"
        mock_agent.instructions = "Base."
        mock_team = MagicMock()
        mock_team.agents = [mock_agent]

        await plugin.on_team_created(mock_team)
        assert "Memory Index" in mock_agent.instructions
        assert "test.md" in mock_agent.instructions

    @pytest.mark.asyncio
    async def test_on_run_start_retrieves(self, runtime, sample_user_entry):
        runtime.store.add_memory(sample_user_entry)
        plugin = MemorySystemPlugin(runtime)

        # Mock the retriever
        mock_result = MagicMock()
        mock_result.path = MagicMock(name="test.md")
        mock_result.entry = MagicMock(title="Test", type=MagicMock(value="user"))
        mock_result.content = "Test content"
        mock_result.age_text = "today"
        runtime.retriever.find_relevant = AsyncMock(return_value=[mock_result])

        context = {"memory": MagicMock(id="session-1")}
        await plugin.on_run_start(MagicMock(), "What about testing?", context)
        assert "memory_context" in context

    @pytest.mark.asyncio
    async def test_on_run_end_increments_session(self, runtime):
        plugin = MemorySystemPlugin(runtime)
        assert runtime.dream_gate._session_counter == 0
        # Main agent result has "memory" key and messages
        result = {
            "agent_name": "main",
            "messages": [{"role": "user", "content": "hi"}],
            "chat_id": "session-1",
            "memory": MagicMock(_messages=[{"role": "user", "content": "hi"}]),
        }
        await plugin.on_run_end(MagicMock(), result)
        assert runtime.dream_gate._session_counter == 1

    @pytest.mark.asyncio
    async def test_on_run_end_skips_sub_agent(self, runtime):
        """Sub-agent results (with 'question' key) should be skipped entirely."""
        plugin = MemorySystemPlugin(runtime)
        assert runtime.dream_gate._session_counter == 0
        sub_result = {
            "agent_name": "sub-agent",
            "messages": [{"role": "user", "content": "do task"}],
            "chat_id": "session-1",
            "question": "Please do this task",  # Sub-agent marker
        }
        await plugin.on_run_end(MagicMock(), sub_result)
        # Should NOT increment session counter
        assert runtime.dream_gate._session_counter == 0

    @pytest.mark.asyncio
    async def test_pre_compression_flush(self, runtime):
        runtime.flusher.flush = AsyncMock(return_value="Extracted info")
        plugin = MemorySystemPlugin(runtime)
        result = await plugin.pre_compression_flush("session-1", [{"role": "user", "content": "test"}])
        assert result == "Extracted info"
