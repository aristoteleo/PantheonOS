"""Owner credential initialization can precede model-provider startup."""
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from test_model_bootstrap import rig, finish
from test_model_bootstrap_abort import abort_rig, abort_all, all_stopped


async def reserve(rig):
    for _ in range(30):
        result = await rig.restart().reserve(**rig.spec)
        if result['state'] == 'reserved': return result
        rig.nodes.finish()
    raise AssertionError('Reservation did not settle')


@pytest.mark.asyncio
async def test_reserve_without_credentials_or_registration_then_start_original_identities(rig):
    result = await reserve(rig)
    calls = deepcopy(rig.nodes.calls)
    instances = {node: deepcopy(state['instances']) for node, state in rig.nodes.states.items()}
    assert calls and all(call[1] in ('install', 'prepare_start') for call in calls)
    assert all(i['state'] == 'prepared' for group in instances.values() for i in group.values())
    assert not rig.registrations and not rig.rows and not rig.nodes.configurations
    assert await reserve(rig) == result
    assert rig.nodes.calls == calls
    await finish(rig)
    for node, group in instances.items():
        assert rig.nodes.states[node]['instances'].keys() == group.keys()
        for identity, old in group.items():
            current = rig.nodes.states[node]['instances'][identity]
            assert current['generation'] == old['generation'] + 1
            assert current['state'] == 'ready'
    assert len(rig.nodes.calls) == 9 and len(rig.registrations) == 1
    with pytest.raises(AssemblyError): await reserve(rig)


@pytest.mark.asyncio
async def test_reserve_does_not_request_platform_budget_credentials(rig):
    entry = rig.spec['model_apps']['connector']
    entry['credential_source'] = 'platform-budget'
    entry['app']['components']['backend']['values']['connector'] = {
        'engine': 'api', 'endpoint': 'https://proxy.example/v1', 'secret_ref': 'node-secret://budget'}
    callback = AsyncMock(side_effect=AssertionError('No credential acquisition during reservation'))
    bootstrap = rig.restart()
    bootstrap.prepare_credentials = callback
    for _ in range(30):
        result = await bootstrap.reserve(**rig.spec)
        if result['state'] == 'reserved': break
        rig.nodes.finish()
    assert result['state'] == 'reserved'
    callback.assert_not_awaited()
    assert not bootstrap._load(bootstrap._path(rig.spec['operation_id'])).get('credential_receipts')
    assert not rig.rows


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['install', 'prepare_start'])
async def test_lost_reservation_reply_reuses_original_node_operation(rig, action):
    rig.nodes.loss = action
    with pytest.raises(TimeoutError): await reserve(rig)
    rig.nodes.finish()
    await reserve(rig)
    assert len(rig.nodes.calls) == 6
    assert len({call[2] for call in rig.nodes.calls}) == 6


@pytest.mark.asyncio
async def test_reservation_aborts_both_groups_without_publishing_models(abort_rig):
    await reserve(abort_rig)
    result = await abort_all(abort_rig)
    assert result['state'] == 'aborted'
    all_stopped(abort_rig)
    assert not abort_rig.rows and not abort_rig.saves
    assert not any(call[1] == 'start' for call in abort_rig.nodes.calls)


@pytest.mark.asyncio
async def test_stale_provider_reservation_is_rejected_before_touching_consumers(rig):
    await reserve(rig)
    instance = next(i for i in rig.nodes.states['platform']['instances'].values()
                    if i['app_id'] == 'model-service')
    instance.update(state='stopped', generation=instance['generation'] + 1)
    before = deepcopy(rig.nodes.calls)
    with pytest.raises(AssemblyError): await reserve(rig)
    assert rig.nodes.calls == before
