"""A tool whose App runs on a Workspace that is still starting waits for it,
shown in the chat, instead of failing for the model to give up on
(docs/apps/standalone-apps.md, "The Agent page before the Workspace")."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pantheon import agent as agent_module
from pantheon.agent import Agent, AgentRunContext, _RUN_CONTEXT
from pantheon.agent_tools import execute_tool_calls, waiting_for_provider
from pantheon.factory import dependency_provisioner as provisioning
from pantheon.factory.dependency_provisioner import DependencyInstanceProvisioner
from pantheonos.appkit.dependency_binding_client import (PROVIDER_STARTING, DependencyProviderStarting,
                                                         RemoteDependencyBindings)
from pantheonos.appkit.dependency_client import DependencyCallError, DependencyClient
from pantheonos.appkit.runtime_config import RuntimeCredential
from pantheonos.toolset import ToolSet, tool
from test_live_dependency_bindings import fixture
from test_pantheon_agent_turn_recovery import FILES, replies


def test_the_controllers_answer_is_told_apart_from_other_failures():
    client = RemoteDependencyBindings(DependencyClient(RuntimeCredential('https://broker.apps.test/rpc', 'a' * 64)))
    args = dict(owner_ref='inst-1', operation_id='op-1', aliases=['files'])
    with patch.object(DependencyClient, 'invoke', return_value={'success': False, 'error': PROVIDER_STARTING}):
        with pytest.raises(DependencyProviderStarting):
            asyncio.run(client.bind(**args))
    with patch.object(DependencyClient, 'invoke', return_value={'success': False, 'error': 'Dependency allocation unavailable'}):
        with pytest.raises(DependencyCallError) as failed:
            asyncio.run(client.bind(**args))
        assert not isinstance(failed.value, DependencyProviderStarting)


def starting_then_ready(f, starts):
    ops = []

    async def bind(**kwargs):
        ops.append(kwargs['operation_id'])
        if starts[0]:
            starts[0] -= 1
            raise DependencyProviderStarting('The App this binding needs is not running yet')
        return await f.capability.bind(**kwargs)
    return bind, ops


@pytest.mark.asyncio
async def test_a_call_waits_for_its_workspace_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(provisioning, 'PROVIDER_POLL_SECONDS', 0.01)
    f = fixture(tmp_path, monkeypatch)
    bind, ops = starting_then_ready(f, [4])
    p = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer, profiles=FILES)
    files = p.allocated('file_manager', owner_ref='agent-views')
    replies(monkeypatch, [{'success': True, 'result': 'ran'}])
    chunks = []
    token = _RUN_CONTEXT.set(AgentRunContext(agent=None, memory=None, process_chunk=chunks.append))
    try:
        assert await files.call_tool('read', {'path': 'a'}) == 'ran'
    finally:
        _RUN_CONTEXT.reset(token)
    assert [c['activity'] for c in chunks] == ['waiting_for_workspace', 'running_tools']
    assert all(c['execution_context_id'] is None for c in chunks)
    assert len(ops) == 5 and len(set(ops)) == 5  # each ask a new operation; nothing allocated before


@pytest.mark.asyncio
async def test_a_workspace_that_never_starts_fails_the_call_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(provisioning, 'PROVIDER_POLL_SECONDS', 0.01)
    monkeypatch.setattr(provisioning, 'PROVIDER_WAIT_SECONDS', 0.1)
    f = fixture(tmp_path, monkeypatch)
    bind, _ = starting_then_ready(f, [10 ** 6])
    files = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer,
                                          profiles=FILES).allocated('file_manager', owner_ref='agent-views')
    with pytest.raises(DependencyCallError, match='did not start within') as failed:
        await files.call_tool('read', {'path': 'a'})
    assert failed.value.outcome_unknown is False


@pytest.mark.asyncio
async def test_a_probe_with_its_own_timeout_is_not_held_by_the_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(provisioning, 'PROVIDER_POLL_SECONDS', 0.05)
    f = fixture(tmp_path, monkeypatch)
    bind, _ = starting_then_ready(f, [10 ** 6])
    files = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer,
                                          profiles=FILES).allocated('file_manager', owner_ref='agent-views')
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(DependencyCallError):
        await files.ready('read', {'path': 'a'}, timeout=0.3, every=0.01)
    assert loop.time() - started < 2


class Waits(ToolSet):
    def __init__(self, seconds):
        super().__init__(name="waits")
        self.seconds = seconds

    @tool
    async def run(self, command: str) -> str:
        """Run a command once its computer is up.

        Args:
            command: The command.
        """
        async with waiting_for_provider():
            await asyncio.sleep(self.seconds)
        return "ran " + command


@pytest.mark.asyncio
async def test_the_wait_does_not_move_the_call_to_the_background(monkeypatch):
    monkeypatch.setattr(agent_module, "TOOL_FOREGROUND_SECONDS", .2)
    agent = Agent(name="a", instructions="", model="gpt-4o-mini")
    await agent.toolset(Waits(0.6))
    call = {"id": "c1", "type": "function", "function": {"name": "waits__run", "arguments": json.dumps({"command": "x"})}}
    [message] = await execute_tool_calls(agent, [call], {}, timeout=agent.tool_timeout, time_delta=.05)
    assert "moved to background" not in message["content"] and "ran x" in message["content"]
    assert not agent._bg_manager.list_tasks()


# ---- Combined check 1: a run asks, the first call checks ----

@pytest.mark.asyncio
async def test_a_prepared_probe_runs_once_before_the_next_call(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    calls = []

    def invoke(self, method, args, timeout_seconds):
        calls.append(args)
        return {'success': True, 'result': args}
    monkeypatch.setattr('pantheonos.appkit.dependency_client.DependencyClient.invoke', invoke)
    files = DependencyInstanceProvisioner(f.capability, consumer=f.consumer,
                                          profiles=FILES).allocated('file_manager', owner_ref='agent-views')
    files.prepare('read', {'path': 'probe'})
    assert files.preparing == ('read', {'path': 'probe'}) and calls == []  # nothing runs when asked
    await asyncio.gather(files.call_tool('read', {'path': 'a'}), files.call_tool('read', {'path': 'b'}))
    paths = [c.get('path') or (c.get('args') or {}).get('path') for c in calls]
    assert paths[0] == 'probe' and sorted(paths[1:]) == ['a', 'b'] and files.preparing is None
    await files.call_tool('read', {'path': 'c'})
    assert len(calls) == 4  # the probe ran once


@pytest.mark.asyncio
async def test_a_workspace_that_never_starts_fails_the_call_after_its_probe_without_running_it(tmp_path, monkeypatch):
    monkeypatch.setattr(provisioning, 'PROVIDER_POLL_SECONDS', 0.01)
    monkeypatch.setattr(provisioning, 'PROVIDER_WAIT_SECONDS', 0.1)
    monkeypatch.setattr(provisioning, 'PREPARE_SECONDS', 5.0)
    f = fixture(tmp_path, monkeypatch)
    bind, _ = starting_then_ready(f, [10 ** 6])
    files = DependencyInstanceProvisioner(SimpleNamespace(bind=bind), consumer=f.consumer,
                                          profiles=FILES).allocated('file_manager', owner_ref='agent-views')
    files.prepare('read', {'path': 'probe'})
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(provisioning.ProviderNotStarted, match='Start Workspace') as failed:
        await files.call_tool('read', {'path': 'a'})
    # Ended by the provider wait (not retried until the probe's own deadline).
    assert loop.time() - started < 1 and failed.value.outcome_unknown is False
    assert files.preparing == ('read', {'path': 'probe'})  # asked again by the next call
