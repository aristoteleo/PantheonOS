import json
from pathlib import Path

import pytest

from apps.desktop.app_supervisor import AppSupervisor
from apps.desktop.store_manager import AppStoreManager
from pantheon.apps.store_release import git
from test_desktop_store_manager import app


@pytest.fixture
def manager(tmp_path):
    roots = [(tmp_path / 'ws' / 'apps', 'workspace'), (tmp_path / 'user' / 'apps', 'user'), (tmp_path / 'official', 'builtin')]
    app(roots[-1][0])
    result = AppStoreManager(roots)
    result.versions.ensure()
    return result


def test_fork_clones_history_without_overriding_or_changing_default(manager, tmp_path):
    first = manager.find('demo', 'builtin')['git']['commit']
    app(manager.roots[-1][0], '1.0.1')
    manager.versions.ensure()
    manager.versions.set_default('demo', 'builtin', first)
    result = manager.branches.fork_app('demo', 'builtin')
    branch = manager.find('demo', 'fork')
    root = Path(branch['dir'])
    assert result['scope'] == 'fork'
    assert git(root, 'branch', '--show-current').strip() == 'my-work'
    assert git(root, 'merge-base', first, 'HEAD').strip() == first
    assert set(git(root, 'tag', '--list').splitlines()) == {'v1.0.0', 'v1.0.1'}
    assert not branch['effective']
    assert manager.find('demo')['scope'] == 'builtin'
    assert manager.versions.default('demo') == {'scope': 'builtin', 'commit': first, 'version': '1.0.0', 'repository_id': manager.find('demo', 'builtin')['repository_id']}
    supervisor = AppSupervisor(workspace=tmp_path, roots=manager.roots, serve=lambda _: '')
    supervisor.scan()
    assert supervisor.entries['demo'].scope == 'builtin'


def test_fork_can_select_old_commit_then_publish_and_choose_default(manager):
    first = manager.find('demo', 'builtin')['git']['commit']
    app(manager.roots[-1][0], '1.0.1')
    manager.versions.ensure()
    manager.branches.fork_app('demo', 'builtin', first)
    branch = manager.find('demo', 'fork')
    assert branch['manifest']['version'] == '1.0.0'
    (Path(branch['dir']) / 'notes.txt').write_text('my changes')
    manager.tag('demo', '1.0.2', 'fork')
    chosen = manager.versions.set_default('demo', 'fork', 'v1.0.2')['default']
    assert chosen['scope'] == 'fork'
    assert manager.versions.resolve('demo')['revision'] == chosen
    assert manager.prepare('demo', 'fork')['app_release']['commit'] == chosen['commit']
    assert manager.find('demo', 'builtin')['manifest']['version'] == '1.0.1'


def test_remove_dirty_branch_keeps_files_all_refs_and_restores_without_override(manager):
    manager.branches.fork_app('demo', 'builtin')
    root = Path(manager.find('demo', 'fork')['dir'])
    manager.versions.set_default('demo', 'fork', 'v1.0.0')
    git(root, 'branch', 'unfinished-work')
    (root / 'index.js').write_text('unsaved local edit')
    (root / 'untracked.txt').write_text('keep me')
    removed = manager.remove('demo', 'fork')
    assert not root.exists()
    assert manager.versions.default('demo') is None
    assert manager.branches.list_trash()['items'][0]['modified']
    manager.branches.restore(removed['archive_id'])
    assert (root / 'index.js').read_text() == 'unsaved local edit'
    assert (root / 'untracked.txt').read_text() == 'keep me'
    assert 'unfinished-work' in git(root, 'branch', '--list')
    assert manager.versions.default('demo') is None
    assert manager.find('demo')['scope'] == 'builtin'
    assert manager.branches.list_trash()['items'] == []


def test_legacy_user_copy_can_be_removed_and_restored_as_non_overriding_branch(manager):
    manager.copy_to_user('demo', 'builtin')
    (manager.user_root / 'demo' / 'notes.txt').write_text('local change')
    result = manager.branches.fork_app('demo', 'builtin')
    assert result['existing'] and result['scope'] == 'user'
    removed = manager.remove('demo')
    assert manager.find('demo')['scope'] == 'builtin'
    assert manager.branches.restore(removed['archive_id'])['scope'] == 'fork'
    assert not (manager.user_root / 'demo').exists()
    assert (manager.branches.root / 'demo' / 'notes.txt').read_text() == 'local change'


def test_standalone_user_install_restores_to_discoverable_installation(manager):
    app(manager.user_root, app_id='standalone')
    manager.versions.ensure()
    removed = manager.remove('standalone')
    assert manager.branches.restore(removed['archive_id'])['scope'] == 'user'
    assert (manager.user_root / 'standalone' / 'app.json').is_file()


def test_fork_and_restore_never_overwrite_an_existing_personal_branch(manager):
    manager.branches.fork_app('demo', 'builtin')
    removed = manager.remove('demo', 'fork')
    manager.branches.fork_app('demo', 'builtin')
    root = manager.branches.root / 'demo'
    (root / 'local.txt').write_text('keep')
    assert manager.branches.fork_app('demo', 'builtin')['existing']
    restored = manager.branches.restore(removed['archive_id'])
    assert len([a for a in manager.inventory()['apps'] if a['scope'] == 'fork']) == 2
    assert restored['repository_id']
    assert (root / 'local.txt').read_text() == 'keep'
    assert manager.branches.list_trash()['items'] == []


def test_reject_invalid_trash_id_and_official_removal(manager):
    with pytest.raises(ValueError, match='Invalid App trash'):
        manager.branches.restore('../elsewhere')
    with pytest.raises(ValueError, match='Only user'):
        manager.remove('demo', 'builtin')
