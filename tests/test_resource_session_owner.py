"""Owner recovery and isolation; provider simulation covers failure injection.

Real native Shell/Fleet wire coverage lives in the Go integration suite. These
tests deliberately simulate clock and acknowledgement loss, not command work.
"""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import pantheon.apps.resource_sessions as sessions
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import FleetLifecycle
from pantheon.apps.resource_sessions import ResourceSessionOwner
from pantheon.platform.fleet_api import FleetAPI


def fixture(tmp_path, monkeypatch, prepared=False):
    clock = SimpleNamespace(now=1_800_000_000)
    monkeypatch.setattr(sessions, 'time', SimpleNamespace(time=lambda: clock.now))
    consumer = dict(node_id='consumer-node', instance_id='consumer', revision='a' * 64, generation=2)
    provider = dict(node_id='provider-node', instance_id='provider', revision='b' * 64,
                    generation=3, component='backend', port='http')
    recipe = dict(consumer=consumer, provider=provider, operation_id='instance-one-shell',
                  owner_ref='instance-one', app_id='shell', kind='shell',
                  preparation_id='prepare' if prepared else '')
    states = {
        'consumer-node': dict(node_id='consumer-node', owner='owner', instances={
            'consumer': dict(digest='a' * 64, generation=1 if prepared else 2,
                            state='prepared' if prepared else 'ready', start_preparation_id='prepare')}),
        'provider-node': dict(node_id='provider-node', owner='owner', instances={
            'provider': dict(app_id='shell', digest='b' * 64, generation=3, state='ready')}),
    }
    async def status(node):
        return copy.deepcopy(states[node])
    manifest = json.loads((Path(__file__).parents[1] / 'apps/shell/app.json').read_text())
    lifecycle = SimpleNamespace(status=AsyncMock(side_effect=status),
                                manifest=AsyncMock(return_value={'manifest': manifest}))
    receipts = {}
    async def operation(app_id, binding, method, args):
        assert app_id == 'shell' and binding == provider
        action = method.removeprefix('resource_session_')
        lease = args['lease_id']
        if action == 'acquire' and lease not in receipts:
            receipts[lease] = dict(owner_ref=args['owner_ref'], lease_id=lease, kind=args['kind'],
                                    session_id='shell-' + str(len(receipts)), state='active',
                                    expires=clock.now + args['ttl_seconds'])
        if lease not in receipts:
            raise RuntimeError('Provider has no such lease')
        receipt = receipts[lease]
        assert receipt['owner_ref'] == args['owner_ref']
        if receipt['state'] == 'active' and receipt['expires'] <= clock.now:
            receipt['state'] = 'expired'
        if receipt['state'] == 'active':
            if action == 'renew':
                receipt['expires'] = max(receipt['expires'], clock.now + args['ttl_seconds'])
            elif action == 'release':
                receipt['state'] = 'released'
        return copy.deepcopy(receipt)
    lifecycle.resource_session = AsyncMock(side_effect=operation)
    return ResourceSessionOwner(lifecycle, tmp_path / 'private'), lifecycle, recipe, states, receipts, clock


def lookup(recipe):
    return {key: recipe[key] for key in ('consumer', 'operation_id')}


@pytest.mark.asyncio
async def test_logical_instances_on_one_deployment_have_independent_sessions(tmp_path, monkeypatch):
    owner, lifecycle, recipe, _, _, _ = fixture(tmp_path, monkeypatch)
    first = await owner.acquire(**recipe)
    second_recipe = {**recipe, 'owner_ref': 'instance-two', 'operation_id': 'instance-two-shell'}
    second = await owner.acquire(**second_recipe)
    assert first['receipt']['session_id'] != second['receipt']['session_id']
    assert first['lease_id'] != second['lease_id']
    assert (await owner.acquire(**recipe))['receipt'] == first['receipt']
    with pytest.raises(AssemblyError, match='different recipe'):
        await owner.acquire(**{**recipe, 'owner_ref': 'instance-two'})
    await owner.release(**lookup(recipe))
    assert (await owner.inspect(**lookup(second_recipe)))['receipt']['state'] == 'active'
    assert sum(c.args[2] == 'resource_session_acquire' for c in lifecycle.resource_session.await_args_list) == 2


@pytest.mark.asyncio
async def test_lost_acquisition_ack_recovers_same_remote_session_after_owner_restart(tmp_path, monkeypatch):
    owner, lifecycle, recipe, _, receipts, _ = fixture(tmp_path, monkeypatch)
    real = lifecycle.resource_session.side_effect
    async def lost(*args):
        await real(*args)
        raise TimeoutError('private response')
    lifecycle.resource_session.side_effect = lost
    with pytest.raises(TimeoutError):
        await owner.acquire(**recipe)
    record = await owner.inspect(**lookup(recipe))
    assert record['phase'] == 'acquiring' and record['receipt'] is None
    assert len(receipts) == 1
    lifecycle.resource_session.side_effect = real
    replacement = ResourceSessionOwner(lifecycle, owner.root)
    # Maintenance observes the original intent instead of replaying creation.
    assert (await replacement.reconcile_once())['observed'] == 1
    recovered = await replacement.acquire(**recipe)
    assert len(receipts) == 1 and recovered['lease_id'] == record['lease_id']
    assert [c.args[2] for c in lifecycle.resource_session.await_args_list] == [
        'resource_session_acquire', 'resource_session_get', 'resource_session_get']


@pytest.mark.asyncio
async def test_unacknowledged_intent_never_creates_session_during_maintenance(tmp_path, monkeypatch):
    owner, lifecycle, recipe, _, receipts, _ = fixture(tmp_path, monkeypatch)
    real = lifecycle.resource_session.side_effect
    lifecycle.resource_session.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await owner.acquire(**recipe)
    lifecycle.resource_session.side_effect = real
    assert (await owner.reconcile_once())['deferred'] == 1
    assert not receipts
    assert lifecycle.resource_session.await_args.args[2] == 'resource_session_get'
    # Explicit retry of the SAME durable intent may finish its acquisition.
    assert (await owner.acquire(**recipe))['phase'] == 'active'
    assert len(receipts) == 1


@pytest.mark.asyncio
async def test_renewal_preserves_identity_and_recovers_lost_ack_after_local_expiry(tmp_path, monkeypatch):
    owner, lifecycle, recipe, _, receipts, clock = fixture(tmp_path, monkeypatch)
    first = await owner.acquire(**recipe)
    clock.now += 600
    real = lifecycle.resource_session.side_effect
    async def lose_renewal(*args):
        result = await real(*args)
        if args[2] == 'resource_session_renew':
            raise TimeoutError('private response')
        return result
    lifecycle.resource_session.side_effect = lose_renewal
    assert (await owner.reconcile_once())['deferred'] == 1
    assert (await owner.inspect(**lookup(recipe)))['receipt'] == first['receipt']
    clock.now += 400
    assert clock.now > first['receipt']['expires']
    lifecycle.resource_session.side_effect = real
    owner = ResourceSessionOwner(lifecycle, owner.root)
    assert (await owner.reconcile_once())['observed'] == 1
    clock.now += 200
    assert (await owner.reconcile_once())['renewed'] == 1
    current = await owner.inspect(**lookup(recipe))
    assert current['lease_id'] == first['lease_id'] and len(receipts) == 1
    assert current['receipt']['expires'] > clock.now


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stopped', 'removed', 'generation', 'revision'])
async def test_terminal_consumer_releases_original_resource_only(tmp_path, monkeypatch, change):
    owner, lifecycle, recipe, states, _, _ = fixture(tmp_path, monkeypatch)
    await owner.acquire(**recipe)
    instance = states['consumer-node']['instances']['consumer']
    if change == 'removed':
        states['consumer-node']['instances'].clear()
    elif change == 'generation':
        instance['generation'] += 1
    elif change == 'revision':
        instance['digest'] = 'c' * 64
    else:
        instance['state'] = 'stopped'
    assert (await owner.reconcile_once())['released'] == 1
    assert (await owner.inspect(**lookup(recipe)))['receipt']['state'] == 'released'
    lifecycle.resource_session.reset_mock()
    await owner.reconcile_once()
    assert (await owner.acquire(**recipe))['phase'] == 'terminal'
    lifecycle.resource_session.assert_not_called()


@pytest.mark.asyncio
async def test_prepared_consumer_does_not_extend_unused_lease_forever(tmp_path, monkeypatch):
    owner, lifecycle, recipe, states, _, clock = fixture(tmp_path, monkeypatch, prepared=True)
    await owner.acquire(**recipe)
    clock.now += 700
    assert (await owner.reconcile_once())['observed'] == 1
    assert lifecycle.resource_session.await_args.args[2] == 'resource_session_get'
    states['consumer-node']['instances']['consumer'].update(generation=2, state='starting')
    assert (await owner.reconcile_once())['renewed'] == 1
    clock.now += 901
    assert (await owner.reconcile_once())['terminal'] == 1
    assert (await owner.acquire(**recipe))['receipt']['state'] == 'expired'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['foreign', 'missing_generation', 'older_generation', 'unknown', 'outage'])
async def test_uncertain_consumer_never_releases_or_renews(tmp_path, monkeypatch, change):
    owner, lifecycle, recipe, states, _, clock = fixture(tmp_path, monkeypatch)
    await owner.acquire(**recipe)
    original = (await owner.inspect(**lookup(recipe)))
    clock.now += 700
    state = states['consumer-node']
    if change == 'foreign':
        state['owner'] = 'other'
    elif change == 'missing_generation':
        state['instances']['consumer'].pop('generation')
    elif change == 'older_generation':
        state['instances']['consumer']['generation'] = 1
    elif change == 'unknown':
        state['instances']['consumer']['state'] = 'unknown'
    else:
        lifecycle.status.side_effect = TimeoutError('private')
    lifecycle.resource_session.reset_mock()
    assert (await owner.reconcile_once())['deferred'] == 1
    assert await owner.inspect(**lookup(recipe)) == original
    lifecycle.resource_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['foreign', 'missing_generation', 'older_generation', 'replaced', 'removed', 'stopped'])
async def test_provider_inventory_cannot_redirect_a_session(tmp_path, monkeypatch, change):
    owner, lifecycle, recipe, states, _, _ = fixture(tmp_path, monkeypatch)
    first = await owner.acquire(**recipe)
    state = states['provider-node']
    instance = state['instances']['provider']
    if change == 'foreign':
        state['owner'] = 'other'
    elif change == 'missing_generation':
        instance.pop('generation')
    elif change == 'older_generation':
        instance['generation'] -= 1
    elif change == 'replaced':
        instance['generation'] += 1
    elif change == 'removed':
        state['instances'].clear()
    else:
        instance['state'] = 'stopped'
    lost = change in {'replaced', 'removed', 'stopped'}
    lifecycle.resource_session.reset_mock()
    assert (await owner.reconcile_once())['lost' if lost else 'deferred'] == 1
    record = await owner.inspect(**lookup(recipe))
    assert record['receipt'] == first['receipt']  # Does not falsely claim remote cleanup.
    assert (record['phase'] == 'terminal') is lost
    lifecycle.resource_session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['session_id', 'owner_ref', 'lease_id', 'expires', 'extra'])
async def test_invalid_receipt_never_replaces_original(tmp_path, monkeypatch, mutation):
    owner, lifecycle, recipe, _, _, clock = fixture(tmp_path, monkeypatch)
    first = await owner.acquire(**recipe)
    malformed = copy.deepcopy(first['receipt'])
    malformed[mutation] = clock.now + 9999 if mutation == 'expires' else 'different'
    lifecycle.resource_session.side_effect = None
    lifecycle.resource_session.return_value = malformed
    assert (await owner.reconcile_once())['invalid'] == 1
    assert await owner.inspect(**lookup(recipe)) == first


@pytest.mark.asyncio
async def test_durable_release_finishes_without_consumer_query_after_lost_ack(tmp_path, monkeypatch):
    owner, lifecycle, recipe, _, receipts, _ = fixture(tmp_path, monkeypatch)
    first = await owner.acquire(**recipe)
    real = lifecycle.resource_session.side_effect
    async def closing(*args):
        if args[2] == 'resource_session_release':
            receipts[first['lease_id']]['state'] = 'closing'
            raise TimeoutError('cleanup still running')
        return await real(*args)
    lifecycle.resource_session.side_effect = closing
    with pytest.raises(TimeoutError):
        await owner.release(**lookup(recipe))
    assert (await owner.inspect(**lookup(recipe)))['phase'] == 'releasing'
    receipts[first['lease_id']]['state'] = 'released'
    lifecycle.resource_session.side_effect = real
    status = lifecycle.status.side_effect
    async def consumer_offline(node):
        if node == 'consumer-node':
            raise TimeoutError()
        return await status(node)
    lifecycle.status.side_effect = consumer_offline
    assert (await ResourceSessionOwner(lifecycle, owner.root).reconcile_once())['released'] == 1


@pytest.mark.asyncio
async def test_concurrent_acquisition_holds_durable_intent_lock(tmp_path, monkeypatch):
    owner, lifecycle, recipe, _, receipts, _ = fixture(tmp_path, monkeypatch)
    entered, finish = asyncio.Event(), asyncio.Event()
    real = lifecycle.resource_session.side_effect
    async def waiting(*args):
        entered.set()
        await finish.wait()
        return await real(*args)
    lifecycle.resource_session.side_effect = waiting
    first = asyncio.create_task(owner.acquire(**recipe))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(TimeoutError):
            await ResourceSessionOwner(lifecycle, owner.root).acquire(**recipe)
    finally:
        finish.set()
    await first
    assert len(receipts) == lifecycle.resource_session.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['permissions', 'owner', 'recipe', 'json'])
async def test_invalid_journal_is_not_used_for_remote_operations(tmp_path, monkeypatch, damage):
    owner, lifecycle, recipe, _, _, _ = fixture(tmp_path, monkeypatch)
    await owner.acquire(**recipe)
    path = next(owner.root.glob('*.json'))
    record = json.loads(path.read_text())
    if damage == 'permissions':
        path.chmod(0o644)
    elif damage == 'json':
        path.write_text('{')
    else:
        if damage == 'owner':
            record['owner'] = 'different'
        else:
            record['recipe']['operation_id'] = 'different'
        path.write_text(json.dumps(record))
    lifecycle.resource_session.reset_mock()
    assert (await owner.reconcile_once())['invalid'] == 1
    lifecycle.resource_session.assert_not_called()


@pytest.mark.asyncio
async def test_lifecycle_uses_exact_owner_wire_and_rejects_other_interfaces():
    lifecycle = FleetLifecycle(None)
    binding = dict(node_id='node', instance_id='instance', revision='a' * 64,
                    generation=2, component='backend', port='http')
    lifecycle._request = AsyncMock(return_value={'response': {'success': True, 'result': {'state': 'active'}}})
    args = {'owner_ref': 'instance-one', 'lease_id': 'd' * 64}
    await lifecycle.resource_session('shell', binding, 'resource_session_get', args)
    lifecycle._request.assert_awaited_once_with('node', 'invoke', app_id='shell', instance_id='instance',
        revision='a' * 64, generation=2, timeout_seconds=10,
        payload={'method': 'resource_session_get', 'args': args, 'timeout_s': 10})
    with pytest.raises(ValueError):
        await lifecycle.resource_session('shell', binding, 'run_command', {'command': 'bad'})
    lifecycle._request.return_value = {'response': {'success': False, 'error': 'private-secret'}}
    with pytest.raises(RuntimeError, match='not acknowledged') as error:
        await lifecycle.resource_session('shell', binding, 'resource_session_get', args)
    assert 'private-secret' not in str(error.value)


@pytest.mark.asyncio
async def test_platform_resource_api_redacts_unknown_outcome_and_wakes_maintenance(tmp_path, monkeypatch):
    owner, _, recipe, _, _, _ = fixture(tmp_path, monkeypatch)
    api = FleetAPI()
    monkeypatch.setattr(api, '_resource_session_owner', lambda: owner)
    wake = []
    monkeypatch.setattr(api, '_start_dependency_maintenance', lambda: wake.append(True))
    result = await api.fleet_app_resource_session('acquire', **recipe)
    assert result['success'] and wake == [True]
    status = await api.fleet_app_resource_session('status', **lookup(recipe))
    assert status['session'] == result['session']
    rejected = await api.fleet_app_resource_session('release', **recipe)
    assert not rejected['success'] and 'only the original' in rejected['error']
    owner.lifecycle.status.side_effect = TimeoutError('private-secret')
    failed = await api.fleet_app_resource_session('release', **lookup(recipe))
    assert not failed['success'] and 'private-secret' not in failed['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('failing', ['grant', 'session'])
async def test_platform_maintenance_failures_do_not_starve_other_owner(monkeypatch, failing):
    api = FleetAPI()
    done = asyncio.Event()
    calls = []
    async def grants():
        calls.append('grant')
        if failing == 'grant':
            raise RuntimeError('unavailable')
        return dict(expired=0, invalid=0)
    async def resources():
        calls.append('session')
        done.set()
        if failing == 'session':
            raise RuntimeError('unavailable')
        return dict(observed=1)
    monkeypatch.setattr(api, '_dependency_starter', lambda: SimpleNamespace(reconcile_once=grants))
    monkeypatch.setattr(api, '_resource_session_owner', lambda: SimpleNamespace(reconcile_once=resources))
    api._start_dependency_maintenance()
    try:
        await asyncio.wait_for(done.wait(), 2)
        assert calls == ['grant', 'session']
    finally:
        await api._stop_dependency_maintenance()


@pytest.mark.asyncio
async def test_slow_grant_authority_cannot_block_session_maintenance_or_shutdown(monkeypatch):
    api = FleetAPI()
    grant_entered, session_ran, grant_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def unavailable():
        grant_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            grant_stopped.set()
    async def resources():
        session_ran.set()
        return dict(observed=1)
    monkeypatch.setattr(api, '_dependency_starter', lambda: SimpleNamespace(reconcile_once=unavailable))
    monkeypatch.setattr(api, '_resource_session_owner', lambda: SimpleNamespace(reconcile_once=resources))
    api._start_dependency_maintenance()
    try:
        await asyncio.wait_for(grant_entered.wait(), 2)
        await asyncio.wait_for(session_ran.wait(), 2)
        session_ran.clear()
        api._start_dependency_maintenance()
        await asyncio.wait_for(session_ran.wait(), 2)
    finally:
        await asyncio.wait_for(api._stop_dependency_maintenance(), 2)
    assert grant_stopped.is_set()


@pytest.mark.asyncio
async def test_resource_rpc_is_registered_on_platform_with_private_owner_storage(tmp_path, monkeypatch):
    from pantheon.apps import resolver
    from pantheon.platform.service import PlatformService
    monkeypatch.setattr(resolver, 'get_shared_resolver', lambda: SimpleNamespace(_seed='owner-seed'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    service = PlatformService()
    assert 'fleet_app_resource_session' in service.functions
    owner = service._resource_session_owner()
    assert owner.root.is_relative_to(tmp_path / '.pantheon/platform-private')
    assert owner.root.name == 'app-resource-sessions'
    other = service._dependency_starter()
    assert owner.root.parent == other.root.parent and owner.root != other.root
