"""Logical owner retirement with acknowledgement loss and live sibling owners."""
import asyncio
import json

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.live_dependencies import LiveDependencyOwner, ScopedDependencyBindings
from test_live_dependency_bindings import fixture, request


@pytest.mark.asyncio
async def test_retirement_revokes_all_revisions_and_releases_only_their_session(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    await f.capability.bind(**request())
    await f.capability.bind(**request(operation='revision-two'))
    sibling = await f.capability.bind(**request(owner='instance-two', operation='other'))
    result = await f.capability.retire(owner_ref='instance-one')
    assert result['state'] == 'retired' and list(result['resources'].values()) == ['released']
    assert f.authority.revoke.await_count == 4
    assert sorted(r['state'] for r in f.receipts.values()) == ['active', 'released']
    assert await f.capability.bind(**request(owner='instance-two', operation='other')) == sibling
    assert await f.capability.retire(owner_ref='instance-one') == result
    assert f.authority.revoke.await_count == 4
    replacement = LiveDependencyOwner(f.lifecycle, f.owner.root, f.sessions, f.authority)
    cap = ScopedDependencyBindings(replacement, consumer=f.consumer, bindings=f.bindings)
    for operation in ('revision-one', 'revision-three'):
        with pytest.raises(AssemblyError, match='retir'):
            await cap.bind(**request(operation=operation))
    assert (await replacement.reconcile_once())['renewed'] == 0
    for path in f.owner.root.rglob('*.json'):
        assert 'access_token' not in path.read_text()


@pytest.mark.asyncio
async def test_revoke_loss_keeps_resources_until_all_grants_acknowledged(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    await f.capability.bind(**request())
    f.authority.revoke.side_effect = TimeoutError('lost revoke reply')
    with pytest.raises(TimeoutError):
        await f.capability.retire(owner_ref='instance-one')
    assert all(r['state'] == 'active' for r in f.receipts.values())
    with pytest.raises(AssemblyError, match='retir'):
        await f.capability.bind(**request(operation='new-revision'))
    f.authority.revoke.side_effect = None
    assert (await f.owner.reconcile_once())['retired'] == 1
    assert all(r['state'] == 'released' for r in f.receipts.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['session', 'grant'])
async def test_partial_binding_and_lost_issue_reply_can_be_retired(tmp_path, monkeypatch, stage):
    f = fixture(tmp_path, monkeypatch)
    target = f.lifecycle.resource_session if stage == 'session' else f.authority.issue
    original = target.side_effect
    async def lost(*args):
        value = original(*args)
        if asyncio.iscoroutine(value):
            await value
        target.side_effect = original
        raise TimeoutError('lost accepted allocation')
    target.side_effect = lost
    with pytest.raises(TimeoutError):
        await f.capability.bind(**request())
    result = await f.capability.retire(owner_ref='instance-one')
    assert result['state'] == 'retired' and list(result['resources'].values()) == ['released']
    assert len(f.receipts) == 1
    assert f.authority.revoke.await_count == (0 if stage == 'session' else 2)


@pytest.mark.asyncio
async def test_never_bound_owner_tombstone_survives_restart(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    result = await f.capability.retire(owner_ref='instance-one')
    assert result['state'] == 'retired' and result['resources'] == {}
    replacement = LiveDependencyOwner(f.lifecycle, f.owner.root, f.sessions, f.authority)
    with pytest.raises(AssemblyError, match='retir'):
        await replacement.bind(consumer=f.consumer, owner_ref='instance-one', operation_id='new', bindings=f.bindings)
    assert not f.receipts and not f.issued


@pytest.mark.asyncio
async def test_active_allocation_cannot_race_retirement(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    entered, resume = asyncio.Event(), asyncio.Event()
    original = f.authority.issue.side_effect
    async def hold(body):
        entered.set()
        await resume.wait()
        return original(body)
    f.authority.issue.side_effect = hold
    binding = asyncio.create_task(f.capability.bind(**request()))
    await entered.wait()
    try:
        with pytest.raises(TimeoutError):
            await f.capability.retire(owner_ref='instance-one')
    finally:
        resume.set()
        await binding
    assert (await f.capability.retire(owner_ref='instance-one'))['state'] == 'retired'


@pytest.mark.asyncio
async def test_release_pending_and_provider_loss_are_not_reported_as_cleanup(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    await f.capability.bind(**request())
    original = f.lifecycle.resource_session.side_effect
    async def pending(app, binding, method, args):
        if method == 'resource_session_release':
            receipt = dict(f.receipts[args['lease_id']])
            receipt['state'] = 'closing'
            return receipt
        return await original(app, binding, method, args)
    f.lifecycle.resource_session.side_effect = pending
    result = await f.capability.retire(owner_ref='instance-one')
    assert result['state'] == 'retiring' and list(result['resources'].values()) == ['closing']
    f.states['provider-node']['instances']['provider']['generation'] += 1
    result = await f.capability.retire(owner_ref='instance-one')
    assert result['state'] == 'retired' and list(result['resources'].values()) == ['lost']


@pytest.mark.asyncio
async def test_substituted_grant_receipt_blocks_release(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    await f.capability.bind(**request())
    path = next(f.owner.root.glob('*.json'))
    value = json.loads(path.read_text())
    value['renewals']['shell']['consumer']['instance_id'] = 'someone-else'
    path.write_text(json.dumps(value))
    with pytest.raises(AssemblyError, match='Invalid dependency record'):
        await f.capability.retire(owner_ref='instance-one')
    assert not f.authority.revoke.await_count
    assert all(r['state'] == 'active' for r in f.receipts.values())
