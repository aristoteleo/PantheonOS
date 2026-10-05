"""Deployment journal recovery against a deterministic asynchronous node ledger.

The Go controller integration additionally runs AppDeployment over real NATS,
native processes and the scoped dependency gateway. This fixture injects exact
commit/acknowledgement failures that would be unreliable to time on live nodes.
"""
import copy
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError, DependencyStarter
from pantheon.apps.deployment import AppDeployment, deployment_recipe
from pantheon.apps.schema import DependencySpec
from pantheon.platform.fleet_api import FleetAPI


class Nodes:
    def __init__(self):
        self.states = {name: dict(node_id=name, owner='owner', dependency_config_protocol=1,
                                 installations={}, instances={}, operations={}) for name in ('platform', 'worker')}
        self.calls, self.configurations, self.applied_configurations = [], {}, 0
        self.loss = None
        self.manifests = {
            'a'*64: dict(protocol=1, revision='a'*64,
                manifest={'apiVersion': 2, 'id': 'dependency-binding', 'version': '0.1.0', 'provides': {
                    'interfaces': [{'name': 'dependency-binding', 'tools': ['bind_dependencies']}],
                    'tools': [{'name': 'bind_dependencies', 'params': [{'name': x} for x in
                              ('policy_id', 'owner_ref', 'operation_id', 'aliases')]}]}},
                definition={'components': [{'name': 'backend', 'configuration': {
                    'values': {'dependency_binding': {'required': True}},
                    'credentials': {'hub': {'required': True}, 'controller': {'required': True}}}}]}),
            'b'*64: dict(protocol=1, revision='b'*64,
                manifest={'apiVersion': 2, 'id': 'test-agent', 'version': '1.0.0', 'dependencies': {
                    'dependency-binding': {'range': '^0.1.0', 'uses': ['dependency-binding@1']},
                    'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'}}},
                definition={'components': [{'name': 'backend', 'configuration': {
                    'values': {'agent': {'required': True}}, 'credentials': {'allocator': {'required': True}}}}]}),
        }

    async def status(self, node):
        return copy.deepcopy(self.states[node])

    async def manifest(self, node, revision):
        return copy.deepcopy(self.manifests[revision])

    async def submit(self, node, action, digest, *, scope, generation, operation_id, **kwargs):
        self.calls.append((node, action, operation_id))
        request = dict(protocol=1, action=action, digest=digest, scope=scope,
                       generation=generation, operation_id=operation_id, **kwargs)
        operations = self.states[node]['operations']
        if operation_id in operations:
            assert operations[operation_id]['request'] == request
        else:
            operations[operation_id] = {'request': request, 'state': 'queued'}
        result = copy.deepcopy(operations[operation_id])
        if self.loss == action:
            self.loss = None
            raise TimeoutError('lost original acknowledgement')
        return result

    def finish(self):
        for node, state in self.states.items():
            for operation in state['operations'].values():
                if operation['state'] != 'queued':
                    continue
                request = operation['request']
                revision, scope, generation = (request[k] for k in ('digest', 'scope', 'generation'))
                app_id = self.manifests[revision]['manifest']['id']
                key = hashlib.sha256(f'{node}:{revision}:{scope}'.encode()).hexdigest()[:32]
                action = request['action']
                if action == 'install':
                    state['installations'][revision] = {'state': 'installed'}
                elif action == 'prepare_start':
                    assert revision in state['installations']
                    previous = state['instances'].get(key)
                    assert (previous is None and generation == 0) or (
                        previous['state'] == 'stopped' and previous['generation'] == generation)
                    state['instances'][key] = dict(app_id=app_id, digest=revision, scope=scope,
                        generation=generation+1, state='prepared', start_preparation_id=request['operation_id'])
                elif action == 'start':
                    instance = state['instances'][key]
                    assert instance['state'] == 'prepared' and instance['generation'] == generation
                    assert instance['start_preparation_id'] == request['start_preparation_id']
                    assert (node, key, generation) in self.configurations
                    instance.update(generation=generation+1, state='ready', ready_generation=generation+1)
                else:
                    raise AssertionError(action)
                operation['state'] = 'succeeded'

    async def configure(self, node, *, instance_id, revision, generation, preparation_id, components):
        instance = self.states[node]['instances'][instance_id]
        assert instance['digest'] == revision and instance['generation'] == generation
        assert instance['state'] == 'prepared' and instance['start_preparation_id'] == preparation_id
        key = (node, instance_id, generation)
        if key in self.configurations:
            assert self.configurations[key] == components
        else:
            self.configurations[key] = copy.deepcopy(components)
            self.applied_configurations += 1
        if self.loss == 'configure':
            self.loss = None
            raise TimeoutError('lost configuration acknowledgement')
        return {'ok': True}


class Authority:
    def __init__(self, nodes):
        self.nodes, self.grants, self.loss = nodes, {}, False

    async def issue(self, body):
        provider = body['provider']
        instance = self.nodes.states[provider['node_id']]['instances'][provider['instance_id']]
        assert instance['state'] == 'ready' and instance['generation'] == provider['generation']
        op = body['operation_id']
        if op not in self.grants:
            token = hashlib.sha256(op.encode()).hexdigest()
            host = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
            self.grants[op] = dict(endpoint=f'https://{host}.apps.test/rpc', access_token=token,
                grant_id=hashlib.sha256(token.encode()).hexdigest(), expires=int(time.time())+899,
                consumer={**body['consumer'], 'fleet_id': 'owner'}, provider={**provider, 'fleet_id': 'owner'})
        if self.loss:
            self.loss = False
            raise TimeoutError('lost grant acknowledgement')
        return copy.deepcopy(self.grants[op])


def apps():
    return {
        'agent': dict(node_id='worker', revision='b'*64, scope='deploy-agent', generation=0,
            components={'backend': {'values': {'agent': {'namespace': 'agent-data'}}}},
            bindings={'allocator': dict(app_id='dependency-binding', component='backend',
                provider={'$app': 'allocator', 'component': 'backend', 'port': 'http'}, methods={
                    'bind_dependencies': {'arguments': ['owner_ref', 'operation_id', 'aliases'],
                                          'bound': {'policy_id': 'agent'}}})}),
        'allocator': dict(node_id='platform', revision='a'*64, scope='deploy-allocator', generation=0,
            bindings={}, components={'backend': {'values': {'dependency_binding': {
                'protocol': 1, 'policies': {'agent': {'consumer': {'$app': 'agent'}, 'bindings': {}}}}},
                'credentials': {key: {'ref': 'private-vault-'+key, 'endpoint': 'https://example.test'}
                                for key in ('hub', 'controller')}}}),
    }


def coordinator(tmp_path, nodes, authority):
    starter = DependencyStarter(nodes, tmp_path/'starts', authority)
    return AppDeployment(starter, tmp_path/'deployments')


async def finish_deployment(tmp_path, nodes, authority, recipe=None, operation_id='deployment-one'):
    for _ in range(20):
        # Fresh coordinator objects exercise persisted intent on each poll.
        deploy = coordinator(tmp_path, nodes, authority)
        result = await deploy.advance(owner='owner', operation_id=operation_id, apps=recipe)
        recipe = None
        if result['state'] == 'ready':
            return result
        nodes.finish()
    pytest.fail('deployment did not reach readiness')


async def restart_source(tmp_path):
    nodes = Nodes()
    authority = Authority(nodes)
    source = apps()
    # Shared providers have no consumer-specific policy. Only their exact
    # identity is embedded in the allocator's runtime configuration.
    source['shared'] = copy.deepcopy(source['allocator'])
    source['shared']['scope'] = 'shared-provider'
    source['shared']['components']['backend']['values']['dependency_binding']['policies'] = {}
    source['allocator']['components']['backend']['values']['dependency_binding']['shared'] = {'$app': 'shared'}
    result = await finish_deployment(tmp_path, nodes, authority, source)
    for name in ('agent', 'allocator'):
        identity = result['prepared'][name]
        nodes.states[identity['node_id']]['instances'][identity['instance_id']].update(
            state='stopped', generation=3, resources=[])
    return nodes, authority, source, result


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', [None, 'prepare_start', 'configure', 'start', 'grant'])
async def test_restart_preserves_shared_provider_and_rebinds_new_generations(tmp_path, loss):
    from pantheon.apps.deployment_restart import plan_restart
    nodes, authority, source, original = await restart_source(tmp_path)
    deploy = coordinator(tmp_path, nodes, authority)
    journal = (tmp_path/'deployments/deployment-one.json').read_bytes()
    calls, grants = len(nodes.calls), len(authority.grants)
    recipe = await plan_restart(deploy, owner='owner', source_operation_id='deployment-one',
                                operation_id='restart-one', apps=['agent', 'allocator'])
    assert len(nodes.calls) == calls and len(authority.grants) == grants
    assert not (tmp_path/'deployments/restart-one.json').exists()
    policy = recipe['apps']['allocator']['components']['backend']['values']['dependency_binding']
    assert policy['shared'] == {**original['prepared']['shared'], 'generation': 2}
    assert policy['policies']['agent']['consumer'] == {'$app': 'agent'}
    assert recipe['apps']['allocator']['components']['backend']['credentials'] == source['allocator']['components']['backend']['credentials']
    nodes.loss = loss if loss != 'grant' else None
    authority.loss = loss == 'grant'
    for _ in range(10):
        try:
            restarted = await finish_deployment(tmp_path, nodes, authority, recipe['apps'], 'restart-one')
            break
        except TimeoutError:
            nodes.finish()
    else:
        pytest.fail('restart acknowledgement was not recovered')
    assert (tmp_path/'deployments/deployment-one.json').read_bytes() == journal
    assert len(nodes.calls) == calls + 4  # prepare/start only, no installation
    assert all(action != 'install' for _, action, _ in nodes.calls[calls:])
    assert len(authority.grants) == grants + 1
    for name in ('agent', 'allocator'):
        identity = restarted['prepared'][name]
        assert identity['instance_id'] == original['prepared'][name]['instance_id']
        assert identity['generation'] == 4
    agent = restarted['prepared']['agent']
    config = nodes.configurations[('worker', agent['instance_id'], 4)]['backend']
    assert config['dependencies']['allocator']['consumer']['generation'] == 5
    assert config['dependencies']['allocator']['provider']['generation'] == 5
    with pytest.raises(AssemblyError, match='Resume the existing'):
        await plan_restart(deploy, owner='owner', source_operation_id='deployment-one',
                           operation_id='restart-one', apps=['agent', 'allocator'])


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'running', 'resources', 'generation', 'revision',
                                  'shared-stopped', 'shared-replaced', 'dependent', 'unknown', 'duplicate', 'incomplete'])
async def test_restart_rejects_unsafe_or_incomplete_source_without_mutation(tmp_path, change):
    from pantheon.apps.deployment_restart import plan_restart
    nodes, authority, _, original = await restart_source(tmp_path)
    deploy = coordinator(tmp_path, nodes, authority)
    owner, selected = 'owner', ['agent', 'allocator']
    agent = original['prepared']['agent']
    instance = nodes.states['worker']['instances'][agent['instance_id']]
    if change == 'owner': owner = 'foreign'
    elif change == 'running': instance.update(state='ready', generation=2)
    elif change == 'resources': instance['resources'] = [{'pid': 123}]
    elif change == 'generation': instance['generation'] = 5
    elif change == 'revision': instance['digest'] = 'c'*64
    elif change.startswith('shared-'):
        shared = original['prepared']['shared']
        nodes.states['platform']['instances'][shared['instance_id']].update(
            state='stopped' if change == 'shared-stopped' else 'ready', generation=3)
    elif change == 'dependent': selected = ['agent']
    elif change == 'unknown': selected = ['unknown']
    elif change == 'duplicate': selected = ['agent', 'agent']
    else:
        path = tmp_path/'deployments/deployment-one.json'
        record = json.loads(path.read_text()); record['state'] = 'pending'
        path.write_text(json.dumps(record))
    before = copy.deepcopy(nodes.states), len(nodes.calls), len(authority.grants)
    with pytest.raises(AssemblyError):
        await plan_restart(deploy, owner=owner, source_operation_id='deployment-one',
                           operation_id='restart-one', apps=selected)
    assert before == (nodes.states, len(nodes.calls), len(authority.grants))
    assert not (tmp_path/'deployments/restart-one.json').exists()


@pytest.mark.asyncio
async def test_restart_owner_api_review_and_redaction(tmp_path, monkeypatch):
    nodes, authority, _, _ = await restart_source(tmp_path)
    api = FleetAPI()
    deploy = coordinator(tmp_path, nodes, authority)
    monkeypatch.setattr(api, '_app_deployments', lambda: deploy)
    reply = await api.fleet_app_restart_plan('owner', 'deployment-one', 'restart-one', ['agent', 'allocator'])
    assert reply['success'] and set(reply['recipe']['apps']) == {'agent', 'allocator'}
    monkeypatch.setattr(deploy, '_state', AsyncMock(side_effect=RuntimeError('private-secret')))
    reply = await api.fleet_app_restart_plan('owner', 'deployment-one', 'restart-one', ['agent', 'allocator'])
    assert not reply['success'] and 'private-secret' not in reply['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('loss', [None, 'install', 'prepare_start', 'configure', 'start', 'grant'])
async def test_deploy_recovers_original_operations_and_delivers_scoped_allocator(tmp_path, loss):
    nodes = Nodes()
    authority = Authority(nodes)
    nodes.loss = loss if loss != 'grant' else None
    authority.loss = loss == 'grant'
    for _ in range(10):
        try:
            result = await finish_deployment(tmp_path, nodes, authority, apps())
            break
        except TimeoutError:
            nodes.finish()
    else:
        pytest.fail('lost acknowledgement was not recovered')
    assert result['state'] == 'ready'
    assert len(nodes.calls) == 6
    assert len({(node, operation_id) for node, _, operation_id in nodes.calls}) == 6
    assert nodes.applied_configurations == 2 and len(authority.grants) == 1
    assert all(not value for value in (nodes.loss, authority.loss))
    prepared = result['prepared']
    allocator_config = nodes.configurations[('platform', prepared['allocator']['instance_id'], 1)]['backend']
    agent_config = nodes.configurations[('worker', prepared['agent']['instance_id'], 1)]['backend']
    policy = allocator_config['values']['dependency_binding']['policies']['agent']
    assert policy['consumer'] == {**prepared['agent'], 'generation': 2}
    assert allocator_config['dependencies'] == {}
    assert agent_config.get('credentials', {}) == {}
    assert set(agent_config['dependencies']) == {'allocator'}
    assert agent_config['dependencies']['allocator']['provider']['instance_id'] == prepared['allocator']['instance_id']
    assert agent_config['dependencies']['allocator']['consumer']['generation'] == 2
    public = json.dumps(coordinator(tmp_path, nodes, authority).inspect(owner='owner', operation_id='deployment-one'))
    assert 'private-vault' not in public and 'access_token' not in public and 'policies' not in public
    assert (await finish_deployment(tmp_path, nodes, authority))['state'] == 'ready'
    assert len(nodes.calls) == 6  # Opening/status polling does not re-install.


@pytest.mark.asyncio
async def test_rejects_changed_recipe_foreign_owner_and_stopped_original(tmp_path):
    nodes = Nodes()
    authority = Authority(nodes)
    result = await finish_deployment(tmp_path, nodes, authority, apps())
    deploy = coordinator(tmp_path, nodes, authority)
    changed = apps()
    changed['agent']['components']['backend']['values']['agent']['namespace'] = 'other'
    with pytest.raises(AssemblyError, match='immutable recipe'):
        await deploy.advance(owner='owner', operation_id='deployment-one', apps=changed)
    with pytest.raises(AssemblyError, match='another Fleet'):
        deploy.inspect(owner='foreign', operation_id='deployment-one')
    instance = nodes.states['worker']['instances'][result['prepared']['agent']['instance_id']]
    instance.update(state='stopped', generation=3)
    with pytest.raises(AssemblyError, match='stopped, replaced'):
        await deploy.advance(owner='owner', operation_id='deployment-one')
    assert len(nodes.calls) == 6


@pytest.mark.asyncio
async def test_pending_and_failed_operations_are_not_replaced(tmp_path):
    nodes = Nodes()
    authority = Authority(nodes)
    deploy = coordinator(tmp_path, nodes, authority)
    result = await deploy.advance(owner='owner', operation_id='deployment-one', apps=apps())
    assert result['phase'] == 'installing'
    assert await deploy.advance(owner='owner', operation_id='deployment-one') == result
    assert len(nodes.calls) == 1
    operation = next(iter(nodes.states['platform']['operations'].values()))
    operation.update(state='failed', error='private-provider-password')
    with pytest.raises(AssemblyError, match='inspect its original') as error:
        await deploy.advance(owner='owner', operation_id='deployment-one')
    assert 'password' not in str(error.value) and len(nodes.calls) == 1


@pytest.mark.parametrize('change', ['cycle', 'missing', 'duplicate', 'reference-fields', 'generation'])
def test_bad_recipe_has_no_node_effects(change):
    recipe = apps()
    if change == 'cycle':
        recipe['allocator']['bindings'] = {'circular': {'$app': 'agent'}}
    elif change == 'missing':
        recipe['agent']['bindings']['allocator']['provider']['$app'] = 'missing'
    elif change == 'duplicate':
        recipe['other'] = copy.deepcopy(recipe['allocator'])
    elif change == 'reference-fields':
        recipe['agent']['bindings']['allocator']['provider']['generation'] = 12
    else:
        recipe['agent']['generation'] = True
    with pytest.raises(AssemblyError):
        deployment_recipe('owner', 'deployment-one', recipe)


@pytest.mark.asyncio
async def test_platform_owner_api_redacts_errors_and_keeps_original_intent(tmp_path, monkeypatch):
    nodes = Nodes()
    authority = Authority(nodes)
    deploy = coordinator(tmp_path, nodes, authority)
    api = FleetAPI()
    monkeypatch.setattr(api, '_app_deployments', lambda: deploy)
    monkeypatch.setattr(api, '_start_dependency_maintenance', lambda: None)
    first = await api.fleet_app_deploy('owner', 'deployment-one', apps=apps())
    assert first['success'] and first['state'] == 'pending'
    assert (await api.fleet_app_deploy('owner', 'deployment-one', action='inspect')) == first
    monkeypatch.setattr(deploy, 'advance', AsyncMock(side_effect=RuntimeError('private-key')))
    error = await api.fleet_app_deploy('owner', 'deployment-one')
    assert not error['success'] and 'private-key' not in error['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', ['startup', 'runtime', 'invalid'])
async def test_runtime_dependency_requires_explicit_declaration(tmp_path, binding):
    nodes = Nodes()
    dependency = nodes.manifests['b'*64]['manifest']['dependencies']['shell']
    dependency['binding'] = binding
    if binding == 'runtime':
        assert DependencySpec(**dependency).binding == 'runtime'
        assert (await finish_deployment(tmp_path, nodes, Authority(nodes), apps()))['state'] == 'ready'
    else:
        with pytest.raises(AssemblyError, match='startup dependency|binding phase'):
            await finish_deployment(tmp_path, nodes, Authority(nodes), apps())


@pytest.mark.asyncio
async def test_configuration_contention_resumes_exact_grants_and_start(tmp_path, monkeypatch):
    from pantheon.apps.lifecycle import ConfigurationBusy
    nodes = Nodes()
    authority = Authority(nodes)
    configure = nodes.configure
    seen = []
    async def busy(node, **configuration):
        if node == 'worker':
            seen.append(copy.deepcopy(configuration))
            if len(seen) <= 2:
                raise ConfigurationBusy('node lifecycle is busy; retry the same configuration')
        return await configure(node, **configuration)
    monkeypatch.setattr(nodes, 'configure', busy)
    result = await finish_deployment(tmp_path, nodes, authority, apps())
    assert result['state'] == 'ready'
    assert len(seen) == 3 and seen[0] == seen[1] == seen[2]
    assert len(authority.grants) == 1 and nodes.applied_configurations == 2
    assert len(nodes.calls) == 6
    assert len({(node, operation_id) for node, _, operation_id in nodes.calls}) == 6
