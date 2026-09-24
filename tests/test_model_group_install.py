"""Installation scheduling, original artifacts and cancellation arbitration."""
import asyncio
from copy import deepcopy
import hashlib

import pytest

import test_model_groups as fixture
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupConflict, GroupJournal


PAYLOAD = b'original immutable rank archive fixture'
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class Packages:
    def __init__(self):
        self.reads, self.missing = [], False

    def artifact(self, digest):
        self.reads.append(digest)
        if self.missing:
            raise ValueError('Original artifact is missing')
        assert digest == DIGEST
        return PAYLOAD


class InstallingFleet(fixture.Fleet):
    def __init__(self):
        super().__init__()
        for state in self.nodes.values():
            state['installations'] = {}
        self.staged, self.executions = [], []
        self.before, self.failed_stage = set(), set()
        self.stage_entered, self.stage_release = asyncio.Event(), None

    async def stage_exact(self, node, payload, digest):
        assert payload == PAYLOAD and hashlib.sha256(payload).hexdigest() == digest
        self.stage_entered.set()
        if self.stage_release:
            await self.stage_release.wait()
        if node in self.failed_stage:
            raise ConnectionError('disconnected during original archive staging')
        self.staged.append((node, digest))
        return digest

    async def submit(self, node, **request):
        if request['action'] == 'install':
            request = dict(protocol=1, **request)
            self.calls.append((node, request))
            if node in self.before:
                self.before.remove(node)
                raise ConnectionError('request did not reach node')
            state = self.nodes[node]
            previous = state['operations'].get(request['operation_id'])
            if previous:
                assert previous['request'] == request
                return deepcopy(previous)
            self.executions.append((node, request['operation_id']))
            action = ('running' if (node, 'install') in self.pending else
                      'failed' if (node, 'install') in self.failed else 'succeeded')
            op = state['operations'][request['operation_id']] = dict(request=request, state=action)
            state['installations'][request['digest']] = dict(digest=request['digest'],
                state={'running': 'installing', 'failed': 'failed', 'succeeded': 'installed'}[action])
            if (node, 'install') in self.lost:
                raise ConnectionError('install committed, acknowledgement lost')
            return deepcopy(op)
        if request['action'] == 'prepare_start':
            assert all(s['installations'].get(DIGEST, {}).get('state') == 'installed' for s in self.nodes.values())
        return await super().submit(node, **request)

    def complete_install(self, node):
        for op in self.nodes[node]['operations'].values():
            if op['request']['action'] == 'install':
                op['state'] = 'succeeded'
        self.nodes[node]['installations'][DIGEST]['state'] = 'installed'


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, 'DIGEST', DIGEST)
    journal = GroupJournal(tmp_path / 'groups.db', fixture.OWNER)
    journal.create('test', fixture.targets(), install=True)
    fleet, packages = InstallingFleet(), Packages()
    return journal, fleet, packages, GroupCoordinator(journal, fleet, packages=packages)


@pytest.mark.asyncio
async def test_stage_install_all_prepare_barrier_lost_reply_and_restart(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    row = await coordinator.advance('test')
    assert all(m['install']['staged'] and not m['install']['sent'] for m in row['members'])
    assert not fleet.calls and len(fleet.staged) == 2
    fleet.lost = {('node-a', 'install'), ('node-b', 'install')}
    await coordinator.advance('test')
    assert [r['action'] for _, r in fleet.calls] == ['install', 'install']
    original = deepcopy([m['install']['request'] for m in journal.load('test')['members']])
    # Stored staging acknowledgements survive Agent replacement; no archive is
    # reread or transmitted while the original accepted installation is polled.
    packages.missing = True
    coordinator = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', fixture.OWNER), fleet, packages=packages)
    await fixture.drive(coordinator, 'ready')
    assert len(packages.reads) == 2 and len(fleet.executions) == 2
    assert [m['install']['request'] for m in journal.load('test')['members']] == original
    await coordinator.stop('test')
    await fixture.drive(coordinator, 'stopped')
    assert all(s['installations'][DIGEST]['state'] == 'installed' for s in fleet.nodes.values())


@pytest.mark.asyncio
async def test_missing_install_delivery_reuses_original_id(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    fleet.before = {'node-a'}
    await fixture.drive(coordinator, 'ready')
    sent = [r['operation_id'] for n, r in fleet.calls if n == 'node-a' and r['action'] == 'install']
    assert len(sent) == 2 and len(set(sent)) == 1
    assert len(fleet.executions) == 2 and len(fleet.staged) == 2
    await coordinator.stop('test')
    await fixture.drive(coordinator, 'stopped')


@pytest.mark.asyncio
async def test_install_claim_lost_before_delivery_is_fenced_on_abort(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    await coordinator.advance('test')
    row = journal.load('test')
    row['members'][0]['install']['sent'] = True
    delayed = deepcopy(row['members'][0]['install']['request'])
    journal.save(row)
    await coordinator.stop('test')
    await fixture.drive(coordinator, 'stopped')
    assert fleet.fences == [('node-a', delayed)] and not fleet.executions
    result = await fleet.submit('node-a', **{k: v for k, v in delayed.items() if k != 'protocol'})
    assert result['state'] == 'cancelled' and not fleet.executions
    assert not any(r['action'] == 'prepare_start' for _, r in fleet.calls)


@pytest.mark.asyncio
async def test_accepted_install_does_not_reinstall_or_release_until_terminal(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    fleet.pending = {('node-b', 'install')}
    await coordinator.advance('test')
    await coordinator.advance('test')
    for _ in range(2):
        await coordinator.advance('test')
    assert not any(r['action'] == 'prepare_start' for _, r in fleet.calls)
    await coordinator.stop('test')
    for _ in range(2):
        assert (await coordinator.advance('test'))['phase'] == 'aborting'
    fleet.complete_install('node-b')
    await fixture.drive(coordinator, 'stopped')
    assert len(fleet.executions) == 2 and len(fleet.staged) == 2


@pytest.mark.asyncio
async def test_stop_racing_staging_never_submits_install(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    fleet.stage_release = asyncio.Event()
    pending = asyncio.create_task(coordinator.advance('test'))
    try:
        await asyncio.wait_for(fleet.stage_entered.wait(), 2)
        other = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', fixture.OWNER), fleet)
        await other.stop('test')
        await fixture.drive(other, 'stopped')
    finally:
        fleet.stage_release.set()
        await asyncio.wait_for(pending, 2)
    assert journal.load('test')['phase'] == 'stopped' and not fleet.calls


@pytest.mark.asyncio
async def test_stage_error_and_install_failure_never_prepare_partial_group(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    fleet.failed_stage = {'node-a'}
    await coordinator.advance('test')
    await coordinator.advance('test')
    assert not journal.load('test')['members'][0]['install']['staged']
    assert {n for n, _ in fleet.calls} == {'node-b'}
    fleet.failed_stage.clear()
    fleet.failed = {('node-a', 'install')}
    await fixture.drive(coordinator, 'stopped')
    assert all(r['action'] == 'install' for _, r in fleet.calls)


@pytest.mark.asyncio
async def test_original_artifact_and_installer_request_are_immutable(tmp_path, monkeypatch):
    journal, fleet, packages, coordinator = setup(tmp_path, monkeypatch)
    packages.missing = True
    row = await coordinator.advance('test')
    assert not any(m['install']['staged'] for m in row['members']) and not fleet.calls
    for field, value in [('sent', True), ('request', {**row['members'][0]['install']['request'], 'operation_id': 'changed'})]:
        changed = deepcopy(row)
        changed['members'][0]['install'][field] = value
        with pytest.raises(GroupConflict):
            journal.save(changed)
    changed = deepcopy(row)
    del changed['members'][0]['install']
    with pytest.raises(GroupConflict):
        journal.save(changed)
    with pytest.raises(ValueError, match='original group package store'):
        await GroupCoordinator(journal, fleet).advance('test')
