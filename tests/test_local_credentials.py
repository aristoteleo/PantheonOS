"""Owner-provided model/App secrets must never become composition data."""
from copy import deepcopy
import json
import os

import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.platform.local_credentials import read_credentials

KEY = 'private-test-model-key'
REF = 'node-secret://model-test'
ENDPOINT = 'https://models.example.test/v1'


@pytest.fixture
def source(tmp_path):
    path = tmp_path/'credentials.json'
    path.write_text(json.dumps({'protocol': 1, 'credentials': {REF: {'endpoint': ENDPOINT, 'key': KEY}}}))
    path.chmod(0o600)
    spec = {'apps': {}, 'model_apps': {'model': {'app': {'components': {'backend': {
        'values': {'connector': {'engine': 'api', 'endpoint': ENDPOINT, 'secret_ref': REF}}}}}}}}
    return path, spec, tmp_path/'workspace'


@pytest.mark.asyncio
async def test_source_is_frozen_delivered_only_to_explicit_vault_and_not_in_recipe(source):
    path, spec, workspace = source
    before = deepcopy(spec)
    credentials = read_credentials(path, spec, workspace)
    path.write_text('changed after approval; not read on retry')
    calls = []
    class Vault:
        async def ensure_async(self, *args): calls.append(args)
    await credentials.deliver(Vault())
    await credentials.deliver(Vault())
    assert calls == [(REF, ENDPOINT, KEY)] * 2
    assert spec == before
    assert KEY not in repr(credentials) and KEY not in repr(credentials.entries)
    assert KEY not in json.dumps(spec)


def test_ordinary_component_credential_and_vault_endpoint_identity(source):
    path, _, workspace = source
    spec = {'apps': {'files': {'components': {'backend': {'credentials': {
        'model': {'ref': REF, 'endpoint': 'https://models.example.test'}}}}}}, 'model_apps': {}}
    assert len(read_credentials(path, spec, workspace).entries) == 1


@pytest.mark.parametrize('change', ['unused', 'endpoint', 'conflict', 'reserved', 'key', 'fields', 'protocol', 'duplicate'])
def test_invalid_selection_never_exposes_source_values(source, change):
    path, spec, workspace = source
    value = json.loads(path.read_text())
    connector = spec['model_apps']['model']['app']['components']['backend']['values']['connector']
    if change == 'unused': connector.pop('secret_ref')
    if change == 'endpoint': connector['endpoint'] = 'https://other.example.test/v1'
    if change == 'conflict':
        spec['apps']['app'] = {'components': {'backend': {'credentials': {'model': {
            'ref': REF, 'endpoint': 'https://other.example.test/v1'}}}}}
    if change == 'reserved':
        ref = 'node-secret://profile-owner-injected'
        value['credentials'][ref] = value['credentials'].pop(REF)
        connector['secret_ref'] = ref
    if change == 'key': value['credentials'][REF]['key'] += '\n'
    if change == 'fields': value['credentials'][REF]['unrecognized'] = KEY
    if change == 'protocol': value['protocol'] = True
    path.write_text(json.dumps(value))
    if change == 'duplicate': path.write_text('{"protocol":1,"protocol":1,"credentials":"' + KEY + '"}')
    with pytest.raises(AssemblyError) as error:
        read_credentials(path, spec, workspace)
    assert KEY not in str(error.value) and ENDPOINT not in str(error.value)


@pytest.mark.parametrize('unsafe', ['permissions', 'symlink', 'hardlink', 'workspace', 'oversized', 'fifo'])
def test_unsafe_source_files_are_rejected(source, unsafe):
    path, spec, workspace = source
    if unsafe == 'permissions': path.chmod(0o644)
    if unsafe in ('symlink', 'hardlink'):
        alias = path.with_name('alias.json')
        if unsafe == 'symlink': alias.symlink_to(path)
        else: os.link(path, alias)
        path = alias
    if unsafe == 'workspace': workspace = path.parent
    if unsafe == 'oversized': path.write_bytes(b' ' * (512 * 1024 + 1))
    if unsafe == 'fifo':
        path.unlink()
        os.mkfifo(path, 0o600)
    with pytest.raises(AssemblyError): read_credentials(path, spec, workspace)


@pytest.mark.asyncio
async def test_vault_conflict_does_not_authorize_replacement(source):
    credentials = read_credentials(*source)
    class Vault:
        async def ensure_async(self, *args): raise ValueError('existing vault conflict')
    with pytest.raises(ValueError, match='existing vault conflict'):
        await credentials.deliver(Vault())


@pytest.mark.parametrize('root', ['package', 'catalog', 'store', 'data'])
def test_secret_source_cannot_be_uploaded_or_served_as_assets(source, root):
    path, spec, workspace = source
    if root == 'package': spec['packages'] = {'code': {'path': str(path.parent)}}
    else:
        desktop = {'catalog': [], 'data_roots': []}
        if root == 'data': desktop['data_roots'] = [str(path.parent)]
        elif root == 'catalog': desktop['catalog'] = [{'path': str(path.parent), 'scope': 'user'}]
        else:
            directory = path.parent/'app-store'/'snapshots'
            directory.mkdir(parents=True)
            moved = directory/'keys.json'
            path.rename(moved)
            path = moved
            desktop['catalog'] = [{'path': str(directory.parent.parent/'apps'), 'scope': 'user'}]
        spec['apps']['desktop'] = {'components': {'backend': {'values': {'desktop': desktop}}}}
    with pytest.raises(AssemblyError): read_credentials(path, spec, workspace)


@pytest.mark.parametrize('valid', [True, False])
def test_cli_reads_credentials_before_starting_infrastructure(source, monkeypatch, capsys, valid):
    from pantheon.platform import local_profile
    path, selected, workspace = source
    app = selected['model_apps']['model']['app']
    app.update(package='model', scope='model', bindings={})
    selected['model_apps']['model'].update(deployment_id='model', name='Model', models=[{'id': 'example'}])
    selected.update(protocol=1, packages={'model': {'path': str(path.parent/'code'), 'revision': 'a' * 64}})
    # The generic profile also requires an ordinary consumer App.
    selected['apps']['consumer'] = {'package': 'model', 'scope': 'consumer', 'components': {}, 'bindings': {}}
    manifest_path = path.parent/'manifest.json'
    manifest_path.write_text(json.dumps(selected)); manifest_path.chmod(0o600)
    captured = []
    async def serve(*args, **kwargs):
        captured.append(kwargs['credentials'])
        assert KEY not in json.dumps(args[3])
    monkeypatch.setattr(local_profile, 'serve', serve)
    arguments = ['--profile', str(path.parent/'profile'), '--workspace', str(workspace),
        '--manifest', str(manifest_path), '--credentials', str(path), '--controller', '/explicit/controller',
        '--broker', '/explicit/broker', '--runner', '/explicit/runner']
    if valid:
        local_profile.main(arguments)
        assert captured[0].entries[0][1].key == KEY
    else:
        path.chmod(0o644)
        with pytest.raises(SystemExit) as error: local_profile.main(arguments)
        assert error.value.code == 2 and captured == []
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
