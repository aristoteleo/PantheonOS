"""Composed startup abort keeps its exact child operations and publications."""
from copy import deepcopy

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from test_model_bootstrap import rig, finish, clean_restart, budget_startup


@pytest.fixture
def abort_rig(rig):
    rig.saves = []
    rig.lose_save = False
    async def save(row):
        current = next(r for r in rig.rows if r['deployment_id'] == row['deployment_id'])
        assert current['revision'] == row['revision']
        updated = {**deepcopy(row), 'revision': row['revision'] + 1}
        rig.rows[rig.rows.index(current)] = updated
        rig.saves.append(deepcopy(updated))
        if rig.lose_save:
            rig.lose_save = False
            raise TimeoutError('lost stopped publication acknowledgement')
        return updated
    rig.manager.save = save
    return rig


def args(rig):
    return dict(owner=rig.spec['owner'], source_operation_id=rig.spec['operation_id'], operation_id='cancel-startup')


async def abort_all(rig, bootstrap=None):
    for _ in range(30):
        result = await (bootstrap or rig.restart()).abort(**args(rig))
        if result['state'] == 'aborted': return result
        rig.nodes.finish()
    pytest.fail('composed abort did not finish')


def all_stopped(rig):
    assert all(i['state'] == 'stopped' and not i.get('resources') and not i.get('reservations')
               for state in rig.nodes.states.values() for i in state['instances'].values())


@pytest.mark.asyncio
async def test_complete_consumer_and_model_abort_order_and_directory_idempotence(abort_rig):
    r = abort_rig
    await finish(r)
    original = deepcopy(r.rows[0])
    result = await abort_all(r)
    all_stopped(r)
    stops = [next(op['request'] for state in r.nodes.states.values() for op in state['operations'].values()
                  if op['request']['operation_id'] == call[2]) for call in r.nodes.calls if call[1] == 'stop']
    assert [s['digest'] for s in stops] == ['b'*64, 'a'*64, 'c'*64]
    assert r.rows == [{**original, 'state': 'stopped', 'revision': original['revision'] + 1,
                       'binding': {**original['binding'], 'generation': original['binding']['generation'] + 1}}]
    calls = deepcopy(r.nodes.calls)
    assert await abort_all(r) == result
    assert r.nodes.calls == calls and len(r.saves) == 1
    with pytest.raises(AssemblyError, match='fenced'): await finish(r)
    assert 'binding' not in result and 'rows' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['install', 'prepare_start', 'start'])
async def test_partial_provider_waits_for_original_operations_and_never_starts_consumer(abort_rig, phase):
    r = abort_rig
    for _ in range(12):
        await r.restart().advance(**r.spec)
        if any(op['request']['action'] == phase and op['state'] == 'queued'
               for op in r.nodes.states['platform']['operations'].values()): break
        r.nodes.finish()
    calls = list(r.nodes.calls)
    for _ in range(2):
        assert (await r.restart().abort(**args(r)))['state'] == 'pending'
    assert r.nodes.calls == calls
    with pytest.raises(AssemblyError, match='fenced'): await finish(r)
    r.nodes.finish()
    await abort_all(r)
    all_stopped(r)
    assert not r.rows and not r.nodes.states['worker']['instances']
    assert not r.registrations


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['save-ack', 'receipt-write'])
async def test_unacknowledged_registration_can_be_stopped_without_discovery(abort_rig, failure):
    r = abort_rig
    bootstrap = r.restart()
    if failure == 'save-ack': r.manager.lose_reply = True
    else:
        original = bootstrap._write
        def write(path, record):
            if record['registered']: raise OSError('lost registration checkpoint')
            original(path, record)
        bootstrap._write = write
    with pytest.raises((OSError, TimeoutError)): await finish(r, bootstrap)
    assert r.rows and not r.nodes.states['worker']['instances']
    async def forbidden(*args, **kwargs): raise AssertionError('Abort must not call the model engine')
    r.manager.rpc = r.manager.register_prepared = forbidden
    await abort_all(r)
    all_stopped(r)
    assert r.rows[0]['state'] == 'stopped'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['node-reply', 'directory-reply', 'abort-checkpoint'])
async def test_interrupted_abort_resumes_original_operations(abort_rig, failure):
    r = abort_rig
    await finish(r)
    bootstrap = r.restart()
    if failure == 'node-reply': r.nodes.loss = 'stop'
    elif failure == 'directory-reply': r.lose_save = True
    else:
        original = bootstrap._write
        def write(path, record):
            if record['phase'] == 'aborted': raise OSError('lost abort checkpoint')
            original(path, record)
        bootstrap._write = write
    with pytest.raises((TimeoutError, OSError)): await abort_all(r, bootstrap)
    r.nodes.finish()
    with pytest.raises(AssemblyError, match='fenced'): await finish(r)
    await abort_all(r)
    all_stopped(r)
    stops = [call[2] for call in r.nodes.calls if call[1] == 'stop']
    assert len(stops) == len(set(stops)) == 3
    assert len(r.saves) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['owner', 'directory', 'deleted', 'pending-model-operation'])
async def test_conflicting_publication_is_not_overwritten_or_stopped(abort_rig, change):
    r = abort_rig
    await finish(r)
    selection = args(r)
    if change == 'owner': selection['owner'] = 'another'
    elif change == 'directory': r.rows[0]['name'] = 'edited'
    elif change == 'deleted': r.rows.clear()
    else: r.rows[0]['recovery'] = {'phase': 'pending'}
    before = deepcopy(r.nodes.calls)
    with pytest.raises(AssemblyError): await r.restart().abort(**selection)
    assert r.nodes.calls == before and not r.saves


@pytest.mark.asyncio
async def test_abort_before_budget_preparation_starts_nothing(abort_rig):
    r = abort_rig
    budget_startup(r)
    bootstrap = r.restart()
    async def fail(**kwargs): raise TimeoutError('credential preparation uncertain')
    bootstrap.prepare_credentials = fail
    with pytest.raises(TimeoutError): await bootstrap.advance(**r.spec)
    assert not r.nodes.calls
    assert (await abort_all(r))['state'] == 'aborted'
    assert not r.nodes.calls and not r.rows
    with pytest.raises(AssemblyError, match='fenced'): await finish(r)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['install', 'prepare_start', 'start', 'rebind-ack'])
async def test_restart_abort_preserves_model_choices_at_verified_stopped_generation(abort_rig, phase):
    r = abort_rig
    await clean_restart(r)
    original = deepcopy(r.rows[0])
    if phase == 'rebind-ack':
        r.manager.lose_reply = True
        with pytest.raises(TimeoutError): await finish(r)
    else:
        for _ in range(12):
            await r.restart().advance(**r.spec)
            if any(op['request']['action'] == phase and op['state'] == 'queued'
                   for op in r.nodes.states['platform']['operations'].values()): break
            r.nodes.finish()
    r.nodes.finish()
    await abort_all(r)
    all_stopped(r)
    assert r.rows[0]['state'] == 'stopped'
    assert r.rows[0]['models'] == original['models']
    binding = r.rows[0]['binding']
    instance = r.nodes.states[binding['node_id']]['instances'][binding['instance_id']]
    assert binding['generation'] == instance['generation']


@pytest.mark.asyncio
async def test_directory_edit_between_pending_stops_keeps_provider_running(abort_rig):
    r = abort_rig
    await finish(r)
    assert (await r.restart().abort(**args(r)))['state'] == 'pending'
    r.nodes.finish()
    calls = deepcopy(r.nodes.calls)
    r.rows[0]['revision'] += 1
    with pytest.raises(AssemblyError, match='publication changed'):
        await r.restart().abort(**args(r))
    assert r.nodes.calls == calls
    provider = r.rows[0]['binding']
    assert r.nodes.states[provider['node_id']]['instances'][provider['instance_id']]['state'] == 'ready'


@pytest.mark.asyncio
async def test_foreign_child_recipe_is_rejected_before_teardown(abort_rig):
    r = abort_rig
    await finish(r)
    bootstrap = r.restart()
    path = r.deployment._path(bootstrap.child_id(r.spec, 'consumers'))
    record = r.deployment._load(path)
    record['recipe']['apps']['agent']['scope'] = 'other-app'
    r.deployment._write(path, record)
    calls = deepcopy(r.nodes.calls)
    with pytest.raises(AssemblyError, match='original recipe'):
        await bootstrap.abort(**args(r))
    assert r.nodes.calls == calls


@pytest.mark.asyncio
async def test_missing_consumer_journal_cannot_abandon_live_consumer(abort_rig):
    r = abort_rig
    await finish(r)
    bootstrap = r.restart()
    r.deployment._path(bootstrap.child_id(r.spec, 'consumers')).unlink()
    calls = deepcopy(r.nodes.calls)
    with pytest.raises(AssemblyError, match='existing node operations'):
        await bootstrap.abort(**args(r))
    assert r.nodes.calls == calls


@pytest.mark.asyncio
async def test_reverted_publication_cannot_replace_recorded_rebind(abort_rig):
    r = abort_rig
    await clean_restart(r)
    previous = deepcopy(r.rows)
    await finish(r)
    r.rows[:] = previous
    calls = deepcopy(r.nodes.calls)
    with pytest.raises(AssemblyError, match='publication changed'):
        await r.restart().abort(**args(r))
    assert r.nodes.calls == calls
