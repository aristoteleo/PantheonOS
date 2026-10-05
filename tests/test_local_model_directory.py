"""Durable local publications, not static catalog fixtures in the launcher."""
import asyncio
import json
import threading

import pytest

from pantheon.models.errors import ControlError
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.dependency_service import ModelServiceControl


def publication():
    return dict(deployment_id='local', name='Local', node_id='node', node_name='Local node',
        engine='ollama', mode='attached', state='ready', config_revision='b'*64, revision=0,
        binding=dict(node_id='node', instance_id='connector', revision='a'*64, generation=2,
                     component='backend', port='http'),
        models=[dict(id='model', name='Model', operations=['text'], tools=True, compute='node', context=8192)])


def alias():
    return dict(route_id='preferred', name='Preferred', candidates=[dict(deployment_id='local', model_id='model')],
                allowed_nodes=['node'], requires={'tools': True})


@pytest.mark.asyncio
async def test_publication_restart_cas_readonly_and_owner_isolation(tmp_path):
    directory = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await directory.initialize()
    row = await directory.save(publication())
    assert row['revision'] == 1 and row['models'][0]['vision'] is None
    other = LocalModelDirectory(directory.root, owner='owner')
    reader = LocalModelDirectory(directory.root, owner='owner', read_only=True)
    assert await reader.deployments() == [row]
    # Independent handles competing with the same revision cannot overwrite.
    results = await asyncio.gather(directory.save(row | {'name': 'A'}),
                                   other.save(row | {'name': 'B'}), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    loser = next(r for r in results if isinstance(r, Exception))
    assert isinstance(loser, ControlError) and loser.status == 409
    saved = (await other.deployments())[0]
    assert saved['revision'] == 2 and saved['name'] in ('A', 'B')
    for op in (reader.save(saved), reader.initialize(),
               LocalModelDirectory(directory.root, owner='someone-else').initialize()):
        with pytest.raises(ControlError): await op
    # A read returns a detached snapshot, not a mutable reference into storage.
    saved['models'].clear()
    assert (await other.deployments())[0]['models']


@pytest.mark.asyncio
async def test_routes_share_publication_policy_and_pin_consumer_revisions(tmp_path):
    directory = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await directory.initialize()
    row = await directory.save(publication())
    route_path = '/api/model-services/routes/preferred'
    route = await directory.hub_request('PUT', route_path, alias())
    plan = await directory.hub_request('POST', route_path+'/resolve', {})
    assert plan['resolved'] and plan['transport'] == 'fleet_relay'
    assert plan['candidates'][0]['deployment'] == row
    assert plan['candidates'][0]['billing'] == 'local'
    for requirements, reason in [({'vision': True}, 'vision_unconfirmed'),
                                  ({'context': 16384}, 'context_unconfirmed_or_insufficient'),
                                  ({'operation': 'image'}, 'operation_not_supported')]:
        failed = await directory.hub_request('POST', route_path+'/resolve', requirements)
        assert not failed['resolved'] and failed['excluded'][0]['reason'] == reason
    control = ModelServiceControl(directory, policies={'consumer': {
        'consumer': dict(node_id='node', instance_id='agent', revision='c'*64, generation=1),
        'deployments': {'local': row['binding']}, 'routes': {'preferred': 1}, 'allow_wake': False}})
    async def resolve():
        return await control.model_services_control(policy_id='consumer', operation='resolve',
            arguments={'route_id': 'preferred', 'requirements': {}})
    assert (await resolve())['status'] == 200
    await directory.hub_request('DELETE', route_path, {'revision': 1})
    replacement = await directory.hub_request('PUT', route_path, alias())
    assert replacement['revision'] == 2  # Old authority cannot revive after deletion.
    assert (await resolve())['status'] == 403
    # Restrictive routes can be stored but must not authorize another location.
    replacement['allowed_nodes'] = ['different']
    replacement = await directory.hub_request('PUT', route_path, replacement)
    assert (await directory.hub_request('POST', route_path+'/resolve', {}))['excluded'][0]['reason'] == 'node_not_allowed'
    with pytest.raises(ControlError):
        await directory.hub_request('DELETE', '/api/model-services/local', {'revision': 1})
    row = await directory.save(row | {'state': 'stopped'})
    with pytest.raises(ControlError):
        await directory.hub_request('DELETE', '/api/model-services/local', {'revision': row['revision']})
    await directory.hub_request('DELETE', route_path, {'revision': replacement['revision']})
    await directory.hub_request('DELETE', '/api/model-services/local', {'revision': row['revision']})
    assert await directory.deployments() == []
    assert (await directory.save(publication()))['revision'] == 3


@pytest.mark.asyncio
async def test_cancelled_publication_finishes_before_returning(tmp_path, monkeypatch):
    directory = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await directory.initialize()
    entered, release = threading.Event(), threading.Event()
    write = directory._write
    def delayed(path, value):
        entered.set()
        assert release.wait(5)
        write(path, value)
    monkeypatch.setattr(directory, '_write', delayed)
    task = asyncio.create_task(directory.save(publication()))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert (await directory.deployments())[0]['revision'] == 1


@pytest.mark.parametrize('change', [
    {'endpoint': 'http://127.0.0.1:1234'}, {'api_key': 'secret'}, {'mode': 'managed'},
    {'engine_binding': {}}, {'engine_idle': {}}, {'config_revision': 'wrong'},
    {'binding': None}, {'revision': True}, {'models': [{'id': 'x', 'tools': 'yes'}]},
    {'models': [{'id': 'x', 'operations': ['unrecognized']}]},
    {'models': [{'id': 'x'}, {'id': 'x'}]},
])
@pytest.mark.asyncio
async def test_reject_credentials_incomplete_or_unimplemented_lifecycles(tmp_path, change):
    directory = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await directory.initialize()
    before = (directory.root/'directory.json').read_bytes()
    with pytest.raises(ControlError): await directory.save(publication() | change)
    assert (directory.root/'directory.json').read_bytes() == before


@pytest.mark.parametrize('damage', ['json', 'owner', 'duplicate', 'permissions', 'symlink', 'missing'])
@pytest.mark.asyncio
async def test_missing_or_damaged_catalog_never_becomes_an_empty_success(tmp_path, damage):
    directory = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await directory.initialize()
    await directory.save(publication())
    path = directory.root/'directory.json'
    if damage == 'json': path.write_text('broken')
    elif damage == 'duplicate': path.write_text('{"protocol":1,"protocol":1}')
    elif damage == 'owner':
        value = json.loads(path.read_text()); value['owner'] = 'foreign'; path.write_text(json.dumps(value))
    elif damage == 'permissions': path.chmod(0o644)
    elif damage == 'symlink':
        original = directory.root/'actual'; path.rename(original); path.symlink_to(original)
    else: path.unlink()
    with pytest.raises((ControlError, ValueError, OSError)):
        await directory.deployments()
    with pytest.raises((ControlError, ValueError, OSError)):
        await directory.initialize()


@pytest.mark.asyncio
async def test_failed_atomic_replace_retains_previous_publication(tmp_path, monkeypatch):
    directory = LocalModelDirectory(tmp_path/'catalog', owner='owner')
    await directory.initialize()
    row = await directory.save(publication())
    def fail(*args): raise OSError('injected replace failure')
    monkeypatch.setattr('pantheon.apps.owner_journal.os.replace', fail)
    with pytest.raises(OSError): await directory.save(row | {'name': 'not committed'})
    assert await directory.deployments() == [row]
    assert not list(directory.root.glob('*.tmp'))
