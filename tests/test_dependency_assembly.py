import copy
import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pantheonos.apps.dependency_assembly import AssemblyError, DependencyStarter, _compatible, _methods


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


def test_local_rpc_grant_requires_explicit_trusted_authority():
    import ssl
    from pantheonos.apps.dependency_assembly import DependencyAuthority, _grant
    from pantheonos.apps.runtime_config import RuntimeCredential
    _, authority, recipe, _ = fixture()
    request = {'consumer': recipe['consumer'], 'provider': recipe['bindings']['files']['provider']}
    grant = authority.issue.side_effect(request)
    origin = 'https://127.0.0.1:19123'
    grant['endpoint'] = origin + '/rpc'
    with pytest.raises(AssemblyError):
        _grant(grant, request, 'owner')
    assert _grant(grant, request, 'owner', rpc_origin=origin) == grant
    for suffix in ('?', '#', '?other=1', '/', '/../admin'):
        changed = {**grant, 'endpoint': grant['endpoint'] + suffix}
        with pytest.raises(AssemblyError):
            _grant(changed, request, 'owner', rpc_origin=origin)
    with pytest.raises(AssemblyError):
        _grant(grant, request, 'other', rpc_origin=origin)
    for kwargs in ({}, {'credential': RuntimeCredential(origin, 'key')},
                   {'credential': RuntimeCredential('https://other.test', 'key'),
                    'tls_context': ssl.create_default_context()}):
        with pytest.raises(AssemblyError):
            DependencyAuthority(rpc_origin=origin, **kwargs)
    configured = DependencyAuthority(rpc_origin=origin, credential=RuntimeCredential(origin, 'key'),
                                     tls_context=ssl.create_default_context())
    assert configured.rpc_origin == origin
    for bad in ('https://localhost:19123', origin+'/', origin+'?', 'https://127.0.0.1:65536'):
        with pytest.raises(AssemblyError):
            DependencyAuthority(rpc_origin=bad, credential=RuntimeCredential(bad, 'key'),
                                tls_context=ssl.create_default_context())


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
async def test_manifest_request_keeps_exact_node_and_rejects_wrong_revision(monkeypatch):
    from pantheonos.apps.lifecycle import FleetLifecycle
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
    from pantheon.chatroom.state_sync import _pack
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


