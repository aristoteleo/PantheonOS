import asyncio
from copy import deepcopy
import json

import pytest

import test_model_groups as lifecycle_fixture
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupConflict, GroupJournal
from pantheon.models.group_network import PeerTopology


OWNER = 'f_' + 'a' * 16


def security():
    return dict(ca_sha256='c' * 64, ready=False, closed=False, topology=dict(
        protocol=1, owner=OWNER, group_id='test', model_sha256='d' * 64,
        launch_sha256='e' * 64, members=[dict(rank=i, node_id=node, generation=2,
            address=f'10.20.0.{i+1}', port=18400) for i, node in enumerate(('node-a', 'node-b'))]))


class SecuredFleet(lifecycle_fixture.Fleet):
    """Fault-injection only. Actual keys/CA/RPC recovery are tested in Go."""
    def __init__(self):
        super().__init__()
        self.peers = PeerTopology(security()['topology'])
        self.issued, self.installed = {}, set()
        self.credential_calls, self.lose = [], set()
        self.closed, self.close_unavailable = False, False
        self.issue_gate, self.issue_entered = None, asyncio.Event()
        self.bad_reply = None

    def drop(self, node, action):
        if (node, action) in self.lose:
            self.lose.remove((node, action))
            raise ConnectionError('reply lost after durable acknowledgement')

    async def group_peer(self, binding, *, certificate=None, authority=None):
        node = binding['node_id']
        self.credential_calls.append((node, 'install' if certificate else 'enroll'))
        current = self.nodes[node]['instances'][binding['instance_id']]
        assert current['state'] == 'prepared' and current['generation'] == binding['generation']
        assert current['digest'] == binding['revision']
        rank = next(m['rank'] for m in self.peers.document()['members'] if m['node_id'] == node)
        if certificate:
            assert authority == 'public CA' and certificate == f'public leaf {rank}'
            self.installed.add(node)
            self.drop(node, 'install')
        result = dict(protocol=1, instance_id=binding['instance_id'], revision=binding['revision'],
            generation=binding['generation'] + 1, topology_sha256=self.peers.fingerprint,
            dns_name=self.peers.certificate_name(rank), csr_pem=f'public CSR {rank}',
            certificate_installed=node in self.installed)
        return {**result, **(self.bad_reply or {})}

    async def group_authority(self, node, action, *, group_id, topology_sha256, claim=None):
        assert node == 'node-a' and group_id == 'test' and topology_sha256 == self.peers.fingerprint
        self.credential_calls.append((node, action))
        if action == 'close':
            if self.close_unavailable:
                raise ConnectionError('issuer unavailable')
            self.closed = True
            self.drop(node, 'close')
            return dict(state='closed')
        assert action == 'issue'
        self.issue_entered.set()
        if self.issue_gate:
            await self.issue_gate.wait()
        if self.closed:
            raise ValueError('group issuance closed')
        rank = claim['rank']
        if rank in self.issued:
            assert self.issued[rank] == claim
        self.issued[rank] = deepcopy(claim)
        self.drop(claim['node_id'], 'issue')
        return dict(certificate_pem=f'public leaf {rank}', ca_pem='public CA', ca_sha256='c' * 64)

    async def submit(self, node, **request):
        if request['action'] == 'start':
            assert self.installed == {'node-a', 'node-b'} and not self.closed
        return await super().submit(node, **request)


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle_fixture, 'OWNER', OWNER)
    journal = GroupJournal(tmp_path / 'groups.db', OWNER)
    journal.create('test', lifecycle_fixture.targets(), peer_security=security())
    fleet = SecuredFleet()
    return journal, fleet, GroupCoordinator(journal, fleet)


@pytest.mark.asyncio
async def test_credentials_barrier_lost_replies_and_restarted_coordinator(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    fleet.lose = {('node-a', 'issue'), ('node-b', 'install')}
    await coordinator.advance('test')
    assert not fleet.credential_calls  # all preparations must first be confirmed
    row = await coordinator.advance('test')
    assert row['phase'] == 'preparing' and not row['peer_security']['ready']
    assert {r['action'] for _, r in fleet.calls} == {'prepare_start'}
    row = await coordinator.advance('test')
    assert row['phase'] == 'committing' and row['peer_security']['ready']
    original = deepcopy(fleet.issued)
    assert len(original) == 2
    assert 'csr_pem' not in json.dumps(row) and 'certificate_pem' not in json.dumps(row)
    reopened = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
    await lifecycle_fixture.drive(reopened, 'ready')
    assert fleet.issued == original
    assert len([r for _, r in fleet.calls if r['action'] == 'start']) == 2
    await reopened.stop('test')
    await lifecycle_fixture.drive(reopened, 'stopped')
    assert journal.load('test')['peer_security']['closed']


@pytest.mark.asyncio
async def test_stop_racing_signing_cannot_cross_start_barrier(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await coordinator.advance('test')
    fleet.issue_gate = asyncio.Event()
    pending = asyncio.create_task(coordinator.advance('test'))
    try:
        await asyncio.wait_for(fleet.issue_entered.wait(), 2)
        other = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
        await other.stop('test')
        await lifecycle_fixture.drive(other, 'stopped')
    finally:
        fleet.issue_gate.set()
        await asyncio.wait_for(pending, 2)
    assert journal.load('test')['phase'] == 'stopped'
    assert not any(r['action'] == 'start' for _, r in fleet.calls)
    assert fleet.closed and not fleet.issued


@pytest.mark.asyncio
async def test_unavailable_or_lost_close_ack_does_not_block_owned_resource_cleanup(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await lifecycle_fixture.drive(coordinator, 'ready')
    fleet.close_unavailable = True
    await coordinator.stop('test')
    await coordinator.advance('test')
    row = await coordinator.advance('test')
    assert row['phase'] == 'aborting' and not row['peer_security']['closed']
    assert all(not i['resources'] and not i['reservations']
               for state in fleet.nodes.values() for i in state['instances'].values())
    fleet.close_unavailable = False
    fleet.lose = {('node-a', 'close')}
    assert (await coordinator.advance('test'))['phase'] == 'aborting'
    assert fleet.closed
    assert (await coordinator.advance('test'))['phase'] == 'stopped'
    assert len([r for _, r in fleet.calls if r['action'] == 'stop']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [dict(generation=True), dict(generation=3), dict(dns_name='wrong'),
    dict(topology_sha256='f' * 64), dict(instance_id='0' * 32), dict(revision='0' * 64)])
async def test_wrong_enrollment_never_starts(tmp_path, monkeypatch, bad):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    fleet.bad_reply = bad
    await coordinator.advance('test')
    for _ in range(2):
        assert (await coordinator.advance('test'))['phase'] == 'preparing'
    assert not fleet.issued
    assert len(fleet.calls) == 2
    await coordinator.stop('test')
    await lifecycle_fixture.drive(coordinator, 'stopped')


def test_local_journal_cannot_replace_or_remove_original_trust(tmp_path, monkeypatch):
    journal, _, _ = setup(tmp_path, monkeypatch)
    for mutate in [lambda s: s.update(ca_sha256='f' * 64),
                   lambda s: s.update(ready=True), lambda s: s.update(closed=True),
                   lambda s: s['topology'].update(launch_sha256='0' * 64)]:
        row = journal.load('test')
        mutate(row['peer_security'])
        with pytest.raises(GroupConflict):
            journal.save(row)
    row = journal.load('test')
    del row['peer_security']
    with pytest.raises(GroupConflict):
        journal.save(row)
