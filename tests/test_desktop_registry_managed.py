import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import nullcontext

import pytest

from apps.desktop.toolset import DesktopToolSet


@pytest.mark.parametrize('icon_kind', ['svg', 'png', 'missing', 'large', 'escape', 'symlink', 'unsupported'])
def test_registry_icons_work_before_data_endpoint_is_ready(tmp_path, monkeypatch, icon_kind):
    import base64
    monkeypatch.setattr('pathlib.Path.home', classmethod(lambda cls: tmp_path))
    directory = tmp_path / 'builtin' / 'demo'
    directory.mkdir(parents=True)
    content = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg>'
    relative = 'icon.svg'
    if icon_kind == 'png':
        relative, content = 'icon.png', b'\x89PNG\r\n\x1a\n'
    elif icon_kind == 'large':
        content = b'x' * (256 * 1024 + 1)
    elif icon_kind == 'escape':
        relative = '../icon.svg'
    elif icon_kind == 'unsupported':
        relative = 'icon.html'
    if icon_kind == 'symlink':
        outside = tmp_path / 'outside.svg'
        outside.write_bytes(content)
        (directory / relative).symlink_to(outside)
    elif icon_kind != 'missing':
        (directory / relative).write_bytes(content)
    (directory / 'app.json').write_text(json.dumps({
        'id': 'demo', 'name': 'Demo', 'version': '1.0.0', 'entry': {'frontend': 'main.js'},
        'icon': {'path': relative},
    }))
    toolset = DesktopToolSet.__new__(DesktopToolSet)
    monkeypatch.setattr(toolset, '_app_scope_roots', lambda: [(tmp_path / 'user', 'user'), (directory.parent, 'builtin')])
    toolset.serve_local_data = AsyncMock(side_effect=AssertionError('No data endpoint yet'))
    result = asyncio.run(toolset.desktop_app_registry())
    assert result['success'], result
    assert len(result['apps']) == 1  # Invalid art never hides an installed app.
    app = result['apps'][0]
    if icon_kind in ('svg', 'png'):
        mime = 'image/svg+xml' if icon_kind == 'svg' else 'image/png'
        assert app['icon_url'] == f'data:{mime};base64,' + base64.b64encode(content).decode()
    else:
        assert 'icon_url' not in app
    toolset.serve_local_data.assert_not_awaited()


def test_bundled_viewers_have_self_contained_icons():
    from pantheon.apps.store_release import release_icon
    root = Path(__file__).resolve().parents[1] / 'apps'
    for name in ('image_viewer', 'pdf_viewer', 'text_viewer'):
        directory = root / name
        manifest = json.loads((directory / 'app.json').read_text())
        assert release_icon(directory, manifest).startswith('data:image/svg+xml;base64,')


def test_registry_keeps_shell_frontend_execution_and_scope_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', classmethod(lambda cls: tmp_path))
    roots = [(tmp_path / 'user', 'user'), (tmp_path / 'builtin', 'builtin')]
    execution = {'protocol': 1, 'manifest': 'fleet.json'}
    for (root, _), version, managed in zip(roots, ['0.4.1', '0.2.0'], [True, False]):
        app = root / 'office'
        app.mkdir(parents=True)
        manifest = {'id': 'office', 'name': 'Office', 'version': version,
                    'apiVersion': 2, 'surface': 'dom', 'entry': {'frontend': 'ui:office'}}
        if managed:
            manifest['execution'] = execution
        (app / 'app.json').write_text(json.dumps(manifest))
    headless = roots[0][0] / 'worker'
    headless.mkdir()
    (headless / 'app.json').write_text(json.dumps({
        'id': 'worker', 'name': 'Worker', 'version': '1.0.0', 'apiVersion': 2,
        'surface': 'headless', 'entry': {'backend': 'backend.py'},
    }))
    toolset = DesktopToolSet.__new__(DesktopToolSet)
    monkeypatch.setattr(toolset, '_app_scope_roots', lambda: roots)

    result = asyncio.run(toolset.desktop_app_registry())

    assert result['success'], result
    apps = {app['manifest']['id']: app for app in result['apps']}
    assert set(apps) == {'office'}
    assert len(result['apps']) == 1
    assert apps['office']['scope'] == 'user'
    assert apps['office']['manifest']['version'] == '0.4.1'
    assert apps['office']['manifest']['execution'] == execution


@pytest.mark.parametrize('explicit_revision', [False, True])
def test_install_on_node_stages_the_resolved_revision(tmp_path, monkeypatch, explicit_revision):
    revision = {'scope': 'user', 'commit': 'abc123', 'repository_id': 'office-repo'}
    directory = tmp_path / 'immutable-office'
    directory.mkdir()
    (directory / 'app.json').write_text(json.dumps({'id': 'office', 'version': '1', 'entry': {}, 'execution': {'protocol': 1}}))
    manager = SimpleNamespace(records=tmp_path / 'records', lock=nullcontext, versions=SimpleNamespace(
        launch_default=lambda app_id: revision,
        resolve=lambda *args: {'dir': str(tmp_path / 'immutable-office')},
    ))
    monkeypatch.setattr('apps.desktop.store_manager.AppStoreManager', lambda roots: manager)
    resolver = object()
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    lifecycle = SimpleNamespace(stage=AsyncMock(return_value='digest'),
                                submit=AsyncMock(return_value={'state': 'queued'}))
    monkeypatch.setattr('apps.desktop.app_placement.FleetLifecycle', lambda value: lifecycle)
    monkeypatch.setattr('apps.desktop.app_placement.AppPlacement.target', AsyncMock(return_value={'node_id': 'node', 'name': 'Mac'}))
    toolset = DesktopToolSet.__new__(DesktopToolSet)
    monkeypatch.setattr(toolset, '_app_scope_roots', lambda: [])

    result = asyncio.run(toolset.desktop_app_install_on_node(
        'office', 'node', revision=revision if explicit_revision else None,
        operation_id='install-office',
    ))

    assert result['success'], result
    lifecycle.stage.assert_awaited_once_with('node', Path(tmp_path / 'immutable-office'))
    lifecycle.submit.assert_awaited_once_with('node', 'install', 'digest', operation_id='install-office')


@pytest.mark.asyncio
async def test_window_usage_routes_through_desktop_with_exact_binding(monkeypatch):
    from pantheon.apps.lifecycle import FleetLifecycle
    resolver = object()
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    request = AsyncMock(return_value={'ok': True})
    monkeypatch.setattr(FleetLifecycle, '_request', request)
    toolset = DesktopToolSet.__new__(DesktopToolSet)
    result = await toolset.desktop_app_usage('mac', 'lease', 'instance', 'a'*64, 7,
                                             lease_id='window-a', release=True)
    assert result == {'success': True, 'ok': True}
    request.assert_awaited_once_with('mac', 'lease', instance_id='instance',
        revision='a'*64, generation=7, lease_id='window-a', release=True, keep_alive=False)
    request.reset_mock()
    result = await toolset.desktop_app_usage('mac', 'stop', 'instance', 'a'*64, 7)
    assert not result['success'] and 'Unsupported' in result['error']
    request.assert_not_awaited()
