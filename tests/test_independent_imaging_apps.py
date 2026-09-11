"""Package discovery, native ownership and ImageJ's private file boundary."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pantheon.apps.registry import BUILTIN_ROOT
from pantheon.apps.builtin.desktop.native_apps import NativeAppManager
try:
    from pantheon.apps.builtin.imagej.backend import register
except ModuleNotFoundError:
    register = None


@pytest.mark.skipif(register is None, reason="Optional App source repos are not checked out")
def test_imaging_frontends_are_self_contained_packages():
    for app_id in ('imagej', 'qupath'):
        root = BUILTIN_ROOT / app_id
        manifest = json.loads((root / 'app.json').read_text())
        assert (root / manifest['entry']['frontend']).is_file()
        assert (root / manifest['icon']['path']).is_file()
        assert manifest['launcher'] and manifest['opens']
    assert json.loads((BUILTIN_ROOT / 'imagej/app.json').read_text())['name'] == 'ImageJ.js'
    for app_id in ('qupath', 'browser'):
        assert json.loads((BUILTIN_ROOT / app_id / 'app.json').read_text())['surface'] == 'stream'


@pytest.mark.asyncio
async def test_native_dispatch_preserves_session_owner_and_graceful_close():
    manager = NativeAppManager(SimpleNamespace())
    driver = SimpleNamespace(launch=AsyncMock(return_value={'running': True}),
                             close=AsyncMock(return_value={'running': True, 'close_requested': True}))
    manager.drivers['qupath'] = driver
    await manager.launch('qupath', 'win-1')
    with pytest.raises(ValueError, match='another App'):
        await manager.launch('other', 'win-1')
    assert (await manager.close('win-1'))['running']
    driver.close.assert_awaited_once_with('win-1')
    assert not (await manager.status('missing'))['running']


def test_native_dispatch_loads_trusted_driver_only_when_app_is_installed(monkeypatch):
    from pantheon.apps.builtin.desktop.store_manager import AppStoreManager
    monkeypatch.setattr(AppStoreManager, "inventory", lambda self, **kw: {"apps": [{"id": "qupath"}]} if kw.get("match_id") == "qupath" else {"apps": []})
    monkeypatch.setattr("pantheon.apps.builtin.desktop.app_versions.AppVersions.migrate_bundled", lambda self: None)
    manager = NativeAppManager(SimpleNamespace())
    assert type(manager._driver('qupath')).__module__.endswith('qupath.native')
    with pytest.raises(ValueError, match='No installed native driver'):
        manager._driver('files')


@pytest.mark.skipif(register is None, reason="Optional ImageJ.js source repo is not checked out")
@pytest.mark.asyncio
async def test_imagej_preparation_confines_files_to_workspace(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    image = workspace / 'sample.png'; image.write_bytes(b'png')
    outside = tmp_path / 'private.png'; outside.write_bytes(b'private')
    (workspace / 'escape.png').symlink_to(outside)
    methods = {}
    def method(fn): methods[fn.__name__] = fn; return fn
    ctx = SimpleNamespace(workspace=workspace, method=method, serve=AsyncMock(return_value='https://data/image'))
    register(ctx)
    result = await methods['prepare']('sample.png')
    assert result['path'] == str(image) and result['url'] == 'https://data/image'
    ctx.serve.reset_mock()
    for path in ('escape.png', '../private.png', str(outside)):
        with pytest.raises(ValueError, match='inside the workspace'):
            await methods['prepare'](path)
    ctx.serve.assert_not_called()
