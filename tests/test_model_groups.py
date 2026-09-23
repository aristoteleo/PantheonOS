import asyncio
from copy import deepcopy
import hashlib

import pytest

from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupConflict, GroupJournal


OWNER = 'test-owner'
DIGEST = 'a' * 64


def targets():
    return [dict(node_id=node, digest=DIGEST, scope='engine-group', generation=0) for node in ('node-a', 'node-b')]


class Fleet:
    """Fault-injection transport. Actual process coverage is in the Go harness."""
    def __init__(self):
        self.nodes = {t['node_id']: dict(protocol=1, owner=OWNER, node_id=t['node_id'],
            instances={}, operations={}) for t in targets()}
        self.calls = []
        self.failed = set()
        self.lost = set()
        self.offline = set()
        self.block = None
        self.pending = set()

    async def status(self, node):
        if node in self.offline:
            raise ConnectionError('offline')
        return deepcopy(self.nodes[node])

    async def submit(self, node, **request):
        request = dict(protocol=1, **request)
        self.calls.append((node, request))
        if self.block:
            await self.block.wait()
        snapshot = self.nodes[node]
        if request['operation_id'] in snapshot['operations']:
            raise AssertionError('Coordinator replayed a mutation')
        action = request['action']
        if (node, action) in self.pending:
            snapshot['operations'][request['operation_id']] = dict(request=request, state='running')
            return
        failed = (node, action) in self.failed
        snapshot['operations'][request['operation_id']] = dict(request=request, state='failed' if failed else 'succeeded')
        identity = hashlib.sha256('\0'.join((OWNER, node, request['digest'], request['scope'])).encode()).hexdigest()[:32]
        if not (failed and action == 'prepare_start'):
            state = {'prepare_start': 'prepared', 'start': 'failed' if failed else 'ready', 'stop': 'stopped'}[action]
            snapshot['instances'][identity] = dict(instance_id=identity, digest=request['digest'], scope=request['scope'],
                generation=request['generation'] + 1, state=state,
                start_preparation_id=request['operation_id'] if action == 'prepare_start' else '',
                ready_generation=request['generation'] + 1 if action == 'start' and not failed else 0,
                resources=[{'id': 'process'}] if action == 'start' else [],
                reservations={'component-backend': {}} if action != 'stop' else {})
        if (node, action) in self.lost:
            raise ConnectionError('reply lost after commit')


def setup(tmp_path):
    journal = GroupJournal(tmp_path / 'groups.db', OWNER)
    journal.create('test', targets())
    fleet = Fleet()
    return journal, fleet, GroupCoordinator(journal, fleet)


async def drive(coordinator, phase, steps=8):
    for _ in range(steps):
        row = await coordinator.advance('test')
        if row['phase'] == phase:
            return row
    pytest.fail(f'Group did not reach {phase}: {row}')


@pytest.mark.asyncio
async def test_barrier_lost_replies_restart_and_exact_cleanup(tmp_path):
    journal, fleet, coordinator = setup(tmp_path)
    fleet.lost = {(n, a) for n in fleet.nodes for a in ('prepare_start', 'start', 'stop')}
    await coordinator.advance('test')
    assert {r['action'] for _, r in fleet.calls} == {'prepare_start'}
    await coordinator.advance('test')
    assert journal.load('test')['phase'] == 'committing'
    assert len(fleet.calls) == 2
    restarted = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
    await drive(restarted, 'ready')
    assert len(fleet.calls) == 4
    restarted.stop('test')
    await drive(restarted, 'stopped')
    assert len(fleet.calls) == 6
    assert all(not i['resources'] and not i['reservations']
               for state in fleet.nodes.values() for i in state['instances'].values())
    assert {r['generation'] for _, r in fleet.calls if r['action'] == 'stop'} == {2}


@pytest.mark.asyncio
async def test_capacity_failure_cancels_only_confirmed_preparation(tmp_path):
    _, fleet, coordinator = setup(tmp_path)
    fleet.failed.add(('node-b', 'prepare_start'))
    await drive(coordinator, 'stopped')
    assert [(n, r['action'], r['generation']) for n, r in fleet.calls] == [
        ('node-a', 'prepare_start', 0), ('node-b', 'prepare_start', 0), ('node-a', 'stop', 1)]


@pytest.mark.asyncio
async def test_partial_start_failure_stops_all_owned_ranks(tmp_path):
    _, fleet, coordinator = setup(tmp_path)
    fleet.failed.add(('node-b', 'start'))
    await drive(coordinator, 'stopped')
    assert len(fleet.calls) == 6
    assert {r['generation'] for _, r in fleet.calls if r['action'] == 'stop'} == {2}


@pytest.mark.asyncio
async def test_queued_start_and_offline_node_are_not_assumed_dead(tmp_path):
    journal, fleet, coordinator = setup(tmp_path)
    fleet.pending.add(('node-b', 'start'))
    await drive(coordinator, 'committing')
    await coordinator.advance('test')
    coordinator.stop('test')
    fleet.offline.add('node-b')
    for _ in range(3):
        await coordinator.advance('test')
    assert journal.load('test')['phase'] == 'aborting'
    assert [(n, r['action']) for n, r in fleet.calls if r['action'] == 'stop'] == [('node-a', 'stop')]
    fleet.offline.clear()
    await coordinator.advance('test')
    assert journal.load('test')['members'][1]['observation']['state'] == 'pending'
    assert len(fleet.calls) == 5


@pytest.mark.asyncio
async def test_crash_before_rpc_is_uncertain_without_automatic_replay(tmp_path):
    journal, fleet, coordinator = setup(tmp_path)
    row = journal.load('test')
    row['members'][0]['prepare']['sent'] = True
    journal.save(row)  # Simulate crash immediately after durable claim.
    coordinator.stop('test')
    for _ in range(3):
        await coordinator.advance('test')
    assert not fleet.calls
    assert journal.load('test')['phase'] == 'aborting'
    assert journal.load('test')['members'][0]['observation']['state'] == 'unknown'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['generation', 'owner', 'operation'])
async def test_foreign_generation_owner_or_request_is_never_stopped(tmp_path, change):
    journal, fleet, coordinator = setup(tmp_path)
    await drive(coordinator, 'ready')
    state = fleet.nodes['node-b']
    if change == 'generation':
        next(iter(state['instances'].values()))['generation'] += 1
    elif change == 'owner':
        state['owner'] = 'another-owner'
    else:
        op = journal.load('test')['members'][1]['start']['request']['operation_id']
        state['operations'][op]['request']['digest'] = 'b' * 64
    coordinator.stop('test')
    await coordinator.advance('test')
    assert not any(n == 'node-b' and r['action'] == 'stop' for n, r in fleet.calls)
    assert journal.load('test')['members'][1]['observation']['state'] == 'conflict'


@pytest.mark.asyncio
async def test_concurrent_workers_and_delayed_sender_during_stop(tmp_path):
    journal, fleet, coordinator = setup(tmp_path)
    other = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
    fleet.block = asyncio.Event()
    worker = asyncio.create_task(coordinator.advance('test'))
    for _ in range(100):
        if len(fleet.calls) == 2:
            break
        await asyncio.sleep(0)
    assert len(fleet.calls) == 2
    other.stop('test')
    await other.advance('test')
    assert journal.load('test')['phase'] == 'aborting'
    assert len(fleet.calls) == 2
    fleet.block.set()
    await worker
    await drive(other, 'stopped')
    assert len(fleet.calls) == 4
    assert not any(r['action'] == 'start' for _, r in fleet.calls)


@pytest.mark.asyncio
async def test_journal_failure_prevents_all_remote_mutations(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path)
    def fail(row):
        raise OSError('disk full')
    monkeypatch.setattr(journal, 'save', fail)
    with pytest.raises(OSError, match='disk full'):
        await coordinator.advance('test')
    assert not fleet.calls


def test_journal_cas_owner_isolation_and_immutable_creation(tmp_path):
    journal, _, _ = setup(tmp_path)
    old = journal.load('test')
    journal.save(journal.load('test'))
    with pytest.raises(GroupConflict, match='changed'):
        journal.save(old)
    with pytest.raises(GroupConflict, match='already exists'):
        journal.create('test', targets())
    with pytest.raises(KeyError):
        GroupJournal(tmp_path / 'groups.db', 'other-owner').load('test')
    with pytest.raises(ValueError, match='duplicate'):
        journal.create('duplicate', [targets()[0], targets()[0]])
    changed = journal.load('test')
    changed['members'][0]['target']['node_id'] = 'replacement-node'
    with pytest.raises(GroupConflict, match='immutable'):
        journal.save(changed)


@pytest.mark.asyncio
async def test_concurrent_observers_claim_each_submission_once(tmp_path):
    journal, fleet, coordinator = setup(tmp_path)
    other = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
    for _ in range(4):
        await asyncio.gather(coordinator.advance('test'), other.advance('test'))
    assert journal.load('test')['phase'] == 'ready'
    assert len(fleet.calls) == 4
    assert len({r['operation_id'] for _, r in fleet.calls}) == 4


@pytest.mark.asyncio
async def test_runner_restart_stops_only_exact_owned_unknown_generation(tmp_path):
    _, fleet, coordinator = setup(tmp_path)
    await drive(coordinator, 'ready')
    for state in fleet.nodes.values():
        for op in state['operations'].values():
            if op['request']['action'] == 'start':
                op['state'] = 'unknown'
        next(iter(state['instances'].values()))['state'] = 'unknown'
    await drive(coordinator, 'stopped')
    assert len(fleet.calls) == 6
    assert {r['generation'] for _, r in fleet.calls if r['action'] == 'stop'} == {2}


@pytest.mark.asyncio
async def test_preparation_cancelled_in_fleet_prevents_group_commit(tmp_path):
    _, fleet, coordinator = setup(tmp_path)
    await coordinator.advance('test')
    await fleet.submit('node-b', action='stop', digest=DIGEST, scope='engine-group',
                       generation=1, operation_id='explicit-fleet-cancel')
    await drive(coordinator, 'stopped')
    assert not any(r['action'] == 'start' for _, r in fleet.calls)
    assert len(fleet.calls) == 4
