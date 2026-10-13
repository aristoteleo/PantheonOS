"""One context mechanism: the model's view, compaction, capped tool outputs
(docs/agent-core.md)."""
import asyncio

import pytest

from pantheon.agent import Agent
from pantheon.agent_context import (
    COMPACTION_ROLE,
    compact,
    estimate_total,
    find_cut,
    model_view,
    needs_compaction,
    trim_to_view,
)


def turn(i, size=400):
    text = f"message {i} " + "x" * size
    return [{"id": f"u{i}", "role": "user", "content": text},
            {"id": f"a{i}", "role": "assistant", "content": None,
             "tool_calls": [{"id": f"c{i}", "type": "function",
                             "function": {"name": "shell__run_command", "arguments": "{}"}}]},
            {"id": f"t{i}", "role": "tool", "tool_call_id": f"c{i}", "content": text},
            {"id": f"r{i}", "role": "assistant", "content": f"done {i}"}]


def conversation(n, size=400):
    return [m for i in range(n) for m in turn(i, size)]


def test_view_starts_at_the_latest_compaction_and_keeps_its_recent_turns():
    history = [{"role": "system", "content": "sys"}, *conversation(6)]
    record = {"id": "c1", "role": COMPACTION_ROLE, "summary": "S1", "first_kept_id": "u4"}
    later = turn(9)
    view = model_view([*history, record, *later])
    assert view[0] == {"role": "system", "content": "sys"}
    assert view[1]["role"] == "user" and "S1" in view[1]["content"] and view[1]["_compaction"]
    assert [m["id"] for m in view[2:]] == [m["id"] for m in [*turn(4), *turn(5), *later]]
    # trim_to_view keeps only what the view is built from.
    assert model_view(trim_to_view([*history, record, *later])) == view


def test_a_compaction_written_before_first_kept_ids_keeps_what_follows_it():
    old = {"role": COMPACTION_ROLE, "content": "{{ CHECKPOINT 1 }} summary"}
    history = [*conversation(2), old, *turn(5)]
    view = model_view(history)
    assert view[0]["content"] == "{{ CHECKPOINT 1 }} summary" and view[0]["role"] == "user"
    assert [m["id"] for m in view[1:]] == [m["id"] for m in turn(5)]


def test_the_cut_never_separates_tool_results_from_their_call():
    messages = conversation(10)
    for keep in (50, 300, 700, 1500):
        cut = find_cut(messages, keep)
        assert 0 < cut < len(messages)
        assert messages[cut]["role"] != "tool"
    assert find_cut(turn(0), 10**6) == 0  # everything fits: nothing to cut


@pytest.mark.asyncio
async def test_compaction_summarizes_the_older_part_and_updates_a_previous_summary():
    prompts = []

    async def complete(messages):
        prompts.append(messages)
        return f"summary {len(prompts)}"

    view = [{"role": "system", "content": "sys"}, *conversation(10, size=2000)]
    first = await compact(view, complete, keep_tokens=1500)
    assert first["role"] == COMPACTION_ROLE and first["summary"] == "summary 1"
    assert first["first_kept_id"] in {m.get("id") for m in view}
    assert first["_metadata"]["tokens_after"] < first["_metadata"]["tokens_before"]
    assert "## Goal" in prompts[0][-1]["content"]

    second = await compact(model_view([*view, first, *conversation(6, size=2000)]), complete, keep_tokens=1500)
    assert second["summary"] == "summary 2"
    # The earlier summary is part of what the update summarizes.
    assert "summary 1" in prompts[1][1]["content"] and "updated summary" in prompts[1][-1]["content"]

    assert await compact(turn(0), complete, keep_tokens=10**6) is None  # nothing to cut
    assert await compact(conversation(3), complete, keep_tokens=100) is None  # too little to summarize


def test_compaction_triggers_before_the_window_fills():
    messages = conversation(10, size=4000)
    total = estimate_total(messages)
    assert needs_compaction(messages, window=total)
    assert not needs_compaction(messages, window=total * 3)
    assert needs_compaction(messages, window=total * 3, overhead=total * 2)


class _Recorder:
    def __init__(self):
        self.steps = []

    async def __call__(self, message):
        self.steps.append(message)


async def _agent(monkeypatch, window, replies):
    agent = Agent("leader", "Test", model="openai/gpt-4o-mini", use_memory=False)
    monkeypatch.setattr("pantheon.agent_context.context_window", lambda *a, **k: window)
    summaries = []

    def completer(model, **kwargs):
        async def complete(messages):
            summaries.append(messages)
            return "the summary"
        return complete
    monkeypatch.setattr("pantheon.agent_context.model_completer", completer)
    seen = []

    async def acompletion(history, model, **kwargs):
        seen.append(model_view(history))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"role": "assistant", "content": reply}
    agent._acompletion = acompletion
    return agent, seen, summaries


@pytest.mark.asyncio
async def test_the_loop_compacts_a_conversation_that_would_not_fit(monkeypatch):
    agent, seen, summaries = await _agent(monkeypatch, window=30_000, replies=["ok"])
    history = conversation(30, size=2000)
    steps = _Recorder()
    response = await agent.run([*history, {"role": "user", "content": "next"}], use_memory=False,
                               process_step_message=steps)
    assert response.content == "ok"
    records = [m for m in steps.steps if m.get("role") == COMPACTION_ROLE]
    assert len(records) == 1 and summaries
    # The model saw the summary and the recent turns, within its window.
    assert "the summary" in seen[0][1]["content"]
    assert estimate_total(seen[0]) < 30_000


@pytest.mark.asyncio
async def test_a_prompt_the_provider_rejects_as_too_long_is_compacted_and_retried(monkeypatch):
    agent, seen, summaries = await _agent(
        monkeypatch, window=10**7,
        replies=[RuntimeError("HTTP 400: prompt is too long: 210000 tokens > 200000 maximum"), "ok"])
    steps = _Recorder()
    response = await agent.run([*conversation(10, size=2000), {"role": "user", "content": "next"}],
                               use_memory=False, process_step_message=steps)
    assert response.content == "ok"
    assert len(seen) == 2 and "the summary" in seen[1][1]["content"]
    assert [m["role"] for m in steps.steps].count(COMPACTION_ROLE) == 1


@pytest.mark.asyncio
async def test_saved_tool_outputs_are_read_on_the_agents_node(monkeypatch, tmp_path):
    from pantheon.agent_tools import tool_output_dir

    agent = Agent("leader", "Test", model="openai/gpt-4o-mini")
    monkeypatch.setattr("pantheon.agent_tools.tool_output_dir", lambda a: tmp_path)
    saved = tmp_path / "tool_output_abc.txt"
    saved.write_text("\n".join(f"line {i}" for i in range(1, 101)))
    reply = await agent.call_tool("file_manager__read_file",
                                  {"file_path": str(saved), "start_line": 10, "end_line": 12})
    assert reply == {"success": True, "content": "line 10\nline 11\nline 12", "total_lines": 100,
                     "start_line": 10, "end_line": 12}
    assert tool_output_dir  # imported for the patch target
    with pytest.raises(ValueError):  # any other path goes to the Files App (absent here)
        await agent.call_tool("file_manager__read_file", {"file_path": str(tmp_path.parent / "x.txt")})
    await asyncio.sleep(0)
