"""Owner restoration deadlines must survive the Fleet command envelope."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.client import AppClient
from pantheon.models import manager


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['configure', 'resume', 'status', 'models_status'])
async def test_restoration_budget_reaches_node_and_nats_without_slowing_observations(monkeypatch, method):
    binding = dict(node_id='test-node', instance_id='owned', revision='a' * 64, generation=3)
    async def request(subject, raw, *, timeout):
        command = json.loads(raw)
        assert subject == 'fleet.test-fleet.node.test-node.cmd'
        assert command['generation'] == binding['generation']
        assert command['payload']['method'] == method
        node_budget = command['timeout_seconds']
        if method in {'configure', 'resume'}:
            # Three possible serial engine calls (unload/load/warmup), plus
            # identity observations. Each engine call may take 180 seconds.
            assert 3 * 180 < node_budget <= 600
        else:
            assert node_budget <= 15
        assert timeout > node_budget
        return SimpleNamespace(data=json.dumps({'response': {'config_revision': 'verified'}}).encode())
    nc = SimpleNamespace(request=AsyncMock(side_effect=request))
    app = AppClient(nc, 'test-fleet')
    monkeypatch.setattr(manager, 'FleetLifecycle', lambda _: SimpleNamespace(_client=AsyncMock(return_value=app)))
    result = await manager.ModelServiceManager(client=object(), resolver=object()).rpc(binding, method)
    assert result == {'config_revision': 'verified'}
    nc.request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['configure', 'resume'])
async def test_uncertain_owner_restore_never_replays_after_deadline(monkeypatch, method):
    binding = dict(node_id='test-node', instance_id='owned', revision='a' * 64, generation=3)
    nc = SimpleNamespace(request=AsyncMock(side_effect=asyncio.TimeoutError))
    app = AppClient(nc, 'test-fleet')
    monkeypatch.setattr(manager, 'FleetLifecycle', lambda _: SimpleNamespace(_client=AsyncMock(return_value=app)))
    with pytest.raises(asyncio.TimeoutError):
        await manager.ModelServiceManager(client=object(), resolver=object()).rpc(binding, method)
    nc.request.assert_awaited_once()
