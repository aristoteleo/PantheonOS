"""Store Apps follow their Store releases unless pinned or changed by the user
(docs/apps/app-auto-update.md §4)."""
import asyncio
import json
from pathlib import Path

import pytest

from apps.desktop import app_auto_update as updates
from apps.desktop.store_manager import AppStoreManager
from pantheonos.appkit.store_release import git, prepare_release

PACKAGE = '00000000-0000-4000-8000-0000000000aa'


def commit(root, message):
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=Tester', '-c', 'user.email=test@example.com', 'commit', '-qm', message)
    return git(root, 'rev-parse', 'HEAD').strip()


class Store:
    """A Store repository: its main is the newest release."""

    def __init__(self, root: Path, app_id='demo'):
        self.root, self.app_id = root, app_id
        root.mkdir(parents=True)
        git(root, 'init', '-q', '-b', 'main')
        self.release('1.0.0')

    def release(self, version, manifest=None, extra=None):
        manifest = manifest or {'id': self.app_id, 'name': 'Demo', 'version': version, 'apiVersion': 2,
                                'surface': 'dom', 'entry': {'frontend': 'index.js'}}
        (self.root / 'app.json').write_text(json.dumps(manifest))
        (self.root / 'index.js').write_text(f'export default "{version}"')
        for name, content in (extra or {}).items():
            (self.root / name).write_text(content)
        head = commit(self.root, f'Release {version}')
        git(self.root, 'tag', f'v{version}')
        self.head, self.version = head, version
        return head

    def repository(self):
        return {'id': PACKAGE, 'app_id': self.app_id, 'name': 'demo', 'visibility': 'public',
                'clone_url': str(self.root), 'head': self.head, 'version': self.version, 'tag': f'v{self.version}'}

    def download(self):
        release = prepare_release(self.root)
        return {'type': 'app', 'package_id': PACKAGE, 'name': 'demo', 'version': self.version,
                'app_release': release['app_release'], 'repository': self.repository()}


@pytest.fixture
def world(tmp_path):
    manager = AppStoreManager([(tmp_path / 'user' / 'apps', 'user')])
    store = Store(tmp_path / 'store')
    installed = manager.install(store.download())
    return manager, store, installed


def app_of(manager):
    return manager.find('demo', 'user')


def step(manager, store):
    """One pass for this App: (decision, update result or None)."""
    [(app, head, decision)] = updates.plan(manager, updates.followed(manager), {PACKAGE: store.repository()})
    if decision != 'update':
        return decision, None
    return decision, updates.update(manager, app, head, head['clone_url'])


def launched(manager):
    return manager.versions.resolve('demo')['revision']['commit']


def test_a_new_install_follows_and_a_new_release_reaches_it(world):
    manager, store, _ = world
    app = app_of(manager)
    assert updates.policy_of(manager, app) == 'follow'
    assert updates.describe(manager, app, store.repository())['state'] == 'up_to_date'
    first = launched(manager)
    new = store.release('1.1.0')
    assert updates.describe(manager, app_of(manager), store.repository())['state'] == 'pending'
    decision, result = step(manager, store)
    assert decision == 'update' and result['success']
    assert updates.current_commit(app_of(manager)) == new
    assert launched(manager) == new != first
    settings = app_of(manager)['install']['auto_update']
    assert settings['applied']['commit'] == new and settings['previous']['commit'] == first
    assert [n['kind'] for n in updates.notices(manager)] == ['updated']
    assert step(manager, store) == ('up_to_date', None)


def test_reading_an_existing_install_rewrites_nothing(world):
    manager, store, _ = world
    path = manager._record_path(app_of(manager)['record_key'])
    before = path.read_text()
    updates.describe(manager, app_of(manager), store.repository())
    updates.plan(manager, updates.followed(manager), {PACKAGE: store.repository()})
    assert path.read_text() == before


def test_an_explicit_version_default_is_pinned_and_stays(world):
    manager, store, _ = world
    app = app_of(manager)
    first = updates.current_commit(app)
    # An earlier "Set default" of a version, before policies existed.
    path = manager.versions._default_path('demo')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'scope': 'user', 'repository_id': app['repository_id'], 'commit': first, 'version': '1.0.0'}))
    assert updates.policy_of(manager, app) == 'pinned'
    store.release('1.1.0')
    assert step(manager, store) == ('pinned', None)
    assert updates.current_commit(app_of(manager)) == first and launched(manager) == first
    view = updates.describe(manager, app_of(manager), store.repository())
    assert view['state'] == 'pinned' and view['available']['version'] == '1.1.0' and view['pinned']['commit'] == first


def test_pin_and_follow_through_set_default_and_set_policy(world):
    manager, store, _ = world
    app = app_of(manager)
    first = updates.current_commit(app)
    updates.set_policy(manager, 'demo', 'user', app['repository_id'], 'pinned')
    assert updates.policy_of(manager, app_of(manager)) == 'pinned'
    new = store.release('1.1.0')
    assert step(manager, store) == ('pinned', None)
    assert launched(manager) == first
    updates.set_policy(manager, 'demo', 'user', app['repository_id'], 'follow')
    assert step(manager, store)[0] == 'update'
    assert launched(manager) == new
    # The Store's "Set default" of a version pins; of branch:official follows.
    from apps.desktop.toolset import _default_and_policy
    _default_and_policy(manager, 'demo', 'user', 'v1.0.0', app['repository_id'])
    assert updates.policy_of(manager, app_of(manager)) == 'pinned' and launched(manager) == first
    _default_and_policy(manager, 'demo', 'user', 'branch:official', app['repository_id'])
    assert updates.policy_of(manager, app_of(manager)) == 'follow' and launched(manager) == new


@pytest.mark.parametrize('change', ['branch', 'dirty'])
def test_a_modified_install_is_not_touched(world, change):
    manager, store, _ = world
    app = app_of(manager)
    root = Path(app['dir'])
    first = updates.current_commit(app)
    if change == 'branch':
        git(root, 'switch', '-q', '-c', 'my-work')
        (root / 'mine.txt').write_text('mine')
        commit(root, 'My change')
    else:
        (root / 'index.js').write_text('edited')
    store.release('1.1.0')
    assert step(manager, store) == ('modified', None)
    assert updates.current_commit(app_of(manager)) == first
    view = updates.describe(manager, app_of(manager), store.repository())
    assert view['state'] == 'modified' and view['modified'] and view['available']['version'] == '1.1.0'
    if change == 'dirty':
        assert (root / 'index.js').read_text() == 'edited'


def test_a_release_that_cannot_launch_is_rolled_back_and_not_retried(world):
    manager, store, _ = world
    first = updates.current_commit(app_of(manager))
    # A release whose window is not in it: the Store accepts it, it cannot launch.
    bad = store.release('1.1.0', manifest={'id': 'demo', 'name': 'Demo', 'version': '1.1.0', 'apiVersion': 2,
                                           'surface': 'dom', 'entry': {'frontend': 'missing.js'}})
    decision, result = step(manager, store)
    assert decision == 'update' and result['rolled_back']
    assert updates.current_commit(app_of(manager)) == first and launched(manager) == first
    settings = app_of(manager)['install']['auto_update']
    assert bad in settings['failed']
    assert updates.notices(manager)[-1]['kind'] == 'failed'
    assert step(manager, store) == ('failed', None)
    assert updates.describe(manager, app_of(manager), store.repository())['state'] == 'failed'
    # A later release is tried as usual.
    good = store.release('1.2.0')
    assert step(manager, store)[0] == 'update' and launched(manager) == good


def test_notices_are_acknowledged_once(world):
    manager, store, _ = world
    store.release('1.1.0')
    step(manager, store)
    [notice] = [n for n in updates.notices(manager) if not n['acknowledged']]
    assert updates.acknowledge(manager, [notice['id']]) == 1
    assert not [n for n in updates.notices(manager) if not n['acknowledged']]


def test_run_pass_uses_store_heads_and_broadcasts(world, monkeypatch):
    manager, store, _ = world
    new = store.release('1.1.0')

    async def heads(binding, ids):
        assert ids == [PACKAGE]
        return {PACKAGE: {**store.repository(), 'clone_url': store.repository()['clone_url']}}, ''
    monkeypatch.setattr(updates, 'store_heads', heads)
    monkeypatch.setattr(updates, 'urljoin', lambda base, url: url)
    sent = []

    class Desktop:
        _store_binding = None
        _apps_supervisor = None

        async def desktop_broadcast(self, topic, payload):
            sent.append((topic, payload))

        def _fleet_resolver(self):
            return None
    result = asyncio.run(updates.run_pass(Desktop(), manager))
    assert result['outcomes'] == {f"demo@{app_of(manager)['repository_id']}": 'updated'}
    assert launched(manager) == new
    assert sent and sent[0][0] == 'store.updates' and sent[0][1]['notices'][0]['version'] == '1.1.0'


class FakeProcess:
    def __init__(self, pending=False):
        self.pending = {'call': object()} if pending else {}
        self.proc = type('P', (), {'returncode': None})()


class FakeSupervisor:
    def __init__(self, fail=False):
        self.procs, self.calls, self.stopped, self.fail = {}, [], [], fail

    async def stop(self, key):
        self.stopped.append(key)
        self.procs.pop(key, None)

    async def call(self, app_id, method, args, timeout, *, pinned=None):
        commit = pinned['revision']['commit']
        self.calls.append(commit)
        if self.fail and len(self.calls) == 1:
            raise RuntimeError('backend exited')
        self.procs[f"{app_id}@{pinned['repository_id']}:{commit}"] = FakeProcess()
        return {}


def backend_world(tmp_path):
    manager = AppStoreManager([(tmp_path / 'user' / 'apps', 'user')])
    store = Store(tmp_path / 'store')
    manifest = lambda v: {'id': 'demo', 'name': 'Demo', 'version': v, 'apiVersion': 2, 'surface': 'dom',
                          'entry': {'frontend': 'index.js', 'backend': 'backend.py'}}
    store.release('1.0.1', manifest=manifest('1.0.1'), extra={'backend.py': 'print(1)'})
    manager.install(store.download())
    return manager, store, manifest


class BackendDesktop:
    def __init__(self, supervisor):
        self._apps_supervisor = supervisor

    def _fleet_resolver(self):
        return None


@pytest.mark.parametrize('busy', [False, True])
def test_an_idle_backend_moves_to_the_new_version_and_a_busy_one_waits(tmp_path, busy):
    manager, store, manifest = backend_world(tmp_path)
    app = app_of(manager)
    old = updates.current_commit(app)
    supervisor = FakeSupervisor()
    supervisor.procs[f"demo@{app['repository_id']}:{old}"] = FakeProcess(pending=busy)
    new = store.release('1.1.0', manifest=manifest('1.1.0'))
    step(manager, store)
    moved = asyncio.run(updates.restart_backends(BackendDesktop(supervisor), manager, app_of(manager)))
    if busy:
        assert moved == [] and supervisor.stopped == [] and supervisor.calls == []
    else:
        assert moved == ['desktop:1.1.0'] and supervisor.calls == [new]
        assert supervisor.stopped == [f"demo@{app['repository_id']}:{old}"]


def test_a_backend_that_does_not_start_reverts_to_the_previous_version(tmp_path):
    manager, store, manifest = backend_world(tmp_path)
    app = app_of(manager)
    old = updates.current_commit(app)
    supervisor = FakeSupervisor(fail=True)
    supervisor.procs[f"demo@{app['repository_id']}:{old}"] = FakeProcess()
    new = store.release('1.1.0', manifest=manifest('1.1.0'))
    step(manager, store)
    moved = asyncio.run(updates.restart_backends(BackendDesktop(supervisor), manager, app_of(manager)))
    assert moved == ['reverted:1.0.1']
    assert supervisor.calls == [new, old]  # the old version runs again
    assert updates.current_commit(app_of(manager)) == old and launched(manager) == old
    assert new in app_of(manager)['install']['auto_update']['failed']
    assert step(manager, store) == ('failed', None)
