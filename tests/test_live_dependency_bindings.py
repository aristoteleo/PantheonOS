"""Live assembly and scoped Agent provisioning, with simulated control services.

Gateway/native-App transport is exercised separately by the Go wire integration.
These tests inject allocation/issuance loss and inspect exact resource ownership.
"""
import asyncio
import copy
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.live_dependencies import LiveDependencyOwner, ScopedDependencyBindings
from pantheon.factory.dependency_provisioner import DependencyInstanceProvisioner
from pantheon.factory.instance_store import AgentInstanceStore
from pantheon.factory.provisioned_instances import ProvisionedAgentInstanceFactory
from pantheon.platform.fleet_api import FleetAPI
from test_resource_session_owner import fixture as session_fixture
from test_agent_model_scope import scopes
from test_agent_dependency_bindings import forbid_ambient_tools
from test_agent_instance_factory import RECIPE


def fixture(tmp_path, monkeypatch):
    sessions, lifecycle, resource, states, receipts, clock = session_fixture(tmp_path, monkeypatch)
    consumer, provider = resource['consumer'], resource['provider']
    shell = copy.deepcopy(lifecycle.manifest.return_value['manifest'])
    files_provider = {**provider, 'instance_id': 'files', 'revision': 'c' * 64}
    states['provider-node']['instances']['files'] = dict(app_id='files', digest='c'*64, generation=3, state='ready')
    manifests = {
        consumer['revision']: {'apiVersion': 2, 'id': 'consumer', 'dependencies': {
            'shell': {'range': '^0.6.0', 'uses': ['shell@1']},
            'files': {'range': '^1.0.0', 'uses': ['fs@1']}}},
        provider['revision']: shell,
        files_provider['revision']: {'apiVersion': 2, 'id': 'files', 'version': '1.0.0', 'provides': {
            'interfaces': [{'name': 'fs', 'tools': ['read']}],
            'tools': [{'name': 'read', 'params': [{'name': 'path'}, {'name': 'workspace'}]}]}}
    }
    async def manifest(node, revision):
        return {'manifest': copy.deepcopy(manifests[revision])}
    lifecycle.manifest.side_effect = manifest
    bindings = {
        'shell': {'app_id': 'shell', 'provider': provider,
            'methods': {'run_command': {'arguments': ['command', 'timeout'], 'bound': {}}},
            'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}},
        'files': {'app_id': 'files', 'provider': files_provider,
            'methods': {'read': {'arguments': ['path'], 'bound': {'workspace': 'project-a'}}}}
    }
    issued = {}
    authority = SimpleNamespace()
    def issue(body):
        key = body['operation_id']
        if key not in issued:
            token = hashlib.sha256(key.encode()).hexdigest()
            p = body['provider']
            host = hashlib.sha256(f"{p['instance_id']}:backend:http:{p['generation']}".encode()).hexdigest()[:32]
            issued[key] = dict(endpoint=f'https://{host}.apps.test/rpc', access_token=token,
                grant_id=hashlib.sha256(token.encode()).hexdigest(), expires=int(time.time())+899,
                consumer={**body['consumer'], 'fleet_id': 'owner'}, provider={**p, 'fleet_id': 'owner'})
        return copy.deepcopy(issued[key])
    authority.issue = AsyncMock(side_effect=issue)
    async def renew(grant_id, ttl_seconds):
        value = next(v for v in issued.values() if v['grant_id'] == grant_id)
        value['expires'] = int(time.time())+900
        return {k: value[k] for k in ('grant_id', 'consumer', 'provider', 'expires')}
    authority.renew = AsyncMock(side_effect=renew)
    authority.revoke = AsyncMock()
    owner = LiveDependencyOwner(lifecycle, tmp_path/'bindings', sessions, authority)
    capability = ScopedDependencyBindings(owner, consumer=consumer, bindings=bindings)
    return SimpleNamespace(owner=owner, capability=capability, bindings=bindings, consumer=consumer,
        states=states, receipts=receipts, clock=clock, sessions=sessions, lifecycle=lifecycle,
        authority=authority, issued=issued, manifests=manifests)


def request(owner='instance-one', operation='revision-one', aliases=None):
    return dict(owner_ref=owner, operation_id=operation, aliases=aliases or ['shell', 'files'])


@pytest.mark.asyncio
@pytest.mark.parametrize('binding_phase', [None, 'runtime'])
async def test_distinct_instances_keep_sessions_across_revisions_and_share_files(tmp_path, monkeypatch, binding_phase):
    f = fixture(tmp_path, monkeypatch)
    if binding_phase:
        for dependency in f.manifests[f.consumer['revision']]['dependencies'].values():
            dependency['binding'] = binding_phase
    first = await f.capability.bind(**request())
    second = await f.capability.bind(**request(owner='instance-two', operation='other'))
    revised = await f.capability.bind(**request(operation='revision-two'))
    shell_calls = [c.args[0] for c in f.authority.issue.await_args_list if c.args[0]['app_id'] == 'shell']
    session_ids = [c['methods']['run_command']['bound']['shell_id'] for c in shell_calls]
    assert session_ids[0] == session_ids[2] != session_ids[1]
    assert len(f.receipts) == 2
    files = [c.args[0] for c in f.authority.issue.await_args_list if c.args[0]['app_id'] == 'files']
    assert len(files) == 3 and all(c['methods']['read']['bound'] == {'workspace': 'project-a'} for c in files)
    assert first['bindings']['shell']['access_token'] != revised['bindings']['shell']['access_token']
    assert first['consumer'] == second['consumer']
    # Durable receipts/recipes never contain bearer keys.
    for path in f.owner.root.glob('*.json'):
        assert 'access_token' not in path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['session', 'grant'])
async def test_lost_ack_fresh_owner_recovers_same_session_and_grant(tmp_path, monkeypatch, stage):
    f = fixture(tmp_path, monkeypatch)
    target = f.lifecycle.resource_session if stage == 'session' else f.authority.issue
    original = target.side_effect
    async def lost(*args):
        value = original(*args)
        if asyncio.iscoroutine(value):
            await value
        target.side_effect = original
        raise TimeoutError('lost accepted operation')
    target.side_effect = lost
    with pytest.raises(TimeoutError):
        await f.capability.bind(**request())
    owner = LiveDependencyOwner(f.lifecycle, f.owner.root, f.sessions, f.authority)
    cap = ScopedDependencyBindings(owner, consumer=f.consumer, bindings=f.bindings)
    value = await cap.bind(**request())
    again = await cap.bind(**request())
    assert value == again and len(f.receipts) == 1 and len(f.issued) == 2
    assert len({r['session_id'] for r in f.receipts.values()}) == 1


@pytest.mark.asyncio
async def test_live_maintenance_renews_and_replacement_revokes(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    before = await f.capability.bind(**request())
    path = next(f.owner.root.glob('*.json'))
    record = json.loads(path.read_text())
    for receipt in record['renewals'].values():
        receipt['expires'] = int(time.time()) + 100
    path.write_text(json.dumps(record))
    assert (await f.owner.reconcile_once())['renewed'] == 2
    assert len(f.issued) == 2
    after = await f.capability.bind(**request())
    assert after['bindings']['shell']['access_token'] == before['bindings']['shell']['access_token']
    f.states['consumer-node']['instances']['consumer']['generation'] += 1
    assert (await f.owner.reconcile_once())['revoked'] == 2
    with pytest.raises(AssemblyError, match='live consumer'):
        await f.capability.bind(**request(operation='never-issued'))
    assert len(f.issued) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['alias', 'method', 'argument', 'interface', 'version', 'consumer', 'owner', 'generation'])
async def test_invalid_policy_has_no_resource_side_effect(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    arguments = request()
    if change == 'alias':
        arguments['aliases'] = ['unapproved']
    elif change == 'method':
        f.bindings['files']['methods'] = {'delete': {'arguments': [], 'bound': {}}}
    elif change == 'argument':
        f.bindings['shell']['methods']['run_command']['arguments'].append('shell_id')
    elif change == 'interface':
        f.manifests[f.consumer['revision']]['dependencies']['shell']['uses'] = ['other@1']
    elif change == 'version':
        f.manifests[f.consumer['revision']]['dependencies']['shell']['range'] = '^9.0.0'
    elif change == 'consumer':
        f.states['consumer-node']['instances']['consumer']['state'] = 'draining'
    elif change == 'owner':
        arguments['owner_ref'] = '../escape'
    elif change == 'generation':
        f.consumer['generation'] += 1
    with pytest.raises(AssemblyError):
        cap = ScopedDependencyBindings(f.owner, consumer=f.consumer, bindings=f.bindings)
        await cap.bind(**arguments)
    assert not f.receipts and not f.issued


@pytest.mark.asyncio
async def test_scoped_policy_snapshot_and_operation_cannot_be_changed(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.bindings['files']['methods']['read']['bound']['workspace'] = 'different'
    first = await f.capability.bind(**request())
    assert f.authority.issue.await_args_list[0].args[0]['methods']['read']['bound']['workspace'] == 'project-a'
    with pytest.raises(AssemblyError, match='another recipe'):
        await f.capability.bind(**request(owner='another-owner'))
    with pytest.raises(AssemblyError, match='another recipe'):
        await f.capability.bind(**request(aliases=['files']))
    assert (await f.capability.bind(**request())) == first
    assert len(f.receipts) == 1


@pytest.mark.asyncio
async def test_expired_session_never_creates_replacement_for_new_revision(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    await f.capability.bind(**request())
    f.clock.now += 901
    with pytest.raises(AssemblyError, match='Original resource session'):
        await f.capability.bind(**request(operation='revision-two'))
    assert len(f.receipts) == 1 and len(f.issued) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['local', 'rpc'])
async def test_dependency_provisioner_composes_real_agent_instances(tmp_path, monkeypatch, scopes, transport):
    f = fixture(tmp_path, monkeypatch)
    capability = f.capability
    if transport == 'rpc':
        from pantheon.apps.dependency_binding_client import RemoteDependencyBindings
        from pantheon.apps.dependency_binding_service import DependencyBindingService
        from pantheon.apps.dependency_client import DependencyClient
        from pantheon.apps.runtime_config import RuntimeCredential
        service = DependencyBindingService(f.owner, policies={'deployment': {'consumer': f.consumer, 'bindings': f.bindings}})
        loop = asyncio.get_running_loop()
        def invoke(client, method, args, *, timeout_seconds):
            assert method == 'bind_dependencies' and set(args) == {'owner_ref', 'operation_id', 'aliases'}
            result = asyncio.run_coroutine_threadsafe(service.bind_dependencies(policy_id='deployment', **args), loop).result(5)
            return {'success': True, 'result': result}
        monkeypatch.setattr(DependencyClient, 'invoke', invoke)
        capability = RemoteDependencyBindings(DependencyClient(RuntimeCredential('https://broker.apps.test/rpc', 'd'*64)))
    profiles = {'toolsets': {'shell': {'alias': 'shell', 'functions': [{
        'name': 'run_command', 'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}},
                                            'required': ['command']}}]}}, 'mcp_servers': {}}
    p = DependencyInstanceProvisioner(capability, consumer=f.consumer, profiles=profiles)
    factory = ProvisionedAgentInstanceFactory(AgentInstanceStore(tmp_path/'instances', namespace='app'), p, model_scope=scopes())
    try:
        a = (await factory({'member': RECIPE}, conversation_id='one'))[0]
        b = (await factory({'member': RECIPE}, conversation_id='two'))[0]
        revision = (await factory({'member': {**RECIPE, 'instructions': 'changed'}}, conversation_id='one'))[0]
        assert a.id == revision.id != b.id
        assert len(f.receipts) == 2 and len(f.issued) == 3
        tools = await a.get_tools_for_llm()
        assert any(t['function']['name'] == 'shell__run_command' for t in tools)
        assert a.providers['shell'] is not b.providers['shell']
        assert a.providers['shell'] is not revision.providers['shell']
        with pytest.raises(ValueError, match='unapproved'):
            await factory({'member': {**RECIPE, 'toolsets': ['unapproved']}}, conversation_id='denied')
        assert len(f.receipts) == 2
    finally:
        await factory.shutdown()
        if transport == 'rpc':
            await capability.shutdown()


@pytest.mark.asyncio
async def test_delivery_identity_substitution_rejected_before_clients(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    actual = f.capability.bind
    async def substituted(**kwargs):
        value = await actual(**kwargs)
        value['owner_ref'] = 'other-instance'
        return value
    capability = SimpleNamespace(bind=substituted)
    profiles = {'toolsets': {'shell': {'alias': 'shell', 'functions': [{'name': 'run_command',
        'parameters': {'type': 'object', 'properties': {}}}]}}, 'mcp_servers': {}}
    p = DependencyInstanceProvisioner(capability, consumer=f.consumer, profiles=profiles)
    store = AgentInstanceStore(tmp_path/'instances', namespace='app')
    try:
        intent = store.reserve('chat', {'member': RECIPE})[0]
        with pytest.raises(ValueError, match='intent'):
            await p.bind(intent)
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('issuer', [None, 'https://127.0.0.1:18901', 'https://127.0.0.1:18900'])
async def test_local_tool_delivery_requires_explicit_matching_issuer(tmp_path, monkeypatch, issuer):
    import ssl
    f = fixture(tmp_path, monkeypatch)
    async def delivery(**kwargs):
        value = await f.capability.bind(**kwargs)
        value['bindings']['shell']['endpoint'] = 'https://127.0.0.1:18900/rpc'
        return value
    profiles = {'toolsets': {'shell': {'alias': 'shell', 'functions': [{'name': 'run_command',
        'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}}}]}}, 'mcp_servers': {}}
    p = DependencyInstanceProvisioner(SimpleNamespace(bind=delivery), consumer=f.consumer, profiles=profiles,
        tls_context=ssl.create_default_context(), owner='owner', rpc_origin=issuer)
    store = AgentInstanceStore(tmp_path / 'instances', namespace='app')
    try:
        intent = store.reserve('chat', {'member': RECIPE})[0]
        if issuer != 'https://127.0.0.1:18900':
            with pytest.raises(AssemblyError, match='Invalid or expired dependency credential'):
                await p.bind(intent)
        else:
            result = await p.bind(intent)
            assert list(result.tools.toolsets) == ['shell']
            await result.tools.toolsets['shell'].shutdown()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_platform_live_maintenance_is_independent_and_shutdown_owned(monkeypatch):
    platform = FleetAPI()
    entered, blocked = asyncio.Event(), asyncio.Event()
    async def pending():
        await blocked.wait()
    async def live():
        entered.set()
        return {'renewed': 1}
    monkeypatch.setattr(platform, '_dependency_starter', lambda: SimpleNamespace(reconcile_once=pending))
    monkeypatch.setattr(platform, '_resource_session_owner', lambda: None)
    monkeypatch.setattr(platform, '_live_dependency_owner', lambda: SimpleNamespace(reconcile_once=live))
    platform._start_dependency_maintenance()
    await asyncio.wait_for(entered.wait(), 1)
    assert platform._live_dependency_maintenance_status == {'renewed': 1}
    await platform._stop_dependency_maintenance()
    assert platform._dependency_maintenance_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['older-generation', 'missing-generation', 'missing-digest', 'malformed-state'])
async def test_ambiguous_live_inventory_defers_without_grant_mutation(tmp_path, monkeypatch, change):
    f = fixture(tmp_path, monkeypatch)
    await f.capability.bind(**request())
    instance = f.states['consumer-node']['instances']['consumer']
    if change == 'older-generation': instance['generation'] -= 1
    elif change == 'missing-generation': instance.pop('generation')
    elif change == 'missing-digest': instance.pop('digest')
    else: instance['state'] = None
    path = next(f.owner.root.glob('*.json'))
    before = path.read_bytes()
    assert (await f.owner.reconcile_once())['deferred'] == 1
    assert path.read_bytes() == before
    f.authority.renew.assert_not_awaited()
    f.authority.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_owner_rpc_keeps_credentials_private_and_starts_maintenance(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    platform = FleetAPI()
    wakes = []
    monkeypatch.setattr(platform, '_live_dependency_owner', lambda: f.owner)
    monkeypatch.setattr(platform, '_start_dependency_maintenance', lambda: wakes.append(True))
    result = await platform.fleet_app_bind_dependencies(consumer=f.consumer, owner_ref='one',
        operation_id='operation-one', bindings=f.bindings)
    assert result['owner_ref'] == 'one' and len(result['bindings']) == 2 and wakes == [True]
    bad = SimpleNamespace(bind=AsyncMock(side_effect=RuntimeError('SECRET-private-transport')))
    monkeypatch.setattr(platform, '_live_dependency_owner', lambda: bad)
    error = await platform.fleet_app_bind_dependencies(consumer=f.consumer, owner_ref='one',
        operation_id='operation-one', bindings=f.bindings)
    assert error['success'] is False and 'SECRET' not in error['error']
