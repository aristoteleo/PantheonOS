"""Owner forget of an aborting group whose unclean members' nodes left the Fleet."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.models import group_management as api
from pantheon.models.group_journal import GroupConflict, GroupJournal

OWNER = 'f_' + 'a' * 16


def fixture(tmp_path, monkeypatch, *, online=(), phase='aborting'):
    journal = GroupJournal(tmp_path / 'groups.db', OWNER)
    row = journal.create('test', [dict(node_id=n, digest='b' * 64, scope='group', generation=0)
                                  for n in ('node-a', 'node-b')])
    if phase == 'aborting':
        row = journal.save({**row, 'phase': 'aborting'})
    async def status(node):
        if node == 'node-b':
            raise TimeoutError('node is gone')
        return dict(protocol=1, owner=OWNER, node_id=node, instances={}, operations={})
    lifecycle = SimpleNamespace(status=AsyncMock(side_effect=status))
    manager = SimpleNamespace(resolver=SimpleNamespace(_list_nodes=AsyncMock(
        return_value=[dict(node_id=n) for n in ('other', *online)])))
    revoked = []
    async def revoke(node_id):
        revoked.append(node_id)
    monkeypatch.setattr(api, 'revoke_node', revoke)
    return journal, row, lifecycle, manager, revoked


@pytest.mark.asyncio
async def test_forget_revokes_exactly_gone_unclean_members(tmp_path, monkeypatch):
    journal, row, lifecycle, manager, revoked = fixture(tmp_path, monkeypatch)
    result = await api.forget(manager, journal, lifecycle, row, {'confirm_node_ids': ['node-b']})
    assert result['phase'] == 'forgotten' and result['forgotten']['node_ids'] == ['node-b']
    assert revoked == ['node-b']
    manager.resolver._list_nodes.assert_awaited_with(max_age=0, strict=True)
    assert [m['observation']['clean'] for m in result['members']] == [True, False]
    assert journal.load('test')['phase'] == 'forgotten'
    # Final and idempotent: no second revoke, no way back.
    assert await api.forget(manager, journal, lifecycle, result, {'confirm_node_ids': ['node-b']}) == result
    assert revoked == ['node-b']
    with pytest.raises(GroupConflict):
        journal.save({**result, 'phase': 'aborting', 'forgotten': None})


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['online', 'confirm_mismatch', 'confirm_missing', 'not_aborting', 'revoke_fails'])
async def test_forget_refusals_have_no_effect(tmp_path, monkeypatch, case):
    journal, row, lifecycle, manager, revoked = fixture(
        tmp_path, monkeypatch, online=('node-b',) if case == 'online' else (),
        phase='preparing' if case == 'not_aborting' else 'aborting')
    config = {'confirm_node_ids': ['node-b']}
    if case == 'confirm_mismatch':
        config = {'confirm_node_ids': ['node-a', 'node-b']}
    elif case == 'confirm_missing':
        config = None
    elif case == 'revoke_fails':
        async def fail(node_id):
            raise RuntimeError('Fleet did not revoke node')
        monkeypatch.setattr(api, 'revoke_node', fail)
    with pytest.raises((ValueError, RuntimeError)):
        await api.forget(manager, journal, lifecycle, row, config)
    assert revoked == []
    assert journal.load('test')['phase'] == row['phase'] and 'forgotten' not in journal.load('test')


@pytest.mark.asyncio
async def test_registry_failure_is_not_absence(tmp_path, monkeypatch):
    journal, row, lifecycle, manager, revoked = fixture(tmp_path, monkeypatch)
    manager.resolver._list_nodes.side_effect = RuntimeError('Fleet node registry is unavailable')
    with pytest.raises(RuntimeError, match='registry'):
        await api.forget(manager, journal, lifecycle, row, {'confirm_node_ids': ['node-b']})
    assert revoked == []


@pytest.mark.asyncio
async def test_revoke_node_requires_controller_ack(monkeypatch):
    import httpx
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json):
            calls.append((url, json))
            return httpx.Response(200, json={'ok': json['node_id'] == 'n_ok'})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://fleet.test/')
    monkeypatch.setenv('FLEET_KEY', 'owner-key')
    await api.revoke_node('n_ok')
    assert calls == [('https://fleet.test/revoke', {'key': 'owner-key', 'node_id': 'n_ok'})]
    with pytest.raises(RuntimeError, match='did not revoke'):
        await api.revoke_node('n_other')
    monkeypatch.delenv('FLEET_KEY')
    monkeypatch.delenv('PANTHEON_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='not configured'):
        await api.revoke_node('n_ok')
