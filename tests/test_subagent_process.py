"""A sub-agent's loop in its own process (docs/pantheon-cli.md): the Agent App
answers its model, tool and compaction calls and relays its steps."""
import asyncio
import json
import os

import pytest

from pantheon.team.subagent_process import child_environment, run_in_child_process


class Target:
    """The sub-agent's Agent in the Agent App: model and tools are here."""

    def __init__(self, replies, *, block=False):
        self.name, self.instructions, self.description = "Worker", "You are a worker.", "Works"
        self.models = ["fake-model"]
        self.replies = list(replies)
        self.seen, self.tools = [], []
        self.block = block

    async def _acompletion_with_models(self, history, tool_use=True, process_chunk=None, model=None,
                                       context_variables=None, response_format=None):
        self.seen.append({"history": history, "model": model, "context": context_variables})
        if self.block:
            await asyncio.sleep(3600)
        return self.replies.pop(0)

    async def call_tool(self, name, args, context_variables=None, tool_call_id=None):
        self.tools.append((name, args, tool_call_id, context_variables))
        return {"success": True, "output": "hi from " + args["command"]}


def tool_call(command):
    return {"role": "assistant", "content": "", "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "shell__run_command", "arguments": json.dumps({"command": command})}}]}


@pytest.mark.asyncio
async def test_the_child_runs_the_loop_and_the_agent_app_runs_model_and_tools():
    target = Target([tool_call("pantheon apps list"), {"role": "assistant", "content": "There are 3 Apps."}])
    steps = []

    async def relay(message):
        steps.append(message)
    response = await run_in_child_process(
        target, "Count the Apps.", process_step_message=relay, process_chunk=None,
        execution_context_id="chat|d1|worker|ab", context_variables={"chat_id": "chat"}, model="leader-model")
    assert response.content == "There are 3 Apps."
    # The model saw the system prompt, the task, then the tool result.
    first, second = target.seen
    assert first["history"][0]["role"] == "system" and "You are a worker." in first["history"][0]["content"]
    assert any(m.get("content") == "Count the Apps." for m in first["history"])
    assert first["model"] == "leader-model" and first["context"] == {"chat_id": "chat"}
    assert any(m.get("role") == "tool" and "hi from pantheon apps list" in str(m.get("content")) for m in second["history"])
    # The tool ran here, with the run's context and its call id.
    assert target.tools == [("shell__run_command", {"command": "pantheon apps list"}, "call_1", {"chat_id": "chat"})]
    # Every step came back to the conversation, in the sub-agent's context.
    assert [m["role"] for m in steps if m["role"] in ("assistant", "tool")] == ["assistant", "tool", "assistant"]
    assert all(m.get("execution_context_id") == "chat|d1|worker|ab" for m in steps if m["role"] != "user")


@pytest.mark.asyncio
async def test_stopping_the_run_ends_the_child():
    target = Target([], block=True)
    task = asyncio.create_task(run_in_child_process(
        target, "Wait.", process_step_message=None, process_chunk=None,
        execution_context_id="chat|d1|worker|cd", context_variables={}))
    for _ in range(200):
        if target.seen:
            break
        await asyncio.sleep(0.05)
    assert target.seen, "the child asked for a model call"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 15)


@pytest.mark.asyncio
async def test_a_message_the_user_sends_the_child_is_read_at_its_next_turn():
    from pantheon.chatroom.thread import SteerQueue
    conversation = SteerQueue()
    steer = conversation.open_run("chat|d1|worker|ef")
    target = Target([tool_call("sleep 2"), {"role": "assistant", "content": "Slept, and here is uname: Linux."}])

    async def tool(name, args, context_variables=None, tool_call_id=None):
        steer.put([{"role": "user", "content": "Also run uname.", "id": "m1"}])
        while len(steer):
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        return {"success": True, "output": ""}
    target.call_tool = tool
    steps = []

    async def relay(message):
        steps.append(message)
    response = await asyncio.wait_for(run_in_child_process(
        target, "Sleep.", process_step_message=relay, process_chunk=None,
        execution_context_id="chat|d1|worker|ef", context_variables={}, steer=steer), 30)
    assert response.content == "Slept, and here is uname: Linux."
    assert "Also run uname." in str(target.seen[1]["history"][-1]["content"])
    # Shown in the conversation as read (clears its Queued mark).
    assert any(m.get("id") == "m1" and m.get("_steer_drained") for m in steps)


@pytest.mark.asyncio
async def test_the_user_stops_the_child_alone():
    from pantheon.chatroom.thread import SteerQueue
    steer = SteerQueue().open_run("chat|d1|worker|gh")
    target = Target([], block=True)
    run = asyncio.create_task(run_in_child_process(
        target, "Wait.", process_step_message=None, process_chunk=None,
        execution_context_id="chat|d1|worker|gh", context_variables={}, steer=steer))
    for _ in range(200):
        if target.seen:
            break
        await asyncio.sleep(0.05)
    steer.stopped = True
    response = await asyncio.wait_for(run, 15)
    assert response.content == ""


def test_the_child_gets_no_credentials(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "s")
    monkeypatch.setenv("PANTHEON_APP_CONFIG", "c")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    env = child_environment("/tmp/home")
    assert env["HOME"] == "/tmp/home" and "PATH" in env
    assert not {"MODAL_TOKEN_SECRET", "PANTHEON_APP_CONFIG", "OPENAI_API_KEY"} & set(env)
    assert os.pathsep in env["PYTHONPATH"] or env["PYTHONPATH"]


@pytest.mark.asyncio
async def test_a_command_started_sub_agent_runs_in_its_own_process_under_the_command():
    from pantheon.agent import _RUN_CONTEXT, Agent, AgentRunContext
    from pantheon.internal.memory import Memory
    from pantheon.team.pantheon import PantheonTeam
    leader = Agent(name="Leader", instructions="Lead", model="gpt-4o-mini")
    worker = Agent(name="Worker", instructions="Work", model="gpt-4o-mini", description="Does work")
    team = PantheonTeam([leader, worker], mode="agent")
    await team.async_setup()
    pids = []

    async def scripted(history, tool_use=True, process_chunk=None, model=None, context_variables=None,
                       response_format=None):
        pids.append(os.getpid())
        return {"role": "assistant", "content": "done in a child"}
    worker._acompletion_with_models = scripted
    steps = []

    async def relay(message):
        steps.append(message)
    token = _RUN_CONTEXT.set(AgentRunContext(agent=leader, memory=Memory(name="chat"), process_step_message=relay))
    try:
        answer = await leader.call_tool("call_agent", {"agent_name": "Worker", "instruction": "Do it."},
                                        {"chat_id": "chat", "_command_parent": True, "_sub_agent_process": True},
                                        "run_cmd_1")
    finally:
        _RUN_CONTEXT.reset(token)
    assert answer == "done in a child"
    final = [m for m in steps if m.get("role") == "assistant"][-1]
    assert final["parent_tool_call_id"] == "run_cmd_1" and "|d1|" in final["execution_context_id"]
    assert final["_metadata"]["chain_path"]
