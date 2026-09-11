import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from apps.file.file_manager import FileManagerToolSet
from apps.file.image_sources import resolve_image_sources
from apps.desktop.toolset import DesktopToolSet


@pytest.mark.asyncio
async def test_requested_screenshot_path_is_saved_and_observed(tmp_path, monkeypatch):
    from pantheon.toolset import ExecutionContext
    monkeypatch.setenv('PANTHEON_FLEET_NODE_ID', 'desktop-node')
    monkeypatch.setattr('pantheon.utils.vision_capability.supports_tool_result_image', lambda _: True)
    desktop = DesktopToolSet()
    monkeypatch.setattr(desktop, '_get_effective_workdir', lambda: str(tmp_path))
    monkeypatch.setattr(desktop, 'get_context', lambda: ExecutionContext(caller_models=['vision-model']))
    source = tmp_path / 'original.png'
    Image.new('RGB', (12, 12), 'red').save(source)
    shot = desktop._package_screenshot('data:image/png;base64,' + base64.b64encode(source.read_bytes()).decode(),
                                       'win-251', path='spatial3d-ui/before.png')
    assert shot['success'] and shot['path'] == str(tmp_path / 'spatial3d-ui/before.png')
    assert shot['node_id'] == 'desktop-node'
    assert shot['content_blocks']
    assert (tmp_path / 'spatial3d-ui/before.png').read_bytes() == source.read_bytes()
    fm = FileManagerToolSet('files', str(tmp_path))
    monkeypatch.setattr(fm, 'get_context', lambda: ExecutionContext(caller_models=['vision-model']))
    result = await fm.observe_images('What is visible?', [shot['image_ref']])
    assert result['success'] and result['image_count'] == 1
    assert result['sources'][0]['path'] == shot['path']


@pytest.mark.asyncio
async def test_exact_node_transfer_not_local_same_name(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_FLEET_NODE_ID', 'local')
    data = b'remote bytes'
    resolver = SimpleNamespace(ensure_instance=AsyncMock(return_value='remote-files'))
    calls = []
    async def invoke(method, args):
        calls.append(args)
        action = args['method']
        if action == 'open_file_for_read': return {'success': True, 'handle_id': 'h', 'total_size': len(data)}
        if action == 'read_chunk_at': return {'success': True, 'data': base64.b64encode(data).decode()}
        return {'success': True}
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    monkeypatch.setattr('pantheon.apps.proxy.ToolsetProxy.from_toolset', lambda _: SimpleNamespace(invoke=invoke))
    same_name = tmp_path / 'same.png'; same_name.write_text('unrelated local bytes')
    resolve = lambda _: same_name
    paths, sources = await resolve_image_sources(['pantheon-node:///remote/shared/same.png'], None, resolve, tmp_path)
    assert __import__('pathlib').Path(paths[0]).read_bytes() == data
    assert sources[0]['node_id'] == 'remote'
    resolver.ensure_instance.assert_awaited_once_with('file_manager', node_id='remote')
    assert calls[-1]['method'] == 'close_file'
    resolver.ensure_instance.side_effect = RuntimeError('node offline')
    with pytest.raises(RuntimeError, match='offline'):
        await resolve_image_sources([str(same_name)], 'remote', resolve, tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['oversized', 'truncated'])
async def test_transfer_failure_closes_handle(tmp_path, monkeypatch, failure):
    resolver = SimpleNamespace(ensure_instance=AsyncMock(return_value='remote-files'))
    calls=[]
    async def invoke(method, args):
        calls.append(args['method'])
        if args['method']=='open_file_for_read':
            return {'success': True, 'handle_id': 'h', 'total_size': 100000000 if failure=='oversized' else 12}
        return {'success': True, 'data': ''}
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    monkeypatch.setattr('pantheon.apps.proxy.ToolsetProxy.from_toolset', lambda _: SimpleNamespace(invoke=invoke))
    with pytest.raises(ValueError):
        await resolve_image_sources(['/shared/image.png'], 'remote', lambda p: tmp_path/p, tmp_path)
    assert calls[-1]=='close_file'


@pytest.mark.asyncio
async def test_conflicting_nodes_and_missing_image_do_not_guess(tmp_path):
    with pytest.raises(ValueError, match='conflicts'):
        await resolve_image_sources(['pantheon-node:///one/shared/a.png'], 'two', lambda p: tmp_path/p, tmp_path)
    fm = FileManagerToolSet('files', str(tmp_path))
    result = await fm.observe_images('Describe', ['missing.png'])
    assert not result['success'] and 'exact image_ref' in result['error']


@pytest.mark.asyncio
async def test_whole_desktop_capture_and_global_tools(monkeypatch):
    desktop = DesktopToolSet()
    monkeypatch.setattr(desktop, '_presence', lambda: SimpleNamespace(anchor_for=lambda _: {'viewport_id': 'v'}))
    monkeypatch.setattr(desktop, '_chat_id', lambda: '')
    package = __import__('unittest.mock', fromlist=['Mock']).Mock(return_value={'success': True})
    monkeypatch.setattr(desktop, '_package_screenshot', package)
    async def publish(ev):
        assert ev['scope']=='desktop' and ev['window_id']=='' and ev['viewport_id']=='v'
        await desktop.report_snapshot(ev['request_id'], True, 'data:image/png;base64,frame', source='browser-region-capture')
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    result = await desktop.desktop_screenshot(path='before.png')
    assert result['success'] and result['scope']=='desktop'
    package.assert_called_once_with('data:image/png;base64,frame', 'desktop', path='before.png')
    assert not (await desktop.desktop_screenshot(source='native'))['success']
    request=AsyncMock(return_value={'success':True})
    monkeypatch.setattr(desktop, '_desktop_request', request)
    await desktop.desktop_inspect()
    request.assert_awaited_with('desktop.inspect', {})
    await desktop.desktop_control([{'type':'launcher','open':True}])
    request.assert_awaited_with('desktop.control', {'actions':[{'type':'launcher','open':True}], 'inspection_id':''})
    assert not (await desktop.desktop_control([{}]*33))['success']
