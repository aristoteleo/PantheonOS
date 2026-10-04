"""Independent release gate: no source checkout or installed Pantheon package.

Set AGENT_RELEASE_PYTHON to a clean venv containing package-requirements.lock,
AGENT_RELEASE_TRANSPORT to a built fleet-app-transport, and AGENT_APP_BUILD_DIR
to build:agent-app output. Model and dependency servers are local fixtures.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
from urllib.error import HTTPError

import pytest

from pantheon.chatroom.package import build_package
from pantheon.apps.lifecycle import build_artifact, MAX_ARTIFACT
from test_agent_application import TEMPLATE
from test_agent_launch import prepared
from test_agent_native_process import request
from test_model_dependency import model_endpoint, model_dependency, tls_material
from test_agent_model_scope import endpoint as byok_endpoint
from test_agent_migration import legacy


@pytest.fixture(scope='module')
def release(tmp_path_factory):
    required = ('AGENT_RELEASE_PYTHON', 'AGENT_RELEASE_TRANSPORT', 'AGENT_APP_BUILD_DIR')
    if any(not os.environ.get(name) for name in required):
        pytest.skip('Supply clean Python, built QUIC transport and built GUI for release acceptance')
    python = Path(os.environ['AGENT_RELEASE_PYTHON'])
    probe = subprocess.run([str(python), '-I', '-c',
        'import importlib.util; assert importlib.util.find_spec("pantheon") is None'],
        cwd=tmp_path_factory.getbasetemp(), capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    target = sys.platform
    target += '-' + {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    root = build_package(tmp_path_factory.mktemp('release') / 'agent', target, version='0.7.0',
        frontend=os.environ['AGENT_APP_BUILD_DIR'], transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies={'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'}})
    # Audit artifact before Python imports create caches.
    inventory = json.loads((root / 'release.json').read_text())['files']
    assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == digest for path, digest in inventory.items())
    assert not any('/chatroom/room.py' in p or '/platform/service.py' in p or '/apps/builtin/file/' in p for p in inventory)
    assert not any('/models/platform_budget.py' in p or '/models/credentials.py' in p for p in inventory), \
        'Owner credential provisioning must not ship inside the Agent App'
    assert not any('/chatroom/migration_models.py' in p or '/chatroom/migration_templates.py' in p for p in inventory)
    assert not any('/chatroom/migration_handoff.py' in p or '/chatroom/migration_budget.py' in p for p in inventory), \
        'Legacy model environment and budget conversion must not ship inside the Agent App'
    assert not any('/chatroom/migration_mcp_' in p for p in inventory), \
        'Imported MCP admission must not pull owner-side capture, backup or vault code into the Agent'
    payload, _ = build_artifact(root)
    assert len(payload) <= MAX_ARTIFACT, 'Release must fit the ordinary Fleet upload protocol'
    selected = subprocess.check_output([str(python), '-I', '-c',
        'import sys; sys.path.insert(0,sys.argv[1]); from pantheon.models.direct import binary; print(binary())',
        str(root / 'backend/_vendor')], text=True).strip()
    assert Path(selected) == root / 'backend/_vendor/pantheon/models/fleet-app-transport'
    return root, python


@contextmanager
def release_process(root, release, configuration):
    package, python = release
    root.mkdir(exist_ok=True)
    snapshot = root / 'configuration.json'
    snapshot.write_text(json.dumps(configuration))
    snapshot.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHON'))}
    env.update(HOME=str(root / 'home'), PANTHEON_APP_CONFIG=str(snapshot),
        PANTHEON_FLEET_ID=configuration['owner'], PANTHEON_NODE_ID=configuration['node_id'],
        PANTHEON_INSTANCE_ID=configuration['instance_id'], PANTHEON_APP_REVISION=configuration['revision'],
        PANTHEON_INSTANCE_GENERATION=str(configuration['generation']), PANTHEON_COMPONENT_NAME='backend',
        PANTHEON_APP_RPC_TOKEN='native-test-token', PANTHEON_PORT_HTTP=str(port))
    # -I drops cwd/user site/PYTHONPATH. The normal App loader must find its own
    # vendor tree; importing absent combined hosts/transports must fail loudly.
    boot = '''import importlib.abc, runpy, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name in ('pantheon.chatroom.room','pantheon.platform.service','pantheon.remote','pantheon.repl','nats'):
   raise AssertionError('Agent release imported platform/legacy transport: '+name)
sys.meta_path.insert(0, Boundary())
path=sys.argv.pop(1)
sys.path.insert(0,str(__import__('pathlib').Path(path).parent))
runpy.run_path(path,run_name='__main__')
'''
    with (root / 'release.log').open('a') as log:
        process = subprocess.Popen([str(python), '-I', '-c', boot, str(package / '.fleet-runtime/host.py'),
            'start', '--package', str(package), '--data', str(root / 'data')],
            cwd=root, env=env, stdout=log, stderr=log)
        try:
            yield process, f'http://127.0.0.1:{port}'
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                pytest.fail('Release failed to drain')


async def ready(process, base, root):
    for _ in range(300):
        try:
            return await request(base, '/health')
        except OSError:
            assert process.poll() is None, (root / 'release.log').read_text()[-10000:]
            await asyncio.sleep(.05)
    pytest.fail('Independent release did not become ready')


@pytest.mark.asyncio
@pytest.mark.parametrize('ref', ['fleet-model://mac/example%3A8b', 'fleet-route://local'])
async def test_release_models_chat_restart_and_drain(release, tmp_path, model_dependency, model_endpoint, monkeypatch, ref):
    config = prepared(tmp_path, model_endpoint.url)
    config['credentials'].pop('model')
    config['credentials']['model_services'] = model_dependency.credential
    config['values']['agent']['models'] = {'model_services': 'model_services'}
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': ref}]}
    async def rpc(base, method, **args):
        try:
            response = await request(base, '/rpc', {'method': method, 'args': args})
        except HTTPError as error:
            pytest.fail(f'{method}: {error.read().decode()}')
        assert response['success'], response
        return response['result']
    with release_process(tmp_path, release, config) as (process, base):
        assert (await ready(process, base, tmp_path))['app_id'] == 'agent'
        listing = await rpc(base, 'list_available_models')
        assert listing['fleet_catalog_ready'] and len(listing['fleet_models']) == 2
        created = await rpc(base, 'create_chat', chat_name='Release', project_name='Shared', template_obj=template)
        assert created['success'], created
        chat = created['chat_id']
        result = await rpc(base, 'chat', chat_id=chat, message=[{'role': 'user', 'content': 'Reply once'}])
        assert result['success'], result
        assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert process.returncode == 0
    with release_process(tmp_path, release, config) as (process, base):
        await ready(process, base, tmp_path)
        history = await rpc(base, 'open_agent_history', chat_id=chat)
        data = await rpc(base, 'read_agent_history', chat_id=chat, snapshot_id=history['snapshot_id'], part=0)
        assert 'scoped reply' in data['json_fragment']
        model_dependency.revoked = True
        listing = await rpc(base, 'list_available_models')
        assert not listing['fleet_models'] and not listing['fleet_catalog_ready']
        assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert process.returncode == 0
    inference = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
    assert len(inference) == 1, 'Restart or revoke replayed inference'


@pytest.mark.asyncio
async def test_release_byok_compatibility(release, tmp_path, model_dependency, model_endpoint, byok_endpoint, monkeypatch):
    config = prepared(tmp_path, byok_endpoint.url)
    config['credentials']['provider'] = config['credentials'].pop('model')
    config['credentials']['model_services'] = model_dependency.credential
    config['values']['agent']['models'] = {'model_services': 'model_services', 'providers': {'openai': 'provider'}}
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': 'openai/fixture'}]}
    with release_process(tmp_path, release, config) as (process, base):
        await ready(process, base, tmp_path)
        created = await request(base, '/rpc', {'method': 'create_chat', 'args': {
            'chat_name': 'BYOK compatibility', 'project_name': 'Shared', 'template_obj': template}})
        assert created['success'] and created['result']['success'], created
        result = await request(base, '/rpc', {'method': 'chat', 'args': {
            'chat_id': created['result']['chat_id'], 'message': [{'role': 'user', 'content': 'Reply once'}]}})
        assert result['success'] and result['result']['success'], result
        assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert process.returncode == 0
    assert byok_endpoint.requests
    assert all(headers['Authorization'] == 'Bearer process-fixture' for _, headers, _ in byok_endpoint.requests)


@pytest.mark.asyncio
async def test_release_admits_only_completed_import_and_continues_legacy_chat(
        release, tmp_path, legacy, model_dependency, model_endpoint, monkeypatch):
    from pantheon.chatroom.migration import fence_legacy
    from pantheon.chatroom.migration_backup import backup_legacy
    from pantheon.chatroom import migration_import
    import sqlite3

    config = prepared(tmp_path, model_endpoint.url)
    spec = config['values']['agent']
    spec.update({key: legacy[key] for key in ('projects', 'active_project', 'default_project')})
    spec['models'] = {'model_services': 'model_services'}
    config['credentials'].pop('model')
    config['credentials']['model_services'] = model_dependency.credential
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': 'fleet-route://local'}]}
    (Path(legacy['project_config']) / 'settings.json').write_text(json.dumps(spec['settings']))
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory']) / name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = template
        path.write_text(json.dumps(value))

    # Fleet's state directory also contains host bookkeeping. Native Agent data
    # is its `agent` child, the exact root used by native.register().
    data = tmp_path / 'data' / 'agent'
    with fence_legacy(legacy, operation='release-import', target=data, namespace=spec['namespace']) as fence:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
        with monkeypatch.context() as interrupted:
            def fail_copy(*args):
                raise OSError('Interrupted copy')
            interrupted.setattr(migration_import, '_copy', fail_copy)
            with pytest.raises(OSError, match='Interrupted copy'):
                migration_import.import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        # Exercise the installed host, not only the source class. It must exit
        # before opening a partially imported history or initiating inference.
        pending = (data / 'migration.json').read_bytes()
        with release_process(tmp_path, release, config) as (process, base):
            code = await asyncio.to_thread(process.wait, timeout=20)
            assert code != 0
        assert 'migration has not committed' in (tmp_path / 'release.log').read_text()
        assert (data / 'migration.json').read_bytes() == pending
        assert not model_dependency.data_calls

        receipt = migration_import.import_backup(backup['directory'], digest=backup['sha256'], fence=fence)
        with release_process(tmp_path, release, config) as (process, base):
            await ready(process, base, tmp_path)
            opened = await request(base, '/rpc', {'method': 'open_agent_history', 'args': {'chat_id': 'chat-b'}})
            assert opened['success'], opened
            history = await request(base, '/rpc', {'method': 'read_agent_history', 'args': {
                'chat_id': 'chat-b', 'snapshot_id': opened['result']['snapshot_id'], 'part': 0}})
            assert history['success'] and 'saved answer' in history['result']['json_fragment']
            result = await request(base, '/rpc', {'method': 'chat', 'args': {
                'chat_id': 'chat-b', 'message': [{'role': 'user', 'content': 'Continue this conversation'}]}})
            assert result['success'] and result['result']['success'], result
            assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
        assert process.returncode == 0
        with sqlite3.connect(data / 'instances/instances.sqlite3') as db:
            identities = db.execute('SELECT conversation_id, config_id, instance_id FROM instances').fetchall()
        assert sorted(identities) == sorted((item['conversation_id'], item['config_id'], item['instance_id'])
                                            for item in receipt['members'])
    assert model_dependency.data_calls == ['/v1/chat/completions']


@pytest.mark.asyncio
async def test_release_gui_conversation_and_settings(release, tmp_path, model_dependency, model_endpoint, monkeypatch):
    script = os.environ.get('PANTHEON_AGENT_FRONTEND_SCRIPT')
    if not script:
        pytest.skip('Supply the rendered Agent GUI acceptance script')
    config = prepared(tmp_path, model_endpoint.url)
    config['credentials'].pop('model')
    config['credentials']['model_services'] = model_dependency.credential
    config['values']['agent']['models'] = {'model_services': 'model_services'}
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': 'fleet-route://local'}]}
    with release_process(tmp_path, release, config) as (process, base):
        await ready(process, base, tmp_path)
        created = await request(base, '/rpc', {'method': 'create_chat', 'args': {
            'chat_name': 'Independent release GUI', 'project_name': 'Shared', 'template_obj': template}})
        assert created['success'] and created['result']['success'], created
        result = await asyncio.to_thread(subprocess.run, ['node', script], capture_output=True, text=True,
            timeout=120, env={**os.environ, 'AGENT_APP_BUILD_DIR': str(release[0] / 'frontend'),
                'PANTHEON_AGENT_TEST_URL': base, 'PANTHEON_AGENT_TEST_CHAT': created['result']['chat_id']})
        assert result.returncode == 0, result.stdout + result.stderr
        assert '"ok":true' in result.stdout
        assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert process.returncode == 0
    assert model_dependency.data_calls == ['/v1/chat/completions']
