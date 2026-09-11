import json
from pathlib import Path
import shutil

from pantheon.apps.store_release import git
from pantheon.apps.builtin.desktop.store_manager import AppStoreManager
from pantheon.apps.builtin.desktop.app_upstream import sync_upstream
import pytest


def repo(path, app_id='cytoscape'):
    path.mkdir(parents=True)
    (path / 'app.json').write_text(json.dumps({'id': app_id, 'name': app_id, 'version': '1.0.0', 'apiVersion': 2,
                                           'surface': 'dom', 'entry': {'frontend': 'main.js'}}))
    (path / 'main.js').write_text('original\n')
    git(path, 'init', '-q', '-b', 'main')
    commit(path)
    git(path, 'tag', 'v1.0.0')
    return path


def commit(path):
    git(path, 'add', '-A')
    git(path, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'Change')
    return git(path, 'rev-parse', 'HEAD').strip()


def test_fresh_volume_does_not_install_optional_image_sources(tmp_path):
    repo(tmp_path / 'image/cytoscape')
    manager = AppStoreManager([(tmp_path / 'user/apps', 'user'), (tmp_path / 'image', 'builtin')])
    manager.versions.ensure()
    assert manager.inventory()['apps'] == []
    assert not (tmp_path / 'user/apps/cytoscape').exists()


def test_existing_repository_migrates_once_preserving_dirty_files_and_default(tmp_path):
    manager = AppStoreManager([(tmp_path / 'user/apps', 'user')])
    source = repo(manager.records / 'repositories/builtin/cytoscape')
    (source / 'main.js').write_text('user edits\n')
    manager.versions._default_path('cytoscape').parent.mkdir(parents=True)
    manager.versions._default_path('cytoscape').write_text(json.dumps({'scope': 'builtin', 'commit': git(source, 'rev-parse', 'HEAD').strip(), 'version': '1.0.0'}))
    manager.versions.ensure()
    app = manager.find('cytoscape')
    assert app['scope'] == 'user' and app['git']['modified']
    assert Path(app['dir'], 'main.js').read_text() == 'user edits\n'
    assert manager.versions.default('cytoscape')['scope'] == 'user'
    archived = manager.remove('cytoscape', 'user', app['repository_id'])
    manager.versions.ensure()
    assert not manager.inventory()['apps']
    assert manager.branches.trash_entry(archived['archive_id'])
    manager.branches.purge(archived['archive_id'])
    assert not source.exists()
    manager.versions.ensure()
    assert not manager.inventory()['apps']


def setup_pull(tmp_path, origin='store'):
    upstream = repo(tmp_path / 'remote')
    local = tmp_path / 'user/apps/cytoscape'
    local.parent.mkdir(parents=True)
    git(tmp_path, 'clone', '-q', str(upstream), str(local))
    manager = AppStoreManager([(local.parent, 'user')])
    manager._save('cytoscape', {'origin': origin, 'installed_commit': git(local, 'rev-parse', 'HEAD').strip()})
    return upstream, local, manager


def test_pull_fast_forwards_main_and_new_launch_while_pinned_snapshot_stays(tmp_path):
    upstream, local, manager = setup_pull(tmp_path)
    app = manager.find('cytoscape')
    old = manager.versions.resolve('cytoscape')
    (upstream / 'main.js').write_text('remote update\n')
    head = commit(upstream)
    result = sync_upstream(manager, app, {'id': 'remote', 'head': head, 'version': '1.0.0'}, str(upstream), pull=True, expected_commit=head)
    assert result['commit'] == head
    assert git(local, 'branch', '--show-current').strip() == 'main'
    assert manager.versions.resolve('cytoscape')['revision']['commit'] == head
    assert Path(old['dir'], 'main.js').read_text() == 'original\n'
    assert git(local, 'rev-parse', 'v1.0.0').strip() != head


def test_pull_dirty_tree_and_stale_confirmation_preserve_work(tmp_path):
    upstream, local, manager = setup_pull(tmp_path)
    (local / 'main.js').write_text('not committed\n')
    head = git(upstream, 'rev-parse', 'HEAD').strip()
    with pytest.raises(ValueError, match='working-tree'):
        sync_upstream(manager, manager.find('cytoscape'), {'head': head}, str(upstream), pull=True, expected_commit=head)
    assert (local / 'main.js').read_text() == 'not committed\n'
    with pytest.raises(ValueError, match='remote main changed'):
        sync_upstream(manager, manager.find('cytoscape'), {'head': head}, str(upstream), pull=True, expected_commit='0' * 40)


def test_fork_pull_merges_independent_changes_and_aborts_conflicts(tmp_path):
    upstream, local, manager = setup_pull(tmp_path, 'fork')
    (local / 'mine.txt').write_text('mine')
    mine = commit(local)
    (upstream / 'remote.txt').write_text('upstream')
    head = commit(upstream)
    sync_upstream(manager, manager.find('cytoscape'), {'head': head}, str(upstream), pull=True, expected_commit=head)
    assert (local / 'mine.txt').read_text() == 'mine'
    assert (local / 'remote.txt').read_text() == 'upstream'
    assert len(git(local, 'show', '-s', '--format=%P', 'HEAD').split()) == 2
    (local / 'main.js').write_text('local conflicting line\n'); before = commit(local)
    (upstream / 'main.js').write_text('remote conflicting line\n'); head = commit(upstream)
    with pytest.raises(ValueError, match='Merge conflicts in main.js'):
        sync_upstream(manager, manager.find('cytoscape'), {'head': head}, str(upstream), pull=True, expected_commit=head)
    assert git(local, 'rev-parse', 'HEAD').strip() == before
    assert not git(local, 'status', '--porcelain').strip()
    assert (local / 'main.js').read_text() == 'local conflicting line\n'
