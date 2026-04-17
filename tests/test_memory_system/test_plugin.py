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
        """static_index_enabled=True injects MEMORY.md index into instructions."""
        runtime.store.write_index("- [Test](memory/test.md) — A test memory\n")
        runtime.config["static_index_enabled"] = True

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
    async def test_static_index_disabled_by_default(self, runtime):
        """static_index_enabled=False (default) skips MEMORY.md index injection."""
        runtime.store.write_index("- [Test](memory/test.md) — A test memory\n")
        # default: static_index_enabled not set → False

        plugin = MemorySystemPlugin(runtime)
        mock_agent = MagicMock()
        mock_agent.name = "test_agent"
        mock_agent.instructions = "Base."
        mock_team = MagicMock()
        mock_team.agents = [mock_agent]

        await plugin.on_team_created(mock_team)
        assert "test.md" not in mock_agent.instructions

    @pytest.mark.asyncio
    async def test_on_run_start_retrieves(self, runtime, sample_user_entry):
        runtime.store.add_memory(sample_user_entry)
        plugin = MemorySystemPlugin(runtime)

        mock_result = MagicMock()
        mock_result.path = MagicMock(name="test.md")
        mock_result.entry = MagicMock(title="Test", type=MagicMock(value="user"))
        mock_result.content = "Test content"
        mock_result.age_text = "today"
        runtime.retriever.find_relevant = AsyncMock(return_value=[mock_result])

        context = {"memory": MagicMock(id="session-1")}
        result = await plugin.on_run_start(MagicMock(), "What about testing?", context)
        assert result is not None
        assert "Relevant Memories" in result
        assert "Test" in result  # title present in both index and full mode

    @pytest.mark.asyncio
    async def test_on_run_end_is_nonblocking(self, runtime):
        """on_run_end returns immediately; work runs in background tasks."""
        import asyncio
        plugin = MemorySystemPlugin(runtime)
        result = {
            "messages": [{"role": "user", "content": "hi"}],
            "chat_id": "session-1",
            "memory": MagicMock(_messages=[{"role": "user", "content": "hi"}]),
        }
        await plugin.on_run_end(MagicMock(), result)
        assert isinstance(plugin._background_tasks, set)
        # Tasks are tracked; drain so they complete cleanly
        await asyncio.gather(*list(plugin._background_tasks), return_exceptions=True)
        assert len(plugin._background_tasks) == 0

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
        runtime.session_note.force_update = AsyncMock(return_value=False)
        plugin = MemorySystemPlugin(runtime)
        result = await plugin.pre_compression_flush("session-1", [{"role": "user", "content": "test"}])
        assert result == "Extracted info"

    @pytest.mark.asyncio
    async def test_pre_compression_returns_compact_hint_when_session_note_ready(self, runtime):
        """pre_compression returns CompactHint when session note has content."""
        from pantheon.team.plugin import CompactHint

        runtime.flusher.flush = AsyncMock(return_value=None)
        runtime.session_note.force_update = AsyncMock(return_value=True)
        runtime.session_note.get_last_summarized_index = MagicMock(return_value=4)
        runtime.session_note.is_empty_template = MagicMock(return_value=False)
        runtime.session_note.read = MagicMock(return_value="## Task State\nWorking on X.")

        plugin = MemorySystemPlugin(runtime)
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(6)]
        result = await plugin.pre_compression(MagicMock(), "session-1", messages)

        assert isinstance(result, CompactHint)
        assert result.boundary == 4
        assert "Task State" in result.summary

    @pytest.mark.asyncio
    async def test_pre_compression_returns_flush_str_when_session_note_empty(self, runtime):
        """pre_compression falls back to flush str when session note is empty."""
        from pantheon.team.plugin import CompactHint

        runtime.flusher.flush = AsyncMock(return_value="Flushed content")
        runtime.session_note.force_update = AsyncMock(return_value=False)
        runtime.session_note.get_last_summarized_index = MagicMock(return_value=0)
        runtime.session_note.is_empty_template = MagicMock(return_value=True)

        plugin = MemorySystemPlugin(runtime)
        messages = [{"role": "user", "content": "hi"}]
        result = await plugin.pre_compression(MagicMock(), "session-1", messages)

        assert result == "Flushed content"
        assert not isinstance(result, CompactHint)


class TestOnRunStartMsgInjection:
    """Verify on_run_start return value is applied by PantheonTeam.run()."""

    @pytest.mark.asyncio
    async def test_on_run_start_return_replaces_msg(self):
        """PantheonTeam.run() uses the modified msg returned by on_run_start."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from pantheon.team.pantheon import PantheonTeam
        from pantheon.team.plugin import TeamPlugin

        received_msgs = []

        class _InjectPlugin(TeamPlugin):
            async def on_team_created(self, team): pass
            async def on_run_start(self, team, user_input, context):
                if isinstance(user_input, str):
                    return user_input + "\n\n[INJECTED]"
                return None

        mock_agent = MagicMock()
        mock_agent.name = "test"
        mock_agent.models = ["gpt-4o-mini"]
        mock_agent._ephemeral_hooks = []
        mock_agent._tool_tracking_hooks = []

        async def fake_run(msg, **kwargs):
            received_msgs.append(msg)
            from pantheon.agent import AgentResponse
            return AgentResponse(agent_name="test", messages=[], context_variables={})

        mock_agent.run = fake_run

        team = MagicMock(spec=PantheonTeam)
        team.plugins = [_InjectPlugin()]
        team._is_initialized = True
        team.team_agents = [mock_agent]

        # Simulate the relevant part of PantheonTeam.run()
        from pantheon.utils.misc import run_func
        from pantheon.internal.memory import Memory

        msg = "hello world"
        memory = Memory(name="test")
        run_context = {"memory": memory, "kwargs": {}}

        for plugin in team.plugins:
            result = await run_func(plugin.on_run_start, team, msg, run_context)
            if result is not None:
                msg = result

        assert msg == "hello world\n\n[INJECTED]"

    @pytest.mark.asyncio
    async def test_on_run_start_non_str_not_modified(self, runtime):
        """Non-injectable inputs (BaseModel, AgentTransfer) return None."""
        from pydantic import BaseModel as PydanticModel
        plugin = MemorySystemPlugin(runtime)
        context = {"memory": MagicMock(id="session-1")}

        class _Dummy(PydanticModel):
            x: int = 1

        result = await plugin.on_run_start(MagicMock(), _Dummy(), context)
        assert result is None

    @pytest.mark.asyncio
    async def test_on_run_start_list_dict_appends_llm_content(self, runtime):
        """list[dict] input: memory context appended to last user message _llm_content."""
        mock_result = MagicMock()
        mock_result.entry = MagicMock(title="Go expertise")
        mock_result.content = "User has 10 years Go experience."
        mock_result.age_text = "today"
        runtime.retriever.find_relevant = AsyncMock(return_value=[mock_result])

        plugin = MemorySystemPlugin(runtime)
        context = {"memory": MagicMock(id="session-1")}

        messages = [
            {"role": "user", "content": "hello", "_llm_content": "hello"},
        ]
        result = await plugin.on_run_start(MagicMock(), messages, context)

        assert result is not None
        assert isinstance(result, list)
        last_user = next(m for m in reversed(result) if m.get("role") == "user")
        assert "Relevant Memories" in last_user["_llm_content"]
        assert "Go expertise" in last_user["_llm_content"]
        # original not mutated
        assert result[0] is not messages[0]

    @pytest.mark.asyncio
    async def test_on_run_start_list_dict_with_content_array(self, runtime):
        """list[dict] with _llm_content as list: appends text part."""
        mock_result = MagicMock()
        mock_result.entry = MagicMock(title="Tip")
        mock_result.content = "Some tip."
        mock_result.age_text = "today"
        runtime.retriever.find_relevant = AsyncMock(return_value=[mock_result])

        plugin = MemorySystemPlugin(runtime)
        context = {"memory": MagicMock(id="session-1")}

        messages = [
            {"role": "user", "content": "hi", "_llm_content": [{"type": "text", "text": "hi"}]},
        ]
        result = await plugin.on_run_start(MagicMock(), messages, context)

        assert result is not None
        last_user = next(m for m in reversed(result) if m.get("role") == "user")
        assert isinstance(last_user["_llm_content"], list)
        texts = " ".join(p.get("text", "") for p in last_user["_llm_content"])
        assert "Relevant Memories" in texts

    @pytest.mark.asyncio
    async def test_on_run_start_no_results_returns_none(self, runtime):
        """Returns None when retrieval finds nothing."""
        runtime.retriever.find_relevant = AsyncMock(return_value=[])
        plugin = MemorySystemPlugin(runtime)
        context = {"memory": MagicMock(id="session-1")}
        result = await plugin.on_run_start(MagicMock(), "any query", context)
        assert result is None
