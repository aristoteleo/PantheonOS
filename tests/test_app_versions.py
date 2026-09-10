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
