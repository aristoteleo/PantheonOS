from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pantheon.agent import Agent, AgentRunContext, _RUN_CONTEXT
from pantheon.internal.memory import Memory
from pantheon.team.pantheon import (
    PantheonTeam,
    _get_cache_safe_child_fork_context_messages,
    _get_cache_safe_child_run_overrides,
    create_delegation_task_message,
)
from pantheon.utils.token_optimization import (
    PERSISTED_OUTPUT_TAG,
    TIME_BASED_MC_CLEARED_MESSAGE,
    TimeBasedMicrocompactConfig,
    ContextCollapseManager,
    apply_token_optimizations,
    apply_tool_result_budget,
    applyCollapsesIfNeeded,
    build_cache_safe_runtime_params,
    build_delegation_context_message,
    build_llm_view,
    evaluate_time_based_trigger,
    estimate_total_tokens_from_chars,
    inject_cache_control_markers,
    is_anthropic_model,
    microcompact_messages,
    projectView,
)


def _build_tool_message(tool_call_id: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "tool_name": "shell",
        "content": content,
    }


def test_apply_tool_result_budget_persists_large_parallel_tool_messages(tmp_path):
    """Aggregate path: 3×90K = 270K exceeds the 200K per-message limit,
    so the budget externalizes the largest fresh results until under limit.
    Per-tool thresholds are now enforced at tool execution time (process_tool_result),
    so apply_tool_result_budget only handles aggregate overflow."""
    memory = Memory("test-memory")
    messages = [
        {
            "role": "assistant",
            "id": "assistant-1",
            "tool_calls": [
                {"id": "tool-1", "function": {"name": "shell"}},
                {"id": "tool-2", "function": {"name": "shell"}},
                {"id": "tool-3", "function": {"name": "shell"}},
            ],
        },
        _build_tool_message("tool-1", "A" * 90_000),
        _build_tool_message("tool-2", "B" * 90_000),
        _build_tool_message("tool-3", "C" * 90_000),
    ]

    optimized = apply_tool_result_budget(messages, memory=memory, base_dir=tmp_path)

    optimized_tool_messages = [msg for msg in optimized if msg["role"] == "tool"]
    persisted = [
        msg for msg in optimized_tool_messages if msg["content"].startswith(PERSISTED_OUTPUT_TAG)
    ]

    # 270K > 200K aggregate limit — at least 1 result must be externalized
    assert len(persisted) >= 1
    assert all("Full output saved to:" in m["content"] for m in persisted)
    assert "token_optimization" in memory.extra_data

    rerun = apply_tool_result_budget(messages, memory=memory, base_dir=tmp_path)
    assert rerun[1]["content"] == optimized[1]["content"]
    assert rerun[2]["content"] == optimized[2]["content"]
    assert rerun[3]["content"] == optimized[3]["content"]


def test_apply_tool_result_budget_aggregate_path_for_unknown_tool(tmp_path):
    """Unknown tools use global aggregate logic, not per-tool limit."""
    memory = Memory("test-aggregate")
    # 3 × 90K = 270K > 200K global limit → only largest(s) get externalized
    messages = [
        {
            "role": "assistant",
            "id": "assistant-1",
            "tool_calls": [
                {"id": "t-1", "function": {"name": "my_custom_tool"}},
                {"id": "t-2", "function": {"name": "my_custom_tool"}},
                {"id": "t-3", "function": {"name": "my_custom_tool"}},
            ],
        },
        _build_tool_message("t-1", "A" * 90_000),
        _build_tool_message("t-2", "B" * 90_000),
        _build_tool_message("t-3", "C" * 90_000),
    ]
    optimized = apply_tool_result_budget(messages, memory=memory, base_dir=tmp_path)
    tool_msgs = [msg for msg in optimized if msg["role"] == "tool"]
    persisted = [m for m in tool_msgs if m["content"].startswith(PERSISTED_OUTPUT_TAG)]
    # Aggregate logic: 270K > 200K limit → some (≥1) get externalized
    assert len(persisted) >= 1
    assert "Full output saved to:" in persisted[0]["content"]


def test_time_based_microcompact_clears_old_tool_messages():
    old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    messages = [
        {"role": "assistant", "id": "assistant-1", "timestamp": old_timestamp},
        _build_tool_message("tool-1", "A" * 20_000),
        _build_tool_message("tool-2", "B" * 20_000),
        _build_tool_message("tool-3", "C" * 20_000),
        _build_tool_message("tool-4", "D" * 20_000),
        _build_tool_message("tool-5", "E" * 20_000),
        _build_tool_message("tool-6", "F" * 20_000),
    ]

    compacted = microcompact_messages(
        messages,
        is_main_thread=True,
        config=TimeBasedMicrocompactConfig(
            enabled=True,
            gap_threshold_minutes=60,
            keep_recent=2,
        ),
    )
    compacted_contents = [msg["content"] for msg in compacted if msg["role"] == "tool"]

    assert compacted_contents[:4] == [TIME_BASED_MC_CLEARED_MESSAGE] * 4
    assert compacted_contents[-2:] == ["E" * 20_000, "F" * 20_000]


def test_time_based_microcompact_only_clears_compactable_tools():
    old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    messages = [
        {
            "role": "assistant",
            "id": "assistant-1",
            "timestamp": old_timestamp,
            "tool_calls": [
                {"id": "tool-1", "function": {"name": "shell"}},
                {"id": "tool-2", "function": {"name": "knowledge__search_knowledge"}},
                {"id": "tool-3", "function": {"name": "web_urllib__web_search"}},
            ],
        },
        _build_tool_message("tool-1", "A" * 20_000),
        {
            "role": "tool",
            "tool_call_id": "tool-2",
            "tool_name": "knowledge__search_knowledge",
            "content": "B" * 20_000,
        },
        {
            "role": "tool",
            "tool_call_id": "tool-3",
            "tool_name": "web_urllib__web_search",
            "content": "C" * 20_000,
        },
    ]

    compacted = microcompact_messages(
        messages,
        is_main_thread=True,
        config=TimeBasedMicrocompactConfig(
            enabled=True,
            gap_threshold_minutes=60,
            keep_recent=1,
        ),
    )
    compacted_contents = [msg["content"] for msg in compacted if msg["role"] == "tool"]

    assert compacted_contents[0] == TIME_BASED_MC_CLEARED_MESSAGE
    assert compacted_contents[1] == "B" * 20_000
    assert compacted_contents[2] == "C" * 20_000


def test_evaluate_time_based_trigger_requires_old_assistant_message():
    recent_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()

    recent = [{"role": "assistant", "timestamp": recent_timestamp}]
    old = [{"role": "assistant", "timestamp": old_timestamp}]

    config = TimeBasedMicrocompactConfig(
        enabled=True,
        gap_threshold_minutes=60,
        keep_recent=5,
    )

    assert evaluate_time_based_trigger(
        recent,
        is_main_thread=True,
        config=config,
    ) is None
    assert evaluate_time_based_trigger(
        old,
        is_main_thread=False,
        config=config,
    ) is None
    assert evaluate_time_based_trigger(
        old,
        is_main_thread=True,
        config=config,
    ) is not None


def test_build_llm_view_skips_time_based_microcompact_for_subagents():
    memory = Memory("subagent-projection-memory")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "assistant",
            "id": "assistant-1",
            "timestamp": old_timestamp,
            "tool_calls": [
                {"id": "tool-1", "function": {"name": "shell"}},
                {"id": "tool-2", "function": {"name": "file_manager__read_file"}},
                {"id": "tool-3", "function": {"name": "file_manager__grep"}},
                {"id": "tool-4", "function": {"name": "web_urllib__web_search"}},
                {"id": "tool-5", "function": {"name": "web_urllib__web_fetch"}},
                {"id": "tool-6", "function": {"name": "file_manager__glob"}},
            ],
        },
        _build_tool_message("tool-1", "A" * 20_000),
        {
            "role": "tool",
            "tool_call_id": "tool-2",
            "tool_name": "file_manager__read_file",
            "content": "B" * 20_000,
        },
        {
            "role": "tool",
            "tool_call_id": "tool-3",
            "tool_name": "file_manager__grep",
            "content": "C" * 20_000,
        },
        {
            "role": "tool",
            "tool_call_id": "tool-4",
            "tool_name": "web_urllib__web_search",
            "content": "D" * 20_000,
        },
        {
            "role": "tool",
            "tool_call_id": "tool-5",
            "tool_name": "web_urllib__web_fetch",
            "content": "E" * 20_000,
        },
        {
            "role": "tool",
            "tool_call_id": "tool-6",
            "tool_name": "file_manager__glob",
            "content": "F" * 20_000,
        },
    ]

    view = build_llm_view(messages, memory=memory, is_main_thread=False)

    tool_contents = [msg["content"] for msg in view if msg["role"] == "tool"]
    assert TIME_BASED_MC_CLEARED_MESSAGE not in tool_contents


def test_build_cache_safe_runtime_params_normalizes_dict_order():
    class ResponseA:
        @staticmethod
        def model_json_schema():
            return {
                "type": "object",
                "properties": {
                    "b": {"type": "string"},
                    "a": {"type": "string"},
                },
                "required": ["b", "a"],
            }

    params_a = build_cache_safe_runtime_params(
        model="openai/gpt-5.1-mini",
        model_params={"top_p": 1, "temperature": 0},
        response_format=ResponseA,
    )
    params_b = build_cache_safe_runtime_params(
        model="openai/gpt-5.1-mini",
        model_params={"temperature": 0, "top_p": 1},
        response_format=ResponseA,
    )

    assert params_a.model_params_normalized == params_b.model_params_normalized
    assert params_a.response_format_normalized == params_b.response_format_normalized


def test_get_cache_safe_child_run_overrides_inherits_compatible_runtime_params():
    caller = Agent(
        name="caller",
        instructions="caller",
        model="openai/gpt-5.1-mini",
        model_params={"temperature": 0},
    )
    target = Agent(
        name="target",
        instructions="target",
        model="openai/gpt-5.1-mini",
        model_params={"temperature": 0},
    )
    run_context = AgentRunContext(
        agent=caller,
        memory=None,
        execution_context_id=None,
        process_step_message=None,
        process_chunk=None,
    )
    run_context.cache_safe_runtime_params = build_cache_safe_runtime_params(
        model="openai/gpt-5.1-mini",
        model_params={"temperature": 0, "top_p": 1},
        response_format=None,
    )

    overrides, child_context_variables = _get_cache_safe_child_run_overrides(
        run_context,
        target,
        {},
    )

    assert overrides == {
        "model": "openai/gpt-5.1-mini",
        "response_format": None,
    }
    assert child_context_variables["model_params"] == {"temperature": 0, "top_p": 1}


def test_get_cache_safe_child_run_overrides_inherits_runtime_model_for_implicit_child():
    caller = Agent(
        name="caller",
        instructions="caller",
        model="gemini/gemini-3-flash-preview",
        model_params={"temperature": 0},
    )
    target = Agent(
        name="target",
        instructions="target",
        model=None,
    )
    run_context = AgentRunContext(
        agent=caller,
        memory=None,
        execution_context_id=None,
        process_step_message=None,
        process_chunk=None,
    )
    run_context.cache_safe_runtime_params = build_cache_safe_runtime_params(
        model="gemini/gemini-3-flash-preview",
        model_params={"temperature": 0.2},
        response_format=None,
    )

    overrides, child_context_variables = _get_cache_safe_child_run_overrides(
        run_context,
        target,
        {},
    )

    assert overrides == {
        "model": "gemini/gemini-3-flash-preview",
    }
    assert child_context_variables["model_params"] == {"temperature": 0.2}


def test_prepare_execution_context_prepends_cache_safe_fork_messages():
    agent = Agent(name="child", instructions="child", model="openai/gpt-5.1-mini")
    fork_context_messages = [
        {"role": "user", "content": "Parent prefix question"},
        {"role": "assistant", "content": "Parent prefix answer"},
    ]

    import asyncio

    exec_context = asyncio.run(
        agent._prepare_execution_context(
            msg="Delegated child task",
            use_memory=False,
            context_variables={
                "_cache_safe_fork_context_messages": fork_context_messages,
            },
        )
    )

    assert exec_context.conversation_history[0]["content"] == "Parent prefix question"
    assert exec_context.conversation_history[1]["content"] == "Parent prefix answer"
    assert exec_context.conversation_history[-1]["content"] == "Delegated child task"
    assert "_cache_safe_fork_context_messages" not in exec_context.context_variables


def test_get_cache_safe_child_fork_context_messages_requires_compatible_agent():
    caller = Agent(
        name="caller",
        instructions="shared instructions",
        model="openai/gpt-5.1-mini",
    )
    target = Agent(
        name="target",
        instructions="shared instructions",
        model="openai/gpt-5.1-mini",
    )

    def alpha_tool(path: str) -> str:
        return path

    caller.tool(alpha_tool)
    target.tool(alpha_tool)

    run_context = AgentRunContext(
        agent=caller,
        memory=None,
        execution_context_id=None,
        process_step_message=None,
        process_chunk=None,
        cache_safe_prompt_messages=[
            {"role": "system", "content": "shared instructions"},
            {"role": "user", "content": "Parent prefix question"},
        ],
    )

    import asyncio

    run_context.cache_safe_tool_definitions = asyncio.run(caller.get_tools_for_llm())
    fork_context_messages = asyncio.run(
        _get_cache_safe_child_fork_context_messages(run_context, target)
    )

    assert fork_context_messages == [
        {"role": "user", "content": "Parent prefix question"},
    ]


def test_apply_token_optimizations_reduces_prompt_size(tmp_path):
    memory = Memory("benchmark-memory")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    messages = [
        {"role": "system", "content": "You are helpful."},
        {
            "role": "assistant",
            "id": "assistant-1",
            "timestamp": old_timestamp,
            "tool_calls": [
                {"id": "tool-1", "function": {"name": "shell"}},
                {"id": "tool-2", "function": {"name": "shell"}},
                {"id": "tool-3", "function": {"name": "shell"}},
                {"id": "tool-4", "function": {"name": "shell"}},
                {"id": "tool-5", "function": {"name": "shell"}},
                {"id": "tool-6", "function": {"name": "shell"}},
            ],
        },
        _build_tool_message("tool-1", "A" * 90_000),
        _build_tool_message("tool-2", "B" * 90_000),
        _build_tool_message("tool-3", "C" * 90_000),
        _build_tool_message("tool-4", "D" * 90_000),
        _build_tool_message("tool-5", "E" * 90_000),
        _build_tool_message("tool-6", "F" * 90_000),
        {"role": "user", "content": "Please summarize the tool outputs."},
    ]

    before_tokens = estimate_total_tokens_from_chars(messages)
    optimized = apply_token_optimizations(
        messages,
        memory=memory,
        base_dir=tmp_path,
    )
    after_tokens = estimate_total_tokens_from_chars(optimized)

    assert after_tokens < before_tokens


def test_build_llm_view_projects_compression_and_preserves_system():
    memory = Memory("projection-memory")
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "first"},
        {"role": "compression", "content": "compressed"},
        {"role": "assistant", "content": "after compression"},
    ]

    view = build_llm_view(messages, memory=memory)

    assert view[0]["role"] == "system"
    assert len(view) == 3
    assert view[1]["role"] == "user"
    assert view[1]["content"] == "compressed"


def test_get_tools_for_llm_is_stably_sorted():
    agent = Agent(name="sorter", instructions="Sort tools")

    def zebra_tool() -> str:
        return "z"

    def alpha_tool() -> str:
        return "a"

    agent.tool(zebra_tool)
    agent.tool(alpha_tool)

    import asyncio

    tools = asyncio.run(agent.get_tools_for_llm())
    tool_names = [tool["function"]["name"] for tool in tools]

    assert tool_names == sorted(tool_names)


def test_create_delegation_task_message_uses_recent_context_and_file_refs(monkeypatch):
    class FakeSummaryGenerator:
        async def generate_summary(self, history, max_tokens=1000):
            return "short summary"

    monkeypatch.setattr(
        "pantheon.chatroom.special_agents.get_summary_generator",
        lambda: FakeSummaryGenerator(),
    )

    history = [
        {"role": "user", "content": "Investigate the failures."},
        {
            "role": "tool",
            "tool_call_id": "tool-1",
            "tool_name": "shell",
            "content": "<persisted-output>\nOutput too large (10KB). Full output saved to: /tmp/tool-1.txt\n\nPreview (first 2KB):\nfoo\n</persisted-output>",
        },
        {"role": "assistant", "content": "I found two likely causes."},
    ]

    import asyncio

    task_message = asyncio.run(
        create_delegation_task_message(
            history,
            "Find the root cause",
            use_summary=True,
        )
    )

    assert "Context Summary:\nshort summary" in task_message
    assert "Recent Context:" in task_message
    assert "Referenced Files (retrieve on demand if needed):\n- /tmp/tool-1.txt" in task_message
    assert "Task: Find the root cause" in task_message
    # On-demand hint is appended when summary is present
    assert "retrieve it on demand" in task_message


def test_create_delegation_task_message_use_summary_false_returns_raw_instruction(monkeypatch):
    """When use_summary=False, only the raw instruction is returned."""
    import asyncio

    result = asyncio.run(
        create_delegation_task_message(
            history=[{"role": "user", "content": "hello"}],
            instruction="Do something",
            use_summary=False,
        )
    )
    assert result == "Do something"


def test_create_delegation_task_message_trims_history_to_recent_tail(monkeypatch):
    """Only the most recent messages are passed to build_delegation_context_message."""
    from pantheon.team.pantheon import DELEGATION_RECENT_TAIL_SIZE

    captured = {}

    original_build = build_delegation_context_message

    def spy_build(history, instruction, summary_text=None):
        captured["history_len"] = len(history)
        return original_build(
            history=history,
            instruction=instruction,
            summary_text=summary_text,
        )

    monkeypatch.setattr(
        "pantheon.utils.token_optimization.build_delegation_context_message",
        spy_build,
    )

    class FakeSummaryGenerator:
        async def generate_summary(self, history, max_tokens=1000):
            captured["summary_input_len"] = len(history)
            return "summary"

    monkeypatch.setattr(
        "pantheon.chatroom.special_agents.get_summary_generator",
        lambda: FakeSummaryGenerator(),
    )

    # Create a history larger than DELEGATION_RECENT_TAIL_SIZE
    big_history = [
        {"role": "user", "content": f"message {i}"}
        for i in range(DELEGATION_RECENT_TAIL_SIZE + 30)
    ]

    import asyncio

    asyncio.run(
        create_delegation_task_message(
            history=big_history,
            instruction="Analyze",
            use_summary=True,
        )
    )

    # Summary generator sees full history
    assert captured["summary_input_len"] == len(big_history)
    # build_delegation_context_message only sees the recent tail
    assert captured["history_len"] == DELEGATION_RECENT_TAIL_SIZE


def test_create_delegation_no_on_demand_hint_without_summary(monkeypatch):
    """When summary generation fails, on-demand hint is not appended."""
    class FailingSummaryGenerator:
        async def generate_summary(self, history, max_tokens=1000):
            raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(
        "pantheon.chatroom.special_agents.get_summary_generator",
        lambda: FailingSummaryGenerator(),
    )

    import asyncio

    result = asyncio.run(
        create_delegation_task_message(
            history=[{"role": "user", "content": "hello"}],
            instruction="Do something",
            use_summary=True,
        )
    )
    # No summary means no on-demand hint
    assert "retrieve it on demand" not in result
    assert "Task: Do something" in result


def test_pantheon_team_use_summary_defaults_to_true():
    """PantheonTeam defaults to use_summary=True for summary-first delegation."""
    from unittest.mock import MagicMock

    agent = MagicMock()
    agent.name = "test-agent"
    agent.models = ["gpt-4"]

    team = PantheonTeam(agents=[agent])
    assert team.use_summary is True


# ---------------------------------------------------------------------------
# Opt3: cache_control injection tests
# ---------------------------------------------------------------------------

def test_is_anthropic_model_detection():
    assert is_anthropic_model("claude-3-5-sonnet-20241022") is True
    assert is_anthropic_model("anthropic/claude-3-haiku") is True
    assert is_anthropic_model("custom_anthropic/claude-3-opus") is True
    assert is_anthropic_model("gpt-4o") is False
    assert is_anthropic_model("gpt-4.1-mini") is False
    assert is_anthropic_model("openai/gpt-4") is False


def test_inject_cache_control_marks_system_and_last_user():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "What is 2+2?"},
    ]
    result = inject_cache_control_markers(messages)

    # System message last block should have cache_control
    sys_content = result[0]["content"]
    assert isinstance(sys_content, list)
    assert sys_content[-1].get("cache_control") == {"type": "ephemeral"}

    # Last user message should have cache_control
    last_user = result[-1]["content"]
    assert isinstance(last_user, list)
    assert last_user[-1].get("cache_control") == {"type": "ephemeral"}

    # Middle assistant message should NOT have cache_control
    mid_asst = result[2]["content"]
    if isinstance(mid_asst, list):
        assert all("cache_control" not in b for b in mid_asst)
    else:
        assert "cache_control" not in str(mid_asst)


def test_inject_cache_control_converts_string_content_to_blocks():
    messages = [{"role": "user", "content": "plain string content"}]
    result = inject_cache_control_markers(messages)
    content = result[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "plain string content"
    assert content[0]["cache_control"] == {"type": "ephemeral"}


def test_inject_cache_control_does_not_mutate_input():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user msg"},
    ]
    original_sys = messages[0]["content"]
    inject_cache_control_markers(messages)
    # Input should be unchanged
    assert messages[0]["content"] == original_sys
    assert isinstance(messages[0]["content"], str)


def test_inject_cache_control_skips_empty_assistant():
    """Last non-empty user/assistant gets the marker, not an empty trailing message."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "   "},  # whitespace-only, skip
    ]
    result = inject_cache_control_markers(messages)
    # user message should get the marker since assistant is empty
    user_content = result[1]["content"]
    assert isinstance(user_content, list)
    assert user_content[-1].get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Opt2 extension: HISTORY_SNIP tests
# ---------------------------------------------------------------------------

def test_snip_messages_drops_oldest_when_over_budget():
    from pantheon.utils.token_optimization import SnipConfig, snip_messages_to_budget

    # 5 user messages of ~1000 tokens each = ~5000 tokens, budget = 3000
    def big_msg(i):
        return {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 4000, "id": str(i)}

    messages = [{"role": "system", "content": "sys"}] + [big_msg(i) for i in range(5)]
    config = SnipConfig(enabled=True, token_budget=3000, keep_recent=2)

    result, freed = snip_messages_to_budget(messages, config=config)

    # System message always kept
    assert result[0]["role"] == "system"
    # Last 2 messages always kept (protected tail)
    assert result[-1]["id"] == "4"
    assert result[-2]["id"] == "3"
    # Some old messages dropped
    assert freed > 0
    total_after = sum(len(m.get("content", "")) // 4 for m in result)
    assert total_after <= 3000


def test_snip_messages_noop_when_under_budget():
    from pantheon.utils.token_optimization import SnipConfig, snip_messages_to_budget

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "short"},
        {"role": "assistant", "content": "reply"},
    ]
    config = SnipConfig(enabled=True, token_budget=100_000, keep_recent=2)
    result, freed = snip_messages_to_budget(messages, config=config)
    assert result is messages  # unchanged
    assert freed == 0


def test_snip_messages_respects_keep_recent():
    from pantheon.utils.token_optimization import SnipConfig, snip_messages_to_budget

    messages = [{"role": "user", "content": "x" * 4000, "id": str(i)} for i in range(10)]
    config = SnipConfig(enabled=True, token_budget=1, keep_recent=4)
    result, freed = snip_messages_to_budget(messages, config=config)
    kept_ids = [m["id"] for m in result]
    # Last 4 must be in result
    assert "6" in kept_ids
    assert "7" in kept_ids
    assert "8" in kept_ids
    assert "9" in kept_ids


def test_snip_messages_removes_orphaned_tool_result_when_pair_is_split():
    from pantheon.utils.token_optimization import SnipConfig, snip_messages_to_budget

    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "shell", "arguments": "{}"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "tool_name": "shell",
            "content": "x" * 4000,
        },
        {"role": "user", "content": "recent message", "id": "u1"},
    ]
    config = SnipConfig(enabled=True, token_budget=1000, keep_recent=2)

    result, freed = snip_messages_to_budget(messages, config=config)

    assert freed > 0
    assert all(msg.get("role") != "tool" for msg in result)
    assert result == [{"role": "user", "content": "recent message", "id": "u1"}]


def test_ensure_tool_history_consistency_inserts_placeholder_for_missing_tool_response():
    from pantheon.utils.token_optimization import ensure_tool_history_consistency
    from pantheon.utils.tool_pairing import INCOMPLETE_TOOL_RESULT_PLACEHOLDER

    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "shell", "arguments": "{}"}}],
        },
        {"role": "user", "content": "recent message", "id": "u1"},
    ]

    result = ensure_tool_history_consistency(messages)

    assert result[0]["tool_calls"][0]["id"] == "call_1"
    assert result[1]["role"] == "tool"
    assert result[1]["tool_call_id"] == "call_1"
    assert result[1]["content"] == INCOMPLETE_TOOL_RESULT_PLACEHOLDER
    assert result[2] == {"role": "user", "content": "recent message", "id": "u1"}


def test_snip_disabled_is_noop():
    from pantheon.utils.token_optimization import SnipConfig, snip_messages_to_budget

    messages = [{"role": "user", "content": "x" * 100_000}]
    config = SnipConfig(enabled=False, token_budget=1, keep_recent=1)
    result, freed = snip_messages_to_budget(messages, config=config)
    assert result is messages
    assert freed == 0


def test_apply_token_optimizations_runs_snip_before_microcompact(tmp_path):
    import time
    from pantheon.utils.token_optimization import SnipConfig

    # Build messages that are over snip budget, with old timestamps → both snip and microcompact fire
    old_ts = time.time() - 7200
    messages = []
    for i in range(8):
        messages.append({
            "role": "assistant", "content": f"turn {i}",
            "tool_calls": [{"id": f"c{i}", "function": {"name": "shell", "arguments": "{}"}}],
            "timestamp": old_ts + i,
        })
        messages.append({
            "role": "tool", "tool_call_id": f"c{i}", "tool_name": "shell",
            "content": "x" * 4000,
        })

    snip_cfg = SnipConfig(enabled=True, token_budget=2000, keep_recent=2)
    mc_cfg = TimeBasedMicrocompactConfig(enabled=True, gap_threshold_minutes=1, keep_recent=1)

    result = apply_token_optimizations(
        messages, snip_config=snip_cfg, is_main_thread=True
    )
    # Result must be smaller than input
    before = sum(len(m.get("content", "")) for m in messages)
    after = sum(len(m.get("content", "")) for m in result)
    assert after < before


# ---------------------------------------------------------------------------
# Opt1 extension: per-tool threshold tests
# ---------------------------------------------------------------------------

def test_per_tool_threshold_externalizes_oversized_single_result(tmp_path):
    """Per-tool threshold is now enforced at process_tool_result() time.
    Here we verify that process_tool_result uses get_per_tool_limit to
    apply the correct per-tool threshold (read_file = 40K)."""
    from pantheon.utils.token_optimization import PER_TOOL_RESULT_SIZE_CHARS
    from pantheon.utils.llm import process_tool_result

    read_file_limit = PER_TOOL_RESULT_SIZE_CHARS["read_file"]
    big_content = "x" * (read_file_limit + 1000)

    # process_tool_result with tool_name="read_file" should apply 40K limit
    result = process_tool_result(big_content, max_length=50_000, tool_name="read_file")
    # Result should be truncated since content exceeds read_file's 40K limit
    assert len(result) < len(big_content)


def test_per_tool_threshold_keeps_small_result_intact(tmp_path):
    """A shell result under its 50K per-tool limit should NOT be externalized."""
    small_content = "x" * 100  # well under any limit

    messages = [
        {
            "role": "assistant",
            "content": "running shell",
            "tool_calls": [{"id": "sh-1", "function": {"name": "shell", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "sh-1", "tool_name": "shell", "content": small_content},
    ]

    mem = Memory("per-tool-small-test")
    result = apply_tool_result_budget(messages, memory=mem, base_dir=tmp_path)

    tool_msg = next(m for m in result if m.get("role") == "tool")
    assert tool_msg["content"] == small_content


def test_per_tool_threshold_unknown_tool_uses_global_limit(tmp_path):
    """An unknown tool falls back to the global per_message_limit."""
    # Content below global limit → should not be externalized
    content = "y" * 1000
    messages = [
        {
            "role": "assistant",
            "content": "custom tool call",
            "tool_calls": [{"id": "ct-1", "function": {"name": "my_custom_tool", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "ct-1", "tool_name": "my_custom_tool", "content": content},
    ]

    mem = Memory("per-tool-unknown-test")
    result = apply_tool_result_budget(
        messages, memory=mem, base_dir=tmp_path, per_message_limit=200_000
    )
    tool_msg = next(m for m in result if m.get("role") == "tool")
    assert tool_msg["content"] == content


# ---------------------------------------------------------------------------
# Opt5: forkContextMessages structured delegation tests
# ---------------------------------------------------------------------------

def test_build_structured_fork_context_uses_cache_safe_messages():
    from unittest.mock import MagicMock
    from pantheon.team.pantheon import _build_structured_fork_context

    run_context = MagicMock()
    run_context.cache_safe_prompt_messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    result = _build_structured_fork_context(run_context)

    # System message must be stripped
    assert all(m["role"] != "system" for m in result)
    assert len(result) == 2
    assert result[0]["content"] == "hello"
    assert result[1]["content"] == "hi"


def test_build_structured_fork_context_returns_none_when_no_memory():
    from unittest.mock import MagicMock
    from pantheon.team.pantheon import _build_structured_fork_context

    run_context = MagicMock()
    run_context.memory = None
    run_context.cache_safe_prompt_messages = None

    assert _build_structured_fork_context(run_context) is None


def test_build_structured_fork_context_returns_none_for_empty_messages():
    from unittest.mock import MagicMock
    from pantheon.team.pantheon import _build_structured_fork_context

    run_context = MagicMock()
    run_context.cache_safe_prompt_messages = [
        {"role": "system", "content": "sys only"},
    ]

    # Only system message → nothing to forward
    assert _build_structured_fork_context(run_context) is None


# ---------------------------------------------------------------------------
# New: empty result guard tests
# ---------------------------------------------------------------------------

def test_guard_empty_tool_results_injects_placeholder():
    from pantheon.utils.token_optimization import (
        EMPTY_TOOL_RESULT_PLACEHOLDER,
        guard_empty_tool_results,
    )
    messages = [
        {"role": "tool", "tool_call_id": "t1", "content": ""},
        {"role": "tool", "tool_call_id": "t2", "content": "   "},
        {"role": "tool", "tool_call_id": "t3", "content": "real output"},
    ]
    result = guard_empty_tool_results(messages)
    assert result[0]["content"] == EMPTY_TOOL_RESULT_PLACEHOLDER
    assert result[1]["content"] == EMPTY_TOOL_RESULT_PLACEHOLDER
    assert result[2]["content"] == "real output"


def test_empty_result_guard_runs_inside_apply_tool_result_budget(tmp_path):
    """Empty tool results get the placeholder even inside the full budget pipeline."""
    from pantheon.utils.token_optimization import EMPTY_TOOL_RESULT_PLACEHOLDER
    from pantheon.internal.memory import Memory
    memory = Memory("empty-guard-test")
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "e1", "function": {"name": "shell"}}],
        },
        {"role": "tool", "tool_call_id": "e1", "tool_name": "shell", "content": ""},
    ]
    result = apply_tool_result_budget(messages, memory=memory, base_dir=tmp_path)
    tool_msg = next(m for m in result if m.get("role") == "tool")
    assert tool_msg["content"] == EMPTY_TOOL_RESULT_PLACEHOLDER


# ---------------------------------------------------------------------------
# New: DEFAULT_MAX_RESULT_SIZE_CHARS = 50K fallback tests
# ---------------------------------------------------------------------------

def test_default_per_tool_limit_is_50k_for_unknown_tools(tmp_path):
    """Unknown tools use DEFAULT_MAX_RESULT_SIZE_CHARS (50K) as their
    per-tool limit, enforced at process_tool_result() time."""
    from pantheon.utils.token_optimization import get_per_tool_limit, DEFAULT_MAX_RESULT_SIZE_CHARS
    from pantheon.utils.llm import process_tool_result

    # Verify the limit value
    limit = get_per_tool_limit("unknown_tool", 200_000)
    assert limit == DEFAULT_MAX_RESULT_SIZE_CHARS

    # Verify process_tool_result applies it
    content = "x" * 60_000
    result = process_tool_result(content, max_length=200_000, tool_name="unknown_tool")
    # 60K > 50K default limit, so should be truncated
    assert len(result) < len(content)


def test_persistence_opt_out_prevents_externalization(tmp_path):
    """Tools in PERSISTENCE_OPT_OUT_TOOLS are never externalized."""
    from pantheon.utils import token_optimization as tok
    from pantheon.internal.memory import Memory

    original = tok.PERSISTENCE_OPT_OUT_TOOLS
    try:
        tok.PERSISTENCE_OPT_OUT_TOOLS = frozenset({"my_special_tool"})
        memory = Memory("opt-out-test")
        messages = [
            {
                "role": "assistant",
                "tool_calls": [{"id": "o1", "function": {"name": "my_special_tool"}}],
            },
            {"role": "tool", "tool_call_id": "o1", "tool_name": "my_special_tool", "content": "x" * 500_000},
        ]
        result = apply_tool_result_budget(messages, memory=memory, base_dir=tmp_path)
        tool_msg = next(m for m in result if m.get("role") == "tool")
        # Should NOT be externalized despite being 500K
        assert PERSISTED_OUTPUT_TAG not in tool_msg["content"]
        assert len(tool_msg["content"]) == 500_000
    finally:
        tok.PERSISTENCE_OPT_OUT_TOOLS = original


# ---------------------------------------------------------------------------
# New: JSON detection tests
# ---------------------------------------------------------------------------

def test_persist_json_content_uses_json_extension(tmp_path):
    from pantheon.utils.token_optimization import persist_tool_result
    json_content = '[{"key": "value"}, {"key2": 123}]'
    result = persist_tool_result(json_content, "json-tool-1", base_dir=tmp_path)
    assert result["filepath"].endswith(".json")


def test_persist_non_json_content_uses_txt_extension(tmp_path):
    from pantheon.utils.token_optimization import persist_tool_result
    result = persist_tool_result("plain text content", "txt-tool-1", base_dir=tmp_path)
    assert result["filepath"].endswith(".txt")


# ---------------------------------------------------------------------------
# New: contextCollapse tests
# ---------------------------------------------------------------------------

def test_collapse_read_search_groups_folds_consecutive_reads():
    from pantheon.utils.token_optimization import collapse_read_search_groups
    messages = [
        {"role": "user", "content": "Find the bug"},
        # Collapsible group: assistant(tool_calls) + 3 tool results with substantial content
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "g1", "function": {"name": "grep"}},
                {"id": "g2", "function": {"name": "read_file"}},
                {"id": "g3", "function": {"name": "glob"}},
            ],
        },
        {"role": "tool", "tool_call_id": "g1", "tool_name": "grep", "content": "match result " * 500},
        {"role": "tool", "tool_call_id": "g2", "tool_name": "read_file", "content": "/src/main.py\n" + "code " * 1000},
        {"role": "tool", "tool_call_id": "g3", "tool_name": "glob", "content": "file_entry\n" * 200},
        # Non-collapsible: assistant with text output
        {"role": "assistant", "content": "I found the issue in main.py"},
    ]
    result, tokens_saved = collapse_read_search_groups(messages, min_group_size=3)
    # Group of 4 (assistant + 3 tools) → collapsed to 1
    assert len(result) < len(messages)
    collapsed = [m for m in result if m.get("_collapsed")]
    assert len(collapsed) == 1
    assert "searched" in collapsed[0]["content"]
    assert "read" in collapsed[0]["content"]
    assert tokens_saved > 0


def test_collapse_preserves_non_collapsible_messages():
    from pantheon.utils.token_optimization import collapse_read_search_groups
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "I'll help you."},
        {"role": "user", "content": "Fix the bug"},
    ]
    result, tokens_saved = collapse_read_search_groups(messages)
    assert result == messages
    assert tokens_saved == 0


def test_collapse_skips_small_groups():
    from pantheon.utils.token_optimization import collapse_read_search_groups
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "s1", "function": {"name": "grep"}}],
        },
        {"role": "tool", "tool_call_id": "s1", "tool_name": "grep", "content": "x"},
    ]
    # Only 2 messages — below min_group_size=3
    result, tokens_saved = collapse_read_search_groups(messages, min_group_size=3)
    assert result == messages
    assert tokens_saved == 0


def test_collapse_breaks_on_assistant_text_output():
    from pantheon.utils.token_optimization import collapse_read_search_groups
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "a1", "function": {"name": "grep"}}],
        },
        {"role": "tool", "tool_call_id": "a1", "tool_name": "grep", "content": "x" * 1000},
        # Assistant with text — breaks the group
        {"role": "assistant", "content": "I see the results."},
        {
            "role": "assistant",
            "tool_calls": [{"id": "a2", "function": {"name": "read_file"}}],
        },
        {"role": "tool", "tool_call_id": "a2", "tool_name": "read_file", "content": "y" * 1000},
    ]
    result, tokens_saved = collapse_read_search_groups(messages, min_group_size=3)
    # No group reaches min_group_size because the text output breaks them
    assert tokens_saved == 0


def test_apply_collapses_if_needed_skips_when_context_usage_is_low(monkeypatch):
    from pantheon.utils.token_optimization import apply_collapses_if_needed

    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda model: {"max_input_tokens": 10_000},
    )
    messages = [
        {"role": "user", "content": "Find the bug"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "g1", "function": {"name": "grep"}},
                {"id": "g2", "function": {"name": "read_file"}},
                {"id": "g3", "function": {"name": "glob"}},
            ],
        },
        {"role": "tool", "tool_call_id": "g1", "tool_name": "grep", "content": "match result " * 50},
        {"role": "tool", "tool_call_id": "g2", "tool_name": "read_file", "content": "/src/main.py\n" + "code " * 100},
        {"role": "tool", "tool_call_id": "g3", "tool_name": "glob", "content": "file_entry\n" * 20},
    ]

    result, tokens_saved = apply_collapses_if_needed(messages, model="codex/gpt-5.4-mini")

    assert result == messages
    assert tokens_saved == 0


def test_apply_collapses_if_needed_commits_when_context_usage_is_high(monkeypatch):
    from pantheon.utils.token_optimization import apply_collapses_if_needed

    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda model: {"max_input_tokens": 1_000},
    )
    messages = [
        {"role": "user", "content": "Find the bug"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "g1", "function": {"name": "grep"}},
                {"id": "g2", "function": {"name": "read_file"}},
                {"id": "g3", "function": {"name": "glob"}},
            ],
        },
        {"role": "tool", "tool_call_id": "g1", "tool_name": "grep", "content": "match result " * 500},
        {"role": "tool", "tool_call_id": "g2", "tool_name": "read_file", "content": "/src/main.py\n" + "code " * 1000},
        {"role": "tool", "tool_call_id": "g3", "tool_name": "glob", "content": "file_entry\n" * 200},
    ]

    result, tokens_saved = apply_collapses_if_needed(messages, model="codex/gpt-5.4-mini")

    assert len(result) < len(messages)
    assert any(message.get("_collapsed") for message in result)
    assert tokens_saved > 0


def test_context_collapse_manager_replays_commits_across_calls(monkeypatch):
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda model: {"max_input_tokens": 1_000},
    )
    manager = ContextCollapseManager()
    messages = [
        {"role": "user", "content": "Inspect the repository"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "g1", "function": {"name": "glob"}},
                {"id": "g2", "function": {"name": "read_file"}},
                {"id": "g3", "function": {"name": "grep"}},
            ],
        },
        {"role": "tool", "tool_call_id": "g1", "tool_name": "glob", "content": "file.py\n" * 200},
        {"role": "tool", "tool_call_id": "g2", "tool_name": "read_file", "content": "code " * 1200},
        {"role": "tool", "tool_call_id": "g3", "tool_name": "grep", "content": "match " * 800},
    ]

    first = manager.applyCollapsesIfNeeded(messages, model="codex/gpt-5.4-mini")
    second = manager.applyCollapsesIfNeeded(messages, model="codex/gpt-5.4-mini")

    assert first.committed > 0
    assert second.committed == 0
    assert second.messages == first.messages
    assert manager.projectView(messages) == first.messages
    assert manager.getStats().collapsed_spans == first.committed


def test_applyCollapsesIfNeeded_uses_run_context_manager(monkeypatch):
    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda model: {"max_input_tokens": 1_000},
    )
    messages = [
        {"role": "user", "content": "Inspect the repository"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "g1", "function": {"name": "glob"}},
                {"id": "g2", "function": {"name": "read_file"}},
                {"id": "g3", "function": {"name": "grep"}},
            ],
        },
        {"role": "tool", "tool_call_id": "g1", "tool_name": "glob", "content": "file.py\n" * 200},
        {"role": "tool", "tool_call_id": "g2", "tool_name": "read_file", "content": "code " * 1200},
        {"role": "tool", "tool_call_id": "g3", "tool_name": "grep", "content": "match " * 800},
    ]
    run_context = AgentRunContext(agent=None, memory=None)
    token = _RUN_CONTEXT.set(run_context)
    try:
        result = applyCollapsesIfNeeded(messages, model="codex/gpt-5.4-mini")
        replayed = projectView(messages)
    finally:
        _RUN_CONTEXT.reset(token)

    assert result.committed > 0
    assert run_context.context_collapse_manager is not None
    assert replayed == result.messages


# ---------------------------------------------------------------------------
# New: autocompact tests
# ---------------------------------------------------------------------------

def test_autocompact_summarizes_when_over_budget():
    import asyncio
    from pantheon.utils.token_optimization import autocompact_messages
    # 20 big messages → way over 1000 token budget, no model → heuristic fallback
    messages = [{"role": "user", "content": f"msg {i}: " + "x" * 4000} for i in range(20)]
    result, freed, tracking = asyncio.run(
        autocompact_messages(messages, token_budget=1000, keep_recent=4)
    )
    assert freed > 0
    assert tracking.compacted is True
    assert tracking.consecutive_failures == 0
    # Last 4 preserved
    assert result[-1]["content"] == messages[-1]["content"]
    assert result[-4]["content"] == messages[-4]["content"]
    # First message is the autocompact summary wrapper
    assert "continued from a previous conversation" in result[0]["content"]


def test_autocompact_noop_when_under_budget():
    import asyncio
    from pantheon.utils.token_optimization import autocompact_messages
    messages = [
        {"role": "user", "content": "short"},
        {"role": "assistant", "content": "reply"},
    ]
    result, freed, tracking = asyncio.run(
        autocompact_messages(messages, token_budget=100_000, keep_recent=4)
    )
    assert result is messages
    assert freed == 0


def test_autocompact_circuit_breaker():
    """After MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES, autocompact stops trying."""
    import asyncio
    from pantheon.utils.token_optimization import (
        AutocompactTrackingState,
        MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
        autocompact_messages,
    )
    messages = [{"role": "user", "content": "x" * 40_000} for _ in range(10)]
    tracking = AutocompactTrackingState(
        consecutive_failures=MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
    )
    result, freed, new_tracking = asyncio.run(
        autocompact_messages(messages, token_budget=1000, tracking=tracking)
    )
    # Circuit breaker tripped — no compaction despite being over budget
    assert result is messages
    assert freed == 0


def test_autocompact_recursion_guard():
    """Autocompact must not fire for compact/session_memory query sources."""
    import asyncio
    from pantheon.utils.token_optimization import autocompact_messages
    messages = [{"role": "user", "content": "x" * 40_000} for _ in range(10)]
    for src in ("compact", "session_memory", "agent_summary"):
        result, freed, _ = asyncio.run(
            autocompact_messages(messages, token_budget=1000, query_source=src)
        )
        assert result is messages, f"Should skip for query_source={src}"


def test_should_autocompact_uses_model_window_and_respects_collapse_suppression(monkeypatch):
    from pantheon.utils.token_optimization import should_autocompact

    monkeypatch.setattr(
        "pantheon.utils.provider_registry.get_model_info",
        lambda model: {"max_input_tokens": 20_000},
    )
    messages = [{"role": "user", "content": "x" * 40_000}]

    assert should_autocompact(
        messages,
        model="codex/gpt-5.4-mini",
        token_budget=100_000,
    )
    assert not should_autocompact(
        messages,
        model="codex/gpt-5.4-mini",
        token_budget=100_000,
        suppress_for_context_collapse=True,
    )


# ---------------------------------------------------------------------------
# New: skip_cache_write tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# New: querySource filtering tests
# ---------------------------------------------------------------------------

def test_query_source_agent_summary_does_not_persist(tmp_path):
    """agent_summary query source must NOT write new disk entries."""
    from pantheon.internal.memory import Memory
    memory = Memory("qs-test")
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "qs1", "function": {"name": "shell"}}],
        },
        {"role": "tool", "tool_call_id": "qs1", "tool_name": "shell", "content": "x" * 100_000},
    ]
    result = apply_tool_result_budget(
        messages, memory=memory, base_dir=tmp_path, query_source="agent_summary"
    )
    tool_msg = next(m for m in result if m.get("role") == "tool")
    # Should NOT be externalized — agent_summary is a skip source
    assert PERSISTED_OUTPUT_TAG not in tool_msg["content"]


def test_query_source_main_thread_persists(tmp_path):
    """Main thread query source SHOULD persist to disk when aggregate limit
    is exceeded. Per-tool threshold enforcement is now at process_tool_result()."""
    from pantheon.internal.memory import Memory
    memory = Memory("qs-main-test")
    # Use 3 × 80K = 240K to exceed aggregate 200K limit
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "qs2", "function": {"name": "shell"}},
                {"id": "qs3", "function": {"name": "shell"}},
                {"id": "qs4", "function": {"name": "shell"}},
            ],
        },
        {"role": "tool", "tool_call_id": "qs2", "tool_name": "shell", "content": "x" * 80_000},
        {"role": "tool", "tool_call_id": "qs3", "tool_name": "shell", "content": "y" * 80_000},
        {"role": "tool", "tool_call_id": "qs4", "tool_name": "shell", "content": "z" * 80_000},
    ]
    result = apply_tool_result_budget(
        messages, memory=memory, base_dir=tmp_path, query_source="repl_main_thread"
    )
    tool_msgs = [m for m in result if m.get("role") == "tool"]
    persisted = [m for m in tool_msgs if PERSISTED_OUTPUT_TAG in m["content"]]
    assert len(persisted) >= 1


# ---------------------------------------------------------------------------
# New: session resume state reconstruction tests
# ---------------------------------------------------------------------------

def test_reconstruct_state_from_existing_messages():
    from pantheon.utils.token_optimization import reconstruct_content_replacement_state
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "tool", "tool_call_id": "r1", "content": "<persisted-output>\nOutput too large (10KB). Full output saved to: /tmp/r1.txt\n\nPreview:\nxxx\n</persisted-output>"},
        {"role": "tool", "tool_call_id": "r2", "content": "[Old tool result content cleared]"},
        {"role": "tool", "tool_call_id": "r3", "content": "normal content"},
    ]
    state = reconstruct_content_replacement_state(messages)
    assert "r1" in state.seen_ids
    assert "r1" in state.replacements  # persisted → replacement recorded
    assert "r2" in state.seen_ids  # cleared → seen
    assert "r2" not in state.replacements  # cleared → no replacement
    assert "r3" not in state.seen_ids  # normal → not tracked


# ---------------------------------------------------------------------------
# New: CC-identical token estimation tests
# ---------------------------------------------------------------------------

def test_estimate_tokens_image_block():
    from pantheon.utils.token_optimization import _estimate_message_tokens, IMAGE_MAX_TOKEN_SIZE
    msg = {"role": "user", "content": [
        {"type": "text", "text": "Look at this:"},
        {"type": "image", "source": {"data": "base64data"}},
    ]}
    tokens = _estimate_message_tokens(msg)
    # Text (~4 tokens) + IMAGE_MAX_TOKEN_SIZE (2000) + padding
    assert tokens >= IMAGE_MAX_TOKEN_SIZE


def test_estimate_tokens_tool_result_block():
    from pantheon.utils.token_optimization import _estimate_message_tokens
    msg = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 4000},
    ]}
    tokens = _estimate_message_tokens(msg)
    # 4000 chars / 4 bytes = 1000 tokens * 4/3 padding = ~1333
    assert tokens >= 1000


def test_estimate_tokens_tool_use_block():
    from pantheon.utils.token_optimization import _estimate_message_tokens
    msg = {"role": "assistant", "content": [
        {"type": "tool_use", "name": "grep", "input": {"query": "test"}},
    ]}
    tokens = _estimate_message_tokens(msg)
    assert tokens > 0


def test_inject_cache_control_skip_cache_write_marks_second_to_last():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "fork directive"},
    ]
    result = inject_cache_control_markers(messages, skip_cache_write=True)
    # With skip_cache_write, the marker goes on the SECOND-to-last user/assistant
    # which is the assistant "first answer"
    asst_content = result[2]["content"]
    assert isinstance(asst_content, list)
    assert asst_content[-1].get("cache_control") == {"type": "ephemeral"}
    # The last user message should NOT have cache_control
    last_user = result[3]["content"]
    if isinstance(last_user, list):
        assert all("cache_control" not in b for b in last_user)


def test_inject_cache_control_normal_marks_last():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "followup"},
    ]
    result = inject_cache_control_markers(messages, skip_cache_write=False)
    # Normal mode: last user/assistant gets the marker
    last_user = result[3]["content"]
    assert isinstance(last_user, list)
    assert last_user[-1].get("cache_control") == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# New: full pipeline integration test with all 5 stages
# ---------------------------------------------------------------------------

def test_apply_token_optimizations_runs_all_stages(tmp_path):
    """Integration: budget → snip → microcompact → collapse (sync 4-stage)."""
    import time
    from pantheon.utils.token_optimization import SnipConfig
    from pantheon.internal.memory import Memory

    memory = Memory("pipeline-test")
    old_ts = time.time() - 7200  # 2 hours ago

    # Build large conversation with collapsible groups
    messages = []
    for i in range(15):
        messages.append({
            "role": "assistant",
            "tool_calls": [
                {"id": f"c{i}a", "function": {"name": "grep"}},
                {"id": f"c{i}b", "function": {"name": "read_file"}},
            ],
            "timestamp": old_ts + i,
        })
        messages.append({
            "role": "tool", "tool_call_id": f"c{i}a",
            "tool_name": "grep", "content": "match " * 5000,
        })
        messages.append({
            "role": "tool", "tool_call_id": f"c{i}b",
            "tool_name": "read_file", "content": "file content " * 5000,
        })

    messages.append({"role": "user", "content": "What did you find?"})

    before = estimate_total_tokens_from_chars(messages)
    result = apply_token_optimizations(
        messages,
        memory=memory,
        base_dir=tmp_path,
        is_main_thread=True,
        snip_config=SnipConfig(enabled=True, token_budget=10_000, keep_recent=4),
    )
    after = estimate_total_tokens_from_chars(result)

    # Massive reduction from stages 1-4 combined
    assert after < before
    savings_pct = (1 - after / before) * 100
    assert savings_pct > 50, f"Expected >50% savings, got {savings_pct:.1f}%"


def test_apply_token_optimizations_async_runs_full_pipeline(tmp_path):
    """Integration: full 5-stage async pipeline with heuristic autocompact."""
    import asyncio
    import time
    from pantheon.utils.token_optimization import (
        SnipConfig,
        apply_token_optimizations_async,
    )
    from pantheon.internal.memory import Memory

    memory = Memory("async-pipeline-test")
    old_ts = time.time() - 7200

    messages = []
    for i in range(20):
        messages.append({
            "role": "assistant",
            "tool_calls": [{"id": f"d{i}", "function": {"name": "shell"}}],
            "timestamp": old_ts + i,
        })
        messages.append({
            "role": "tool", "tool_call_id": f"d{i}",
            "tool_name": "shell", "content": "output " * 5000,
        })
    messages.append({"role": "user", "content": "done?"})

    before = estimate_total_tokens_from_chars(messages)
    result, tracking = asyncio.run(
        apply_token_optimizations_async(
            messages,
            memory=memory,
            base_dir=tmp_path,
            is_main_thread=True,
            snip_config=SnipConfig(enabled=True, token_budget=5_000, keep_recent=4),
            autocompact_model=None,  # heuristic fallback
        )
    )
    after = estimate_total_tokens_from_chars(result)
    assert after < before


# ---------------------------------------------------------------------------
# Adaptation tests: Layer 2 ↔ Layer 3 integration
# ---------------------------------------------------------------------------


def test_process_tool_result_uses_per_tool_limit_for_grep():
    """process_tool_result should apply grep's 20K limit, not the 50K global."""
    from pantheon.utils.llm import process_tool_result

    content = "x" * 30_000  # > 20K (grep limit) but < 50K (global)
    result = process_tool_result(content, max_length=50_000, tool_name="grep")
    assert len(result) < 30_000, "grep output above 20K should be truncated"


def test_process_tool_result_uses_global_for_unknown_tool():
    """Unknown tools fall back to min(DEFAULT_MAX_RESULT_SIZE_CHARS, global_limit)."""
    from pantheon.utils.llm import process_tool_result

    content = "x" * 40_000  # < 50K
    result = process_tool_result(content, max_length=50_000, tool_name="my_custom_tool")
    assert len(result) == 40_000, "40K < 50K fallback, should pass through"


def test_process_tool_result_truncated_field_skips_base64_but_not_length():
    """Tools with 'truncated' field skip base64 filtering but per-tool
    length limits are ALWAYS applied (P0 fix: no more bypass)."""
    from pantheon.utils.llm import process_tool_result

    # read_file limit is 40K; content is 45K with 'truncated' flag.
    # Old behavior: trusted → pass through. New behavior: still externalized.
    result = {"content": "x" * 45_000, "truncated": True}
    output = process_tool_result(result, max_length=50_000, tool_name="read_file")
    # 45K > read_file's 40K limit → should be externalized
    assert len(output) < 45_000, "truncated field should NOT bypass per-tool limits"


def test_unified_format_recognized_by_is_already_externalized():
    """Content produced by smart_truncate_result (Layer 2) should be
    recognized by _is_already_externalized (Layer 3) to avoid double processing."""
    from pantheon.utils.token_optimization import _is_already_externalized
    from pantheon.utils.truncate import PERSISTED_OUTPUT_TAG, PERSISTED_OUTPUT_CLOSING_TAG

    # Simulate Layer 2 output
    layer2_output = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"Output too large (100.0KB). Full output saved to: /tmp/test.json\n\n"
        f"Preview (first 2.0KB):\nsome preview content\n"
        f"{PERSISTED_OUTPUT_CLOSING_TAG}"
    )
    assert _is_already_externalized(layer2_output), (
        "Layer 2's <persisted-output> format must be recognized by Layer 3"
    )


def test_stage1_skips_already_externalized_by_layer2(tmp_path):
    """If Layer 2 already externalized content, Stage 1 should not re-process it."""
    from pantheon.utils.truncate import PERSISTED_OUTPUT_TAG, PERSISTED_OUTPUT_CLOSING_TAG

    externalized_content = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"Output too large (100.0KB). Full output saved to: /tmp/test.json\n\n"
        f"Preview (first 2.0KB):\npreview\n"
        f"{PERSISTED_OUTPUT_CLOSING_TAG}"
    )
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "t1", "function": {"name": "shell"}}],
        },
        {"role": "tool", "tool_call_id": "t1", "tool_name": "shell",
         "content": externalized_content},
    ]
    mem = Memory("skip-test")
    result = apply_tool_result_budget(messages, memory=mem, base_dir=tmp_path)
    tool_msg = next(m for m in result if m.get("role") == "tool")
    # Content should be unchanged — already externalized
    assert tool_msg["content"] == externalized_content


def test_per_tool_limit_values():
    """Verify per-tool limits match the expected CC-aligned values."""
    from pantheon.utils.token_optimization import get_per_tool_limit

    assert get_per_tool_limit("grep", 200_000) == 20_000
    assert get_per_tool_limit("read_file", 200_000) == 40_000
    assert get_per_tool_limit("shell", 200_000) == 50_000
    assert get_per_tool_limit("bash", 200_000) == 50_000
    assert get_per_tool_limit("glob", 200_000) == 10_000
    assert get_per_tool_limit("web_fetch", 200_000) == 30_000
    # Unknown tool: min(DEFAULT_MAX_RESULT_SIZE_CHARS=50K, global_limit)
    assert get_per_tool_limit("unknown", 200_000) == 50_000
    assert get_per_tool_limit("unknown", 30_000) == 30_000
    # MCP-prefixed tool name normalization
    assert get_per_tool_limit("mcp__server__grep", 200_000) == 20_000


def test_full_pipeline_layer2_then_layer3(tmp_path):
    """End-to-end: Layer 2 externalizes large content, Layer 3 preserves it."""
    from pantheon.utils.llm import process_tool_result

    # Simulate Layer 2: large grep output gets externalized
    big_output = "x" * 25_000  # > grep's 20K limit
    layer2_result = process_tool_result(big_output, max_length=50_000, tool_name="grep")

    # Build message as agent.py would
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "search for pattern"},
        {
            "role": "assistant",
            "tool_calls": [{"id": "g1", "function": {"name": "grep"}}],
        },
        {"role": "tool", "tool_call_id": "g1", "tool_name": "grep",
         "content": layer2_result},
    ]

    # Layer 3: build_llm_view should preserve the externalized content
    mem = Memory("e2e-test")
    view = build_llm_view(messages, memory=mem, base_dir=tmp_path)

    tool_msg = next(m for m in view if m.get("role") == "tool")
    # Should still be externalized (not re-expanded)
    assert len(tool_msg["content"]) < 25_000
