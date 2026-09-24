"""Owner management API against real Hub persistence; node reads are controlled.

Real frozen package capture/build, no GPU or deployed Fleet acceptance claim.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('pantheon_hub')
from test_model_group_creation_hub import durable, Authority
from test_model_group_package import prepared
from pantheon.models.manager import ModelServiceManager
from pantheon.models.group_creation import topology_for
from pantheon.models import group_management as api


async def setup(durable, prepared, tmp_path, monkeypatch):
    journal = await durable.reopen()
    model, plan = prepared
    rows = [dict(deployment_id=f'snapshot-{i}', mode='managed', engine='sglang', state='draft',
                 node_id=m['node_id'], binding=dict(node_id=m['node_id'])) for i, m in enumerate(plan['members'])]
    client = SimpleNamespace(deployments=AsyncMock(return_value=rows), hub_request=journal.client.hub_request)
    manager = ModelServiceManager(client, SimpleNamespace(_fleet=journal.owner, _ensure_client=AsyncMock()),
                                  group_store_root=tmp_path / 'packages')
    manager.node = AsyncMock(return_value=dict(capability=dict(os='linux', arch='amd64', caps=['model-group-private-network'])))
    manager.rpc = AsyncMock(return_value=model)
    authority = Authority()
    fleet = SimpleNamespace()
    async def status(node):
        return dict(owner=journal.owner, node_id=node, instances={}, operations={})
    async def resources(node):
        member = next(m for m in plan['members'] if m['node_id'] == node)
        return dict(inventory=dict(accelerators=[dict(id=d['id'], backend='cuda', memory=dict(total_bytes=24 << 30))
            for d in member['resources']['devices']]))
    fleet.status, fleet.resource_status = AsyncMock(side_effect=status), AsyncMock(side_effect=resources)
    async def trust(node, action, **kwargs):
        row = (await journal.list())[0]
        return dict(protocol=1, owner=journal.owner, node_id=node, group_id=row['group_id'],
            topology_sha256=topology_for(row['plan']).fingerprint, state='closed' if action == 'close' else 'open',
            ca_pem=authority.pem, ca_sha256=authority.pin)
    fleet.group_authority = AsyncMock(side_effect=trust)
    monkeypatch.setattr(api, 'FleetLifecycle', lambda _: fleet)
    config = dict(model_sha256=model['sha256'], context_length=4096, parallel=1,
                  members=[dict(deployment_id=r['deployment_id'], underlay=f'192.168.20.{10+i}:18441',
                                resources=deepcopy(plan['members'][i]['resources'])) for i, r in enumerate(rows)])
    return manager, config, fleet, journal


@pytest.mark.asyncio
async def test_create_read_reopen_build_handoff_uses_original_intent(durable, prepared, tmp_path, monkeypatch):
    manager, config, fleet, journal = await setup(durable, prepared, tmp_path, monkeypatch)
    assert await manager.group_deployments() == {'creations': []}
    assert not manager.group_store_root.exists()
    name = 'g' * 64  # full public ID length, regardless of lock namespace
    result = await manager.group_deployments('create', name, config)
    original = result['creation']
    assert original['phase'] == 'intent' and result['group'] is None
    assert original['plan']['members'][0]['generation'] == 2
    assert original['plan']['members'][0]['physical_gpu_bytes'] == [24 << 30]
    assert not fleet.group_authority.called  # create only saves original inputs
    assert (await manager.group_deployments('inspect', name)) == result
    with pytest.raises(ValueError, match='already has'):
        await manager.group_deployments('create', name, config)
    assert manager.rpc.await_count == 2
    # Recreate Agent manager and Hub client; frozen source survives in workspace.
    reopened = await durable.reopen()
    manager = ModelServiceManager(SimpleNamespace(hub_request=reopened.client.hub_request), manager.resolver,
                                  group_store_root=manager.group_store_root)
    for _ in range(5):
        result = await manager.group_deployments('advance', name)
    assert result['creation']['phase'] == 'handed_off'
    assert result['creation']['source_sha256'] == original['source_sha256']
    assert result['group']['phase'] == 'preparing'
    assert all(not m['install']['sent'] and not m['start']['sent'] for m in result['group']['members'])
    assert not result['group']['inference']['activated']
    assert fleet.group_authority.await_count == 1


@pytest.mark.asyncio
async def test_lost_create_reply_can_be_discovered_and_stopped_without_duplication(durable, prepared, tmp_path, monkeypatch):
    manager, config, fleet, journal = await setup(durable, prepared, tmp_path, monkeypatch)
    durable.lose_write = True
    with pytest.raises(TimeoutError):
        await manager.group_deployments('create', 'lost', config)
    rows = (await manager.group_deployments())['creations']
    assert len(rows) == 1 and rows[0]['group_id'] == 'lost'
    with pytest.raises(ValueError, match='already has'):
        await manager.group_deployments('create', 'lost', config)
    with pytest.raises(ValueError, match='Stop the deployment'):
        await manager.group_deployments('continue_stop', 'lost')
    row = await manager.group_deployments('stop', 'lost')
    assert row['creation']['phase'] == 'stopped'
    assert not fleet.group_authority.called
    assert await manager.group_deployments('continue_stop', 'lost') == row


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['capability', 'duplicate_node', 'duplicate_gpu', 'bad_gpu', 'unknown_capacity', 'mismatch', 'old_scope', 'public_underlay', 'boolean_budget'])
async def test_invalid_creation_has_no_durable_or_lifecycle_effect(durable, prepared, tmp_path, monkeypatch, fault):
    manager, config, fleet, journal = await setup(durable, prepared, tmp_path, monkeypatch)
    if fault == 'capability': manager.node.return_value['capability']['caps'] = []
    elif fault == 'duplicate_node': config['members'][1]['deployment_id'] = config['members'][0]['deployment_id']
    elif fault == 'duplicate_gpu': config['members'][0]['resources']['devices'] *= 2
    elif fault == 'bad_gpu': config['members'][0]['resources']['devices'][0]['id'] = []
    elif fault == 'unknown_capacity': fleet.resource_status.side_effect = None; fleet.resource_status.return_value = {'inventory': {}}
    elif fault == 'mismatch':
        altered = deepcopy(prepared[0]); altered['config']['hidden_size'] *= 2
        manager.rpc.side_effect = [prepared[0], altered]
    elif fault == 'old_scope': fleet.status.side_effect = None; fleet.status.return_value = dict(owner=journal.owner, node_id='n_0', instances={'old': {'scope': 'model-group-test'}})
    elif fault == 'public_underlay': config['members'][0]['underlay'] = '8.8.8.8:18441'
    elif fault == 'boolean_budget': config['members'][0]['resources']['memory_bytes'] = True
    with pytest.raises(ValueError):
        await manager.group_deployments('create', 'test', config)
    assert await journal.list() == []
    assert not fleet.group_authority.called
    assert not manager.group_store_root.exists()


@pytest.mark.asyncio
async def test_impossible_memory_is_rejected_before_saving_immutable_plan(durable, prepared, tmp_path, monkeypatch):
    manager, config, fleet, journal = await setup(durable, prepared, tmp_path, monkeypatch)
    config['members'][1]['resources']['memory_bytes'] = 256 << 20
    with pytest.raises(ValueError, match='system memory'):
        await manager.group_deployments('create', 'budget', config)
    assert await journal.list() == []
    assert not fleet.group_authority.called
    # Correcting the form can use the same name; no stranded immutable intent.
    config['members'][1]['resources']['memory_bytes'] = 4 << 30
    assert (await manager.group_deployments('create', 'budget', config))['creation']['phase'] == 'intent'


def test_snapshot_receipt_rpc_is_owner_only_and_does_not_download(tmp_path, monkeypatch, prepared):
    import httpx
    from test_model_services import connector_module, serve
    from pantheon.models.managed import module
    monkeypatch.setenv('PANTHEON_APP_RPC_TOKEN', 'group-receipt-owner-token')
    connector = connector_module.Connector(tmp_path / 'connector')
    # Real prepared cache from fixture; no connector download manager needed.
    monkeypatch.setattr(connector, 'downloads', lambda: SimpleNamespace(cache=SimpleNamespace(root=tmp_path / 'blobs')))
    before = sorted((p.relative_to(tmp_path).as_posix(), p.stat().st_size) for p in tmp_path.rglob('*') if p.is_file())
    with serve(connector_module.handler(connector)) as endpoint, httpx.Client() as client:
        body = {'method': 'group_snapshot', 'args': {'sha256': prepared[0]['sha256']}}
        for headers in ({}, {'X-Pantheon-App-Token': 'consumer'}, {'X-Fleet-RPC-Token': 'wrong'}):
            assert client.post(endpoint + '/rpc', json=body, headers=headers).status_code == 403
        result = client.post(endpoint + '/rpc', json=body, headers={'X-Fleet-RPC-Token': connector.rpc_token})
        assert result.status_code == 200
        assert result.json() == module('group_model').descriptor(prepared[0])
        assert 'mtime_ns' not in result.text and str(tmp_path) not in result.text
    after = sorted((p.relative_to(tmp_path).as_posix(), p.stat().st_size) for p in tmp_path.rglob('*') if p.is_file())
    assert before == after


async def platform_setup(durable, prepared, tmp_path, monkeypatch, scopes=('app-1/us-east', 'app-1/us-east')):
    manager, config, fleet, journal = await setup(durable, prepared, tmp_path, monkeypatch)
    nodes = {m['node_id']: i for i, m in enumerate(prepared[1]['members'])}
    async def node(node_id, managed=False):
        rank = nodes[node_id]
        return dict(capability=dict(os='linux', arch='amd64', caps=['proc', 'model-group-platform-network'],
            runtimes={'group-platform-network': 'modal-i6pn', 'group-platform-address': f'fdaa:0:0:{rank+1}::5',
                      'group-platform-interface': 'eth1', 'group-platform-scope': scopes[rank]}))
    manager.node = AsyncMock(side_effect=node)
    config['network_mode'] = 'platform-private'
    for member in config['members']:
        del member['underlay']
    return manager, config, fleet, journal


@pytest.mark.asyncio
async def test_platform_private_group_uses_node_provider_addresses_without_overlay(durable, prepared, tmp_path, monkeypatch):
    # Sub-regions differ in practice (us-east / us-east1); the environment matches.
    manager, config, fleet, journal = await platform_setup(durable, prepared, tmp_path, monkeypatch,
                                                           ('main/us-east', 'main/us-east1'))
    created = (await manager.group_deployments('create', 'modal', config))['creation']
    plan = created['plan']
    assert plan['network_mode'] == 'platform-private' and 'underlay' not in plan
    assert plan['recipe_id'] == 'sglang-0.5.20-linux-amd64-process'
    assert [m['address'] for m in plan['members']] == ['fdaa:0:0:1::5', 'fdaa:0:0:2::5']
    assert {m['interface'] for m in plan['members']} == {'eth1'}
    assert not hasattr(fleet, 'group_overlay')  # any overlay RPC would fail loudly
    for _ in range(5):
        result = await manager.group_deployments('advance', 'modal')
    assert result['creation']['phase'] == 'handed_off'
    network = result['group']['peer_security']['network']
    assert network == dict(mode='platform-private', addresses=['fdaa:0:0:1::5', 'fdaa:0:0:2::5'],
                           endpoints=[], ready=False, closed=False)
    assert all(m['install']['request']['action'] == 'install' for m in result['group']['members'])


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['no_capability', 'other_app', 'underlay'])
async def test_platform_private_creation_rejects_unsafe_topology(durable, prepared, tmp_path, monkeypatch, fault):
    scopes = ('app-1/us-east', 'app-2/us-east') if fault == 'other_app' else ('app-1/us-east',) * 2
    manager, config, fleet, journal = await platform_setup(durable, prepared, tmp_path, monkeypatch, scopes)
    if fault == 'no_capability':
        manager.node = AsyncMock(return_value=dict(capability=dict(os='linux', arch='amd64', caps=['model-group-private-network'])))
    elif fault == 'underlay':
        config['members'][0]['underlay'] = '192.168.20.10:18441'
    with pytest.raises(ValueError):
        await manager.group_deployments('create', 'modal', config)
    assert await journal.list() == []
