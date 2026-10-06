"""Original management protocols backed by the real, durable local directory."""
import asyncio
from copy import deepcopy

import pytest

from pantheon.models.errors import ControlError
from pantheon.models.local_directory import LocalModelDirectory
from test_local_model_directory import publication
from test_model_recovery import setup as recovery_setup


def managed(row=None):
    row = deepcopy(row or publication())
    row.update(mode='managed', managed={
        'recipe_id': 'ollama-0.34.2-linux-amd64', 'context_length': 4096,
        'parallel': 1, 'keep_alive_seconds': 0, 'load_policy': 'manual',
        'resources': {'memory_bytes': 4 << 30, 'devices': []}},
        engine_binding={**row['binding'], 'instance_id': 'engine', 'revision': 'e'*64})
    return row


async def directory(tmp_path, row=None):
    db = LocalModelDirectory(tmp_path/'directory', owner='owner')
    await db.initialize()
    return db, await db.save((row or managed()) | {'revision': 0})


async def refused_without_write(db, row):
    before = (db.root/'directory.json').read_bytes()
    with pytest.raises(ControlError):
        await db.save(row)
    assert (db.root/'directory.json').read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('dead', [False, True])
@pytest.mark.parametrize('lost', ['', 'recover', 'configure', 'resume'])
async def test_original_manager_recovers_against_durable_directory(tmp_path, monkeypatch, dead, lost):
    manager, original, lifecycle, state, connector = recovery_setup(monkeypatch, dead=dead, managed=True, lost=lost)
    db, row = await directory(tmp_path, managed(original.row))
    manager.client = db
    if lost:
        with pytest.raises(ConnectionError):
            await manager.recover(row['deployment_id'])
        interrupted = await db.deployment(row['deployment_id'])
        assert interrupted['state'] == 'recovering' and interrupted['recovery']
        # Reopen durable storage. Original manager resumes the persisted intent;
        # the uncertain RPC cannot authorize a new engine or model download.
        manager.client = LocalModelDirectory(db.root, owner='owner')
    result = await manager.recover(row['deployment_id'])
    assert result['state'] == 'ready' and result['recovery'] is None
    assert result['managed'] == row['managed'] and result['models'] == row['models']
    assert result['binding']['generation'] == (4 if dead else 2)
    assert connector.accepting
    assert (await LocalModelDirectory(db.root, owner='owner', read_only=True).deployments()) == [result]
    assert all(action in {'recover', 'start'} for action, _ in lifecycle.actions)


@pytest.mark.asyncio
async def test_engine_update_pins_exact_target_across_reopen(tmp_path):
    db, row = await directory(tmp_path)
    target = row['managed'] | {'recipe_id': 'ollama-0.34.3-linux-amd64'}
    intent = {'operation_id': '1'*32, 'source': row['engine_binding'], 'connector': row['binding'],
              'target_revision': 'f'*64, 'target_instance_id': 'replacement', 'target_config': target,
              'started_at': 1.0, 'phase': 'draining'}
    pending = await db.save(row | {'state': 'stopping', 'engine_update': intent})
    db = LocalModelDirectory(db.root, owner='owner')
    await refused_without_write(db, pending | {'engine_update': intent | {'target_revision': '9'*64}})
    await refused_without_write(db, pending | {'state': 'ready', 'engine_update': None})
    replacement = row['engine_binding'] | {'revision': 'f'*64, 'instance_id': 'replacement', 'generation': 1}
    finished = await db.save(pending | {'state': 'ready', 'engine_update': None,
                                      'managed': target, 'engine_binding': replacement})
    assert finished['engine_binding'] == replacement and finished['managed'] == target
    await refused_without_write(db, finished | {'managed': row['managed']})


@pytest.mark.asyncio
async def test_explicit_stop_fences_late_recovery_publication(tmp_path):
    db, row = await directory(tmp_path)
    recovering = await db.save(row | {'state': 'recovering', 'recovery': {
        'operation_id': '1'*32, 'binding': row['binding'], 'engine_binding': row['engine_binding']}})
    stop = {'operation_id': '2'*32, 'kind': 'recovery', 'started_at': 1.0, 'targets': [
        {'role': role, 'scope': prefix+row['deployment_id'], 'binding': row[role]}
        for role, prefix in [('binding', 'model-'), ('engine_binding', 'engine-')]]}
    stopping = await db.save(recovering | {'operation_stop': stop})
    db = LocalModelDirectory(db.root, owner='owner')
    await refused_without_write(db, stopping | {'state': 'ready', 'recovery': None, 'operation_stop': None})
    stopped = await db.save(stopping | {'state': 'stopped', 'recovery': None, 'operation_stop': None,
        'last_operation_stop': stop | {'completed_at': 2.0},
        'binding': row['binding'] | {'generation': row['binding']['generation']+1},
        'engine_binding': row['engine_binding'] | {'generation': row['engine_binding']['generation']+1}})
    await refused_without_write(db, stopped | {'last_operation_stop': None})
    assert await db.remove(stopped['deployment_id'], stopped['revision']) == {'removed': stopped['deployment_id']}


@pytest.mark.asyncio
async def test_idle_intent_cannot_rebind_or_drop_its_revision(tmp_path):
    value = managed()
    value['managed']['load_policy'] = 'on_demand'
    db, row = await directory(tmp_path, value)
    registration = {'id': row['deployment_id'], 'revision': 0, 'config_revision': row['config_revision'],
        'idle_seconds': 60, **{name: {key: row[source][key] for key in ('instance_id', 'revision', 'generation')}
                             for name, source in [('connector', 'binding'), ('engine', 'engine_binding')]}}
    policy = {'idle_seconds': 60, 'policy_revision': 0, 'phase': 'registering', 'registration': registration}
    row = await db.save(row | {'engine_idle': policy})
    row = await db.save(row | {'engine_idle': policy | {'phase': 'enabled', 'policy_revision': 1}})
    await refused_without_write(db, row | {'engine_idle': None})
    await refused_without_write(db, row | {'binding': row['binding'] | {'generation': 99}})
    row = await db.save(row | {'engine_idle': row['engine_idle'] | {'phase': 'disabling'}})
    row = await db.save(row | {'engine_idle': row['engine_idle'] | {'phase': 'disabled', 'policy_revision': 2}})
    assert (await db.save(row | {'state': 'stopped'}))['state'] == 'stopped'


@pytest.mark.asyncio
async def test_competing_managed_writers_cannot_replace_each_others_intent(tmp_path):
    db, row = await directory(tmp_path)
    peer = LocalModelDirectory(db.root, owner='owner')
    results = await asyncio.gather(db.save(row | {'state': 'stopping'}),
                                  peer.save(row | {'name': 'Changed'}), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert next(result for result in results if isinstance(result, ControlError)).status == 409


@pytest.mark.asyncio
async def test_rejected_credentials_are_not_echoed_in_validation_errors(tmp_path):
    db, row = await directory(tmp_path)
    before = (db.root/'directory.json').read_bytes()
    secret = 'do-not-expose-this-accidental-credential'
    with pytest.raises(ControlError) as rejected:
        await db.save(row | {'api_key': secret})
    assert rejected.value.status == 400 and secret not in str(rejected.value)
    assert (db.root/'directory.json').read_bytes() == before
