from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.fleet.inventory import node_inventory
from apps.task.output_paths import output_metadata
from apps.task.task_state import ConversationState
from pantheon.apps.resolver import AppInstanceResolver


def record(node_id='Node-A', *, stale=False, files=True):
    return {'node_id': node_id, 'name': node_id, 'kind': 'machine',
            'last_seen': (datetime.now(timezone.utc) - timedelta(minutes=10 if stale else 0)).isoformat(),
            'capability': {'caps': []}, 'state': {'status': 'online', 'instances':
            [{'app_id': 'file-manager', 'scope': 'app', 'service_id': f'files-{node_id}',
              'health': 'healthy', 'env': {'SECRET': 'never expose'}}] if files else []}}


def test_inventory_offline_file_nodes_and_no_credentials():
    result = node_inventory([record(), record('Node-B', stale=True), record('browser', files=False)])
    assert [node['has_files'] for node in result['nodes']] == [True, True, False]
    assert result['nodes'][1]['status'] == 'offline'
    assert result['instances'][1]['node_status'] == 'offline'
    assert 'SECRET' not in str(result)


@pytest.mark.asyncio
async def test_exact_node_routing_reuses_backend_and_refuses_unknown_or_offline():
    resolver = AppInstanceResolver('user-fleet', 'Node-A', 'seed', '/wrong-local-directory')
    resolver._ensure_client = AsyncMock()
    resolver._list_nodes = AsyncMock(return_value=[record(), record('Node-B', stale=True)])
    assert await resolver.ensure_instance('file_manager', node_id='Node-A') == 'files-Node-A'
    with pytest.raises(ValueError, match='member'):
        await resolver.ensure_instance('file_manager', node_id='other-user-node')
    with pytest.raises(RuntimeError, match='offline'):
        await resolver.ensure_instance('file_manager', node_id='Node-B')
    assert not resolver._started  # No misplaced process or local-path fallback.


@pytest.mark.asyncio
async def test_output_exists_only_on_remote_node(monkeypatch, tmp_path):
    resolver = SimpleNamespace(ensure_instance=AsyncMock(return_value='remote-files'))
    proxy = SimpleNamespace(bind_instance=lambda *a: None, invoke=AsyncMock(return_value={
        'success': True, 'exists': True, 'is_dir': False, 'node_id': 'Node-A',
        'path': '/reports/chart.png', 'store_path': 'reports/chart.png'}))
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    monkeypatch.setattr('pantheon.apps.proxy.ToolsetProxy.from_toolset', lambda sid: proxy)
    result = await output_metadata('reports/chart.png', {'project_root': str(tmp_path)})
    assert result['exists'] and not (tmp_path / 'reports/chart.png').exists()
    assert result['source'] == {'node_id': 'Node-A', 'path': '/reports/chart.png', 'service_id': 'remote-files'}
    await output_metadata('/reports/chart.png', {}, node_id='Node-A')
    assert resolver.ensure_instance.call_args.kwargs['node_id'] == 'Node-A'


@pytest.mark.asyncio
async def test_offline_output_does_not_fall_back_to_same_local_path(monkeypatch, tmp_path):
    path = tmp_path / 'result.txt'; path.write_text('unrelated local data')
    resolver = SimpleNamespace(ensure_instance=AsyncMock(side_effect=RuntimeError('node offline')))
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    with pytest.raises(RuntimeError, match='offline'):
        await output_metadata(str(path), {}, 'Node-B')
    with pytest.raises(ValueError, match='absolute'):
        await output_metadata('result.txt', {}, 'Node-B')


def test_outputs_are_deduplicated_by_node_and_path():
    state = ConversationState()
    for node in ['Node-A', 'Node-B', 'Node-A']:
        state.on_register_output('chart.png', source={'node_id': node, 'path': '/reports/chart.png'})
    assert len(state.outputs) == 2
    assert {item['source']['node_id'] for item in state.outputs} == {'Node-A', 'Node-B'}


@pytest.mark.asyncio
async def test_file_backend_chunked_bytes_and_metadata(tmp_path):
    from apps.file.file_manager import FileManagerToolSet
    import base64
    files = FileManagerToolSet('files', tmp_path)
    target = tmp_path / 'image.bin'
    opened = await files.file_transfer('open_file_for_write', {'file_path': str(target)})
    assert opened['success']
    assert (await files.file_transfer('write_chunk', {'handle_id': opened['handle_id'], 'data': base64.b64encode(b'\x00\xffimage').decode()}))['success']
    await files.file_transfer('close_file', {'handle_id': opened['handle_id']})
    assert target.read_bytes() == b'\x00\xffimage'
    opened = await files.file_transfer('open_file_for_read', {'file_path': str(target)})
    chunk = await files.file_transfer('read_chunk_at', {'handle_id': opened['handle_id'], 'offset': 0, 'size': 7})
    await files.file_transfer('close_file', {'handle_id': opened['handle_id']})
    assert base64.b64decode(chunk['data']) == target.read_bytes()
    assert not (await files.file_transfer('run_code', {}))['success']


@pytest.mark.asyncio
async def test_fleet_summary_and_app_list_both_work(monkeypatch):
    from apps.fleet.fleet import FleetToolSet
    fleet = FleetToolSet('fleet')
    monkeypatch.setattr(fleet, '_read_nodes', AsyncMock(return_value=[record()]))
    summary = await fleet.fleet_status()
    assert summary['success'] and summary['nodes_total'] == 1
    apps = await fleet.fleet_list_apps('Node-A')
    assert apps['success'] and apps['instances'][0]['service_id'] == 'files-Node-A'
    assert not (await fleet.fleet_list_apps('not-mine'))['success']


def test_local_node_identity_never_adopts_another_machine(monkeypatch, tmp_path):
    from apps.fleet.local_node import local_node_id
    monkeypatch.delenv('PANTHEON_FLEET_NODE_ID', raising=False)
    monkeypatch.setenv('PANTHEON_FLEET_STATE_DIR', str(tmp_path))
    assert local_node_id() is None
    (tmp_path / 'runtime.json').write_text('{"node_id":"actual-local-node"}')
    assert local_node_id() == 'actual-local-node'
    monkeypatch.setenv('PANTHEON_FLEET_NODE_ID', 'explicit-node')
    assert local_node_id() == 'explicit-node'


@pytest.mark.asyncio
async def test_desktop_http_rejects_other_nodes_before_resolving_path(monkeypatch):
    from apps.desktop.toolset import DesktopToolSet
    monkeypatch.setenv('PANTHEON_FLEET_NODE_ID', 'A')
    reply = await DesktopToolSet('desktop').serve_local_data('/exists/on/another/node', node_id='B')
    assert not reply['success'] and reply['error_code'] == 'different_file_node'


def test_fleet_is_registered_as_a_builtin_app():
    from pantheon.apps.registry import builtin_apps
    manifest = next(app.manifest for app in builtin_apps() if app.manifest.id == 'fleet')
    assert manifest.kind.value == 'service'
    assert manifest.entry.frontend == 'ui:fleet'
    assert manifest.entry.backend


@pytest.mark.asyncio
async def test_output_panel_reads_agent_owned_state_with_source_node(tmp_path):
    import json
    from pantheon.chatroom.room import ChatRoom
    room = ChatRoom.__new__(ChatRoom)
    room.memory_manager = SimpleNamespace(get_memory=lambda chat_id: {"id": chat_id})
    room._project_dir_for_chat = AsyncMock(return_value=str(tmp_path))
    state = tmp_path / '.pantheon' / 'brain' / 'chat-1' / 'task_state.json'
    state.parent.mkdir(parents=True)
    output = {'path': 'report.md', 'source': {'node_id': 'Node-B', 'path': '/reports/report.md'}}
    state.write_text(json.dumps({'state': {'outputs': [output], 'task_dirs': {'Report': 'reports'}}}))
    result = await room.get_chat_outputs('chat-1')
    assert result == {'success': True, 'outputs': [output], 'task_dirs': {'Report': 'reports'}}
    assert (await room.get_chat_outputs('../other-chat'))['success'] is False
