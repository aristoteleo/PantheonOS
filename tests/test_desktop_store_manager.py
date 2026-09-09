import json
from pathlib import Path

import pytest

from apps.desktop.store_manager import AppStoreManager
from pantheon.apps.store_release import git, prepare_release, unpack_release


def app(root: Path, version='1.0.0', app_id='demo'):
    directory = root / app_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'app.json').write_text(json.dumps({
        'id': app_id, 'name': 'Demo', 'version': version, 'apiVersion': 2,
        'surface': 'dom', 'entry': {'frontend': 'index.js'},
    }))
    (directory / 'index.js').write_text('export default {}')
    (directory / 'icon.png').write_bytes(bytes(range(256)))
    return directory


@pytest.fixture
def manager(tmp_path):
    roots = [(tmp_path / 'ws' / 'apps', 'workspace'), (tmp_path / 'user' / 'apps', 'user'), (tmp_path / 'official', 'builtin')]
    app(roots[2][0])
    return AppStoreManager(roots)


def download(directory):
    release = prepare_release(directory)
    return {'type': 'app', 'package_id': 'publisher-demo', 'name': 'publisher-demo',
            'version': release['manifest']['version'], 'app_release': release['app_release']}


def test_user_copy_keeps_official_and_binary_files(manager):
    manager.copy_to_user('demo', 'builtin')
    rows = manager.inventory()['apps']
    assert [a['scope'] for a in rows] == ['user', 'builtin']
    assert [a['effective'] for a in rows] == [True, False]
    assert rows[0]['git']['independent'] and not rows[0]['git']['modified']
    assert (Path(rows[0]['dir']) / 'icon.png').read_bytes() == bytes(range(256))
    assert not (manager.roots[-1][0] / 'demo' / '.git').exists()


def test_release_roundtrip_upgrade_and_rollback(manager, tmp_path):
    manager.copy_to_user('demo', 'builtin')
    directory = manager.user_root / 'demo'
    first = download(directory)
    (directory / 'old.txt').write_text('new release')
    manager.tag('demo', '1.0.1')
    second = download(directory)
    target = AppStoreManager([(tmp_path / 'other' / 'apps', 'user')])
    target.install(first)
    target.install(second)
    assert (target.user_root / 'demo' / 'old.txt').exists()
    target.install(first)
    assert not (target.user_root / 'demo' / 'old.txt').exists()
    assert (target.user_root / 'demo' / 'icon.png').read_bytes() == bytes(range(256))
    assert target.inventory()['apps'][0]['install']['package_id'] == 'publisher-demo'


@pytest.mark.parametrize('edit', ['dirty', 'committed'])
def test_replacement_preserves_local_changes(manager, tmp_path, edit):
    manager.copy_to_user('demo', 'builtin')
    directory = manager.user_root / 'demo'
    release = download(directory)
    (directory / 'index.js').write_text('my change')
    if edit == 'committed':
        git(directory, 'add', '-A')
        git(directory, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'user edit')
    with pytest.raises(ValueError, match='local changes'):
        manager.install(release)
    with pytest.raises(ValueError, match='local changes'):
        manager.copy_to_user('demo', 'builtin')
    assert (directory / 'index.js').read_text() == 'my change'


def test_tag_and_bundle_cannot_lie(manager, tmp_path):
    manager.copy_to_user('demo', 'builtin')
    release = download(manager.user_root / 'demo')
    bad = {**release['app_release'], 'commit': 'a' * 40}
    with pytest.raises(ValueError, match='recorded commit'):
        unpack_release(bad, tmp_path / 'bad', '1.0.0')
    with pytest.raises(ValueError, match='checksum'):
        unpack_release({**release['app_release'], 'sha256': 'a' * 64}, tmp_path / 'bad2', '1.0.0')
    with pytest.raises(ValueError, match='already exists'):
        manager.tag('demo', '1.0.0')


def test_remove_user_copy_reveals_official(manager):
    manager.copy_to_user('demo', 'builtin')
    manager.remove('demo')
    row, = manager.inventory()['apps']
    assert row['scope'] == 'builtin' and row['effective']


def test_inventory_includes_headless_and_workspace_overrides(manager):
    directory = app(manager.roots[0][0])
    manager.copy_to_user('demo', 'builtin')
    manifest = json.loads((directory / 'app.json').read_text())
    manifest.update(surface='headless', entry={'backend': 'backend.py'})
    (directory / 'app.json').write_text(json.dumps(manifest))
    rows = manager.inventory()['apps']
    assert [a['effective'] for a in rows] == [True, False, False]
    assert rows[0]['manifest']['surface'] == 'headless'


def test_legacy_traversal_and_manifest_mismatch_do_not_install(manager):
    with pytest.raises(ValueError, match='escapes'):
        manager.install({'type': 'app', 'version': '1.0.0', 'files': {'../outside': 'no'}})
    assert not (manager.user_root / 'demo').exists()


def test_reject_symlink_release(manager):
    manager.copy_to_user('demo', 'builtin')
    directory = manager.user_root / 'demo'
    (directory / 'escape').symlink_to('/etc/passwd')
    manager.tag('demo', '1.0.1')
    with pytest.raises(ValueError, match='Unsupported App file'):
        prepare_release(directory)


def test_unmerged_branch_work_is_not_discarded(manager):
    manager.copy_to_user('demo', 'builtin')
    directory = manager.user_root / 'demo'
    first = download(directory)
    original = git(directory, 'rev-parse', 'HEAD').strip()
    git(directory, 'checkout', '-qb', 'work')
    (directory / 'work.txt').write_text('unfinished branch')
    git(directory, 'add', '-A')
    git(directory, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'work')
    git(directory, 'checkout', '-q', '--detach', original)
    with pytest.raises(ValueError, match='local changes'):
        manager.install(first)
    assert git(directory, 'show', 'work:work.txt') == 'unfinished branch'


def test_official_update_changes_user_copy_only(manager):
    manager.copy_to_user('demo', 'builtin')
    source = app(manager.roots[-1][0], '1.0.1')
    (source / 'new.txt').write_text('official update')
    manager.copy_to_user('demo', 'builtin')
    assert (manager.user_root / 'demo' / 'new.txt').read_text() == 'official update'
    assert manager.find('demo', 'user')['manifest']['version'] == '1.0.1'
    assert len(manager.history('demo', 'user')['commits']) == 2
    assert manager.find('demo', 'user')['git']['tags'] == ['v1.0.1', 'v1.0.0']
    assert not (source / '.git').exists()


def test_dependency_failure_leaves_current_version_intact(manager):
    manager.copy_to_user('demo', 'builtin')
    directory = manager.user_root / 'demo'
    manifest = json.loads((directory / 'app.json').read_text())
    manifest['dependencies'] = {'missing': {'range': '^1.0.0'}}
    (directory / 'app.json').write_text(json.dumps(manifest))
    manager.tag('demo', '1.0.1')
    with pytest.raises(ValueError, match='requires missing'):
        manager.install(download(directory))


def test_history_reports_branches_merge_tags_detached_head_and_pagination(manager, tmp_path):
    manager.copy_to_user('demo', 'builtin')
    directory = manager.user_root / 'demo'
    original = git(directory, 'rev-parse', 'HEAD').strip()
    branch = git(directory, 'branch', '--show-current').strip()
    git(directory, 'checkout', '-qb', 'feature')
    (directory / 'feature.txt').write_text('feature')
    git(directory, 'add', '-A')
    git(directory, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'feature work')
    feature = git(directory, 'rev-parse', 'HEAD').strip()
    git(directory, 'checkout', '-q', branch)
    (directory / 'main.txt').write_text('main')
    manager.tag('demo', '1.0.1')
    main = git(directory, 'rev-parse', 'HEAD').strip()
    git(directory, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'merge', '--no-ff', '-qm', 'merge feature', 'feature')
    manager.tag('demo', '1.0.2')
    release = download(directory)
    history = manager.history('demo', 'user')
    merge = next(c for c in history['commits'] if c['subject'] == 'merge feature')
    assert merge['parents'] == [main, feature]
    assert history['commits'][-1]['id'] == original
    assert any(ref['name'] == 'refs/heads/feature' and ref['commit'] == feature for ref in history['refs'])
    page = manager.history('demo', 'user', 2)
    assert len(page['commits']) == 2 and page['has_more']
    unpack_release(release['app_release'], tmp_path / 'restored', '1.0.2')
    assert git(tmp_path / 'restored', 'tag', '--list').splitlines() == ['v1.0.0', 'v1.0.1', 'v1.0.2']
    assert git(tmp_path / 'restored', 'rev-list', '--all', '--count').strip() == str(len(history['commits']))
    git(directory, 'checkout', '-q', '--detach', 'HEAD')
    (directory / 'detached.txt').write_text('detached work')
    git(directory, 'add', '-A')
    git(directory, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'detached work')
    assert manager.history('demo', 'user')['commits'][0]['subject'] == 'detached work'


def test_headless_release_install_and_icons(manager, tmp_path):
    from pantheon.apps.store_release import release_icon
    directory = manager.roots[-1][0] / 'demo'
    manifest = json.loads((directory / 'app.json').read_text())
    manifest.update(surface='headless', entry={'backend': 'backend.py'}, icon={'path': 'icon.png'})
    (directory / 'app.json').write_text(json.dumps(manifest))
    (directory / 'backend.py').write_text('# service')
    manager.copy_to_user('demo', 'builtin')
    target = AppStoreManager([(tmp_path / 'headless' / 'apps', 'user')])
    target.install(download(manager.user_root / 'demo'))
    assert target.find('demo', 'user')['manifest']['surface'] == 'headless'
    assert release_icon(directory, manifest).startswith('data:image/png;base64,')
    assert release_icon(directory, {**manifest, 'icon': {'path': '../escape.png'}}) is None


@pytest.mark.asyncio
async def test_store_rpc_serves_each_scoped_icon_and_rejects_escape(manager):
    from apps.desktop.toolset import DesktopToolSet
    from unittest.mock import AsyncMock
    directory = manager.roots[-1][0] / 'demo'
    manifest = json.loads((directory / 'app.json').read_text())
    manifest['icon'] = {'path': 'icon.png'}
    (directory / 'app.json').write_text(json.dumps(manifest))
    manager.copy_to_user('demo', 'builtin')
    toolset = DesktopToolSet()
    toolset._app_scope_roots = lambda: manager.roots
    toolset.serve_local_data = AsyncMock(side_effect=lambda path: {'success': True, 'url': 'https://desktop.test' + path})
    result = await toolset.desktop_store_apps()
    assert result['success']
    assert all(row['icon_url'].endswith(row['dir'] + '/icon.png') for row in result['apps'])
    manifest['icon'] = {'path': '../escape.png'}
    (directory / 'app.json').write_text(json.dumps(manifest))
    (directory.parent / 'escape.png').write_bytes(b'not an app asset')
    result = await toolset.desktop_store_apps()
    assert 'icon_url' not in next(row for row in result['apps'] if row['scope'] == 'builtin')
