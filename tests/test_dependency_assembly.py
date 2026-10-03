import copy
import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.dependency_assembly import AssemblyError, DependencyStarter, compile_assembly, _compatible, _methods


def test_raw_catalog_interface_uses_the_schema_default_version():
    manifest = json.loads((Path(__file__).resolve().parents[1] / 'apps/shell/app.json').read_text())
    assert 'version' not in manifest['provides']['interfaces'][0]
    requested = {'run_command': {'arguments': ['command', 'timeout'], 'bound': {'shell_id': 'session-a'}}}
    assert _methods({'uses': ['shell@1']}, manifest, requested) == requested
    with pytest.raises(AssemblyError):
        _methods({'uses': ['shell@2']}, manifest, requested)
    for invalid in (None, True, 0, '1'):
        manifest['provides']['interfaces'][0]['version'] = invalid
        with pytest.raises(AssemblyError):
            _methods({'uses': ['shell@1']}, manifest, requested)


def fixture():
    consumer = dict(node_id='consumer', instance_id='a'*32, revision='b'*64, generation=1)
    provider = dict(node_id='provider', instance_id='c'*32, revision='d'*64, generation=3, component='backend', port='http')
    binding = {'files': dict(app_id='files', component='backend', provider=provider,
                            methods={'read': {'arguments': ['path'], 'bound': {'workspace': 'project-a'}}})}
    components = {'backend': {'values': {}}}
    cm = {'apiVersion': 2, 'id': 'consumer-app', 'version': '1.0.0',
          'dependencies': {'files': {'range': '^1.0.0', 'uses': ['fs@1']}}}
    pm = {'apiVersion': 2, 'id': 'files', 'version': '1.2.0',
          'provides': {'interfaces': [{'name': 'fs', 'version': 1, 'tools': ['read']}],
                       'tools': [{'name': 'read', 'params': [{'name': 'path'}, {'name': 'workspace'}]}]}}
    definition = {'components': [{'name': 'backend', 'configuration': {'credentials': {'files': {'required': True}}}}]}
    manifests = {consumer['revision']: {'manifest': cm, 'definition': definition},
                 provider['revision']: {'manifest': pm, 'definition': {}}}
    lifecycle = AsyncMock()
    lifecycle.status.return_value = {'owner': 'owner', 'node_id': 'consumer', 'dependency_config_protocol': 1,
        'instances': {consumer['instance_id']: {'digest': consumer['revision'], 'generation': 1,
            'state': 'prepared', 'scope': 'app', 'start_preparation_id': 'prepare-one'}}, 'operations': {}}
    lifecycle.manifest.side_effect = lambda node, revision: copy.deepcopy(manifests[revision])
    lifecycle.configure.return_value = {'ok': True}
    lifecycle.submit.return_value = {'state': 'queued'}
    authority = AsyncMock()
    def issue(request):
        provider = request['provider']
        token = 'e'*64
        prefix = hashlib.sha256(f"{provider['instance_id']}:backend:http:{provider['generation']}".encode()).hexdigest()[:32]
        return {'endpoint': f'https://{prefix}.apps.test/rpc', 'access_token': token,
                'grant_id': hashlib.sha256(token.encode()).hexdigest(), 'expires': int(time.time())+899,
                'consumer': {**request['consumer'], 'fleet_id': 'owner'},
                'provider': {**provider, 'fleet_id': 'owner'}}
    authority.issue.side_effect = issue
    recipe = dict(consumer=consumer, bindings=binding, components=components,
                  operation_id='start-one', preparation_id='prepare-one')
    return lifecycle, authority, recipe, manifests


@pytest.mark.asyncio
async def test_actual_assembly_retains_grant_after_configure_lost_ack(tmp_path):
    lifecycle, authority, recipe, manifests = fixture()
    lifecycle.configure.side_effect = [TimeoutError('lost reply'), {'ok': True}]
    starter = DependencyStarter(lifecycle, tmp_path/'private', authority)
    with pytest.raises(TimeoutError):
        await starter.start(**recipe)
    assert authority.issue.await_count == 1
    first = copy.deepcopy((lifecycle.configure.await_args.args, lifecycle.configure.await_args.kwargs))
    # New coordinator object, same durable attempt, same exact private config.
    result = await DependencyStarter(lifecycle, tmp_path/'private', authority).start(**recipe)
    assert result == {'operation': {'state': 'queued'}, 'dependencies': 1}
    assert authority.issue.await_count == 1 and (lifecycle.configure.await_args.args, lifecycle.configure.await_args.kwargs) == first
    grant = lifecycle.configure.await_args.kwargs['components']['backend']['dependencies']['files']
    assert grant['consumer']['generation'] == 2
    assert 'access_token' not in json.dumps(result)
    assert lifecycle.submit.await_args.kwargs['operation_id'] == 'start-one'
    assert recipe['components'] == {'backend': {'values': {}}}
    saved = json.loads(next((tmp_path/'private').glob('*.json')).read_text())
    assert saved['grants'] == {} and 'access_token' not in json.dumps(saved)
    for path in (tmp_path/'private').glob('*.json'):
        assert path.stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_accepted_start_is_observed_without_reissuing_grants(tmp_path):
    lifecycle, authority, recipe, _ = fixture()
    lifecycle.submit.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await DependencyStarter(lifecycle, tmp_path/'private', authority).start(**recipe)
    request = dict(protocol=1, action='start', operation_id='start-one', digest='b'*64,
                   scope='app', generation=1, start_preparation_id='prepare-one')
    operation = {'request': request, 'state': 'succeeded'}
    lifecycle.status.return_value['operations']['start-one'] = operation
    lifecycle.status.return_value['instances'][recipe['consumer']['instance_id']]['state'] = 'ready'
    result = await DependencyStarter(lifecycle, tmp_path/'private', authority).start(**recipe)
    assert result['operation'] == operation
    assert authority.issue.await_count == lifecycle.configure.await_count == lifecycle.submit.await_count == 1


@pytest.mark.asyncio
async def test_unacknowledged_start_uses_same_operation_without_reconfigure(tmp_path):
    lifecycle, authority, recipe, _ = fixture()
    lifecycle.submit.side_effect = [TimeoutError(), {'state': 'queued'}]
    starter = DependencyStarter(lifecycle, tmp_path/'private', authority)
    with pytest.raises(TimeoutError):
        await starter.start(**recipe)
    await starter.start(**recipe)
    assert lifecycle.submit.await_args_list[0] == lifecycle.submit.await_args_list[1]
    assert authority.issue.await_count == lifecycle.configure.await_count == 1


@pytest.mark.asyncio
async def test_cannot_change_recipe_or_reuse_another_owner(tmp_path):
    lifecycle, authority, recipe, _ = fixture()
    starter = DependencyStarter(lifecycle, tmp_path/'private', authority)
    await starter.start(**recipe)
    changed = copy.deepcopy(recipe)
    changed['bindings']['files']['methods']['read']['bound']['workspace'] = 'project-b'
    with pytest.raises(AssemblyError, match='different dependency recipe'):
        await starter.start(**changed)
    lifecycle.status.return_value['owner'] = 'other-owner'
    with pytest.raises(AssemblyError, match='another Fleet owner'):
        await starter.start(**recipe)
    assert authority.issue.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['generation', 'preparation', 'capability', 'version', 'interface', 'method', 'argument', 'missing-argument', 'duplicate-tool', 'alias'])
async def test_invalid_binding_fails_before_grant_or_configuration(tmp_path, change):
    lifecycle, authority, recipe, manifests = fixture()
    p = manifests['d'*64]['manifest']
    selected = recipe['bindings']['files']
    state = lifecycle.status.return_value
    if change == 'generation': recipe['consumer']['generation'] = 2
    if change == 'preparation': recipe['preparation_id'] = 'other'
    if change == 'capability': state['dependency_config_protocol'] = 0
    if change == 'version': p['version'] = '2.0.0'
    if change == 'interface': p['provides']['interfaces'][0]['version'] = 2
    if change == 'method': selected['methods'] = {'delete': {'arguments': [], 'bound': {}}}
    if change == 'argument': selected['methods']['read']['arguments'].append('arbitrary')
    if change == 'missing-argument': selected['methods']['read']['bound'] = {}
    if change == 'duplicate-tool': p['provides']['tools'] *= 2
    if change == 'alias': recipe['bindings']['other'] = recipe['bindings'].pop('files')
    with pytest.raises(AssemblyError):
        await DependencyStarter(lifecycle, tmp_path/'private', authority).start(**recipe)
    authority.issue.assert_not_awaited()
    lifecycle.configure.assert_not_awaited()
    lifecycle.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_persisted_grant_requires_new_preparation(tmp_path):
    lifecycle, authority, recipe, _ = fixture()
    lifecycle.configure.side_effect = TimeoutError()
    starter = DependencyStarter(lifecycle, tmp_path/'private', authority)
    with pytest.raises(TimeoutError): await starter.start(**recipe)
    path = next((tmp_path/'private').glob('*.json'))
    record = json.loads(path.read_text())
    record['grants']['files']['expires'] = int(time.time())-1
    path.write_text(json.dumps(record))
    with pytest.raises(AssemblyError, match='expired'):
        await starter.start(**recipe)
    assert authority.issue.await_count == lifecycle.configure.await_count == 1


@pytest.mark.asyncio
async def test_concurrent_same_attempt_never_issues_twice(tmp_path):
    import asyncio
    lifecycle, authority, recipe, _ = fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    original = authority.issue.side_effect
    async def delayed(body):
        entered.set()
        await release.wait()
        return original(body)
    authority.issue.side_effect = delayed
    starter = DependencyStarter(lifecycle, tmp_path/'private', authority)
    task = asyncio.create_task(starter.start(**recipe))
    await entered.wait()
    try:
        with pytest.raises(TimeoutError):
            await starter.start(**recipe)
    finally:
        release.set()
        await task
    assert authority.issue.await_count == 1


@pytest.mark.asyncio
async def test_insecure_journal_and_symlink_rejected(tmp_path):
    lifecycle, authority, recipe, _ = fixture()
    root=tmp_path/'private';root.mkdir(mode=0o755)
    with pytest.raises(AssemblyError, match='private'):
        await DependencyStarter(lifecycle, root, authority).start(**recipe)
    root.chmod(0o700)
    linked=tmp_path/'linked';linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(AssemblyError, match='private'):
        await DependencyStarter(lifecycle, linked, authority).start(**recipe)
    authority.issue.assert_not_awaited()


def test_version_ranges_fail_closed_and_zero_major_caret_is_precise():
    assert _compatible('1.2.3', '^1.0.0')
    assert not _compatible('2.0.0', '^1.0.0')
    assert not _compatible('0.2.0', '^0.1.0')
    assert not _compatible('0.0.2', '^0.0.1')
    assert _compatible('1.2.9', '~1.2.0')
    assert not _compatible('1.3.0', '~1.2.0')
    for constraint in ['invalid', '>=1.0.0 <2.0.0', '^01.0.0', '1.0.0-beta']:
        with pytest.raises(AssemblyError): _compatible('1.0.0', constraint)


@pytest.mark.asyncio
async def test_cancellation_does_not_unlock_before_checkpoint_finishes(tmp_path):
    import asyncio
    import threading
    lifecycle, authority, recipe, _ = fixture()
    starter = DependencyStarter(lifecycle, tmp_path/'private', authority)
    entered, release = threading.Event(), threading.Event()
    original = starter._write
    def slow_write(path, record):
        entered.set()
        assert release.wait(5)
        original(path, record)
    starter._write = slow_write
    task = asyncio.create_task(starter.start(**recipe))
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    try:
        assert not task.done()
        with pytest.raises(TimeoutError):
            await DependencyStarter(lifecycle, tmp_path/'private', authority).start(**recipe)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    record = json.loads(next((tmp_path/'private').glob('*.json')).read_text())
    assert record['recipe'] == recipe
    authority.issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_platform_api_uses_private_nonproject_storage_and_redacts_errors(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from pantheon.apps import resolver
    from pantheon.platform.service import PlatformService
    lifecycle, authority, recipe, _ = fixture()
    monkeypatch.setattr(resolver, 'get_shared_resolver', lambda: SimpleNamespace(_seed='owner-seed', _workdir='/user-project'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    observed = []
    async def fail(self, **kwargs):
        observed.append(self.root)
        raise RuntimeError('secret-key-in-upstream-exception')
    monkeypatch.setattr(DependencyStarter, 'start', fail)
    service = PlatformService()
    result = await service.fleet_app_start_dependencies(**recipe)
    assert not result['success'] and 'secret-key' not in json.dumps(result)
    assert observed[0].is_relative_to(tmp_path/'.pantheon/platform-private')
    assert 'fleet_app_start_dependencies' in service.functions


@pytest.mark.asyncio
async def test_manifest_request_keeps_exact_node_and_rejects_wrong_revision(monkeypatch):
    from pantheon.apps.lifecycle import FleetLifecycle
    lifecycle = FleetLifecycle(None)
    wire = AsyncMock(return_value={'protocol': 1, 'revision': 'a'*64,
        'manifest': {'id': 'files', 'version': '1.0.0'},
        'definition': {'app_id': 'files', 'version': '1.0.0'}})
    monkeypatch.setattr(lifecycle, '_request', wire)
    result = await lifecycle.manifest('hpc-job', 'a'*64)
    wire.assert_awaited_once_with('hpc-job', 'app_manifest', revision='a'*64)
    wire.return_value = {**result, 'revision': 'b'*64}
    with pytest.raises(RuntimeError, match='invalid installed'):
        await lifecycle.manifest('hpc-job', 'a'*64)


def test_private_dependency_journal_is_excluded_from_platform_snapshots(tmp_path):
    from pantheon.platform.state_sync import _pack
    import io
    import tarfile
    private = tmp_path/'.pantheon/platform-private/user/app-dependency-starts'
    private.mkdir(parents=True)
    (private/'attempt.json').write_text('{"access_token":"must-stay-local"}')
    state = tmp_path/'.pantheon/settings.json'
    state.write_text('{}')
    payload = _pack(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        assert not any('platform-private' in name for name in archive.getnames())
        assert not any(b'must-stay-local' in archive.extractfile(info).read()
                       for info in archive.getmembers() if info.isfile())
