"""Agent preset through the generic coordinator and real package declarations.

Node operations/issuance are deterministic fixtures; actual Fleet installation
and packaged GUI/model conversations have separate acceptance gates.
"""
from copy import deepcopy
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheonos.apps.dependency_assembly import AssemblyError
from pantheonos.apps.dependency_binding_service import DependencyBindingService
from pantheonos.apps.lifecycle import build_artifact
from pantheonos.apps.agent_deployment import compose_deployment
from pantheonos.models.dependency_service import ModelServiceControl
from pantheonos.apps.allocator_package import build_package as build_allocator
from pantheonos.models.access_package import build_package as build_models
from test_agent_launch import prepared
from test_agent_release import release
from test_model_dependency import deployment


def inputs(tmp_path):
    config = prepared(tmp_path, 'http://127.0.0.1:1234')['values']['agent']
    config['models'] = {'providers': {'openai': 'provider'}}
    def ref(name):
        return {'ref': 'node-secret://' + name, 'endpoint': 'https://owner.test'}
    return dict(owner='owner', operation_id='deployment-one',
        targets={name: {'node_id': 'worker' if name == 'agent' else 'platform',
            'revision': digest*64, 'scope': name, 'generation': 0}
            for name, digest in [('agent', 'b'), ('allocator', 'a'), ('model-access', 'c')]},
        agent=config, tools={}, models={'deployments': {}, 'routes': {}, 'allow_wake': False},
        credentials={'agent': {'provider': ref('byok')},
            'allocator': {'hub': ref('hub'), 'controller': ref('controller')},
            'model-access': {'hub': ref('hub')}})


def test_preset_is_explicit_immutable_composition(tmp_path):
    spec = inputs(tmp_path)
    saved = deepcopy(spec)
    recipe = compose_deployment(**spec)
    assert spec == saved
    from pantheonos.apps.recipe import deployment_recipe
    assert deployment_recipe(**recipe)[1] == ['allocator', 'model-access', 'agent']
    app = recipe['apps']['agent']
    assert set(app['components']['backend']['credentials']) == {'provider'}
    assert app['components']['backend']['values']['agent']['models']['model_services'] == 'model_services'
    assert app['bindings']['model_services']['methods']['model_services_control']['bound'] == {'policy_id': 'agent'}
    assert recipe['apps']['allocator']['components']['backend']['values']['dependency_binding']['policies']['agent']['consumer'] == {'$app': 'agent'}


@pytest.mark.parametrize('change', ['inline-secret', 'owner-in-agent', 'extra-alias', 'replace-model', 'replace-allocator', 'missing-target', 'invalid-credentials'])
def test_preset_rejects_confused_configuration(tmp_path, change):
    spec = inputs(tmp_path)
    if change == 'inline-secret': spec['credentials']['agent']['provider'] = {'endpoint': 'https://model.test', 'key': 'do-not-print'}
    elif change == 'owner-in-agent': spec['credentials']['agent']['allocator'] = spec['credentials']['allocator']['hub']
    elif change == 'extra-alias': spec['tools']['unapproved'] = {}
    elif change == 'replace-model': spec['agent']['models']['model_services'] = 'other'
    elif change == 'replace-allocator': spec['extra_bindings'] = {'allocator': {}}
    elif change == 'missing-target': del spec['targets']['model-access']
    else: spec['credentials']['allocator'] = None
    with pytest.raises(AssemblyError) as error:
        compose_deployment(**spec)
    assert 'do-not-print' not in str(error.value)


@pytest.mark.asyncio
async def test_empty_policies_start_without_ambient_calls_and_reject_allocation():
    consumer = {'node_id': 'node', 'instance_id': 'agent', 'revision': 'a'*64, 'generation': 2}
    owner = SimpleNamespace(bind=AsyncMock(), deployments=AsyncMock(side_effect=AssertionError('ambient Hub call')))
    service = DependencyBindingService(owner, policies={'agent': {'consumer': consumer, 'bindings': {}}})
    for aliases in ([], ['shell']):
        with pytest.raises(AssemblyError):
            await service.bind_dependencies(policy_id='agent', owner_ref='agent-one', operation_id='allocation-one', aliases=aliases)
    owner.bind.assert_not_awaited()
    model = ModelServiceControl(owner, policies={'agent': {'consumer': consumer, 'deployments': {}, 'routes': {}, 'allow_wake': False}})
    for operation in ('deployments', 'routes'):
        response = await model.model_services_control(policy_id='agent', operation=operation, arguments={})
        assert response['status'] == 200 and response['result'] == {operation: []}
    assert (await model.model_services_control(policy_id='agent', operation='connect', arguments={'binding': {}}))['status'] == 403
    owner.deployments.assert_not_awaited()


def test_preset_can_prepare_shared_ordinary_providers_without_replacing_core(tmp_path):
    spec = inputs(tmp_path)
    target = dict(node_id='worker', revision='d'*64, scope='shared-files', generation=0,
                  bindings={}, components={'backend':{'values':{'files':{'workspace':'/project'}}}})
    spec['provider_apps'] = {'files':target}
    spec['tools'] = {'files':{'app_id':'file-manager',
        'provider':{'$app':'files','component':'backend','port':'http'},
        'methods':{'read_file':{'arguments':[],'bound':{'file_path':'shared.txt'}}}}}
    spec['agent']['dependencies']['profiles']['toolsets'] = {'file_manager':{'alias':'files','functions':[
        {'name':'read_file','parameters':{'type':'object','properties':{}}}]}}
    recipe = compose_deployment(**spec)
    assert recipe['apps']['files'] == target
    from pantheonos.apps.recipe import deployment_recipe
    _, order = deployment_recipe(**recipe)
    assert order.index('files') < order.index('agent')
    spec['provider_apps']['files']['components']['backend']['values']['files']['workspace'] = '/later'
    assert recipe['apps']['files']['components']['backend']['values']['files']['workspace'] == '/project'
    spec['provider_apps'] = {'agent':target}
    with pytest.raises(AssemblyError, match='cannot replace'):
        compose_deployment(**spec)


