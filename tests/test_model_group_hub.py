import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from pantheon.models.client import ModelServices, ControlError
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_hub import HubGroupJournal
from pantheon.models.group_journal import GroupConflict, GroupJournal
from pantheon.models.manager import ModelServiceManager


OWNER = 'f_' + 'a' * 16


def plan():
    return GroupJournal.plan(OWNER, 'test', [dict(node_id=node, digest='b' * 64,
        scope='group', generation=0) for node in ('node-a', 'node-b')])


@pytest.mark.asyncio
async def test_lost_hub_commit_ack_never_sends_before_durable_acknowledgement():
    row = plan()
    lose = True

    def respond(request):
        nonlocal row, lose
        if request.method == 'GET':
            return httpx.Response(200, json=row)
        submitted = json.loads(request.content)
        assert submitted['revision'] == row['revision']
        row = {**submitted, 'revision': row['revision'] + 1}
        if lose:
            lose = False
            raise httpx.ReadError('Hub committed before the connection was lost')
        return httpx.Response(200, json=row)

    client = ModelServices(hub='https://hub.test', token='fixture', transport=httpx.MockTransport(respond))
    async def snapshot(node):
        return dict(protocol=1, owner=OWNER, node_id=node, instances={}, operations={})
    fleet = SimpleNamespace(status=AsyncMock(side_effect=snapshot), submit=AsyncMock(), fence_start=AsyncMock())
    coordinator = GroupCoordinator(HubGroupJournal(client, OWNER), fleet)
    with pytest.raises(httpx.ReadError):
        await coordinator.advance('test')
    assert all(m['prepare']['sent'] for m in row['members'])
    fleet.submit.assert_not_called()
    fleet.fence_start.assert_not_called()
    original_requests = [deepcopy(m['prepare']['request']) for m in row['members']]

    # A new Agent sees the committed claims. It neither chooses new operation
    # IDs nor resends starts; explicit stop fences the original delayed work.
    restored = GroupCoordinator(HubGroupJournal(client, OWNER), fleet)
    await restored.advance('test')
    fleet.submit.assert_not_called()
    await restored.stop('test')
    await restored.advance('test')
    assert [call.args[1] for call in fleet.fence_start.await_args_list] == original_requests
    fleet.submit.assert_not_called()
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected', [(409, GroupConflict), (404, KeyError), (503, ControlError)])
async def test_hub_errors_do_not_fall_back_to_agent_local_journal(status, expected):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={'detail': 'unavailable'})
    client = ModelServices(hub='https://hub.test', token='fixture', transport=httpx.MockTransport(respond))
    with pytest.raises(expected):
        await HubGroupJournal(client, OWNER).load('test')
    assert len(requests) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_group_views_cannot_start_and_cleanup_requires_explicit_abort(monkeypatch):
    row = plan()
    writes = []
    def respond(request):
        if request.method != 'GET':
            writes.append(request)
        return httpx.Response(200, json={'groups': [row]} if request.url.path.endswith('/groups') else row)
    client = ModelServices(hub='https://hub.test', token='fixture', transport=httpx.MockTransport(respond))
    resolver = SimpleNamespace(_fleet=OWNER, _ensure_client=AsyncMock())
    fleet = SimpleNamespace(status=AsyncMock(), submit=AsyncMock(), fence_start=AsyncMock())
    monkeypatch.setattr('pantheon.models.manager.FleetLifecycle', lambda _: fleet)
    manager = ModelServiceManager(client, resolver)
    assert (await manager.groups())['groups'] == [row]
    assert await manager.groups('inspect', 'test') == row
    with pytest.raises(ValueError, match='explicit group stop'):
        await manager.groups('continue_stop', 'test')
    for action in ('create', 'start', 'advance'):
        with pytest.raises(ValueError, match='Unsupported'):
            await manager.groups(action, 'test')
    assert not writes
    fleet.status.assert_not_called()
    fleet.submit.assert_not_called()
    fleet.fence_start.assert_not_called()
    await client.aclose()
