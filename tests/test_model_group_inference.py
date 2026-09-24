"""Leader activation/drain ordering with durable journals and fault injection."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import test_model_groups as fixture
from test_model_group_overlay import OverlayFleet, network
from test_model_group_security import OWNER, security
from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_inference import deployment, transition
from pantheon.models.group_journal import GroupJournal, GroupConflict


class InferenceFleet(OverlayFleet):
    def __init__(self, journal):
        super().__init__()
        self.journal = journal
        self.inference_calls = []
        self.accepting = False
        self.drained = False
        self.busy = True
        self.lose_control = set()
        self.unavailable = False
        self.resume_entered = asyncio.Event()
        self.resume_gate = None

    async def group_inference(self, binding, method, args):
        row = self.journal.load('test')
        value = row['inference']
        assert binding == value['binding']
        self.inference_calls.append((deepcopy(binding), method, deepcopy(args)))
        if self.unavailable:
            raise ConnectionError('Original leader unreachable')
        if method == 'resume':
            assert value['activation_sent'] and row['phase'] == 'ready'
            self.resume_entered.set()
            if self.resume_gate:
                await self.resume_gate.wait()
            if self.drained:
                raise ValueError('Original leader already drained')
            assert args == {'config_revision': value['config_revision']}
            self.accepting = True
            result = dict(accepting=True, config_revision=value['config_revision'])
        else:
            assert method == 'drain' and row['phase'] == 'aborting' and value['drain_sent']
            self.drained = True
            self.accepting = False
            result = dict(status='waiting' if self.busy else 'succeeded', safe_to_stop=not self.busy)
        if method in self.lose_control:
            self.lose_control.remove(method)
            raise TimeoutError('Leader committed but reply lost')
        return result

    async def group_overlay(self, node, action, **args):
        if action == 'close':
            assert self.journal.load('test')['inference']['drained']
        return await super().group_overlay(node, action, **args)

    async def submit(self, node, **request):
        if request['action'] == 'stop':
            assert self.journal.load('test')['inference']['drained']
        return await super().submit(node, **request)


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, 'OWNER', OWNER)
    journal = GroupJournal(tmp_path / 'groups.db', OWNER)
    journal.create('test', fixture.targets(), peer_security={**security(), 'network': network()},
        inference=dict(context_length=4096, parallel=2))
    fleet = InferenceFleet(journal)
    return journal, fleet, GroupCoordinator(journal, fleet)


@pytest.mark.asyncio
async def test_activation_publication_and_drain_survive_lost_replies_and_restart(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    row = await fixture.drive(coordinator, 'ready')
    assert deployment(row)['models'] == [] and not fleet.inference_calls
    row = await coordinator.advance('test')
    assert row['inference']['activation_sent'] and not row['inference']['activated']
    assert not fleet.inference_calls
    fleet.lose_control = {'resume'}
    row = await coordinator.advance('test')
    assert not row['inference']['activated'] and deployment(row)['state'] != 'ready'
    assert fleet.accepting
    coordinator = GroupCoordinator(GroupJournal(journal.path, OWNER), fleet)
    row = await coordinator.advance('test')
    assert row['inference']['activated'] and deployment(row)['state'] == 'ready'
    assert fleet.inference_calls[0] == fleet.inference_calls[1]
    row = await coordinator.stop('test')
    assert deployment(row)['models'] == [] and not row['inference']['drain_sent']
    row = await coordinator.advance('test')
    assert row['inference']['drain_sent'] and not fleet.drained
    fleet.lose_control = {'drain'}
    await coordinator.advance('test')
    assert fleet.drained and not fleet.accepting
    row = await coordinator.advance('test')
    assert not row['inference']['drained']
    assert all(r['state'] == 'pinned' for r in fleet.overlays.values())
    assert not any(r['action'] == 'stop' for _, r in fleet.calls)
    fleet.busy = False
    row = await coordinator.advance('test')
    assert row['inference']['drained']
    # The acknowledgement is its own commit; network remains intact until a later pass.
    assert all(r['state'] == 'pinned' for r in fleet.overlays.values())
    row = await fixture.drive(coordinator, 'stopped')
    assert deployment(row)['state'] == 'stopped'
    assert all(r['state'] == 'closed' for r in fleet.overlays.values())
    assert len([r for _, r in fleet.calls if r['action'] == 'start']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_activation_first', [False, True])
async def test_stop_racing_delayed_activation_never_republishes(tmp_path, monkeypatch, finish_activation_first):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await fixture.drive(coordinator, 'ready')
    await coordinator.advance('test')
    fleet.resume_gate = asyncio.Event()
    pending = asyncio.create_task(coordinator.advance('test'))
    try:
        await asyncio.wait_for(fleet.resume_entered.wait(), 2)
        other = GroupCoordinator(GroupJournal(journal.path, OWNER), fleet)
        await other.stop('test')
        if finish_activation_first:
            fleet.resume_gate.set()
            await pending
            assert fleet.accepting
        await other.advance('test')
        fleet.busy = False
        await other.advance('test')
        assert fleet.drained and not fleet.accepting
        fleet.resume_gate.set()
        await pending
        row = await fixture.drive(other, 'stopped')
        assert not row['inference']['activated'] and deployment(row)['models'] == []
    finally:
        fleet.resume_gate.set()
        await asyncio.wait_for(pending, 2)


@pytest.mark.asyncio
async def test_unreachable_leader_does_not_authorize_teardown_but_confirmed_exit_does(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await fixture.drive(coordinator, 'ready')
    await coordinator.advance('test'); await coordinator.advance('test')
    await coordinator.stop('test')
    fleet.unavailable = True
    for _ in range(4):
        row = await coordinator.advance('test')
        assert not row['inference']['drained']
    assert all(r['state'] == 'pinned' for r in fleet.overlays.values())
    assert not any(r['action'] == 'stop' for _, r in fleet.calls)
    binding = row['inference']['binding']
    original = fleet.nodes[binding['node_id']]['instances'][binding['instance_id']]
    original.update(state='stopped', resources=[], reservations={})
    row = await fixture.drive(coordinator, 'stopped')
    assert row['inference']['drained']


@pytest.mark.asyncio
async def test_stop_before_activation_needs_no_leader_rpc(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    await fixture.drive(coordinator, 'ready')
    await coordinator.stop('test')
    row = await fixture.drive(coordinator, 'stopped')
    assert row['inference']['drained'] and not row['inference']['activation_sent']
    assert not fleet.inference_calls


@pytest.mark.asyncio
async def test_journal_rejects_early_ack_rebinding_and_teardown(tmp_path, monkeypatch):
    journal, fleet, coordinator = setup(tmp_path, monkeypatch)
    row = await fixture.drive(coordinator, 'ready')
    for change in ('ack', 'binding', 'configuration', 'remove'):
        bad = deepcopy(row)
        if change == 'ack': bad['inference'].update(activation_sent=True, activated=True)
        elif change == 'binding': bad['inference']['binding']['generation'] += 1
        elif change == 'configuration': bad['inference']['parallel'] += 1
        else: del bad['inference']
        with pytest.raises(GroupConflict): journal.save(bad)
    await coordinator.advance('test'); await coordinator.advance('test')
    row = await coordinator.stop('test')
    for change in ('network', 'ca', 'drained'):
        bad = deepcopy(row)
        if change == 'network': bad['peer_security']['network']['closed'] = True
        elif change == 'ca': bad['peer_security']['closed'] = True
        else: bad['inference']['drained'] = True
        with pytest.raises(GroupConflict): journal.save(bad)
    row = await coordinator.advance('test')
    bad = deepcopy(row)
    bad['inference']['drained'] = True
    bad['peer_security']['network']['closed'] = True
    with pytest.raises(GroupConflict, match='Persist leader drain'):
        journal.save(bad)


@pytest.mark.asyncio
async def test_rpc_uses_exact_generation_and_owner_app_control(monkeypatch):
    client = SimpleNamespace(invoke=AsyncMock(return_value={'response': {'safe_to_stop': True}}))
    lifecycle = FleetLifecycle(None)
    monkeypatch.setattr(lifecycle, '_client', AsyncMock(return_value=client))
    binding = dict(node_id='node-a', instance_id='a'*32, revision='b'*64, generation=2, component='backend', port='http')
    assert await lifecycle.group_inference(binding, 'drain', {}) == {'safe_to_stop': True}
    client.invoke.assert_awaited_once_with('node-a', 'model-service', binding, 'drain', {}, 15)
    with pytest.raises(ValueError):
        await lifecycle.group_inference(binding, 'configure', {})


@pytest.mark.asyncio
async def test_normal_manager_stops_whole_group_and_cannot_mutate_single_rank():
    from pantheon.models.manager import ModelServiceManager
    row = dict(mode='group', group_id='test', deployment_id='group-test', state='ready', revision=8)
    client = SimpleNamespace(deployment=AsyncMock(return_value=row), save=AsyncMock())
    manager = ModelServiceManager(client, SimpleNamespace())
    manager.groups = AsyncMock()
    manager.rpc = AsyncMock()
    manager.ensure = AsyncMock()
    assert await manager.set_running('group-test', False) == row
    manager.groups.assert_awaited_once_with('stop', 'test')
    for call in (manager.publish('group-test', [], 8), manager.upgrade_connector('group-test'), manager.recover('group-test')):
        with pytest.raises(ValueError, match='group'):
            await call
    assert await manager.set_running('group-test', True) == row
    row['state'] = 'stopped'
    with pytest.raises(ValueError, match='group'):
        await manager.set_running('group-test', True)
    client.save.assert_not_called()
    manager.rpc.assert_not_called()
    manager.ensure.assert_not_called()
