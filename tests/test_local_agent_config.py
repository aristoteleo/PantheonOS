"""Local composition must remain explicit through review, edit and disconnect."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.agent_deployment import compose_deployment, update_selected_deployment
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.resolver import AppInstanceResolver, NotJoinedError
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.platform.local_tls import prepare_tls
from test_agent_deployment_recipe import inputs
from test_agent_launch import prepared, snapshot
from test_model_dependency import deployment


def local_spec(tmp_path):
    ca, _ = prepare_tls(tmp_path)
    spec = inputs(tmp_path)
    origin = 'https://127.0.0.1:18900'
    for target in spec['targets'].values():
        target['node_id'] = 'local'
    for name in ('allocator', 'model-access'):
        for credential in spec['credentials'][name].values():
            credential['endpoint'] = origin
    spec['local_transport'] = {'origin': origin, 'trust_roots_pem': ca.read_text(),
                               'directory_root': str(tmp_path / 'directory')}
    return spec


@pytest.mark.asyncio
async def test_local_review_and_model_edit_keep_same_transport(tmp_path):
    spec = local_spec(tmp_path)
    original = deepcopy(spec)
    recipe = compose_deployment(**spec)
    assert original == spec
    row = deployment()
    row['models'][0]['context'] = 8192
    directory = SimpleNamespace(deployments=AsyncMock(return_value=[row]))
    edited = await update_selected_deployment(directory, recipe=recipe, operation_id='edit-model',
        fleet_tiers={'normal': 'fleet-model://mac/example%3A8b'})
    apps = edited['recipe']['apps']
    trust = spec['local_transport']['trust_roots_pem']
    assert apps['agent']['components']['backend']['values']['agent']['trust_roots_pem'] == trust
    assert apps['allocator']['components']['backend']['values']['dependency_binding']['rpc_origin'] == spec['local_transport']['origin']
    control = apps['model-access']['components']['backend']['values']['model_services']
    assert control['http_origin'] == spec['local_transport']['origin']
    assert control['directory_root'] == spec['local_transport']['directory_root']
    assert control['trust_roots_pem'] == trust
    assert recipe == compose_deployment(**spec)


@pytest.mark.parametrize('change', ['remote', 'http', 'path', 'bad-port', 'relative-directory', 'foreign-endpoint',
                                  'different-node', 'bad-ca', 'conflicting-ca', 'conflicting-origin', 'extra-key'])
def test_local_composer_rejects_ambiguous_authority_before_deployment(tmp_path, change):
    spec = local_spec(tmp_path)
    local = spec['local_transport']
    if change in ('remote', 'http', 'path', 'bad-port'):
        local['origin'] = {'remote': 'https://owner.test', 'http': 'http://127.0.0.1:18900',
                          'path': 'https://127.0.0.1:18900/rpc', 'bad-port': 'https://127.0.0.1:99999'}[change]
    elif change == 'relative-directory': local['directory_root'] = 'relative'
    elif change == 'foreign-endpoint': spec['credentials']['allocator']['hub']['endpoint'] = 'https://owner.test'
    elif change == 'different-node': spec['targets']['agent']['node_id'] = 'other'
    elif change == 'bad-ca': local['trust_roots_pem'] = 'private-do-not-print'
    elif change == 'conflicting-ca': spec['agent']['trust_roots_pem'] = 'different'
    elif change == 'conflicting-origin': spec['agent']['rpc_origin'] = 'https://127.0.0.1:18901'
    else: local['key'] = 'private-do-not-print'
    with pytest.raises(AssemblyError) as error:
        compose_deployment(**spec)
    assert 'private-do-not-print' not in str(error.value)


@pytest.mark.parametrize('pem', ['', False, 'private-do-not-print', 'x' * 16385])
def test_invalid_agent_trust_fails_before_creating_owned_data(tmp_path, pem):
    config = prepared(tmp_path, 'http://127.0.0.1:1234')
    config['values']['agent']['trust_roots_pem'] = pem
    with pytest.raises(ValueError, match='configuration is invalid') as error:
        ConfiguredAgentApplication('agent', data_dir=tmp_path / 'data', configuration=snapshot(config))
    assert 'private-do-not-print' not in str(error.value)
    assert not (tmp_path / 'data').exists()


@pytest.mark.parametrize('change', ['untrusted', 'other-allocator', 'remote', 'bad-port', 'duplicate-trust'])
def test_local_agent_requires_one_trust_source_and_matching_allocator(tmp_path, change):
    spec = local_spec(tmp_path)
    config = prepared(tmp_path, 'http://127.0.0.1:1234')
    origin = spec['local_transport']['origin']
    config['values']['agent'].update(rpc_origin=origin, trust_roots_pem=spec['local_transport']['trust_roots_pem'])
    config['credentials']['allocator']['endpoint'] = origin + '/rpc'
    kwargs = {}
    if change == 'untrusted': del config['values']['agent']['trust_roots_pem']
    elif change == 'other-allocator': config['credentials']['allocator']['endpoint'] = 'https://127.0.0.1:18901/rpc'
    elif change in ('remote', 'bad-port'):
        changed = 'https://cloud.test' if change == 'remote' else 'https://127.0.0.1:99999'
        config['values']['agent']['rpc_origin'] = changed
        config['credentials']['allocator']['endpoint'] = changed + '/rpc'
    else: kwargs['dependency_ca_file'] = str(tmp_path / 'tls-ca.pem')
    with pytest.raises(ValueError, match='configuration is invalid'):
        ConfiguredAgentApplication('agent', data_dir=tmp_path / 'data', configuration=snapshot(config), **kwargs)
    assert not (tmp_path / 'data').exists()


@pytest.mark.asyncio
async def test_explicit_resolver_cannot_switch_identity_after_connection_loss(tmp_path, monkeypatch):
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://other.test')
    monkeypatch.setenv('FLEET_KEY', 'foreign')
    connection = SimpleNamespace(is_connected=True, close=AsyncMock())
    resolver = AppInstanceResolver('profile', 'node', 'owner', str(tmp_path), connection=connection)
    client = await resolver._ensure_client()
    connection.is_connected = False
    with pytest.raises(NotJoinedError):
        await resolver._ensure_client()
    connection.close.assert_not_awaited()
    # A temporary reconnect remains the same transport/identity. There is no
    # replay of the caller's operation and no second owner login.
    connection.is_connected = True
    assert await resolver._ensure_client() is client
    assert resolver._fleet == 'profile'
    await resolver.close()
    with pytest.raises(NotJoinedError):
        await resolver._ensure_client()
    connection.close.assert_awaited_once()
