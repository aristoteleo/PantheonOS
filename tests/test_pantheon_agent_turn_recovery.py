"""A Pantheon Agent conversation after its Workspace restarts, a Team Chat
turn whose conversation is busy, and the agent's folder (docs/apps/team-chat.md,
"Fixes after the first PA check")."""
import asyncio
from types import SimpleNamespace

import pytest

from pantheonos.appkit.dependency_client import DependencyCallError
from pantheon.chatroom import command_gateway
from pantheon.chatroom.command_gateway import TeamTurns
from pantheon.chatroom.runtime import WORKSPACE_STARTING, AgentRuntime
from pantheon.factory.dependency_provisioner import AllocatedToolProvider, DependencyInstanceProvisioner
from pantheon.internal.memory import Memory

from test_agent_nodes import agent_on
from test_live_dependency_bindings import fixture

FILES = {'toolsets': {'file_manager': {'alias': 'files', 'functions': [{'name': 'read',
         'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}}}]}}, 'mcp_servers': {}}


def replies(monkeypatch, answers):
    """The provider's answers, in order (an exception is raised)."""
    def invoke(self, method, args, timeout_seconds):
        reply = answers.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr('pantheonos.appkit.dependency_client.DependencyClient.invoke', invoke)


# ---- H1: the shell after a Workspace restart ----

@pytest.mark.asyncio
async def test_the_shell_is_bound_again_and_waited_for_after_its_workspace_restarted(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    ops, refuse = [], [0]

    async def bind(**kwargs):
        ops.append(kwargs['operation_id'])
        if refuse[0]:
            # The Workspace is still starting: the allocator has no provider.
            refuse[0] -= 1
            raise DependencyCallError('Dependency allocation unavailable', status=400)
        return await f.capability.bind(**kwargs)
    p = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer, profiles=FILES)
    files = p.allocated('file_manager', owner_ref='agent-views')
    replies(monkeypatch, [{'success': True, 'result': 'before'},
                          # The provider of the bound grant is gone (a new generation).
                          DependencyCallError('Dependency RPC failed; outcome may be unknown', status=502,
                                              outcome_unknown=True),
                          {'success': True, 'result': 'after'}])
    assert await files.call_tool('read', {'path': 'a'}) == 'before'
    refuse[0] = 3
    assert await files.ready('read', {'path': 'a'}, timeout=30, every=0.01) == 'after'
    # Bound again (each attempt a new operation) after the allocator said
    # "not yet" three times.
    assert len(ops) == 5 and len(set(ops)) == 5


@pytest.mark.asyncio
async def test_a_shell_that_does_not_come_back_is_reported_not_answered_without(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)

    async def bind(**kwargs):
        raise DependencyCallError('Dependency allocation unavailable', status=400)
    p = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer, profiles=FILES)
    files = p.allocated('file_manager', owner_ref='agent-views')
    with pytest.raises(DependencyCallError, match='not available yet'):
        await files.ready('read', {'path': 'a'}, timeout=0.2, every=0.01)


@pytest.mark.asyncio
async def test_an_instances_lost_allocation_is_replaced_within_the_same_call(tmp_path, monkeypatch):
    # A restarted Agent re-reads its instance's first allocation, whose
    # session the Workspace restart replaced: the allocator refuses it, and
    # the next attempt is this process's own operation, at once.
    f = fixture(tmp_path, monkeypatch)
    ops = []

    async def bind(**kwargs):
        ops.append(kwargs['operation_id'])
        if kwargs['operation_id'] == 'rev-0-1':
            raise DependencyCallError('Dependency allocation unavailable', status=400)
        return await f.capability.bind(**kwargs)
    p = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer, profiles=FILES)
    files = AllocatedToolProvider(p, 'file_manager', 'inst-1', profile=FILES['toolsets']['file_manager'],
                                  operation='rev-0')
    replies(monkeypatch, [{'success': True, 'result': 'ok'}])
    assert await files.call_tool('read', {'path': 'a'}) == 'ok'
    assert ops == ['rev-0-1', f'rev-0-{p._process}-2']


class Shell:
    """The conversation's Shell App binding."""

    def __init__(self, fail=False, bound=True):
        self.fail, self.calls, self.is_bound = fail, [], bound

    async def ready(self, name, args, *, timeout):
        self.calls.append((name, args))
        if self.fail:
            raise DependencyCallError('shell is not available yet', status=503)
        return {'success': True}


def runtime_with(shell, memory):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.check_before_chat = None
    runtime.threads = {}
    agent = SimpleNamespace(providers={'shell': shell})

    async def team_for(chat_id):
        return SimpleNamespace(team_agents=[agent])
    runtime.get_team_for_chat = team_for
    runtime.memory_manager = SimpleNamespace(get_memory=lambda chat_id, *a: memory, save_one=lambda chat_id: None)
    return runtime


@pytest.mark.asyncio
async def test_a_run_waits_for_its_shell_and_says_the_workspace_is_starting():
    memory = Memory('chat')
    shell = Shell(fail=True)
    runtime = runtime_with(shell, memory)
    out = await runtime.chat(chat_id='chat', message=[{'role': 'user', 'content': 'hi'}])
    # Not answered without tools: the reason, which Team Chat reads as a
    # Workspace still starting (it waits and answers then).
    assert out == {'success': False, 'message': WORKSPACE_STARTING}
    assert 'is not ready (' in WORKSPACE_STARTING and 'App ' in WORKSPACE_STARTING
    assert shell.calls == [('run_command', {'command': 'true', 'timeout': 15})]
    assert not (memory.extra_data or {}).get('running')
    # A plain chat whose shell was never bound binds it on its first command
    # (a turn without commands does not wait for the Workspace).
    unbound = Shell(fail=True, bound=False)
    runtime = runtime_with(unbound, memory)
    assert await runtime._prepare_shell('chat', memory) is None and unbound.calls == []


# ---- M2: the agent's folder ----

@pytest.mark.asyncio
async def test_a_turns_shell_starts_in_its_folder_on_the_agents_own_node():
    memory = Memory('chat')
    shell = Shell()
    runtime = runtime_with(shell, memory)
    leader = await agent_on()

    async def team_for(chat_id):
        return SimpleNamespace(team_agents=[leader])
    runtime.get_team_for_chat = team_for
    # The turn names its node (the Workspace, the Agent's own) and folder.
    assert (await runtime.set_chat_node('chat', node_id='ws', workdir='/workspace/agents/pix'))['success']
    assert memory.extra_data.get('chat_node') is None
    assert memory.extra_data['chat_workdir'] == '/workspace/agents/pix'
    runtime.get_team_for_chat = lambda chat_id: team_for_shell(shell)
    assert await runtime._prepare_shell('chat', memory) is None
    assert shell.calls == [('run_command', {'command': 'cd -- /workspace/agents/pix', 'timeout': 15})]
    # Working on another node, the Workspace's shell is not its to prepare.
    memory.set_metadata('chat_node', 'mac')
    assert await runtime._prepare_shell('chat', memory) is None and len(shell.calls) == 1


async def team_for_shell(shell):
    return SimpleNamespace(team_agents=[SimpleNamespace(providers={'shell': shell})])


@pytest.mark.asyncio
async def test_relative_paths_lead_into_the_folder_on_the_agents_own_node():
    agent = await agent_on()
    files = agent.providers['file_manager'].toolset
    cv = {'chat_workdir': '/workspace/agents/pix'}
    await agent.call_tool('file_manager__read_file', {'file_path': 'MEMORY.md'}, context_variables=dict(cv))
    await agent.call_tool('file_manager__read_file', {'file_path': '/etc/hostname'}, context_variables=dict(cv))
    assert files.calls == ['/workspace/agents/pix/MEMORY.md', '/etc/hostname']


# ---- M1: a Team Chat turn while its conversation runs something else ----

def busy_runtime(lease_turns):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.check_before_chat = None
    injected = []
    runtime.threads = {'c1': SimpleNamespace(inject_user_messages=injected.append)}
    runtime.command_gateway = SimpleNamespace(team=lease_turns)

    def gone(chat_id, *a):
        raise KeyError(chat_id)  # past the wait: the run itself is not under test
    runtime.memory_manager = SimpleNamespace(get_memory=gone)
    return runtime, injected


MESSAGE = [{'role': 'user', 'content': '[Team Chat] @pix: hello'}]


@pytest.mark.asyncio
async def test_a_team_chat_turns_message_is_never_queued_into_another_run():
    turns = TeamTurns()
    lease = turns.open('c1')
    runtime, injected = busy_runtime(turns)
    # The conversation runs a background task's follow-up: the turn is told,
    # nothing joins that run, and the turn's commands are not the turn's yet.
    out = await runtime.chat(chat_id='c1', message=MESSAGE)
    assert out == {'success': False, 'busy': True, 'message': command_gateway.TEAM_BUSY}
    assert injected == [] and turns.current('c1') == '' and turns.starting('c1')
    # A background task's notification is not the turn's message.
    note = [{'role': 'user', 'content': '<bg_task_notification>[done]</bg_task_notification>'}]
    assert (await runtime.chat(chat_id='c1', message=note))['success'] is False and turns.starting('c1')
    # With no Team Chat turn waiting, a message is queued into the run, as before.
    turns.close('c1', lease)
    assert await runtime.chat(chat_id='c1', message=MESSAGE) == {'success': True, 'queued': True}
    assert injected == [MESSAGE]
    # Once the other run ended, the turn's message is its own run (here the
    # run itself is not under test) and starts the turn.
    turns.open('c1')
    del runtime.threads['c1']
    assert await runtime.chat(chat_id='c1', message=MESSAGE) == {'success': False, 'message': "Chat 'c1' not found"}
