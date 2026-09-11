from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pantheon.utils.file_paths import resolve_workspace_path
from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


@pytest.fixture
def app_paths(tmp_path, monkeypatch):
    home = tmp_path / 'volume'
    workspace = home / 'default_workspace'
    workspace.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    app = home / '.pantheon/apps/spatial3d'
    (app / 'frontend').mkdir(parents=True)
    (app / 'app.json').write_text('{"id":"spatial3d"}')
    source = app / 'frontend/index.js'
    source.write_text('export const value = 1;\n')
    return workspace, app, source


def test_app_namespace_resolves_user_install_and_volume_alias(app_paths, tmp_path):
    workspace, app, source = app_paths
    relative = '.pantheon/apps/spatial3d/frontend/index.js'
    alias = tmp_path / 'workspace'
    alias.symlink_to(workspace)
    for root in (workspace, alias):
        for path in (relative, str(workspace / relative), str(alias / relative), str(source), '~/.pantheon/apps/spatial3d/frontend/index.js'):
            assert resolve_workspace_path(path, root).resolve() == source


def test_missing_file_never_switches_from_workspace_app_to_user_app(app_paths):
    workspace, app, source = app_paths
    local = workspace / '.pantheon/apps/spatial3d'
    local.mkdir(parents=True)
    requested = local / 'frontend/index.js'
    assert resolve_workspace_path(str(requested), workspace) == requested
    requested.parent.mkdir()
    requested.write_text('workspace version')
    assert resolve_workspace_path(str(requested), workspace) == requested


def test_no_guessing_outside_app_namespace_or_through_traversal(app_paths, tmp_path):
    workspace, app, source = app_paths
    for value in ('other/index.js', '.pantheon/apps/unknown/frontend/index.js',
                  '.pantheon/apps/spatial3d/../other/index.js', str(tmp_path / 'unrelated/.pantheon/apps/spatial3d/frontend/index.js')):
        p = Path(value)
        assert resolve_workspace_path(value, workspace) == (p if p.is_absolute() else workspace / p)
    local = workspace / '.pantheon/apps/spatial3d'
    local.parent.mkdir(parents=True)
    local.symlink_to(tmp_path / 'deleted-app')
    assert resolve_workspace_path(str(local / 'frontend/index.js'), workspace) == local / 'frontend/index.js'


@pytest.mark.asyncio
async def test_read_and_edit_use_same_app_and_return_actual_identity(app_paths):
    workspace, app, source = app_paths
    fm = FileManagerToolSet('file_manager', str(workspace))
    path = '.pantheon/apps/spatial3d/frontend/index.js'
    for args in ({}, {'max_chars': 5}, {'start_line': 1, 'end_line': 1}):
        result = await fm.read_file(path, **args)
        assert result['success'] and result['resolved_path'] == str(source)
    await fm.write_file(result['resolved_path'], 'updated', overwrite=True)
    assert source.read_text() == 'updated'
    assert not (workspace / '.pantheon/apps/spatial3d').exists()
    result = await fm.read_file('.pantheon/apps/spatial3d/frontend/missing.js')
    assert not result['success']
    assert result['resolved_path'] == str(source.parent / 'missing.js')


@pytest.mark.asyncio
async def test_viewer_serves_the_same_file_as_the_agent(app_paths, monkeypatch):
    workspace, app, source = app_paths
    from pantheon.settings import get_settings
    monkeypatch.setattr(get_settings(), 'work_dir', workspace)
    desktop = DesktopToolSet('desktop')
    server = type('Server', (), {'base_url': 'http://localhost', 'url_for': lambda self, p: 'http://localhost/index.js' if p == source else None})()
    monkeypatch.setattr(desktop, '_ensure_data_server', AsyncMock(return_value=server))
    for path in ('.pantheon/apps/spatial3d/frontend/index.js', str(workspace / '.pantheon/apps/spatial3d/frontend/index.js'), '~/.pantheon/apps/spatial3d/frontend/index.js'):
        result = await desktop.serve_local_data(path)
        assert result['success'], result
        assert result['resolved_path'] == str(source)


@pytest.mark.asyncio
async def test_relative_file_returns_canonical_path_from_project_context(app_paths, monkeypatch):
    workspace, app, source = app_paths
    project = workspace / 'project'
    project.mkdir()
    (project / 'index.js').write_text('project file')
    fm = FileManagerToolSet('file_manager', str(workspace))
    monkeypatch.setattr(fm, '_get_effective_workdir', lambda: str(project))
    result = await fm.read_file('index.js')
    assert result['success'] and result['resolved_path'] == str(project / 'index.js')
