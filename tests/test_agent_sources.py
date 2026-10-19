"""Sub-agents defined elsewhere (`pantheon agent run --profile FILE`,
`--team NAME|FILE`, `--resume ID`): what defines them, what bounds them, and
how their runs are listed and continued."""
import asyncio
from types import SimpleNamespace

import pytest

from pantheon.agent import _RUN_CONTEXT, Agent, AgentRunContext
from pantheon.agent_pantheonos import AppFilter
from pantheon.chatroom import command_gateway
from pantheon.chatroom.command_gateway import CommandGateway, context_of, run_id_of
from pantheon.internal.memory import Memory
from pantheon.team.pantheon import PantheonTeam, sub_agent_source

REVIEWER = """---
id: reviewer
name: Reviewer
description: Reviews a change.
apps:
  allow: [file_manager, shell, web]
---

You review the change you are given.
"""

TEAM = """---
id: review_team
name: Review Team
type: team
mode: agent
agents:
  - lead
  - ./reviewer.md
  - worker
lead:
  id: lead
  name: Lead Reviewer
  model: normal
---

You lead a review.
"""


def test_a_run_id_is_its_context_without_shell_metacharacters():
    context = "0123abcd-9f8e7d|d1|worker|a1b2"
    assert run_id_of(context) == "0123abcd-9f8e7d.d1.worker.a1b2"
    assert context_of(run_id_of(context)) == context


def test_an_agent_defined_elsewhere_gets_no_app_its_starter_may_not_use():
    lead = AppFilter(allow=["file_manager", "shell"], deny=["web"])
    assert AppFilter.within({"allow": ["file_manager", "web", "notebook"]}, lead) == {
        "allow": ["file_manager"], "deny": ["web"]}
    assert AppFilter.within(None, lead) == {"allow": ["file_manager", "shell"], "deny": ["web"]}
    assert AppFilter.within({"deny": ["shell"]}, AppFilter()) == {"deny": ["shell"]}
    assert AppFilter.within({"allow": ["web"]}, None) == {"allow": ["web"]}


def room():
    from pantheon.chatroom.room import ChatRoom
    from pantheon.factory.template_manager import get_template_manager
    chatroom = ChatRoom.__new__(ChatRoom)
    chatroom.template_manager = get_template_manager()
    return chatroom


def test_an_agent_file_is_a_team_of_one_in_its_starters_mode():
    config = room()._source_team_config({"profile_file": {"path": "reviewer.md", "text": REVIEWER}}, "agent")
    assert [a.name for a in config.agents] == ["Reviewer"] and config.mode == "agent"
    assert config.agents[0].instructions.startswith("You review")
    with pytest.raises(ValueError, match="team file"):
        room()._source_team_config({"profile_file": {"path": "t.md", "text": TEAM}}, "agent")


def test_a_team_file_resolves_its_members_from_the_files_sent_and_the_library():
    source = {"team_file": {"path": "/w/team.md", "text": TEAM, "refs": {"./reviewer.md": REVIEWER}}}
    config = room()._source_team_config(source, "agent")
    assert [a.name for a in config.agents] == ["Lead Reviewer", "Reviewer", "Worker"]
    assert config.mode == "agent"
    with pytest.raises(ValueError, match="could not read"):
        room()._source_team_config({"team_file": {"path": "/w/team.md", "text": TEAM}}, "agent")
    with pytest.raises(ValueError, match="not a team file"):
        room()._source_team_config({"team_file": {"path": "r.md", "text": REVIEWER}}, "agent")
    assert room()._source_team_config({"team": "default"}, "agent").agents[0].name == "Leader"
    with pytest.raises(ValueError, match="No team template"):
        room()._source_team_config({"team": "no_such_team"}, "agent")


async def lead_and_team():
    lead = Agent(name="Leader", instructions="Lead", model="gpt-4o-mini")
    worker = Agent(name="Worker", instructions="Work", model="gpt-4o-mini", description="Does work")
    team = PantheonTeam([lead, worker], mode="agent")
    await team.async_setup()
    return team, lead, worker


@pytest.mark.asyncio
async def test_call_agent_runs_a_given_agent_reports_its_context_and_records_its_source(monkeypatch):
    team, lead, worker = await lead_and_team()
    reviewer = Agent(name="Reviewer", instructions="Review", model="gpt-4o-mini")
    ran = {}

    async def child(target, task, *, execution_context_id, **kwargs):
        ran["agent"] = target
        return SimpleNamespace(content="Looks right.")
    monkeypatch.setattr("pantheon.team.subagent_process.run_in_child_process", child)
    memory = Memory(name="chat")
    started = asyncio.get_running_loop().create_future()
    source = {"profile_file": {"path": "reviewer.md", "text": REVIEWER}}
    token = _RUN_CONTEXT.set(AgentRunContext(agent=lead, memory=memory))
    try:
        answer = await lead.call_tool("call_agent", {"agent_name": "Reviewer", "instruction": "Review it."},
                                      {"chat_id": "chat", "_command_parent": True, "_sub_agent_process": True,
                                       "_sub_agent_target": reviewer, "_sub_agent_source": source,
                                       "_sub_agent_started": started}, "run_cmd_1")
    finally:
        _RUN_CONTEXT.reset(token)
    assert answer == "Looks right." and ran["agent"] is reviewer
    context = started.result()
    assert context.split("|")[1:3] == ["d1", "reviewer"]
    assert sub_agent_source(memory, context) == source


@pytest.mark.asyncio
async def test_a_sub_teams_members_runs_are_recorded_in_the_conversation(monkeypatch):
    team, lead, worker = await lead_and_team()
    conversation = Memory(name="conversation")
    team._source = {"team": "review_team"}
    team._conversation_memory = conversation

    async def child(target, task, **kwargs):
        return SimpleNamespace(content="done")
    monkeypatch.setattr("pantheon.team.subagent_process.run_in_child_process", child)
    started = asyncio.get_running_loop().create_future()
    token = _RUN_CONTEXT.set(AgentRunContext(agent=lead, memory=Memory(name="leader's own")))
    try:
        await lead.call_tool("call_agent", {"agent_name": "Worker", "instruction": "Check."},
                             {"chat_id": "chat", "_command_parent": True, "_sub_agent_process": True,
                              "_sub_agent_started": started}, "run_cmd_2")
    finally:
        _RUN_CONTEXT.reset(token)
    assert sub_agent_source(conversation, started.result()) == {"team": "review_team", "profile": "Worker"}


class Grant:
    async def _current(self):
        credential = SimpleNamespace(endpoint="https://agent.apps.example/rpc", key="k" * 64)
        return SimpleNamespace(_client=SimpleNamespace(credential=credential))


def gateway_for(lead, team, room_):
    async def team_for_chat(chat_id):
        return team
    gateway = CommandGateway(lambda: Grant(), team_for_chat, room=room_)
    token = gateway.mint(chat_id="c1", agent=lead.name, tool_call_id="t1")
    gateway._live["t1"] = command_gateway.LiveCall(lead, "c1", "t1", AgentRunContext(agent=lead, memory=None),
                                                   {"chat_id": "c1"})
    return gateway, token


@pytest.mark.asyncio
async def test_a_command_starts_an_agent_file_or_a_team_through_the_chatroom():
    team, lead, worker = await lead_and_team()
    reviewer = Agent(name="Reviewer", instructions="Review", model="gpt-4o-mini")
    sub_leader = Agent(name="Lead Reviewer", instructions="Lead", model="gpt-4o-mini")
    built, called = [], []

    class Room:
        async def sub_team(self, chat_id, source, caller=None):
            built.append((chat_id, source, caller))
            return SimpleNamespace(team_agents=[reviewer] if "profile_file" in source else [sub_leader, reviewer])

        sub_team_member = staticmethod(lambda team, profile: next(
            (a for a in team.team_agents if a.name == profile), None) if profile else team.team_agents[0])

    async def call_agent(agent_name: str, instruction: str, context_variables: dict | None = None):
        """Delegate.

        Args:
            agent_name: Profile.
            instruction: Task.
        """
        called.append((agent_name, context_variables.get("_sub_agent_target"),
                       context_variables.get("_sub_agent_source")))
        context_variables["_sub_agent_started"].set_result(f"c1-0abc|d1|x|{len(called):04x}")
        return "ok"
    lead._base_functions["call_agent"] = call_agent
    gateway, token = gateway_for(lead, team, Room())
    file = {"path": "/w/reviewer.md", "text": REVIEWER}

    started = await gateway.handle(token, {"op": "agent.start", "params": {"profile_file": file, "task": "Review."}})
    assert started["profile"] == "Reviewer"
    assert called[-1] == ("Reviewer", reviewer, {"profile_file": file})
    assert built[-1][2] is lead
    # A team: its leader; with --profile, its member.
    await gateway.handle(token, {"op": "agent.start", "params": {"team": "review_team", "task": "Review."}})
    assert called[-1] == ("Lead Reviewer", sub_leader, {"team": "review_team"})
    await gateway.handle(token, {"op": "agent.start", "params": {"team": "review_team", "profile": "Reviewer",
                                                                 "task": "Review."}})
    assert called[-1] == ("Reviewer", reviewer, {"team": "review_team", "profile": "Reviewer"})
    with pytest.raises(ValueError, match="names a member"):
        await gateway.handle(token, {"op": "agent.start", "params": {"team": "t", "profile_file": file, "task": "x"}})
    with pytest.raises(ValueError, match="agent file"):
        await gateway.handle(token, {"op": "agent.start", "params": {"profile": "Nobody", "task": "x"}})


@pytest.mark.asyncio
async def test_a_command_lists_the_runs_and_resumes_one():
    team, lead, worker = await lead_and_team()
    memory = Memory(name="c1")
    done, working = "c1-0abc|d1|worker|0001", "c1-0abc|d1|worker|0002"
    memory.add_messages([
        {"role": "user", "content": "Count the cells.", "execution_context_id": done},
        {"role": "assistant", "content": "120 cells.", "agent_name": "Worker", "execution_context_id": done},
        {"role": "user", "content": "Sleep a while.", "execution_context_id": working},
        {"role": "assistant", "content": "", "agent_name": "Worker", "execution_context_id": working,
         "tool_calls": [{"id": "s1", "type": "function", "function": {"name": "shell__run_command", "arguments": "{}"}}]},
    ])
    continued = []

    class Room:
        memory_manager = SimpleNamespace(get_memory=lambda chat_id: memory)

        def command_continue(self, chat_id, context, message):
            continued.append((context, message[0]["content"], message[0]["execution_context_id"]))
            if context == working:
                return None

            async def run():
                return SimpleNamespace(content="Of those, 40 are T cells.")
            return asyncio.get_running_loop().create_task(run())
    from pantheon.chatroom.thread import SteerQueue
    lead._steer_queue = SteerQueue()
    lead._steer_queue.open_run(working)
    gateway, token = gateway_for(lead, team, Room())

    runs = (await gateway.handle(token, {"op": "agent.runs", "params": {}}))["runs"]
    assert [(r["run_id"], r["profile"], r["status"], r["task"]) for r in runs] == [
        (run_id_of(done), "Worker", "done", "Count the cells."),
        (run_id_of(working), "Worker", "working", "Sleep a while.")]

    resumed = await gateway.handle(token, {"op": "agent.start", "params": {
        "resume": run_id_of(done), "task": "How many are T cells?"}})
    assert resumed == {"run_id": run_id_of(done), "resumed": True, "queued": False}
    answer = await gateway.handle(token, {"op": "agent.wait", "params": {"run_id": run_id_of(done)}})
    assert answer["answer"] == "Of those, 40 are T cells."
    assert continued[0] == (done, "How many are T cells?", done)
    # A working run reads it at its next turn.
    queued = await gateway.handle(token, {"op": "agent.start", "params": {"resume": run_id_of(working), "task": "Hurry."}})
    assert queued["queued"] is True
    with pytest.raises(ValueError, match="as it is"):
        await gateway.handle(token, {"op": "agent.start", "params": {"resume": run_id_of(done), "profile": "Worker",
                                                                     "task": "x"}})
    with pytest.raises(ValueError, match="No sub-agent run"):
        await gateway.handle(token, {"op": "agent.start", "params": {"resume": "c1-0abc.d1.worker.ffff", "task": "x"}})


@pytest.mark.asyncio
async def test_a_run_of_an_agent_built_elsewhere_continues_with_the_agent_its_source_builds():
    from pantheon.chatroom.thread import Thread
    team, lead, worker = await lead_and_team()
    reviewer = Agent(name="Reviewer", instructions="Review", model="gpt-4o-mini")
    seen = []

    async def scripted(history, tool_use=True, process_chunk=None, model=None, context_variables=None,
                       response_format=None):
        seen.append([m.get("content") for m in history[1:]])
        return {"role": "assistant", "content": "Also fix the test."}
    reviewer._acompletion_with_models = scripted
    context = "c1-0abc|d1|reviewer|0001"
    memory = Memory(name="c1")
    memory.add_messages([
        {"role": "user", "content": "Review it.", "execution_context_id": context},
        {"role": "assistant", "content": "One bug.", "agent_name": "Reviewer", "execution_context_id": context}])
    asked = []

    async def agent_for(ctx):
        asked.append(ctx)
        return reviewer
    thread = Thread(team, memory, [{"role": "user", "content": "Anything else?", "execution_context_id": context}],
                    sub_agent=context, agent_for=agent_for)
    await thread.run()
    assert thread.response["response"] == "Also fix the test." and asked == [context]
    assert seen[0] == ["Review it.", "One bug.", "Anything else?"]
