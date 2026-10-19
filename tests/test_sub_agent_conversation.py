"""The user talks to the sub-agents of a conversation (the Team view): a
running one reads their messages and can be stopped alone, a finished one
continues its work, and the lead hears of it at its next turn."""
import asyncio

import pytest

from pantheon.agent import _RUN_CONTEXT, Agent, AgentRunContext
from pantheon.chatroom.thread import SteerQueue, Thread, take_sub_agent_notes
from pantheon.internal.memory import Memory
from pantheonos.utils.misc import run_func
from pantheon.team.pantheon import SUB_AGENT_ENDED, SUB_AGENT_STOPPED, PantheonTeam

CONTEXT = "chat-1234|d1|worker|beef"


def user(text, **extra):
    return {"role": "user", "content": text, **extra}


async def team_of(*replies_per_agent):
    """A lead and a worker whose models answer from scripts."""
    lead = Agent(name="Leader", instructions="Lead", model="gpt-4o-mini")
    worker = Agent(name="Worker", instructions="Work", model="gpt-4o-mini", description="Does work")
    team = PantheonTeam([lead, worker], mode="agent")
    await team.async_setup()
    for agent, replies in zip((lead, worker), replies_per_agent):
        agent.seen = []

        async def scripted(history, tool_use=True, process_chunk=None, model=None, context_variables=None,
                           response_format=None, _agent=agent, _replies=list(replies)):
            _agent.seen.append([dict(m) for m in history])
            reply = _replies.pop(0)
            if callable(reply):
                reply = await reply()
            return {"role": "assistant", "content": reply}
        agent._acompletion_with_models = scripted
    return team, lead, worker


def test_a_sub_agent_queue_reads_its_own_messages_and_the_conversations_only_when_shared():
    conversation = SteerQueue()
    shared = conversation.open_run("a", shared=True)
    own = conversation.open_run("b")
    conversation.put([user("to the lead", id="1")])
    own.put([user("to b", id="2")])
    assert len(own) == 1 and len(shared) == 1
    assert [m["id"] for m in conversation.peek()] == ["1", "2"]
    assert conversation.update("2", "to b, edited") and own.peek()[0]["content"] == "to b, edited"
    assert [m["id"] for m in shared.drain()] == ["1"]
    assert conversation.remove("2") and len(own) == 0


def test_messages_a_sub_agent_did_not_read_continue_it_unless_the_user_stopped_it():
    conversation = SteerQueue()
    unread = []
    conversation.on_unread = lambda context, messages: unread.append((context, messages))
    conversation.open_run("a").put([user("late")])
    conversation.close_run("a")
    stopped = conversation.open_run("b")
    stopped.put([user("dropped")])
    stopped.stopped = True
    conversation.close_run("b")
    assert unread == [("a", [user("late")])]
    assert conversation.run_queue("a") is None


async def call_worker(lead, steer, instruction="Count the cells."):
    memory = Memory(name="chat")
    lead._steer_queue = steer
    token = _RUN_CONTEXT.set(AgentRunContext(agent=lead, memory=memory))
    try:
        return await lead.call_tool("call_agent", {"agent_name": "Worker", "instruction": instruction},
                                    {"chat_id": "chat"}, "call_1")
    finally:
        _RUN_CONTEXT.reset(token)


@pytest.mark.asyncio
async def test_a_running_sub_agent_reads_the_message_the_user_sent_it():
    steer = SteerQueue()

    async def first():
        (context,) = steer._runs
        steer.run_queue(context).put([user("Count only T cells.")])
        return "There are 120 cells."
    team, lead, worker = await team_of([], [first, "There are 40 T cells."])
    worker._steer_queue = steer

    answer = await call_worker(lead, steer)

    assert answer == "There are 40 T cells."
    assert "Count only T cells." in str(worker.seen[1][-1]["content"])
    assert not steer._runs


@pytest.mark.asyncio
async def test_the_user_stops_one_sub_agent_and_the_lead_gets_its_work_so_far():
    steer = SteerQueue()

    async def forever():
        (context,) = steer._runs
        steer.run_queue(context).stopped = True
        await asyncio.sleep(3600)
    team, lead, worker = await team_of([], [forever])
    worker._steer_queue = steer

    answer = await asyncio.wait_for(call_worker(lead, steer), timeout=5)

    assert answer.startswith(SUB_AGENT_STOPPED)
    # The lead must not take it for a limit to work around.
    assert "on purpose" in answer and "not a time limit" in answer


def finished_run(memory):
    """A conversation in which the lead had the worker count cells."""
    memory.add_messages([
        user("Count the cells.", id="u1"),
        {"role": "assistant", "id": "a1", "agent_name": "Leader", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "call_agent", "arguments": '{"agent_name": "Worker"}'}}]},
        user("Count the cells in data.h5ad.", id="w1", execution_context_id=CONTEXT,
             _metadata={"chain_path": ["Leader", "Worker:call_1"]}),
        {"role": "assistant", "id": "w2", "agent_name": "Worker", "content": "There are 120 cells.",
         "execution_context_id": CONTEXT},
        {"role": "tool", "id": "t1", "tool_call_id": "call_1", "tool_name": "call_agent",
         "content": "There are 120 cells.", "execution_context_id": CONTEXT},
        {"role": "assistant", "id": "a2", "agent_name": "Leader", "content": "The worker found 120 cells."},
    ])


@pytest.mark.asyncio
async def test_a_finished_sub_agent_continues_its_own_conversation_and_the_lead_hears_of_it():
    team, lead, worker = await team_of(["Noted."], ["Of those, 40 are T cells."])
    memory = Memory(name="chat")
    finished_run(memory)
    steps = []

    async def shown(message):
        steps.append(message)
    thread = Thread(team, memory, [user("How many are T cells?", id="u2", execution_context_id=CONTEXT)],
                    sub_agent=CONTEXT)
    thread.add_step_message_hook(shown)
    await thread.run()

    assert thread.response["success"] and thread.response["response"] == "Of those, 40 are T cells."
    seen = worker.seen[0]
    assert [m.get("content") for m in seen[1:]] == [
        "Count the cells in data.h5ad.", "There are 120 cells.", "How many are T cells?"]
    assert not lead.seen
    new = memory.get_messages(CONTEXT, for_llm=False)[-2:]
    assert [m["role"] for m in new] == ["user", "assistant"]
    assert all(m["execution_context_id"] == CONTEXT for m in new)
    assert memory.get_messages(None, for_llm=False)[-1]["id"] == "a2"

    note = take_sub_agent_notes(memory)
    assert "Worker" in note and "How many are T cells?" in note and "40 are T cells" in note
    assert take_sub_agent_notes(memory) is None


@pytest.mark.asyncio
async def test_a_finished_sub_agent_messaged_during_the_leads_run_continues_beside_it():
    release = asyncio.Event()

    async def lead_works():
        await release.wait()
        return "Done."
    team, lead, worker = await team_of([lead_works], ["Of those, 40 are T cells."])
    memory = Memory(name="chat")
    finished_run(memory)
    thread = Thread(team, memory, [user("Write the report.")])
    for agent in team.agents.values():
        agent._steer_queue = thread.steer_queue
    running = asyncio.create_task(thread.run())
    while not lead.seen:
        await asyncio.sleep(0.01)

    assert thread.message_sub_agent(CONTEXT, [user("How many are T cells?", execution_context_id=CONTEXT)]) == {
        "success": True}
    while not worker.seen:
        await asyncio.sleep(0.01)
    release.set()
    await asyncio.wait_for(running, timeout=5)

    assert thread.response["response"] == "Done."
    assert memory.get_messages(CONTEXT, for_llm=False)[-1]["content"] == "Of those, 40 are T cells."
    assert "How many are T cells?" in take_sub_agent_notes(memory)


@pytest.mark.asyncio
async def test_a_conversation_without_that_sub_agent_refuses_the_message():
    team, lead, worker = await team_of([], [])
    memory = Memory(name="chat")
    with pytest.raises(ValueError, match="No sub-agent run"):
        team.sub_agent_of(memory, CONTEXT)
    finished_run(memory)
    assert team.sub_agent_of(memory, CONTEXT) is worker


@pytest.mark.asyncio
async def test_a_command_started_sub_agent_reads_only_its_own_messages_and_can_be_stopped(monkeypatch):
    team, lead, worker = await team_of([], [])
    steer = SteerQueue()
    worker._steer_queue = steer
    seen = {}

    async def child(target, task, *, steer, process_step_message, **kwargs):
        seen["shared"] = steer._conversation is not None
        await process_step_message({"role": "assistant", "content": "", "tool_calls": [
            {"id": "sleep_1", "type": "function", "function": {"name": "shell__run_command", "arguments": "{}"}}]})
        steer.stopped = True
        return type("Response", (), {"content": "half done"})()
    monkeypatch.setattr("pantheon.team.subagent_process.run_in_child_process", child)
    memory = Memory(name="chat")
    steps = []

    async def shown(message):
        steps.append(message)
    token = _RUN_CONTEXT.set(AgentRunContext(agent=lead, memory=memory, process_step_message=shown))
    try:
        answer = await lead.call_tool("call_agent", {"agent_name": "Worker", "instruction": "Wait."},
                                      {"chat_id": "chat", "_command_parent": True, "_sub_agent_process": True},
                                      "run_cmd_1")
    finally:
        _RUN_CONTEXT.reset(token)
    # The lead may work on while a command's sub-agent runs: its messages stay the lead's.
    assert seen["shared"] is False
    assert answer.startswith(SUB_AGENT_STOPPED) and "half done" in answer
    # The call the stop cut short is closed in the run's history, when it stopped.
    closed = steps[-1]
    assert closed["role"] == "tool" and closed["tool_call_id"] == "sleep_1"
    assert closed["content"] == SUB_AGENT_STOPPED and closed["timestamp"]
    assert closed["execution_context_id"] == steps[0]["execution_context_id"]


@pytest.mark.asyncio
async def test_a_sub_agent_run_that_fails_mid_call_closes_the_call(monkeypatch):
    team, lead, worker = await team_of([], [])
    worker._steer_queue = SteerQueue()

    async def child(target, task, *, steer, process_step_message, **kwargs):
        await process_step_message({"role": "assistant", "content": "", "tool_calls": [
            {"id": "sleep_1", "type": "function", "function": {"name": "shell__run_command", "arguments": "{}"}}]})
        raise RuntimeError("the child process died")
    monkeypatch.setattr("pantheon.team.subagent_process.run_in_child_process", child)
    steps = []

    async def shown(message):
        steps.append(message)
    token = _RUN_CONTEXT.set(AgentRunContext(agent=lead, memory=Memory(name="chat"), process_step_message=shown))
    try:
        with pytest.raises(RuntimeError):
            await lead.call_tool("call_agent", {"agent_name": "Worker", "instruction": "Wait."},
                                 {"chat_id": "chat", "_command_parent": True, "_sub_agent_process": True},
                                 "run_cmd_2")
    finally:
        _RUN_CONTEXT.reset(token)
    assert steps[-1]["role"] == "tool" and steps[-1]["tool_call_id"] == "sleep_1"
    assert steps[-1]["content"] == SUB_AGENT_ENDED


NEW = "chat-1234|d1|worker|cafe"


@pytest.mark.asyncio
async def test_the_user_starts_a_run_of_a_profile_and_the_lead_hears_of_it():
    team, lead, worker = await team_of([], ["There are 120 cells."])
    memory = Memory(name="chat")
    finished_run(memory)
    thread = Thread(team, memory, [user("Count the cells.", execution_context_id=NEW)],
                    sub_agent=NEW, sub_agent_profile="Worker")
    await thread.run()

    assert thread.response["response"] == "There are 120 cells."
    # A new run: the worker reads only its task.
    assert [m.get("content") for m in worker.seen[0][1:]] == ["Count the cells."]
    assert [m["role"] for m in memory.get_messages(NEW, for_llm=False)] == ["user", "assistant"]
    note = take_sub_agent_notes(memory)
    assert "The user started Worker" in note and "120 cells" in note


@pytest.mark.asyncio
async def test_a_new_run_needs_a_new_context_and_a_sub_agent_profile():
    team, lead, worker = await team_of([], [])
    memory = Memory(name="chat")
    finished_run(memory)
    with pytest.raises(ValueError, match="exists already"):
        await team.continue_sub_agent(CONTEXT, [user("Again.")], memory, profile="Worker")
    with pytest.raises(ValueError, match="not a sub-agent profile"):
        await team.continue_sub_agent(NEW, [user("Lead.")], memory, profile="Leader")


def chatroom_with(memory, team, thread=None):
    from types import SimpleNamespace
    from pantheon.chatroom.room import ChatRoom

    room = ChatRoom.__new__(ChatRoom)
    room.check_before_chat = None
    room.threads = {"chat": thread} if thread is not None else {}
    room.memory_manager = SimpleNamespace(get_memory=lambda chat_id, auto_fix=False: memory,
                                          save_one=lambda chat_id: None)

    async def team_for(chat_id, save_to_memory=True):
        return team
    room.get_team_for_chat = team_for
    return room


@pytest.mark.asyncio
async def test_a_running_conversation_starts_the_users_run_beside_the_lead():
    from pantheon.chatroom.room import ChatRoom

    team, lead, worker = await team_of([], [])
    memory = Memory(name="chat")
    finished_run(memory)
    sent = []

    class Running:
        def message_sub_agent(self, context, message, profile=None):
            sent.append((context, profile, message[0]["execution_context_id"]))
            return {"success": True}
    room = chatroom_with(memory, team, Running())

    assert (await ChatRoom.chat(room, "chat", [user("Count.")], sub_agent=NEW, profile="worker"))["success"]
    assert sent == [(NEW, "Worker", NEW)]
    for bad in (CONTEXT, "chat-1234|d1|researcher|cafe", "chat-1234|d2|worker|cafe"):
        assert not (await ChatRoom.chat(room, "chat", [user("Count.")], sub_agent=bad, profile="Worker"))["success"]
    assert not (await ChatRoom.chat(room, "chat", [user("Lead.")], sub_agent=NEW, profile="Leader"))["success"]


@pytest.mark.asyncio
async def test_the_user_closes_a_run_stopping_it_and_a_message_opens_it_again():
    from pantheon.chatroom.room import ChatRoom

    team, lead, worker = await team_of([], [])
    memory = Memory(name="chat")
    finished_run(memory)
    stopped, sent = [], []

    class Running:
        def stop_sub_agent(self, context):
            stopped.append(context)
            return True

        def message_sub_agent(self, context, message, profile=None):
            sent.append(context)
            return {"success": True}
    room = chatroom_with(memory, team, Running())

    assert (await ChatRoom.close_sub_agent(room, "chat", CONTEXT))["closed"] == [CONTEXT]
    assert stopped == [CONTEXT]
    assert (await ChatRoom.get_sub_agents(room, "chat"))["closed"] == [CONTEXT]
    assert not (await ChatRoom.close_sub_agent(room, "chat", NEW))["success"]
    await ChatRoom.chat(room, "chat", [user("And T cells?")], sub_agent=CONTEXT)
    assert sent == [CONTEXT] and (await ChatRoom.get_sub_agents(room, "chat"))["closed"] == []
    await ChatRoom.close_sub_agent(room, "chat", CONTEXT)
    assert (await ChatRoom.close_sub_agent(room, "chat", CONTEXT, closed=False))["closed"] == []


@pytest.mark.asyncio
async def test_stopping_a_continued_run_mid_call_keeps_the_call_and_closes_it():
    started = asyncio.Event()

    async def wait_long() -> str:
        """Wait a long time."""
        started.set()
        await asyncio.sleep(3600)
        return "never"
    team, lead, worker = await team_of([], [])
    await run_func(worker.tool, wait_long)

    async def scripted(history, tool_use=True, process_chunk=None, model=None, context_variables=None,
                       response_format=None):
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": "wait_1", "type": "function", "function": {"name": "wait_long", "arguments": "{}"}}]}
    worker._acompletion_with_models = scripted
    memory = Memory(name="chat")
    finished_run(memory)
    thread = Thread(team, memory, [user("Wait.", execution_context_id=CONTEXT)], sub_agent=CONTEXT)
    running = asyncio.create_task(thread.run())
    await asyncio.wait_for(started.wait(), timeout=5)
    assert thread.stop_sub_agent(CONTEXT)
    await asyncio.wait_for(running, timeout=10)

    run = memory.get_messages(CONTEXT, for_llm=False)
    call = next(m for m in run if m.get("tool_calls"))
    assert call["tool_calls"][0]["id"] == "wait_1"
    closed = run[run.index(call) + 1]
    assert closed["role"] == "tool" and closed["tool_call_id"] == "wait_1"
    assert closed["content"] == SUB_AGENT_STOPPED
