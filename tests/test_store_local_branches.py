import json
from pathlib import Path
import pytest
from apps.desktop.store_manager import AppStoreManager
from apps.desktop.app_development import develop
from apps.desktop.app_upstream import sync_upstream
from pantheon.apps.store_release import git
from test_desktop_store_manager import app


def commit(root, message):
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=Tester', '-c', 'user.email=test@example.com', 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD').strip()


@pytest.fixture
def legacy(tmp_path):
    m = AppStoreManager([(tmp_path / 'user/apps', 'user')])
    source = app(m.user_root)
    m.versions.ensure()
    original = m.find('demo', 'user')
    m._save('demo', {'repository_id': original['repository_id'], 'origin': 'bundled', 'installed_commit': original['git']['commit']})
    target = m.branches.root / 'demo'
    target.parent.mkdir(parents=True)
    git(source, 'clone', '-q', str(source), str(target))
    git(target, 'switch', '-c', 'my-work')
    (target / 'index.js').write_text('my improved code')
    improved = commit(target, 'Improve app')
    git(target, 'tag', 'v1.1.0')
    repo = '00000000-0000-4000-8000-000000000001'
    m._save('demo', {'origin': 'fork', 'repository_id': repo, 'parent_repository_id': original['repository_id'], 'parent_commit': original['git']['commit']}, 'fork')
    return m, source, target, original, improved


def test_migration_keeps_dirty_index_untracked_tags_defaults_and_snapshots(legacy):
    m, source, root, original, improved = legacy
    snapshot = m.versions.resolve('demo', 'user', original['git']['commit'], original['repository_id'])
    (root / 'index.js').write_text('staged edit')
    git(root, 'add', 'index.js')
    (root / 'index.js').write_text('unstaged edit')
    (root / 'draft.md').write_text('not committed')
    index = (root / '.git/index').read_bytes()
    before = git(root, 'status', '--porcelain')
    m.versions.ensure()
    apps = m.inventory()['apps']
    assert len(apps) == 1
    a = apps[0]
    assert a['repository_id'] != original['repository_id']
    assert {b['name'] for b in a['git']['branches']} == {'official', 'my-work'}
    assert git(root, 'rev-parse', 'HEAD').strip() == improved
    assert git(root, 'rev-parse', 'v1.1.0').strip() == improved
    assert (root / '.git/index').read_bytes() == index
    assert git(root, 'status', '--porcelain') == before
    assert (root / 'draft.md').read_text() == 'not committed'
    assert not source.exists()
    assert Path(snapshot['dir']).exists()
    assert m.versions.resolve('demo')['revision']['commit'] == improved
    assert m.find('demo', 'user', original['repository_id'])['repository_id'] == a['repository_id']
    m.versions.ensure()
    assert len(m.inventory()['apps']) == 1
    assert m.versions.resolve('demo', 'user', original['git']['commit'], original['repository_id'])['revision'] == snapshot['revision']


def test_branch_default_follows_its_head_without_checkout(legacy):
    m, _, root, original, improved = legacy
    m.versions.ensure()
    a = m.find('demo', 'fork')
    m.versions.set_default('demo', 'fork', 'branch:official', a['repository_id'])
    assert m.versions.resolve('demo')['revision']['commit'] == original['git']['commit']
    assert git(root, 'branch', '--show-current').strip() == 'my-work'
    (root / 'new.txt').write_text('version 2')
    latest = commit(root, 'Next version')
    assert m.versions.resolve('demo')['revision']['commit'] == original['git']['commit']
    m.versions.set_default('demo', 'fork', 'branch:my-work', a['repository_id'])
    assert m.versions.resolve('demo')['revision']['commit'] == latest
    versions = m.versions.versions('demo', 'fork', a['repository_id'], 'official')['versions']
    assert all(v['commit'] != improved for v in versions)
    assert {'refs/heads/official', 'refs/heads/my-work'} <= {r['name'] for r in m.history('demo', 'fork')['refs']}


def test_in_place_branch_creation_keeps_identity(legacy):
    m, _, root, _, _ = legacy
    m.versions.ensure()
    a = m.find('demo', 'fork')
    created = m.branches.fork_app('demo', 'fork', repository_id=a['repository_id'], name='experiment')
    assert created['repository_id'] == a['repository_id']
    assert created['directory'] == str(root)
    assert len(m.inventory()['apps']) == 1
    assert m.versions.launch_default('demo')['branch'] == 'experiment'
    with pytest.raises(ValueError):
        m.branches.fork_app('demo', 'fork', repository_id=a['repository_id'], name='official')


def test_pull_only_advances_official_and_agent_can_merge_it(legacy, tmp_path):
    m, _, root, original, improved = legacy
    m.versions.ensure()
    remote = tmp_path / 'remote'
    git(root, 'clone', '-q', str(root), str(remote))
    git(remote, 'switch', '-C', 'main', original['git']['commit'])
    (remote / 'upstream.txt').write_text('new upstream file')
    new = commit(remote, 'Upstream update')
    a = m.find('demo', 'fork')
    (root / 'draft.txt').write_text('dirty personal file')
    repo = {'id': 'public', 'app_id': 'demo', 'head': new, 'name': 'Official', 'visibility': 'public'}
    result = sync_upstream(m, a, repo, str(remote), pull=True, expected_commit=new)
    assert result['branch'] == 'official'
    assert git(root, 'rev-parse', 'official').strip() == new
    assert git(root, 'rev-parse', 'HEAD').strip() == improved
    assert not (root / 'upstream.txt').exists()
    assert (root / 'draft.txt').read_text() == 'dirty personal file'
    commit(root, 'Save draft')
    develop(m, 'merge', 'demo', 'fork', a['repository_id'], branch='official')
    assert (root / 'upstream.txt').read_text() == 'new upstream file'
    assert (root / 'index.js').read_text() == 'my improved code'
    assert git(root, 'merge-base', '--is-ancestor', new, 'HEAD') == ''
    develop(m, 'switch', 'demo', 'fork', a['repository_id'], branch='official')
    with pytest.raises(ValueError, match='official branch'):
        develop(m, 'write', 'demo', 'fork', a['repository_id'], files={'index.js': 'bad'})
    with pytest.raises(ValueError, match='official branch'):
        m.tag('demo', '2.0.0', 'fork', a['repository_id'])


def test_dirty_previous_installation_is_not_hidden(legacy):
    m, source, _, _, _ = legacy
    (source / 'index.js').write_text('other work')
    result = m.versions.ensure()
    assert len(m.inventory()['apps']) == 2
    assert result['warnings']
    assert (source / 'index.js').read_text() == 'other work'


def test_connecting_unrelated_public_history_merges_from_preserved_legacy_base(legacy, tmp_path):
    m, _, root, _, improved = legacy
    m.versions.ensure()
    public = app(tmp_path / 'public')
    git(public, 'init', '-q', '-b', 'main')
    (public / 'README.md').write_text('Public documentation')
    remote_commit = commit(public, 'Publish official sources')
    a = m.find('demo', 'fork')
    sync_upstream(m, a, {'id': 'public', 'app_id': 'demo', 'head': remote_commit}, str(public), pull=True, expected_commit=remote_commit)
    develop(m, 'merge', 'demo', 'fork', a['repository_id'], branch='official')
    assert (root / 'README.md').read_text() == 'Public documentation'
    assert (root / 'index.js').read_text() == 'my improved code'
    assert git(root, 'merge-base', '--is-ancestor', improved, 'HEAD') == ''
    assert git(root, 'merge-base', '--is-ancestor', remote_commit, 'HEAD') == ''
    assert git(root, 'rev-parse', 'official').strip() == remote_commit
    assert git(root, 'rev-parse', 'v1.1.0').strip() == improved


def test_explicit_old_official_default_survives_consolidation(legacy):
    m, _, root, old, _ = legacy
    m.versions.set_default('demo', 'user', 'latest', old['repository_id'])
    m.versions.ensure()
    assert m.versions.launch_default('demo')['branch'] == 'official'
    assert m.versions.resolve('demo')['revision']['commit'] == old['git']['commit']
    assert git(root, 'branch', '--show-current').strip() == 'my-work'
    assert m.versions.resolve('demo', 'user', 'latest', old['repository_id'])['revision']['commit'] == old['git']['commit']


def test_purging_consolidated_repository_removes_legacy_backup_and_alias(legacy):
    m, _, _, old, _ = legacy
    m.versions.ensure()
    a = m.find('demo', 'fork')
    backup = m.records / 'legacy-repositories' / old['repository_id']
    alias = m.records / 'repository-aliases' / f"{old['repository_id']}.json"
    assert backup.is_dir() and alias.is_file()
    removed = m.branches.remove('demo', 'fork', a['repository_id'])
    assert backup.is_dir()
    m.branches.purge(removed['archive_id'])
    assert not backup.exists() and not alias.exists()


def test_canonical_checkout_remains_in_launcher_and_backend_discovery(legacy):
    from apps.desktop.app_supervisor import AppSupervisor
    m, _, root, _, _ = legacy
    m.versions.ensure()
    supervisor = AppSupervisor(workspace=root.parent, roots=m.roots, serve=lambda _: '')
    entries = supervisor.scan()
    assert any(e['id'] == 'demo' for e in entries)
    assert supervisor.entries['demo'].dir == root
    assert m.find('demo', 'fork')['effective']
