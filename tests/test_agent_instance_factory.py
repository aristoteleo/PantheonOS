"""Instance assembly through actual Agent/Team and HTTPS dependency clients.

The HTTPS endpoint models already-issued grants. Provisioning, gateway policy,
remote Shell processes and model inference have separate acceptance gates.
"""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential, load_runtime_configuration
from pantheon.agent import AgentRunContext, _RUN_CONTEXT
from pantheon.chatroom.environment import AgentEnvironment
from pantheon.chatroom.lifecycle import AgentLifetime
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instances import AgentInstanceBinding, AgentInstanceFactory, config_revision
from pantheon.factory.models import TeamConfig
from pantheon.team import PantheonTeam
from test_agent_dependency_bindings import (CONFIG, FUNCTION, configured_bindings, endpoint,
                                           forbid_ambient_tools, provider)


IDS = ('10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000002')
RECIPE = {**CONFIG, 'toolsets': ['shell']}


def configuration(endpoint):
    instances = {}
    credentials = {}
    for n, identity in enumerate(IDS):
        credentials[f'shell_{n}'] = RuntimeCredential(endpoint.url, 'ab'[n] * 64)
        instances[identity] = dict(conversation_id=f'chat-{n}', config_id='same-config',
            config_revision=config_revision(RECIPE), mcp_servers={}, toolsets={'shell': {
                'credential': f'shell_{n}', 'functions': copy.deepcopy([FUNCTION]),
                'owner_ref': identity, 'timeout_seconds': 2}})
    return RuntimeConfiguration({'agent_instances': {'protocol': 1, 'instances': instances}},
                                credentials, 'deployment', 'c' * 64, 3, 'backend')


def factory(endpoint):
    return AgentInstanceFactory.from_runtime_configuration(configuration(endpoint), tls_context=endpoint.tls)


@pytest.mark.asyncio
async def test_file_snapshot_same_config_two_instances_real_agent_calls(endpoint, configured_bindings):
    path, body = configured_bindings
    snapshot = configuration(endpoint)
    body['values'] = snapshot.values
    body['credentials'] = {name: {'endpoint': value.endpoint, 'key': value.key}
                           for name, value in snapshot.credentials.items()}
    path.write_text(json.dumps(body))
    loaded = load_runtime_configuration(required=True)
    f = AgentInstanceFactory.from_runtime_configuration(loaded, tls_context=endpoint.tls)
    try:
        groups = await asyncio.gather(*(f({'same-config': RECIPE}, conversation_id=f'chat-{n}')
                                        for n in range(2)))
        agents = [group[0] for group in groups]
        assert [str(a.id) for a in agents] == list(IDS)
        for n, agent in enumerate(agents):
            assert (await f({'same-config': RECIPE}, conversation_id=f'chat-{n}'))[0] is agent
            assert agent._instance_identity == dict(instance_id=IDS[n], conversation_id=f'chat-{n}',
                config_id='same-config', config_revision=config_revision(RECIPE))
        results = await asyncio.gather(*(a.call_tool('shell__execute', {'command': 'pwd'}) for a in agents))
        assert [r['session'] for r in results] == ['session-a', 'session-b']
        assert not {'session_id', 'owner_ref'} & set(endpoint.calls[0][2]['args'])
        # Loading does not consume/mutate the immutable owner snapshot.
        assert loaded.values['agent_instances']['instances'][IDS[0]]['toolsets']['shell']['owner_ref'] == IDS[0]
        assert 'a' * 64 not in repr(f._instances[IDS[0]])
    finally:
        await f.shutdown()


@pytest.mark.parametrize('change', ['owner', 'credential_alias', 'owned_shared', 'protocol', 'identity', 'unknown_field'])
def test_invalid_snapshot_cannot_alias_owned_resources(endpoint, change):
    c = configuration(endpoint)
    entries = c.values['agent_instances']['instances']
    tool = entries[IDS[1]]['toolsets']['shell']
    if change == 'owner':
        tool['owner_ref'] = IDS[0]
    elif change in ('credential_alias', 'owned_shared'):
        c.credentials['shell_1'] = c.credentials['shell_0']
        if change == 'owned_shared':
            tool['owner_ref'] = None
    elif change == 'protocol':
        c.values['agent_instances']['protocol'] = True
    elif change == 'identity':
        entries['not-an-instance'] = entries.pop(IDS[1])
    else:
        tool['endpoint'] = 'https://escape.invalid/rpc'
    with pytest.raises(ValueError, match='invalid or incomplete') as exc:
        AgentInstanceFactory.from_runtime_configuration(c, tls_context=endpoint.tls)
    assert 'a' * 64 not in str(exc.value) and not endpoint.calls


@pytest.mark.asyncio
async def test_shared_service_has_separately_owned_clients(endpoint):
    c = configuration(endpoint)
    # Model a stateless shared interface using the fixture's single RPC.
    for entry in c.values['agent_instances']['instances'].values():
        entry['toolsets']['shell']['owner_ref'] = None
        entry['toolsets']['shell']['credential'] = 'shell_0'
    f = AgentInstanceFactory.from_runtime_configuration(c, tls_context=endpoint.tls)
    try:
        a = (await f({'same-config': RECIPE}, conversation_id='chat-0'))[0]
        b = (await f({'same-config': RECIPE}, conversation_id='chat-1'))[0]
        assert a.providers['shell'] is not b.providers['shell']
        await a.providers['shell'].shutdown()
        assert (await b.call_tool('shell__execute', {'command': 'read'}))['session'] == 'session-a'
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_unknown_conversation_config_change_and_template_authority_rejected(endpoint):
    f = factory(endpoint)
    try:
        for configs, chat in [({'same-config': RECIPE}, None), ({'same-config': RECIPE}, 'unknown'),
                              ({}, 'chat-0'), ({'same-config': {**RECIPE, 'model': 'other'}}, 'chat-0'),
                              ({'same-config': {**RECIPE, 'instance_id': IDS[1]}}, 'chat-0')]:
            with pytest.raises(ValueError):
                await f(configs, conversation_id=chat)
        assert not f._agents and not endpoint.calls
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_singleflight_snapshots_input_before_await(endpoint, monkeypatch):
    import pantheon.factory as module
    f = factory(endpoint)
    original, entered, release = module.create_agent, asyncio.Event(), asyncio.Event()
    calls = []

    async def delayed(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(module, 'create_agent', delayed)
    configs = {'same-config': copy.deepcopy(RECIPE)}
    first = asyncio.create_task(f(configs, conversation_id='chat-0'))
    await entered.wait()
    second = asyncio.create_task(f(configs, conversation_id='chat-0'))
    await asyncio.sleep(0)
    configs['same-config']['toolsets'].append('untrusted')
    configs['same-config']['instructions'] = 'changed while awaiting'
    release.set()
    try:
        a, b = await asyncio.gather(first, second)
        assert a[0] is b[0] and len(calls) == 1
        assert a[0].instructions == RECIPE['instructions']
        assert list(a[0].providers) == ['shell']
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_shutdown_drains_real_inflight_call_and_unused_clients(endpoint):
    f = factory(endpoint)
    a = (await f({'same-config': RECIPE}, conversation_id='chat-0'))[0]
    endpoint.hold = True
    call = asyncio.create_task(a.call_tool('shell__execute', {'command': 'write'}))
    assert await asyncio.to_thread(endpoint.entered.wait, 2)
    closing = asyncio.create_task(f.shutdown())
    try:
        await asyncio.sleep(.02)
        assert not closing.done()
        with pytest.raises(RuntimeError, match='stopping'):
            await f({'same-config': RECIPE}, conversation_id='chat-1')
        endpoint.release.set()
        assert (await call)['session'] == 'session-a'
        await closing
        for binding in f._instances.values():
            with pytest.raises(RuntimeError, match='closed'):
                await binding.tools.toolsets['shell'].call_tool('execute', {'command': 'late'})
        assert len(endpoint.calls) == 1
    finally:
        endpoint.release.set()
        await asyncio.gather(call, closing, return_exceptions=True)
        await f.shutdown()


@pytest.mark.asyncio
async def test_actual_runtime_team_assembly_carries_identity_and_cleanup(endpoint, tmp_path):
    f, ensured = factory(endpoint), []

    async def ensure(kind, names):
        ensured.append((kind, names))

    runtime = AgentRuntime.__new__(AgentRuntime)
    templates = SimpleNamespace(prepare_team=lambda _: ({'same-config': RECIPE}, ['shell'], []))
    runtime.template_manager = templates
    runtime._environment = AgentEnvironment(projects=None, templates=templates, settings=lambda: None,
        ensure_services=ensure, create_agents=f, validate_model=lambda _: (True, ''), close_agents=f.shutdown)
    runtime._ensure_plugins = AsyncMock(return_value=[])
    runtime._project_dir_for_chat = AsyncMock(return_value=str(tmp_path))
    runtime.chat_teams = {}
    runtime._default_team = None
    try:
        for n in range(2):
            runtime.chat_teams[f'chat-{n}'] = await runtime._create_team_from_template(
                TeamConfig(id='template', name='Test', description='', icon=''), f'chat-{n}')
        # get_team_for_chat uses the real populated team cache.
        for n in range(2):
            result = await runtime.get_agents(f'chat-{n}')
            assert result['success']
            assert result['agents'][0]['instance']['instance_id'] == IDS[n]
            assert 'a' * 64 not in json.dumps(result)
            a = runtime.chat_teams[f'chat-{n}'].team_agents[0]
            assert (await a.call_tool('shell__execute', {'command': 'pwd'}))['session'] == f'session-{"ab"[n]}'
        assert ensured == [('mcp', []), ('toolset', ['shell'])] * 2
    finally:
        # Exercise the actual lifecycle hook without a fully booted server.
        lifetime = AgentLifetime()
        lifetime.chat_teams = runtime.chat_teams
        lifetime._environment = runtime._environment
        await lifetime.cleanup()
    with pytest.raises(RuntimeError, match='stopping'):
        await f({'same-config': RECIPE}, conversation_id='chat-0')


def test_duplicate_identity_or_shared_client_object_rejected(endpoint):
    p = provider(endpoint)
    binding = AgentInstanceBinding(IDS[0], 'chat-0', 'same-config', config_revision(RECIPE), AgentToolBindings({'shell': p}))
    with pytest.raises(ValueError, match='unique'):
        AgentInstanceFactory([binding, binding])
    second = AgentInstanceBinding(IDS[1], 'chat-1', 'same-config', config_revision(RECIPE), binding.tools)
    with pytest.raises(ValueError, match='client objects'):
        AgentInstanceFactory([binding, second])


@pytest.mark.asyncio
async def test_duplicate_member_names_fail_before_team_can_drop_resources(endpoint):
    c = configuration(endpoint)
    second = c.values['agent_instances']['instances'][IDS[1]]
    second['conversation_id'] = 'chat-0'
    second['config_id'] = 'other-config'
    f = AgentInstanceFactory.from_runtime_configuration(c, tls_context=endpoint.tls)
    try:
        with pytest.raises(ValueError, match='names must be distinct'):
            await f({'same-config': RECIPE, 'other-config': RECIPE}, conversation_id='chat-0')
        assert not f._agents and not endpoint.calls
    finally:
        await f.shutdown()


@pytest.mark.asyncio
async def test_stop_waits_for_accepted_assembly_and_blocks_queued_creation(endpoint, monkeypatch):
    import pantheon.factory as module
    f = factory(endpoint)
    original, entered, release = module.create_agent, asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(module, 'create_agent', delayed)
    creating = asyncio.create_task(f({'same-config': RECIPE}, conversation_id='chat-0'))
    await entered.wait()
    queued = asyncio.create_task(f({'same-config': RECIPE}, conversation_id='chat-0'))
    await asyncio.sleep(0)
    closing = asyncio.create_task(f.shutdown())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    try:
        a = (await creating)[0]
        with pytest.raises(RuntimeError, match='stopping'):
            await queued
        await closing
        with pytest.raises(RuntimeError, match='closed'):
            await a.call_tool('shell__execute', {'command': 'late'})
        assert not endpoint.calls
    finally:
        release.set()
        await asyncio.gather(creating, queued, closing, return_exceptions=True)
        await f.shutdown()


@pytest.mark.asyncio
async def test_lifecycle_closes_clients_when_no_team_was_ever_published(endpoint, monkeypatch):
    import pantheon.factory as module
    f = factory(endpoint)
    monkeypatch.setattr(module, 'create_agent', AsyncMock(side_effect=RuntimeError('assembly failed')))
    with pytest.raises(RuntimeError, match='assembly failed'):
        await f({'same-config': RECIPE}, conversation_id='chat-0')
    lifetime = AgentLifetime()
    lifetime._environment = SimpleNamespace(close_agents=f.shutdown)
    await lifetime.cleanup()
    await lifetime.cleanup()
    assert not f._agents
    for binding in f._instances.values():
        with pytest.raises(RuntimeError, match='closed'):
            await binding.tools.toolsets['shell'].call_tool('execute', {'command': 'late'})
    assert not endpoint.calls


@pytest.mark.asyncio
async def test_delegation_keeps_target_instance_binding_across_runs(endpoint, monkeypatch):
    c = configuration(endpoint)
    child_recipe = {**RECIPE, 'name': 'Worker'}
    child = c.values['agent_instances']['instances'][IDS[1]]
    child.update(conversation_id='chat-0', config_id='worker', config_revision=config_revision(child_recipe))
    f = AgentInstanceFactory.from_runtime_configuration(c, tls_context=endpoint.tls)
    agents = await f({'same-config': RECIPE, 'worker': child_recipe}, conversation_id='chat-0')
    team = PantheonTeam(agents=agents, use_summary=False, plugins=[])
    await team.async_setup()
    executions = []

    async def worker_run(message, **kwargs):
        # Inference is replaced, but Team dispatch and the target's HTTPS tool
        # transport are real. Each delegated Run must retain its target owner.
        executions.append(kwargs['context_variables']['execution_context_id'])
        result = await agents[1].call_tool('shell__execute', {'command': message})
        return SimpleNamespace(content=json.dumps(result))

    monkeypatch.setattr(agents[1], 'run', worker_run)
    token = _RUN_CONTEXT.set(AgentRunContext(agent=agents[0], memory=None))
    try:
        for command in ('first', 'second'):
            result = await agents[0].functions['call_agent'](agent_name='worker', instruction=command)
            assert json.loads(result) == {'session': 'session-b', 'command': command}
        assert len(set(executions)) == 2
        assert str(agents[1].id) == IDS[1]
        assert (await agents[0].call_tool('shell__execute', {'command': 'parent'}))['session'] == 'session-a'
    finally:
        _RUN_CONTEXT.reset(token)
        await f.shutdown()
