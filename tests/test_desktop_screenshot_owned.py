"""Desktop screenshot artifacts do not need their consumer's runtime/settings."""
import base64
import builtins
from pathlib import Path

import pytest

from pantheon.apps.builtin.desktop.files_binding import DesktopFilesBinding
from pantheon.apps.builtin.desktop.data_server import DataServerConfig, LiveViewDataServer
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.toolset import ExecutionContext


@pytest.fixture
def bound(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    return DesktopToolSet(files_binding=DesktopFilesBinding(workspace=workspace,
        app_roots=[(workspace / 'apps', 'user')], data_roots=[workspace],
        server=LiveViewDataServer(config=DataServerConfig()), node_id='explicit-node'))


def test_screenshot_stem_and_requested_path_cannot_escape_bound_workspace(bound, tmp_path):
    uri = 'data:image/png;base64,' + base64.b64encode(b'pixels').decode()
    shot = bound._package_screenshot(uri, '../../outside')
    assert shot['success'], shot
    assert Path(shot['path']).parent == bound._work_root() / '.pantheon/live_view_snapshots'
    assert shot['node_id'] == 'explicit-node'
    assert shot['content_blocks'][0]['image_url']['url'] == uri
    assert Path(shot['path']).read_bytes() == b'pixels'
    target = tmp_path / 'existing.png'
    target.write_bytes(b'original')
    (bound._work_root() / 'link.png').symlink_to(target)
    for path in ('../existing.png', str(target), 'link.png'):
        failed = bound._package_screenshot(uri, 'win-1', path=path)
        assert not failed['success'] and 'workspace' in failed['error']
    assert target.read_bytes() == b'original'


def test_invalid_or_oversized_capture_does_not_replace_a_saved_file(bound, monkeypatch):
    target = bound._work_root() / 'existing.png'
    target.write_bytes(b'original')
    monkeypatch.setattr('pantheon.apps.builtin.file.image_sources.MAX_IMAGE_BYTES', 8)
    for uri in ('data:image/png;base64,%%%=', 'data:image/png;base64,',
                'data:image/png;base64,' + base64.b64encode(b'oversized').decode(),
                'data:text/html;base64,YQ=='):
        assert not bound._package_screenshot(uri, 'win-1', path=str(target))['success']
    assert target.read_bytes() == b'original'


@pytest.mark.parametrize('native', [False, True])
def test_bound_screenshot_keeps_capture_provenance(bound, native):
    result = bound._package_screenshot('data:image/png;base64,YQ==', 'win-1', native=native)
    assert result['success']
    assert ('NOT a screenshot' in result['note']) == native
    assert ('Browser-composited' in result['note']) == (not native)


def test_legacy_screenshot_uses_wire_context_without_importing_agent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('pantheon.settings.get_settings', lambda: SimpleNamespace(pantheon_dir=tmp_path, work_dir=tmp_path))
    monkeypatch.setenv('PANTHEON_FLEET_NODE_ID', 'legacy-node')
    service = DesktopToolSet()
    monkeypatch.setattr(service, 'get_context', lambda: ExecutionContext(caller_models=['anthropic/claude-test']))
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name != 'pantheon.agent', 'Legacy capture must not import its caller either'
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    result = service._package_screenshot('data:image/png;base64,YQ==', 'win-1')
    assert result['success'] and result['content_blocks']
    assert result['node_id'] == 'legacy-node'
    monkeypatch.setattr(service, 'get_context', lambda: ExecutionContext(caller_models=['deepseek/example']))
    result = service._package_screenshot('data:image/png;base64,YQ==', 'win-2')
    assert result['success'] and 'content_blocks' not in result
    assert 'observe_images' in result['note']


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True])
async def test_native_capture_success_cannot_hide_artifact_failure(bound, tmp_path, monkeypatch, remote):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pantheon.apps.builtin.desktop.native_control import NativeWindowController

    shot = {'success': True, 'data_url': 'data:image/png;base64,YQ=='}
    if remote:
        monkeypatch.setattr(bound, '_stream_window', lambda _: (None, {'app_id': 'pkg:browser'}, {}))
        monkeypatch.setattr(bound, 'desktop_stream_call', AsyncMock(return_value=shot))
    else:
        monkeypatch.setattr(bound, '_stream_window', lambda _: None)
        async def call(coro):
            return await coro
        monkeypatch.setattr(bound, '_native_target', AsyncMock(return_value=(
            SimpleNamespace(call=call), {'xid': 123}, [])))
        monkeypatch.setattr(NativeWindowController, 'screenshot', AsyncMock(return_value=shot))
    target = tmp_path / 'private.png'
    target.write_bytes(b'private')
    result = await bound.desktop_screenshot('win-1', source='native', path=str(target))
    assert result['success'] is False, result
    assert 'workspace' in result['error']
    assert 'path' not in result and 'content_blocks' not in result
    assert target.read_bytes() == b'private'
