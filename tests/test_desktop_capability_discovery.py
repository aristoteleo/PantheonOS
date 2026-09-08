"""Discovery must describe the app behind an existing desktop window."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from apps.desktop.app_supervisor import AppSupervisor
from apps.desktop.desktop_session import DesktopSessionStore
from apps.desktop.toolset import DesktopToolSet
from pantheon.apps.registry import BUILTIN_ROOT


@pytest.fixture
def rig(tmp_path, monkeypatch):
    store = DesktopSessionStore(work_dir=tmp_path)
    store.load()
    toolset = DesktopToolSet()
    monkeypatch.setattr(toolset, '_desktop', lambda: store)
    toolset._apps_supervisor = AppSupervisor(
        tmp_path, [(tmp_path / 'installed', 'workspace'), (BUILTIN_ROOT, 'builtin')], AsyncMock(),
    )
    return toolset, store, tmp_path / 'installed'


@pytest.mark.asyncio
async def test_existing_files_and_terminal_advertise_their_real_bridge_actions(rig):
    toolset, store, _ = rig
    for app in ('files', 'terminal', 'browser'):
        store.apply('open', {'app_id': app})
    entries = {w['app_id']: w for w in (await toolset.desktop_windows())['result']['windows']}
    assert set(entries['files']['actions']) == {'navigate', 'refresh', 'select', 'open', 'delete'}
    assert set(entries['terminal']['actions']) == {'run', 'input', 'interrupt', 'clear'}
    assert {'navigate', 'reload', 'back', 'forward'} <= set(entries['browser']['actions'])
    assert all(w['controllable'] for w in entries.values())


@pytest.mark.asyncio
async def test_packaged_state_viewers_are_discoverable_without_named_actions(rig):
    toolset, store, _ = rig
    for app in ('gosling', 'igv', 'molstar', 'image-viewer', 'pdf-viewer', 'text-viewer'):
        store.apply('open', {'app_id': 'pkg:' + app})
    entries = (await toolset.desktop_windows())['result']['windows']
    assert all(w['controllable'] for w in entries)
    assert all(not w['app_id'].startswith('pkg:') for w in entries)
    assert all(w['app_id'].startswith('pkg:') for w in store.session.windows.values())
    for entry in entries:
        if entry['app_id'] in ('gosling', 'igv', 'molstar'):
            assert Path(entry['skill']).is_file()


@pytest.mark.asyncio
async def test_shell_apps_without_a_bridge_are_not_reported_as_controllable(rig):
    toolset, store, _ = rig
    for app in ('settings', 'store', 'agent', 'interfaces', 'missing-app'):
        store.apply('open', {'app_id': app})
    entries = (await toolset.desktop_windows())['result']['windows']
    assert all(not w['controllable'] and w['actions'] == [] for w in entries)


@pytest.mark.asyncio
async def test_agent_authored_view_advertises_its_state_bridge_without_inventing_actions(rig):
    toolset, store, _ = rig
    store.apply('open', {'app_id': 'agent-view'})
    entry = (await toolset.desktop_windows())['result']['windows'][0]
    assert entry['app_id'] == 'agent-view'
    assert entry['controllable'] is True
    assert entry['actions'] == []  # defineAction belongs to the dynamically loaded module


@pytest.mark.asyncio
async def test_installed_override_and_manifest_changes_are_reflected(rig, monkeypatch):
    toolset, store, installed = rig
    package = installed / 'local-gosling'
    package.mkdir(parents=True)
    manifest = {'id': 'gosling', 'name': 'Local Plot', 'entry': {'frontend': 'frontend.js'},
                'actions': [{'name': 'changeColor'}], 'skill': 'LOCAL.md'}
    path = package / 'app.json'
    path.write_text(json.dumps(manifest))
    (package / 'LOCAL.md').write_text('Local plot state contract')
    store.apply('open', {'app_id': 'pkg:gosling'})
    window = (await toolset.desktop_windows())['result']['windows'][0]
    assert window['app_id'] == 'gosling' and window['name'] == 'Local Plot'
    assert window['actions'] == ['changeColor'] and window['skill'] == str(package / 'LOCAL.md')
    manifest['actions'] = [{'name': 'changePalette'}]
    path.write_text(json.dumps(manifest))
    assert (await toolset.desktop_windows())['result']['windows'][0]['actions'] == ['changePalette']
    monkeypatch.setattr(toolset, '_desktop_request', AsyncMock(return_value={
        'success': True, 'result': {'apps': [{'app_id': 'gosling', 'actions': []}]},
    }))
    entry = (await toolset.desktop_apps())['result']['apps'][0]
    assert entry['actions'] == ['changePalette'] and entry['controllable']


@pytest.mark.asyncio
async def test_scientific_menu_actions_are_agent_discoverable(rig):
    toolset, store, _ = rig
    for app in ('spatial3d', 'volume3d', 'vitessce'):
        store.apply('open', {'app_id': 'pkg:' + app})
    entries = {w['app_id']: w for w in (await toolset.desktop_windows())['result']['windows']}
    assert 'loadDataset' in entries['spatial3d']['actions']
    assert 'loadDataset' in entries['volume3d']['actions']
    assert 'toggleView' in entries['vitessce']['actions']
