"""Durable owner maintenance: same grant, same generation, no tool/start replay."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import pantheon.apps.dependency_assembly as assembly
from pantheon.apps.dependency_assembly import DependencyStarter, DependencyAuthorizationError
from pantheon.platform.fleet_api import FleetAPI
from test_dependency_assembly import fixture


async def running(tmp_path, monkeypatch):
    lifecycle, authority, recipe, _ = fixture()
    starter = DependencyStarter(lifecycle, tmp_path / 'private', authority)
    await starter.start(**recipe)
    path = next(starter.root.glob('*.json'))
    record = json.loads(path.read_text())
    receipt = record['renewals']['files']
    assert 'access_token' not in json.dumps(record)
    lifecycle.status.return_value = {
        'owner': 'owner', 'node_id': 'consumer', 'instances': {
            recipe['consumer']['instance_id']: {'digest': recipe['consumer']['revision'], 'generation': 2, 'state': 'ready'}}}
    clock = SimpleNamespace(now=time.time())
    monkeypatch.setattr(assembly, 'time', SimpleNamespace(time=lambda: clock.now))
    async def renew(grant_id, ttl_seconds):
        assert grant_id == receipt['grant_id'] and ttl_seconds == 900
        return {**receipt, 'expires': int(clock.now) + 899}
    authority.renew.side_effect = renew
    return starter, lifecycle, authority, recipe, path, clock


@pytest.mark.asyncio
async def test_renewal_survives_owner_restart_and_original_ttl_without_reconfiguring(tmp_path, monkeypatch):
    starter, lifecycle, authority, recipe, path, clock = await running(tmp_path, monkeypatch)
    original = json.loads(path.read_text())['renewals']['files']
    assert (await starter.reconcile_once()) == dict(renewed=0, revoked=0, expired=0, deferred=0, invalid=0)
    for _ in range(3):
        clock.now += 600
        # A replacement platform loads receipts without needing the App bearer.
        restored = DependencyStarter(lifecycle, starter.root, authority)
        assert (await restored.reconcile_once())['renewed'] == 1
        current = json.loads(path.read_text())['renewals']['files']
        assert current['grant_id'] == original['grant_id'] and current['consumer'] == original['consumer']
        assert current['expires'] > clock.now
    assert clock.now > original['expires']
    assert authority.issue.await_count == lifecycle.configure.await_count == lifecycle.submit.await_count == 1
    assert authority.revoke.await_count == 0
    assert 'access_token' not in path.read_text()


@pytest.mark.asyncio
async def test_lost_renewal_ack_does_not_treat_stale_local_expiry_as_terminal(tmp_path, monkeypatch):
    starter, _, authority, _, path, clock = await running(tmp_path, monkeypatch)
    real_renew = authority.renew.side_effect
    original_expiry = json.loads(path.read_text())['renewals']['files']['expires']
    clock.now += 600
    authority.renew.side_effect = TimeoutError('private upstream data')
    assert (await starter.reconcile_once())['deferred'] == 1
    assert json.loads(path.read_text())['renewals']['files']['expires'] == original_expiry
    # Authority accepted that first request but its response was lost. Its grant
    # is still live after the stale local expiry; retry the same ID, never issue.
    clock.now += 400
    authority.renew.side_effect = real_renew
    assert clock.now > original_expiry
    assert (await starter.reconcile_once())['renewed'] == 1
    assert authority.issue.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [409, 410, 503])
async def test_authority_expiry_is_terminal_but_unavailability_is_deferred(tmp_path, monkeypatch, status):
    starter, lifecycle, authority, _, path, clock = await running(tmp_path, monkeypatch)
    clock.now += 950
    authority.renew.side_effect = DependencyAuthorizationError(status)
    totals = await starter.reconcile_once()
    assert totals['expired' if status == 410 else 'deferred'] == 1
    saved = json.loads(path.read_text())['renewals']['files']
    assert (saved.get('state') == 'expired') is (status == 410)
    if status == 410:
        await starter.reconcile_once()
        assert authority.renew.await_count == 1
    assert authority.issue.await_count == lifecycle.submit.await_count == lifecycle.configure.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stopped', 'replaced', 'removed'])
async def test_known_terminal_or_replaced_consumer_revokes_only_recorded_grant(tmp_path, monkeypatch, change):
    starter, lifecycle, authority, recipe, path, _ = await running(tmp_path, monkeypatch)
    instance = lifecycle.status.return_value['instances'][recipe['consumer']['instance_id']]
    if change == 'stopped':
        instance['state'] = 'stopped'
    elif change == 'replaced':
        instance['generation'] += 1
    else:
        lifecycle.status.return_value['instances'] = {}
    assert (await starter.reconcile_once())['revoked'] == 1
    receipt = json.loads(path.read_text())['renewals']['files']
    authority.revoke.assert_awaited_once_with(receipt['grant_id'])
    assert receipt['state'] == 'revoked'
    await starter.reconcile_once()
    assert authority.revoke.await_count == 1 and not authority.renew.called


@pytest.mark.asyncio
async def test_node_outage_and_foreign_state_do_not_trigger_revocation_or_replacement(tmp_path, monkeypatch):
    starter, lifecycle, authority, _, path, clock = await running(tmp_path, monkeypatch)
    saved = path.read_bytes()
    clock.now += 600
    lifecycle.status.side_effect = TimeoutError('private upstream data')
    assert (await starter.reconcile_once())['deferred'] == 1
    lifecycle.status.side_effect = None
    lifecycle.status.return_value['owner'] = 'somebody-else'
    assert (await starter.reconcile_once())['deferred'] == 1
    assert not authority.renew.called and not authority.revoke.called
    assert path.read_bytes() == saved and authority.issue.await_count == 1


@pytest.mark.asyncio
async def test_prepared_consumer_is_not_renewed_until_the_same_generation_starts(tmp_path, monkeypatch):
    starter, lifecycle, authority, recipe, _, clock = await running(tmp_path, monkeypatch)
    instance = lifecycle.status.return_value['instances'][recipe['consumer']['instance_id']]
    instance.update(generation=1, state='prepared', start_preparation_id=recipe['preparation_id'])
    clock.now += 600
    assert (await starter.reconcile_once())['renewed'] == 0
    assert not authority.renew.called and not authority.revoke.called
    instance.update(generation=2, state='starting')
    assert (await starter.reconcile_once())['renewed'] == 1


@pytest.mark.asyncio
async def test_concurrent_owner_passes_share_start_journal_lock(tmp_path, monkeypatch):
    starter, lifecycle, authority, _, _, clock = await running(tmp_path, monkeypatch)
    clock.now += 600
    entered, release = asyncio.Event(), asyncio.Event()
    real_renew = authority.renew.side_effect
    async def wait(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real_renew(*args, **kwargs)
    authority.renew.side_effect = wait
    first = asyncio.create_task(starter.reconcile_once())
    await entered.wait()
    try:
        other = DependencyStarter(lifecycle, starter.root, authority)
        assert (await other.reconcile_once())['deferred'] == 1
    finally:
        release.set()
    assert (await first)['renewed'] == 1 and authority.renew.await_count == 1


@pytest.mark.asyncio
async def test_corrupt_receipt_cannot_be_used_for_owner_operations(tmp_path, monkeypatch):
    starter, _, authority, _, path, clock = await running(tmp_path, monkeypatch)
    clock.now += 600
    value = json.loads(path.read_text())
    value['renewals']['files']['consumer']['fleet_id'] = 'another-owner'
    path.write_text(json.dumps(value))
    assert (await starter.reconcile_once())['invalid'] == 1
    assert not authority.renew.called and not authority.revoke.called


@pytest.mark.asyncio
async def test_changed_provider_in_renewal_response_is_not_adopted(tmp_path, monkeypatch):
    starter, _, authority, _, path, clock = await running(tmp_path, monkeypatch)
    clock.now += 600
    saved = path.read_bytes()
    real_renew = authority.renew.side_effect
    async def changed(*args, **kwargs):
        value = copy.deepcopy(await real_renew(*args, **kwargs))
        value['provider']['generation'] += 1
        return value
    authority.renew.side_effect = changed
    assert (await starter.reconcile_once())['deferred'] == 1
    assert path.read_bytes() == saved


@pytest.mark.asyncio
async def test_platform_maintenance_runs_without_agent_and_shutdown_owns_task(monkeypatch):
    owner = FleetAPI()
    entered = asyncio.Event()
    calls = []
    async def reconcile():
        calls.append('maintain')
        entered.set()
        return dict(renewed=1, revoked=0, expired=0, deferred=0, invalid=0)
    starter = SimpleNamespace(reconcile_once=reconcile)
    monkeypatch.setattr(owner, '_dependency_starter', lambda: starter)
    owner._start_dependency_maintenance()
    await asyncio.wait_for(entered.wait(), 1)
    task = owner._dependency_maintenance_task
    entered.clear()
    owner._start_dependency_maintenance()  # Wake the same owner task.
    assert owner._dependency_maintenance_task is task
    await asyncio.wait_for(entered.wait(), 1)
    await owner._stop_dependency_maintenance()
    assert task.done() and owner._dependency_maintenance_task is None
    assert calls == ['maintain', 'maintain']
