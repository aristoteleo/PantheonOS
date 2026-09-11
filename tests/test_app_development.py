import json
import sys
from pathlib import Path

import pytest

from apps.desktop.app_development import develop
from apps.desktop.store_manager import AppStoreManager
from apps.desktop.toolset import DesktopToolSet
from pantheon.apps.store_release import git


@pytest.fixture
def manager(tmp_path):
    return AppStoreManager([(tmp_path / 'user/apps', 'user'), (tmp_path / 'official', 'builtin')])


def new_app(manager):
    return develop(manager, 'create', 'demo', '', '')['app']


def test_agent_create_edit_test_commit_tag_and_pin(manager):
    app = new_app(manager)
    repo = app['repository_id']
    def call(action, **kwargs):
        return develop(manager, action, 'demo', 'user', repo, **kwargs)
    assert app['repository']['visibility'] == 'private'
    call('branch', branch='new-feature')
    call('write', files={'index.js': 'export const value = 42', 'test.py': 'from pathlib import Path\nassert "42" in Path("index.js").read_text()'})
    result = call('test', command=[sys.executable, 'test.py'])
    assert result['success'] and result['exit_code'] == 0
    call('commit', message='Add feature')
    with pytest.raises(ValueError, match='Repository changed'):
        call('write', files={'index.js': 'stale'}, expected_commit=app['git']['commit'])
    manager.tag('demo', '0.1.1', 'user', repo)
    pinned = manager.versions.resolve('demo', 'user', 'v0.1.1', repo)
    call('write', files={'index.js': 'unfinished next version'})
    assert '42' in (Path(pinned['dir']) / 'index.js').read_text()
    assert pinned['revision']['repository_id'] == repo
    assert manager.versions.default('demo') is None
    assert 'unfinished' in call('read', path='index.js')['content']


def test_development_branches_share_one_repo_and_immutable_version_tags(manager):
    original = new_app(manager)
    repo_id = original['repository_id']
    manager.tag('demo', '0.1.0', 'user', repo_id)
    one = manager.branches.fork_app('demo', 'user', repository_id=repo_id, name='experiment-a')
    develop(manager, 'write', 'demo', 'user', repo_id, files={'index.js': 'experiment-a'})
    manager.tag('demo', '0.2.0', 'user', repo_id)
    snapshot = manager.versions.resolve('demo', 'user', 'v0.2.0', repo_id)
    two = manager.branches.fork_app('demo', 'user', 'v0.1.0', repo_id, name='experiment-b')
    develop(manager, 'write', 'demo', 'user', repo_id, files={'index.js': 'experiment-b'})
    with pytest.raises(ValueError, match='tag already exists'):
        manager.tag('demo', '0.2.0', 'user', repo_id)
    manager.tag('demo', '0.3.0', 'user', repo_id)
    assert one['repository_id'] == two['repository_id'] == repo_id
    assert len(manager.inventory()['apps']) == 1
    assert (Path(snapshot['dir']) / 'index.js').read_text() == 'experiment-a'
    default = manager.versions.set_default('demo', 'user', 'branch:experiment-b', repo_id)['default']
    assert default['branch'] == 'experiment-b'
    removed = manager.remove('demo', 'user', repo_id)
    restored = manager.branches.restore(removed['archive_id'])
    assert restored['repository_id'] == repo_id
    assert {'experiment-a', 'experiment-b', 'main'} == {b['name'] for b in manager.find('demo', 'user', repo_id)['git']['branches']}


def test_edit_batch_validates_all_paths_and_manifest_before_writes(manager, tmp_path):
    app = new_app(manager)
    original = (Path(app['dir']) / 'index.js').read_text()
    for path in ('../escape', '/tmp/escape', '.git/config'):
        with pytest.raises(ValueError):
            develop(manager, 'write', 'demo', 'user', app['repository_id'], files={'index.js': 'changed', path: 'bad'})
        assert (Path(app['dir']) / 'index.js').read_text() == original
    with pytest.raises(ValueError, match='identity'):
        develop(manager, 'write', 'demo', 'user', app['repository_id'], files={'app.json': json.dumps({**app['manifest'], 'id': 'other'})})


def test_public_fork_records_upstream_and_never_changes_default(manager):
    app = new_app(manager)
    manager.tag('demo', '0.1.0', 'user', app['repository_id'])
    release = manager.prepare('demo', 'user', app['repository_id'])
    public = {'id': app['repository_id'], 'app_id': 'demo', 'name': 'owner-demo', 'visibility': 'public',
              'clone_url': 'https://store.test/api/store/repositories/' + app['repository_id'] + '.git'}
    download = {'repository': public, 'app_release': release['app_release'], 'version': '0.1.0'}
    fork = manager.branches.fork_download(download)
    local = manager.find('demo', fork['scope'], fork['repository_id'])
    assert local['repository']['visibility'] == 'private'
    assert local['repository']['upstream']['commit'] == release['app_release']['commit']
    assert git(Path(local['dir']), 'remote', 'get-url', 'upstream').strip() == public['clone_url']
    prepared = manager.prepare('demo', fork['scope'], fork['repository_id'])
    assert prepared['forked_from'] == {'repository_id': public['id'], 'version': '0.1.0'}
    assert prepared['repository_id'] != public['id']
    assert manager.versions.default('demo') is None


def test_agent_management_tools_are_discoverable():
    for name in ('desktop_store_apps', 'desktop_store_git', 'desktop_store_manage', 'desktop_app_develop', 'desktop_app_store'):
        assert not getattr(getattr(DesktopToolSet, name), '_exclude', False)


@pytest.mark.asyncio
async def test_agent_window_launch_carries_exact_repository_and_commit(manager):
    app = new_app(manager)
    manager.tag('demo', '0.1.0', 'user', app['repository_id'])
    toolset = object.__new__(DesktopToolSet)
    toolset._app_scope_roots = lambda: manager.roots
    calls = []
    async def request(event, args, **kwargs):
        calls.append(args)
        return {'success': True, 'result': {'window_id': 'new'}}
    toolset._desktop_request = request
    revision = {'repository_id': app['repository_id'], 'scope': 'user', 'commit': app['git']['commit']}
    result = await toolset.desktop_open(app='demo', revision=revision)
    assert result['success']
    assert calls[0]['revision']['repository_id'] == app['repository_id']
    assert calls[0]['revision']['commit'] == revision['commit']


@pytest.mark.asyncio
async def test_publish_retries_bind_the_same_repo_and_use_versions_endpoint(manager, monkeypatch):
    import httpx
    from apps.desktop.app_store_client import store_action
    app = new_app(manager)
    repo_id = app['repository_id']
    manager.tag('demo', '0.1.0', 'user', repo_id)
    commit = manager.prepare('demo', 'user', repo_id)['app_release']['commit']
    clone = f'/api/store/repositories/{repo_id}.git'
    metadata = {'id': repo_id, 'name': 'owner-demo', 'app_id': 'demo', 'visibility': 'public', 'clone_url': clone}
    calls, fail_get = [], [True]
    def handler(request):
        calls.append(request)
        if request.method == 'GET':
            if fail_get.pop() if fail_get else False:
                raise httpx.ReadError('Lost response', request=request)
            return httpx.Response(200, json={'repository': metadata})
        return httpx.Response(200, json={'success': True, 'package_id': repo_id})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setenv('PANTHEON_HUB_URL', 'https://store.test')
    monkeypatch.setenv('PANTHEON_STORE_TOKEN', 'fixture-token')
    async def publish(expected):
        return await store_action(manager, 'publish', 'demo', repo_id, 'user', '', 'owner-demo', '', '', expected)
    with pytest.raises(ValueError, match='connection failed'):
        await publish(commit)
    assert not manager.find('demo', 'user', repo_id)['install'].get('published')
    assert (await publish(commit))['repository']['id'] == repo_id
    assert git(Path(app['dir']), 'remote', 'get-url', 'store').strip() == 'https://store.test' + clone
    manager.tag('demo', '0.1.1', 'user', repo_id)
    await publish(manager.prepare('demo', 'user', repo_id)['app_release']['commit'])
    posts = [r for r in calls if r.method == 'POST']
    assert [r.url.path for r in posts] == ['/api/store/packages', '/api/store/packages', f'/api/store/packages/{repo_id}/versions']
    assert all(json.loads(r.content)['repository_id'] == repo_id for r in posts)


def test_workspace_publication_does_not_rebind_same_named_user_repo(manager):
    app = new_app(manager)
    workspace = manager.user_root.parent / 'workspace'
    manager.roots.insert(0, (workspace, 'workspace'))
    root = workspace / 'demo'
    root.mkdir(parents=True)
    (root / 'app.json').write_text(json.dumps(app['manifest']))
    (root / 'index.js').write_text('export default {}')
    manager.versions.ensure()
    work = manager.find('demo', 'workspace')
    public = {'id': work['repository_id'], 'app_id': 'demo', 'visibility': 'public',
              'clone_url': f'https://store.test/api/store/repositories/{work["repository_id"]}.git'}
    manager.bind_publication('demo', 'workspace', work['repository_id'], public)
    assert manager.find('demo', 'workspace')['repository']['publication']['id'] == work['repository_id']
    assert not manager.find('demo', 'user')['repository']['publication']


def test_targeted_repository_lookup_does_not_inspect_unrelated_git_trees(manager, monkeypatch):
    app = new_app(manager)
    fork = manager.branches.fork_app('demo', 'user', name='Experiment')
    original = manager._git_info
    inspected = []
    def inspect(path, record):
        inspected.append(path)
        return original(path, record)
    monkeypatch.setattr(manager, '_git_info', inspect)
    selected = manager.find('demo', fork['scope'], fork['repository_id'])
    assert inspected == [Path(fork['directory'])]
    assert selected['effective']
    inspected.clear()
    assert manager.find('demo', 'user', app['repository_id'])['effective']
    assert inspected == [Path(app['dir'])]


@pytest.mark.asyncio
async def test_agent_contribution_calls_preserve_pins_and_candidate_checkout_is_private(manager, monkeypatch):
    import httpx
    from apps.desktop.app_store_client import store_action
    app = new_app(manager)
    manager.tag('demo', '0.1.0', 'user', app['repository_id'])
    release = manager.prepare('demo', 'user', app['repository_id'])
    candidate = release['app_release']['commit']
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('/candidate'):
            return httpx.Response(200, json=release)
        return httpx.Response(200, json={'id': 'request', 'candidate_commit': candidate})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setenv('PANTHEON_HUB_URL', 'https://store.test')
    monkeypatch.setenv('PANTHEON_STORE_TOKEN', 'fixture-token')
    async def call(action, **kwargs):
        return await store_action(manager, action, 'demo', 'source', 'user', '0.1.0', '', '', '', candidate, **kwargs)
    await call('submit', target_repository_id='upstream', expected_base='base-sha', title='Improve UI', description='Tested')
    assert json.loads(calls[-1].content) == {'source_id': 'source', 'source_version': '0.1.0',
        'target_id': 'upstream', 'expected_source': candidate, 'expected_base': 'base-sha', 'title': 'Improve UI', 'description': 'Tested'}
    await call('prepare_merge', request_id='request')
    assert calls[-1].url.path.endswith('/request/prepare')
    before = [a['repository_id'] for a in manager.inventory()['apps']]
    checked = await call('checkout_review', request_id='request')
    assert checked['commit'] == candidate
    assert git(Path(checked['directory']), 'rev-parse', 'HEAD').strip() == candidate
    assert [a['repository_id'] for a in manager.inventory()['apps']] == before
    assert manager.versions.default('demo') is None
    assert not any(r.url.path.endswith('/merge') for r in calls)
    await call('review', request_id='request', decision='approve', description='Reviewed')
    assert json.loads(calls[-1].content)['expected_commit'] == candidate
    await call('merge', request_id='request')
    assert json.loads(calls[-1].content) == {'expected_commit': candidate}
    assert calls[-1].headers['authorization'] == 'Bearer fixture-token'
    with pytest.raises(ValueError, match='Inspect source and upstream'):
        await call('submit')


def test_store_manifest_exposes_contribution_parameters():
    import inspect
    manifest = json.loads(Path('apps/desktop/app.json').read_text())
    entry = next(tool for tool in manifest['provides']['tools'] if tool['name'] == 'desktop_app_store')
    names = {param['name'] for param in entry['params']}
    expected = {'target_repository_id', 'expected_base', 'request_id', 'title', 'description', 'decision', 'inbox'}
    assert expected <= names
    assert expected <= set(inspect.signature(DesktopToolSet.desktop_app_store).parameters)
    assert 'checkout_review' in entry['description']
