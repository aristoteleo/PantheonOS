"""Network barriers, loss/restart/stop races; actual kernel coverage lives in Go."""
import asyncio
import base64
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

import test_model_groups as fixture
from test_model_group_security import OWNER, SecuredFleet, security
from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_journal import GroupJournal, GroupConflict
from pantheon.models.group_network import PeerTopology, private_endpoint
from pantheon.models.group_overlay import validate_network


def network():
    return dict(addresses=['192.168.20.10:18441', '192.168.20.11:18441'], endpoints=[], ready=False, closed=False)


def endpoint(rank):
    return dict(rank=rank, address=network()['addresses'][rank],
                public_key=base64.b64encode(bytes([rank + 7])*32).decode())


class OverlayFleet(SecuredFleet):
    def __init__(self):
        super().__init__()
        self.overlays, self.overlay_calls = {}, []
        self.overlay_entered, self.overlay_gate = asyncio.Event(), None
        self.overlay_unavailable, self.tamper = set(), None

    async def group_overlay(self, node, action, **args):
        self.overlay_calls.append((node, action, deepcopy(args)))
        assert args['topology'] == self.peers.document()
        rank = next(p['rank'] for p in self.peers.document()['members'] if p['node_id'] == node)
        self.overlay_entered.set()
        if self.overlay_gate and action == 'prepare':
            await self.overlay_gate.wait()
        if (node, action) in self.overlay_unavailable:
            raise ConnectionError('unreachable original node')
        row = self.overlays.get(node)
        if row is None:
            row = self.overlays[node] = dict(protocol=1, owner=OWNER, node_id=node, group_id='test',
                topology_sha256=self.peers.fingerprint, state='closed' if action == 'close' else 'prepared')
        if row['state'] != 'closed':
            row.setdefault('endpoint', endpoint(rank))
            if action == 'pin':
                assert args['endpoints'][rank] == row['endpoint']
                if row['state'] == 'pinned':
                    assert args['endpoints'] == row['endpoints']
                row.update(state='pinned', endpoints=deepcopy(args['endpoints']))
            elif action == 'close':
                row['state'] = 'closed'
        if action == 'prepare':
            assert args['address'] == network()['addresses'][rank]
            assert args['ca_sha256'] == security()['ca_sha256']
        self.drop(node, 'overlay-' + action)
        return {**deepcopy(row), **(self.tamper or {})}

    async def submit(self, node, **request):
        if request['action'] in {'prepare_start', 'start'}:
            assert len(self.overlays) == 2 and all(s['state'] == 'pinned' for s in self.overlays.values())
        return await super().submit(node, **request)


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, 'OWNER', OWNER)
    journal = GroupJournal(tmp_path / 'groups.db', OWNER)
    journal.create('test', fixture.targets(), peer_security={**security(), 'network': network()})
    fleet = OverlayFleet()
    return journal, fleet, GroupCoordinator(journal, fleet)


@pytest.mark.asyncio
async def test_network_is_durable_before_any_prepare_loss_and_restart(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    fleet.lose = {('node-a', 'overlay-prepare')}
    row = await coordinator.advance('test')
    assert row['peer_security']['network']['endpoints'] == [] and not fleet.calls
    row = await coordinator.advance('test')
    assert row['peer_security']['network']['endpoints'] == [endpoint(0), endpoint(1)]
    assert not any(action == 'pin' for _, action, _ in fleet.overlay_calls) and not fleet.calls
    coordinator = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
    fleet.lose = {('node-b', 'overlay-pin')}
    row = await coordinator.advance('test')
    assert not row['peer_security']['network']['ready'] and not fleet.calls
    row = await coordinator.advance('test')
    assert row['peer_security']['network']['ready'] and not fleet.calls
    await fixture.drive(coordinator, 'ready')
    assert len(fleet.calls) == 4
    original = deepcopy(journal.load('test')['peer_security']['network']['endpoints'])
    await coordinator.stop('test')
    fleet.lose = {('node-a', 'overlay-close')}
    await fixture.drive(coordinator, 'stopped')
    assert journal.load('test')['peer_security']['network']['endpoints'] == original
    assert all(r['state'] == 'closed' for r in fleet.overlays.values())
    assert len(fleet.calls) == 6


@pytest.mark.asyncio
async def test_cancel_racing_enrollment_fences_late_requests(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    fleet.overlay_gate = asyncio.Event()
    pending = asyncio.create_task(coordinator.advance('test'))
    try:
        await asyncio.wait_for(fleet.overlay_entered.wait(), 2)
        other = GroupCoordinator(GroupJournal(tmp_path / 'groups.db', OWNER), fleet)
        await other.stop('test')
        await fixture.drive(other, 'stopped')
    finally:
        fleet.overlay_gate.set()
        await asyncio.wait_for(pending, 2)
    assert journal.load('test')['phase'] == 'stopped'
    assert not fleet.calls and all(s['state'] == 'closed' for s in fleet.overlays.values())


@pytest.mark.asyncio
async def test_network_close_failure_does_not_block_owned_process_stop(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await fixture.drive(coordinator, 'ready')
    fleet.overlay_unavailable = {('node-b', 'close')}
    await coordinator.stop('test')
    for _ in range(3):
        assert (await coordinator.advance('test'))['phase'] == 'aborting'
    assert all(not i['resources'] and not i['reservations']
               for n in fleet.nodes.values() for i in n['instances'].values())
    fleet.overlay_unavailable.clear()
    await fixture.drive(coordinator, 'stopped')
    assert journal.load('test')['peer_security']['network']['closed']


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', [dict(owner='f_'+'b'*16), dict(protocol=True), dict(node_id='another'),
    dict(topology_sha256='0'*64), dict(endpoint=endpoint(1)), dict(private_key='forbidden')])
async def test_wrong_node_identity_or_endpoint_cannot_start(tmp_path, monkeypatch, tamper):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    fleet.tamper = tamper
    row = await coordinator.advance('test')
    assert not row['peer_security']['network']['ready'] and not fleet.calls
    assert row['peer_security']['network']['endpoints'] == []


@pytest.mark.asyncio
async def test_journal_prevents_key_replacement_and_premature_claims(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await coordinator.advance('test')
    for mutate in [lambda n: n.update(addresses=list(reversed(n['addresses']))),
                   lambda n: n.update(endpoints=[]), lambda n: n.update(closed=True)]:
        row = journal.load('test')
        mutate(row['peer_security']['network'])
        with pytest.raises(GroupConflict):
            journal.save(row)
    row = journal.load('test')
    row['peer_security']['network']['ready'] = True
    row['members'][0]['prepare']['sent'] = True
    with pytest.raises(GroupConflict, match='Persist network readiness'):
        journal.save(row)
    row = journal.load('test')
    del row['peer_security']['network']
    with pytest.raises(GroupConflict):
        journal.save(row)


@pytest.mark.parametrize('value', ['127.0.0.1:1234', '169.254.169.254:8080', '8.8.8.8:1234',
    'node.local:1234', '10.0.0.1:80', '10.0.0.1:01234', '[::ffff:10.0.0.1]:1234',
    '[fd00::1%eth0]:1234', '[FD00::1]:1234', '[10.0.0.1]:1234', '10.0.0.1:65536'])
def test_underlay_is_canonical_private_not_dns_or_special_address(value):
    with pytest.raises(ValueError):
        private_endpoint(value)


def test_ipv6_and_duplicate_key_validation():
    assert private_endpoint('[fd00::1]:1234') == '[fd00::1]:1234'
    value = network()
    value['endpoints'] = [endpoint(0), {**endpoint(1), 'public_key': endpoint(0)['public_key']}]
    with pytest.raises(ValueError):
        validate_network(value, 2)


@pytest.mark.asyncio
async def test_rpc_envelope_and_reply_validation(monkeypatch):
    client = FleetLifecycle(None)
    peers = PeerTopology(security()['topology'])
    reply = dict(protocol=1, owner=OWNER, node_id='node-a', group_id='test',
                 topology_sha256=peers.fingerprint, state='prepared', endpoint=endpoint(0))
    rpc = AsyncMock(return_value=reply)
    monkeypatch.setattr(client, '_request', rpc)
    await client.group_overlay('node-a', 'prepare', topology=peers.document(), rank=0,
        ca_sha256='c'*64, address=network()['addresses'][0])
    rpc.assert_awaited_once_with('node-a', 'group_overlay_prepare', group_overlay=dict(
        manifest=dict(protocol=1, rank=0, topology=peers.document(), ca_sha256='c'*64),
        address=network()['addresses'][0]))
    for args in [dict(rank=1, ca_sha256='c'*64, address=network()['addresses'][0]),
                 dict(rank=0, ca_sha256='c'*64, address='127.0.0.1:1234')]:
        with pytest.raises(ValueError):
            await client.group_overlay('node-a', 'prepare', topology=peers.document(), **args)
    assert rpc.await_count == 1
    roster = [endpoint(0), endpoint(1)]
    rpc.return_value = {**reply, 'state': 'pinned', 'endpoints': roster}
    await client.group_overlay('node-a', 'pin', topology=peers.document(), endpoints=roster)
    rpc.assert_awaited_with('node-a', 'group_overlay_pin', group_overlay=dict(group_id='test',
        topology_sha256=peers.fingerprint, endpoints=roster))
    rpc.return_value = {**reply, 'private_key': 'must never cross RPC'}
    with pytest.raises(ValueError):
        await client.group_overlay('node-a', 'status', topology=peers.document())


@pytest.mark.asyncio
async def test_platform_private_network_needs_no_fleet_overlay():
    from pantheon.models.group_overlay import advance_network, validate_network, validate_transition
    network = dict(mode='platform-private', addresses=['fdaa::1', 'fdaa::2'], endpoints=[], ready=False, closed=False)
    validate_network(network, 2)
    row = dict(phase='preparing', members=[dict(prepare=dict(sent=False))] * 2,
               peer_security=dict(network=deepcopy(network)))
    class NoOverlay:
        async def group_overlay(self, *args, **kwargs):
            raise AssertionError('platform networks have no Fleet overlay')
    assert await advance_network(NoOverlay(), row, None, 1) is True
    validate_transition(network, row['peer_security']['network'], row)  # ready without a roster
    row['phase'] = 'aborting'
    assert await advance_network(NoOverlay(), row, None, 1) is False
    assert row['peer_security']['network']['closed']
    for bad in ({**network, 'addresses': ['10.0.0.1', '10.0.0.2']}, {**network, 'endpoints': [{}]},
                {**network, 'addresses': ['fdaa::1', 'fdaa::1']}):
        with pytest.raises(ValueError):
            validate_network(bad, 2)
    with pytest.raises(ValueError):  # mode is immutable
        validate_transition(network, {k: v for k, v in network.items() if k != 'mode'}, row)
