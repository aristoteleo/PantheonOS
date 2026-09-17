import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.desktop.toolset import DesktopToolSet


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
    manager = SimpleNamespace(versions=SimpleNamespace(
        launch_default=lambda app_id: revision,
        resolve=lambda *args: {'dir': str(tmp_path / 'immutable-office')},
    ))
    monkeypatch.setattr('apps.desktop.store_manager.AppStoreManager', lambda roots: manager)
    resolver = object()
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
    lifecycle = SimpleNamespace(stage=AsyncMock(return_value='digest'),
                                submit=AsyncMock(return_value={'state': 'queued'}))
    monkeypatch.setattr('pantheon.apps.lifecycle.FleetLifecycle', lambda value: lifecycle)
    toolset = DesktopToolSet.__new__(DesktopToolSet)
    monkeypatch.setattr(toolset, '_app_scope_roots', lambda: [])

    result = asyncio.run(toolset.desktop_app_install_on_node(
        'office', 'node', revision=revision if explicit_revision else None,
        operation_id='install-office',
    ))

    assert result['success'], result
    lifecycle.stage.assert_awaited_once_with('node', Path(tmp_path / 'immutable-office'))
    lifecycle.submit.assert_awaited_once_with('node', 'install', 'digest', operation_id='install-office')
