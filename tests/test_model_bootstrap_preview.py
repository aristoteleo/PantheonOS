"""Read-only review retains model-provider phases and exact startup identities."""
from copy import deepcopy

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment import AppDeployment
from pantheon.models.bootstrap import ModelServiceBootstrap
from pantheon.models.bootstrap_preview import preview_bootstrap
from pantheon.platform.service import PlatformService
from test_model_bootstrap import rig


def install(rig):
    for app in [*rig.spec['apps'].values(), *(p['app'] for p in rig.spec['model_apps'].values())]:
        rig.nodes.states[app['node_id']]['installations'][app['revision']] = {'state': 'installed'}


@pytest.mark.asyncio
async def test_review_uses_real_contracts_without_manager_credentials_or_journal(rig):
    install(rig)
    before = deepcopy((rig.spec, rig.nodes.states))
    platform = PlatformService(workspace_path=str(rig.root/'platform'))
    platform._app_deployments = lambda: rig.deployment
    platform._model_service_bootstrap = lambda: pytest.fail('review constructed mutating bootstrap')
    platform._start_dependency_maintenance = lambda: pytest.fail('review started maintenance')
    try:
        result = await platform.model_services_bootstrap(action='preview', **{k: v for k, v in rig.spec.items() if k != 'kind'})
        assert result['success'] and result['operation_id'] == rig.spec['operation_id']
        assert result['order'] == ['connector', 'allocator', 'agent']
        assert result['observation'] == 'read-only-snapshot'
        assert [a['app_id'] for a in result['apps']] == ['model-service', 'dependency-binding', 'test-agent']
        assert (rig.spec, rig.nodes.states) == before
        assert not rig.nodes.calls and not rig.registrations and not rig.rows
        assert not (rig.root/'bootstrap').exists() and not rig.deployment.root.exists()
    finally:
        await platform.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['provider-missing', 'provider-running', 'consumer-generation', 'model-reference',
                                  'provider-submitted', 'consumer-submitted'])
async def test_bad_target_or_original_accepted_operation_cannot_be_saved_as_new(rig, change):
    install(rig)
    spec = rig.spec
    if change == 'provider-missing': rig.nodes.states['platform']['installations'].pop('c'*64)
    elif change == 'provider-running':
        rig.nodes.states['platform']['instances']['existing'] = dict(digest='c'*64, scope='model-local', state='ready', generation=2)
    elif change == 'consumer-generation': spec['apps']['agent']['generation'] = 1
    elif change == 'model-reference': spec['apps']['agent']['components']['backend']['values']['agent']['model_binding'] = {'$model': 'absent'}
    else:
        role, name, node = ('providers', 'connector', 'platform') if change == 'provider-submitted' else ('consumers', 'agent', 'worker')
        child = dict(owner=spec['owner'], operation_id=ModelServiceBootstrap.child_id(spec, role))
        operation = AppDeployment.operation_id(child, name, 'prepare_start')
        rig.nodes.states[node]['operations'][operation] = {'state': 'queued'}
    before = deepcopy(rig.nodes.states)
    with pytest.raises(AssemblyError): await preview_bootstrap(rig.nodes, **spec)
    assert rig.nodes.states == before and not rig.nodes.calls and not rig.registrations


@pytest.mark.asyncio
async def test_planned_model_binding_is_reviewed_as_declaration_not_ready_instance(rig):
    install(rig)
    manifest = rig.nodes.manifests['c'*64]['manifest']
    manifest['provides'] = {'interfaces': [{'name': 'models', 'tools': ['infer']}],
                            'tools': [{'name': 'infer', 'params': []}]}
    consumer = rig.nodes.manifests['b'*64]
    consumer['manifest']['dependencies']['model-service'] = {'range': '*', 'uses': ['models@1']}
    consumer['definition']['components'][0]['configuration']['credentials']['models'] = {'required': True}
    rig.spec['apps']['agent']['bindings']['models'] = dict(app_id='model-service', component='backend',
        provider={'$model': 'connector'}, methods={'infer': {'arguments': [], 'bound': {}}})
    result = await preview_bootstrap(rig.nodes, **rig.spec)
    assert result['order'] == ['connector', 'allocator', 'agent']
    assert all(not state['instances'] for state in rig.nodes.states.values())
    manifest['version'] = 'invalid'
    with pytest.raises(AssemblyError): await preview_bootstrap(rig.nodes, **rig.spec)
    assert not rig.nodes.calls


@pytest.mark.asyncio
async def test_full_separate_phase_limits_are_supported(rig):
    model = rig.spec['model_apps']['connector']
    rig.spec['model_apps'] = {f'provider-{i}': {**deepcopy(model), 'deployment_id': f'model-{i}',
        'app': {**deepcopy(model['app']), 'scope': f'model-model-{i}'}} for i in range(8)}
    allocator = deepcopy(rig.spec['apps']['allocator'])
    allocator['components']['backend']['values']['dependency_binding']['policies'] = {}
    rig.spec['apps'] = {f'consumer-{i}': {**deepcopy(allocator), 'scope': f'consumer-{i}'} for i in range(16)}
    install(rig)
    result = await preview_bootstrap(rig.nodes, **rig.spec)
    assert len(result['apps']) == len(result['order']) == 24
    assert set(result['order'][:8]) == set(rig.spec['model_apps'])
    assert not rig.nodes.calls and not rig.registrations
