from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from apps.fleet.inventory import node_inventory
from pantheon.apps.resolver import AppInstanceResolver


def record(node_id, *, os='darwin', caps=None, status='online'):
    return {'node_id': node_id, 'name': node_id, 'kind': 'machine',
            'last_seen': datetime.now(timezone.utc).isoformat(),
            'capability': {'caps': ['proc', 'net'] if caps is None else caps, 'os': os},
            'state': {'status': status, 'instances': []}}


@pytest.mark.asyncio
async def test_pty_starts_go_builtin_on_exact_node_without_python_or_file_shares(monkeypatch):
    nodes = [record('Mac-Aa'), record('Linux-B', os='linux')]
    resolver = AppInstanceResolver('fleet', 'workspace', 'seed', '/cloud/workspace')
    client = SimpleNamespace(ping=AsyncMock(return_value=True), start=AsyncMock(return_value={'ok': True}))
    resolver._ensure_client = AsyncMock(return_value=client)
    resolver._list_nodes = AsyncMock(return_value=nodes)
    monkeypatch.setenv('PANTHEON_INSTANCE_NATS_SERVERS', 'wss://bus.example.test')
    a = await resolver.ensure_instance('pty', node_id='Mac-Aa')
    b = await resolver.ensure_instance('pty', node_id='Linux-B')
    assert a != b
    assert await resolver.ensure_instance('pty', node_id='Mac-Aa') == a
    assert client.start.await_count == 2
    for call, node in zip(client.start.call_args_list, nodes):
        target, spec = call.args
        assert target == node['node_id']
        assert spec['app_id'] == 'pty' and spec['runtime'] == 'builtin' and spec['command'] == []
        assert spec['dir'] == '/' and 'PYTHONPATH' not in spec['env'] and 'PATH' not in spec['env']
        assert spec['env']['NATS_SERVERS'] == 'wss://bus.example.test'
    resolver.invalidate('pty', node_id='Mac-Aa')
    assert list(resolver._started.values()) == [b]
    assert await resolver.ensure_instance('pty', node_id='Mac-Aa') == a
    assert client.start.await_count == 3


@pytest.mark.parametrize('target,nodes,message', [
    ('other-user', [record('A')], 'member'),
    ('A', [record('A', status='offline')], 'offline'),
    ('A', [record('A', caps=['net'])], 'proc'),
    ('A', [record('A', os='windows')], 'ConPTY'),
])
@pytest.mark.asyncio
async def test_pty_never_falls_back_to_workspace(target, nodes, message):
    resolver = AppInstanceResolver('fleet', 'workspace', 'seed', '/cloud/workspace')
    client = SimpleNamespace(start=AsyncMock())
    resolver._ensure_client = AsyncMock(return_value=client)
    resolver._list_nodes = AsyncMock(return_value=nodes)
    with pytest.raises((RuntimeError, ValueError), match=message):
        await resolver.ensure_instance('pty', node_id=target)
    client.start.assert_not_awaited()


def test_inventory_distinguishes_running_pty_from_can_start_pty():
    a, b, c = record('A'), record('B'), record('C', os='windows')
    a['state']['instances'] = [{'app_id': 'pty', 'health': 'healthy', 'service_id': 'pty-A'}]
    nodes = node_inventory([a, b, c])['nodes']
    assert [(n['has_pty'], n['can_start_pty']) for n in nodes] == [(True, True), (False, True), (False, False)]
    assert all(not n['has_files'] for n in nodes)


@pytest.mark.asyncio
async def test_default_terminal_stays_on_workspace_instead_of_another_proc_node():
    from pantheon.apps.registry import by_service_type
    from pantheon.apps.resolver import NotJoinedError
    resolver = AppInstanceResolver('fleet', 'agent', 'seed', '/cloud/workspace')
    agent, personal = record('agent'), record('personal')
    workspace = record('workspace', os='linux', caps=['proc', 'fs:workspace'])
    resolver._list_nodes = AsyncMock(return_value=[agent, personal, workspace])
    assert await resolver._place(by_service_type()['pty']) == 'workspace'
    resolver._list_nodes.return_value = [agent, personal]
    with pytest.raises(NotJoinedError):
        await resolver._place(by_service_type()['pty'])


@pytest.mark.asyncio
async def test_proxy_preserves_node_and_session_without_project_lookup(monkeypatch):
    from pantheon.chatroom.room import ChatRoom
    from pantheon.apps.proxy import ToolsetProxy
    from nats.errors import NoRespondersError
    resolver = SimpleNamespace(ensure_instance=AsyncMock(side_effect=['dead', 'new']), invalidate=Mock())
    invoke = AsyncMock(side_effect=[NoRespondersError(), {'success': True}])
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    monkeypatch.setattr(ToolsetProxy, 'from_toolset', lambda sid: SimpleNamespace(invoke=invoke))
    room = ChatRoom.__new__(ChatRoom)
    room._project_dir_for_chat = AsyncMock(side_effect=AssertionError('wrong workspace'))
    result = await room.proxy_toolset('pty_write', {'_node_id': 'Mac-Aa', 'session_id': 'shell-A', 'data': 'bHM='}, 'pty')
    assert result == {'success': True, 'node_id': 'Mac-Aa'}
    assert resolver.ensure_instance.call_args.kwargs == {'node_id': 'Mac-Aa'}
    resolver.invalidate.assert_called_once_with('pty', node_id='Mac-Aa')
    assert invoke.call_args.args == ('pty_write', {'session_id': 'shell-A', 'data': 'bHM='})
    room._project_dir_for_chat.assert_not_awaited()
