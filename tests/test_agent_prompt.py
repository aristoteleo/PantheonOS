"""Prompt assembly: cache stability, cache markers, delegation context, capped tool results."""
from __future__ import annotations


from pantheon.agent import Agent, AgentRunContext, _RUN_CONTEXT
from pantheon.internal.memory import Memory
from pantheon.settings import Settings
from pantheon.team.pantheon import (
    PantheonTeam,
    _get_cache_safe_child_fork_context_messages,
    _get_cache_safe_child_run_overrides,
    create_delegation_task_message,
)
from pantheon.agent_model import build_cache_safe_runtime_params
from pantheon.models.messages import inject_cache_control_markers, is_anthropic_model
from pantheon.team.pantheon import build_delegation_context_message


def _build_tool_message(tool_call_id: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "tool_name": "shell",
        "content": content,
    }


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
        "pantheon.team.pantheon.build_delegation_context_message",
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


def test_pantheon_team_delegation_defaults(tmp_path):
    """Settings default to direct, non-fork delegation."""
    settings = Settings(work_dir=tmp_path)
    settings.user_home = tmp_path / ".missing-home"
    delegation = settings.get_section("delegation")

    assert delegation.get("fork_context", False) is False


# ---------------------------------------------------------------------------
# Opt3: cache_control injection tests
# ---------------------------------------------------------------------------

def test_is_anthropic_model_detection():
    assert is_anthropic_model("claude-3-5-sonnet-20241022") is True
    assert is_anthropic_model("anthropic/claude-3-haiku") is True
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

def test_tool_results_have_one_cap_with_the_full_output_in_a_file(tmp_path):
    """Any tool's result above the cap (characters or lines) is replaced by
    its start and the path of a file with the full output."""
    import re
    from pathlib import Path
    from pantheon.utils.llm import process_tool_result
    from pantheon.utils.truncate import MAX_OUTPUT_LINES

    small = process_tool_result({"content": "x" * 100, "truncated": True}, max_length=50_000,
                                tool_name="file_manager__read_file", temp_dir=str(tmp_path))
    assert "Full output saved to" not in small

    for result in ({"content": "x" * 60_000, "truncated": True},       # a tool that truncates itself
                   {"output": "\n".join(str(i) for i in range(5000))},   # command output: too many lines
                   "\n".join(f"line {i}" for i in range(MAX_OUTPUT_LINES + 10)),  # too many lines
                   "y" * 60_000):                                        # too many characters
        output = process_tool_result(result, max_length=50_000, tool_name="shell__run_command",
                                     temp_dir=str(tmp_path))
        assert len(output) < 50_000
        path = Path(re.search(r"saved to: (\S+)", output).group(1))
        full = path.read_text()
        # The bulk text of a result is saved as text, so it reads by lines.
        assert full == (max(result.values(), key=lambda v: len(str(v))) if isinstance(result, dict) else result)
