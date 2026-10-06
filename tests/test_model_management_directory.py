"""Management and inference share one owner journal, cloud authority is explicit."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.models.errors import ControlError
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.management_app import create_service
from pantheon.models.management_directory import LocalManagementDirectory
from pantheon.models.management_tools import ModelManagementToolSet
from test_local_model_directory import publication, alias


@pytest.mark.asyncio
async def test_local_publication_and_routes_never_use_cloud_directory(tmp_path):
    original = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await original.initialize()
    await original.save(publication())
    cloud = SimpleNamespace(hub_request=AsyncMock(return_value={'services': []}), aclose=AsyncMock())
    manager = LocalManagementDirectory(original.root, owner='owner', cloud=cloud)
    reader = LocalModelDirectory(original.root, owner='owner', read_only=True)
    route = await manager.route_operation('save', route=alias())
    assert await reader.routes() == [route]
    assert (await manager.route_operation('resolve', route_id='preferred'))['resolved']
    row = await manager.deployment('local')
    await manager.save(row | {'state': 'stopped'})
    assert (await reader.deployment('local'))['state'] == 'stopped'
    assert not (await manager.route_operation('resolve', route_id='preferred'))['resolved']
    cloud.hub_request.assert_not_called()
    with pytest.raises(ControlError):
        await manager.hub_request('GET', '/api/model-services/groups')
    cloud.hub_request.assert_not_called()
    assert await manager.hub_request('GET', '/api/model-services/modal-gpu') == {'services': []}
    cloud.hub_request.assert_awaited_once_with('GET', '/api/model-services/modal-gpu', None)
    await manager.aclose()
    cloud.aclose.assert_awaited_once()
    with pytest.raises(RuntimeError, match='closed'): await manager.deployments()
    assert (await reader.deployment('local'))['state'] == 'stopped'


@pytest.mark.asyncio
async def test_no_cloud_inventory_does_not_expire_existing_modal_publication(tmp_path, monkeypatch):
    from pantheon.models import modal_gpu, model_deploy
    original = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await original.initialize()
    await original.save(publication() | {'deployment_id': 'modal-existing'})
    manager = LocalManagementDirectory(original.root, owner='owner')
    resolver = SimpleNamespace(_client=True)
    tools = ModelManagementToolSet(SimpleNamespace(client=manager, resolver=resolver))
    forbidden = AsyncMock(side_effect=AssertionError('Unavailable cloud must not start or settle work'))
    for name in ('settle_expired', 'start', 'advance', 'stop'):
        monkeypatch.setattr(modal_gpu, name, forbidden)
    monkeypatch.setattr(model_deploy, 'resolve', forbidden)
    overview = await tools.model_services_overview()
    assert overview['modal_available'] is False and overview['modal_gpu'] is None
    assert overview['modal_unavailable_reason']
    assert overview['deployments'][0]['state'] == 'ready'
    denied = await tools.deploy_model('sglang', repo='must/not-fetch')
    assert denied['started'] is False and 'H100' in denied['message']
    for call in (tools.modal_gpu_start('example', user_confirmed=True),
                 tools.modal_gpu_status('existing'), tools.modal_gpu_stop('existing'),
                 tools.deploy_model('sglang', repo='must/not-fetch', user_confirmed=True)):
        with pytest.raises(ControlError) as caught: await call
        assert caught.value.status == 503
    forbidden.assert_not_called()
    assert (await original.deployment('modal-existing'))['state'] == 'ready'


@pytest.mark.parametrize('mode', ['missing', 'wrong-owner', 'relative', 'null', 'hub-ca-without-hub'])
@pytest.mark.asyncio
async def test_prepared_local_directory_validated_before_connections(tmp_path, monkeypatch, mode):
    from pantheon.models import management_app
    directory = LocalModelDirectory(tmp_path/'catalog', owner='someone-else' if mode == 'wrong-owner' else 'owner')
    if mode != 'missing': await directory.initialize()
    workspace = tmp_path/'workspace'; workspace.mkdir()
    state = tmp_path/'state'; state.mkdir(mode=0o700)
    values = {'bus': {'auth': 'token'}, 'directory_root': str(directory.root)}
    if mode == 'relative': values['directory_root'] = 'relative'
    if mode == 'null': values['directory_root'] = None
    if mode == 'hub-ca-without-hub': values['hub_ca_pem'] = 'unused'
    config = RuntimeConfiguration({'model_management': values}, {
        'controller': RuntimeCredential('http://127.0.0.1:2', 'controller-owner'),
        'fleet': RuntimeCredential('nats://127.0.0.1:3', 'bus-owner')},
        'manager', 'a'*64, 1, 'backend', 'owner', 'node')
    connect = AsyncMock(side_effect=AssertionError('Should validate before connecting'))
    monkeypatch.setattr(management_app.OwnedBus, 'connect', connect)
    with pytest.raises((ValueError, ControlError, FileNotFoundError)):
        await create_service(config, workspace, state)
    connect.assert_not_called()
    if mode == 'missing': assert not directory.root.exists()
