import json
from pathlib import Path

import pytest

from apps.desktop.store_manager import AppStoreManager
from apps.desktop.app_supervisor import AppSupervisor
from pantheon.apps.store_release import git


def make_app(root, version='1.0.0', **entry):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'app.json').write_text(json.dumps({'id': 'counter', 'name': 'Counter', 'version': version,
        'apiVersion': 2, 'surface': 'dom', 'entry': {'frontend': 'index.js', **entry}}))
    (root / 'index.js').write_text(f'export const version = "{version}"')
    (root / 'backend.py').write_text(f'def register(ctx):\n    @ctx.method\n    def version():\n        return "{version}"\n')


@pytest.fixture
def manager(tmp_path):
    roots = [(tmp_path / 'workspace/apps', 'workspace'), (tmp_path / 'user/apps', 'user'), (tmp_path / 'official', 'builtin')]
    make_app(roots[0][0] / 'counter', backend='backend.py')
    make_app(roots[2][0] / 'counter')
    return AppStoreManager(roots)


def test_migration_is_real_idempotent_and_keeps_official_source(manager):
    assert manager.versions.ensure()['warnings'] == []
    first = manager.history('counter', 'workspace')['head']
    assert manager.versions.ensure()['warnings'] == []
    assert manager.history('counter', 'workspace')['head'] == first
    assert not (manager.roots[-1][0] / 'counter/.git').exists()
    for app in manager.inventory()['apps']:
        assert app['git']['independent']
        assert len(manager.history('counter', app['scope'])['commits']) == 1


def test_defaults_snapshots_and_dirty_work_survive(manager):
    manager.versions.ensure()
    first = manager.versions.resolve('counter', 'workspace', 'v1.0.0')
    source = manager.roots[0][0] / 'counter'
    make_app(source, '1.1.0', backend='backend.py')
    manager.tag('counter', '1.1.0', 'workspace')
    second = manager.versions.resolve('counter', 'workspace', 'v1.1.0')
    (source / 'index.js').write_text('unfinished edit')
    manager.versions.set_default('counter', 'workspace', 'v1.0.0')
    fresh = AppStoreManager(manager.roots)
    assert fresh.versions.resolve('counter')['revision'] == first['revision']
    fresh.versions.set_default('counter', 'workspace', second['revision']['commit'])
    assert fresh.versions.resolve('counter')['revision'] == second['revision']
    assert (source / 'index.js').read_text() == 'unfinished edit'
    assert '1.0.0' in (Path(first['dir']) / 'index.js').read_text()
    assert fresh.versions.resolve('counter', 'workspace', first['revision']['commit'])['dir'] == first['dir']
    with pytest.raises(ValueError, match='already exists'):
        manager.tag('counter', '1.0.0', 'workspace')


def test_official_upgrade_extends_graph(manager):
    manager.versions.ensure()
    original = manager.history('counter', 'builtin')['head']
    make_app(manager.roots[-1][0] / 'counter', '1.2.0')
    assert not manager.versions.ensure()['warnings']
    history = manager.history('counter', 'builtin')
    assert len(history['commits']) == 2
    assert history['commits'][0]['parents'] == [original]
    assert {v['tag'] for v in manager.versions.versions('counter', 'builtin')['versions']} == {'v1.0.0', 'v1.2.0'}


def test_migration_checks_manifests_without_reading_git_status(manager, monkeypatch):
    manager.versions.ensure()
    def unexpected(*args):
        pytest.fail('Migration must not rescan working trees or resolve launch defaults')
    monkeypatch.setattr(manager, '_git_info', unexpected)
    monkeypatch.setattr(manager.versions, 'launch_default', unexpected)
    # Newly installed Apps and official upgrades still migrate on subsequent reads.
    make_app(manager.roots[-1][0] / 'counter', '1.2.0')
    assert not manager.versions.ensure()['warnings']
    repo = manager.versions.repository(manager.roots[-1][0] / 'counter', 'builtin', 'counter')
    assert git(repo, 'tag', '--list', 'v1.2.0').strip() == 'v1.2.0'


@pytest.mark.parametrize('scope,revision', [('../escape', 'v1.0.0'), ('workspace', '--all'), ('workspace', 'HEAD~1')])
def test_invalid_scope_and_revisions_refused(manager, scope, revision):
    manager.versions.ensure()
    with pytest.raises(ValueError):
        manager.versions.resolve('counter', scope, revision)


def test_runtime_bound_apps_have_history_but_do_not_fake_switches(manager):
    source = manager.roots[0][0] / 'counter'
    make_app(source, backend='example.service:Service')
    manager.versions.ensure()
    assert manager.versions.versions('counter', 'workspace')['restriction']
    assert manager.history('counter', 'workspace')['commits']
    with pytest.raises(ValueError, match='node-managed'):
        manager.versions.set_default('counter', 'workspace', 'v1.0.0')


def test_snapshot_rejects_symlinks(manager):
    source = manager.roots[0][0] / 'counter'
    (source / 'outside').symlink_to('/etc/passwd')
    manager.versions.ensure()
    with pytest.raises(ValueError, match='Unsupported App file'):
        manager.versions.resolve('counter', 'workspace', 'v1.0.0')


@pytest.mark.asyncio
async def test_two_backends_remain_pinned_across_default_change_and_rescan(manager):
    manager.versions.ensure()
    first = manager.versions.resolve('counter', 'workspace', 'v1.0.0')
    make_app(manager.roots[0][0] / 'counter', '2.0.0', backend='backend.py')
    manager.tag('counter', '2.0.0', 'workspace')
    second = manager.versions.resolve('counter', 'workspace', 'v2.0.0')
    async def serve(path):
        return path
    supervisor = AppSupervisor(manager.roots[0][0].parent, manager.roots, serve)
    try:
        assert await supervisor.call('counter', 'version', {}, 10, pinned=first) == '1.0.0'
        assert await supervisor.call('counter', 'version', {}, 10, pinned=second) == '2.0.0'
        assert len(supervisor.procs) == 2
        pids = {k: v.proc.pid for k, v in supervisor.procs.items()}
        manager.versions.set_default('counter', 'workspace', 'v2.0.0')
        supervisor.scan()
        assert await supervisor.call('counter', 'version', {}, 10, pinned=first) == '1.0.0'
        assert {k: v.proc.pid for k, v in supervisor.procs.items()} == pids
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_two_branch_commits_have_independent_instances_and_stop(manager):
    manager.versions.ensure()
    snapshots = []
    for name in ('A', 'B'):
        fork = manager.branches.fork_app('counter', 'workspace', name=name)
        root = Path(fork['directory'])
        (root / 'branch-note.txt').write_text(name)
        git(root, 'add', 'branch-note.txt')
        git(root, '-c', 'user.name=Tester', '-c', 'user.email=test@example.com', 'commit', '-qm', name)
        snapshots.append(manager.versions.resolve('counter', fork['scope'], 'branch:' + name, fork['repository_id']))
    async def serve(path):
        return path
    supervisor = AppSupervisor(manager.roots[0][0].parent, manager.roots, serve)
    try:
        for pinned in snapshots:
            assert await supervisor.call('counter', 'version', {}, 10, pinned=pinned) == '1.0.0'
        instances = supervisor.instances()
        assert len(instances) == 2
        assert len({i['repository_id'] for i in instances}) == 1
        assert len({i['pid'] for i in instances}) == 2
        assert (await supervisor.stop(instances[0]['instance_id']))['stopped']
        assert [i['instance_id'] for i in supervisor.instances()] == [instances[1]['instance_id']]
        assert await supervisor.call('counter', 'version', {}, 10, pinned=snapshots[1]) == '1.0.0'
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_window_intent_pins_default_and_preserves_it_on_reload(manager, tmp_path):
    from apps.desktop.toolset import DesktopToolSet
    from apps.desktop.desktop_session import DesktopSessionStore
    from unittest.mock import AsyncMock
    manager.versions.ensure()
    session = DesktopSessionStore(work_dir=tmp_path / 'session')
    session.load()
    desktop = object.__new__(DesktopToolSet)
    desktop._desktop = lambda: session
    desktop._app_scope_roots = lambda: manager.roots
    desktop._publish_desktop = AsyncMock()
    opened = await desktop.desktop_intent('open', {'app_id': 'pkg:counter'})
    assert opened['success']
    wid = opened['window_id']
    first = session.session.windows[wid]['args']['appRevision']
    make_app(manager.roots[0][0] / 'counter', '1.1.0')
    manager.tag('counter', '1.1.0', 'workspace')
    manager.versions.set_default('counter', 'workspace', 'v1.1.0')
    new = await desktop.desktop_intent('open', {'app_id': 'pkg:counter'})
    assert session.session.windows[new['window_id']]['args']['appRevision']['commit'] != first['commit']
    restored = DesktopSessionStore(work_dir=tmp_path / 'session')
    restored.load()
    assert restored.session.windows[wid]['args']['appRevision'] == first


@pytest.mark.asyncio
async def test_snapshot_frontend_is_served_outside_workspace(manager):
    import httpx
    from apps.desktop.toolset import DesktopToolSet
    from apps.desktop.data_server import LiveViewDataServer
    manager.versions.ensure()
    resolved = manager.versions.resolve('counter', 'workspace', 'v1.0.0')
    desktop = object.__new__(DesktopToolSet)
    desktop._app_scope_roots = lambda: manager.roots
    server = LiveViewDataServer()
    await server.ensure_started(desktop._data_roots())
    server.set_tunnel_base(server._base_url)
    url = server.url_for(Path(resolved['dir']) / 'index.js')
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    assert response.status_code == 200
    assert '1.0.0' in response.text


def test_existing_fork_becomes_automatic_default_and_follows_untagged_commits(manager):
    manager.versions.ensure()
    branch = manager.branches.fork_app('counter', 'builtin')
    fresh = AppStoreManager(manager.roots)
    first = fresh.versions.resolve('counter')
    assert first['repository_id'] == branch['repository_id']
    assert fresh.versions.default('counter') is None  # No fabricated explicit preference.
    root = Path(branch['directory'])
    (root / 'index.js').write_text('new committed code')
    git(root, 'add', 'index.js')
    git(root, '-c', 'user.name=Test', '-c', 'user.email=test@local', 'commit', '-qm', 'Improve UI')
    second = fresh.versions.resolve('counter')
    assert second['revision']['commit'] != first['revision']['commit']
    (root / 'index.js').write_text('unfinished work')
    assert fresh.versions.resolve('counter')['revision'] == second['revision']
    assert (Path(second['dir']) / 'index.js').read_text() == 'new committed code'
    assert '1.0.0' in (Path(first['dir']) / 'index.js').read_text()
    versions = fresh.versions.versions('counter', 'fork', branch['repository_id'])
    assert versions['versions'][0]['commit'] == second['revision']['commit']
    assert versions['versions'][0]['tag'] == ''
    assert versions['default']['mode'] == 'latest'
    assert all(app['default']['repository_id'] == branch['repository_id']
               for app in fresh.inventory()['apps'])


def test_explicit_official_and_pinned_defaults_win_over_personal_forks(manager):
    manager.versions.ensure()
    first = manager.branches.fork_app('counter', 'builtin')
    official = manager.versions.set_default('counter', 'builtin', 'latest')['default']
    manager.branches.fork_app('counter', 'builtin', name='Another experiment')
    fresh = AppStoreManager(manager.roots)
    assert fresh.versions.resolve('counter')['revision']['repository_id'] == official['repository_id']
    fresh.versions.set_default('counter', 'fork', 'v1.0.0', first['repository_id'])
    pinned = fresh.versions.resolve('counter')['revision']
    fresh.tag('counter', '1.1.0', 'fork', first['repository_id'])
    assert fresh.versions.resolve('counter')['revision'] == pinned
    follow = fresh.versions.set_default('counter', 'fork', 'latest', first['repository_id'])
    assert follow['default']['mode'] == 'latest'
    assert fresh.versions.resolve('counter')['revision']['version'] == '1.1.0'
    fresh.tag('counter', '1.2.0', 'fork', first['repository_id'])
    assert AppStoreManager(manager.roots).versions.resolve('counter')['revision']['version'] == '1.2.0'


def test_automatic_selection_is_deterministic_and_excludes_runtime_bound_forks(manager):
    manager.versions.ensure()
    first = manager.branches.fork_app('counter', 'builtin')
    second = manager.branches.fork_app('counter', 'builtin', name='Another experiment')
    assert manager.versions.resolve('counter')['repository_id'] == second['repository_id']
    manager.remove('counter', 'fork', second['repository_id'])
    assert manager.versions.resolve('counter')['repository_id'] == first['repository_id']
    manager.remove('counter', 'fork', first['repository_id'])
    make_app(manager.roots[-1][0] / 'counter', '2.0.0', backend='example.service:Service')
    manager.versions.ensure()
    manager.branches.fork_app('counter', 'builtin')
    assert manager.versions.launch_default('counter') is None


@pytest.mark.asyncio
async def test_desktop_new_windows_follow_fork_until_explicit_official_switch(manager, tmp_path):
    from apps.desktop.toolset import DesktopToolSet
    from apps.desktop.desktop_session import DesktopSessionStore
    from unittest.mock import AsyncMock
    manager.versions.ensure()
    session = DesktopSessionStore(work_dir=tmp_path / 'session')
    session.load()
    desktop = object.__new__(DesktopToolSet)
    desktop._desktop = lambda: session
    desktop._app_scope_roots = lambda: manager.roots
    desktop._publish_desktop = AsyncMock()
    async def opened_revision():
        result = await desktop.desktop_intent('open', {'app_id': 'pkg:counter'})
        assert result['success']
        return session.session.windows[result['window_id']]['args']['appRevision']
    original = await opened_revision()
    branch = manager.branches.fork_app('counter', 'builtin')
    manager.tag('counter', '1.1.0', 'fork', branch['repository_id'])
    forked = await opened_revision()
    assert forked['repository_id'] == branch['repository_id']
    assert forked['version'] == '1.1.0'
    manager.versions.set_default('counter', 'builtin', 'latest')
    assert (await opened_revision())['scope'] == 'builtin'
    revisions = [w['args']['appRevision'] for w in session.session.windows.values()]
    assert original in revisions and forked in revisions
