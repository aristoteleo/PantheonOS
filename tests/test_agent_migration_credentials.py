"""Real Fleet vault provisioning and Agent launch after legacy key conversion.

Set AGENT_MIGRATION_FLEET to the built Fleet CLI. Keys and directories are
synthetic test data; no OS login, user vault or external model is contacted.
"""
import copy
import asyncio
import json
import os
from pathlib import Path
import subprocess
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.migration_credentials import LocalModelCredentialVault, ModelCredentialConversion
from pantheon.chatroom.migration_import import import_backup, abort_pending_import
from test_agent_migration import legacy
from test_agent_migration_backup import user_tree
from test_agent_launch import prepared, snapshot
from test_agent_application import TEMPLATE
from test_agent_model_scope import endpoint
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_model_services import connector_module, serve
from test_agent_release import release, release_process, ready
from test_agent_native_process import request


@pytest.fixture
def vault(tmp_path):
    binary = os.environ.get('AGENT_MIGRATION_FLEET')
    if not binary:
        pytest.skip('Supply the built Fleet CLI for native credential migration acceptance')
    root = tmp_path / 'fleet-state'; root.mkdir(mode=0o700)
    (root / 'node_id').write_text('node\n')
    return LocalModelCredentialVault(binary, state_dir=root, owner='owner', node_id='node')


def read_key(vault, ref, endpoint):
    result = subprocess.run([str(vault.executable), 'model-credential-read'],
        input=json.dumps({'ref': ref, 'endpoint': endpoint}), text=True, capture_output=True,
        env={**os.environ, 'PANTHEON_MODEL_CREDENTIALS': str(vault.state_dir / 'apps/owner/model-credentials')},
        timeout=10)
    assert result.returncode == 0, 'Private Fleet credential reader failed'
    return json.loads(result.stdout)['key']


def stage(legacy, tmp_path, endpoint, vault, *, extra_keys=None, target=None, dotenv=False):
    config = prepared(tmp_path, endpoint.url)
    spec = config['values']['agent']
    spec.update({key: legacy[key] for key in ('projects', 'active_project', 'default_project')})
    settings = Path(legacy['project_config']) / 'settings.json'
    settings.write_text(json.dumps({**spec['settings'], 'api_keys': {
        'OPENAI_API_KEY': 'legacy-synthetic-key', 'OPENAI_API_BASE': endpoint.url + '/byok/v1',
        **(extra_keys or {})}}))
    source = settings
    if dotenv:
        value = json.loads(settings.read_text())
        source = settings.parent.parent / '.env'
        source.write_text(''.join(name + '=' + key + '\n' for name, key in value.pop('api_keys').items()))
        value['env_file'] = '.env'
        settings.write_text(json.dumps(value))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': 'openai/fixture'}]}
    for name in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory']) / name
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = template
        path.write_text(json.dumps(value))
    target = target or tmp_path / 'data'
    fence = fence_legacy(legacy, operation='model-migration', target=target, namespace=spec['namespace'])
    backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
    bindings = [{'source': str(source), 'provider': 'openai', 'alias': 'provider',
                 'ref': 'node-secret://legacy-openai', 'endpoint': endpoint.url + '/byok/v1'}]
    return config, target, fence, backup, bindings


def conversion(backup, fence, bindings, vault):
    return ModelCredentialConversion(backup['directory'], digest=backup['sha256'],
                                     fence=fence, bindings=bindings, vault=vault)


def restore(backup, fence, plan):
    return import_backup(backup['directory'], digest=backup['sha256'], fence=fence, model_credentials=plan)


@pytest.mark.asyncio
async def test_imported_key_uses_existing_vault_and_preserves_real_agent_endpoint(
        legacy, tmp_path, endpoint, vault, model_dependency, model_endpoint):
    config, root, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault)
    try:
        original = user_tree(Path(legacy['project_config']))
        plan = conversion(backup, fence, bindings, vault)
        assert not (vault.state_dir / 'apps').exists(), 'Planning must not provision credentials'
        descriptor = plan.describe()
        assert 'legacy-synthetic-key' not in json.dumps(descriptor) + repr(plan)
        receipt = restore(backup, fence, plan)
        assert receipt['model_bindings'] == descriptor
        assert 'legacy-synthetic-key' not in json.dumps(receipt)
        assert 'api_keys' not in json.loads((root / 'configuration/.pantheon/settings.json').read_text())
        assert user_tree(Path(legacy['project_config'])) == original
        assert read_key(vault, bindings[0]['ref'], bindings[0]['endpoint']) == 'legacy-synthetic-key'
        assert restore(backup, fence, plan) == receipt

        config['values']['agent']['models'] = descriptor['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        for alias, binding in descriptor['credentials'].items():
            config['credentials'][alias] = {'endpoint': binding['endpoint'],
                'key': read_key(vault, binding['ref'], binding['endpoint'])}
        projects = AppProjects(legacy['projects'])
        with pytest.raises(ValueError, match='migrated model bindings'):
            AgentAppData(root, namespace=config['values']['agent']['namespace'], projects=projects)
        for kind in ('node', 'endpoint', 'provider'):
            wrong = copy.deepcopy(config)
            if kind == 'node': wrong['node_id'] = 'different-node'
            elif kind == 'endpoint': wrong['credentials']['provider']['endpoint'] = 'https://different.example/v1'
            else: wrong['values']['agent']['models']['providers'] = {'anthropic': 'provider'}
            with pytest.raises(ValueError, match='migrated model bindings'):
                ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(wrong),
                                           dependency_ca_file=tmp_path / 'cert.pem')

        app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                        dependency_ca_file=tmp_path / 'cert.pem')
        try:
            await app.run_setup()
            result = await app.chat(chat_id='chat-b', message=[{'role': 'user', 'content': 'Continue'}])
            assert result['success'], result
        finally:
            await app.cleanup()
        assert endpoint.requests and not model_endpoint.requests
        assert all(path in ('/byok/v1/chat/completions', '/byok/v1/responses')
                   and headers['Authorization'] == 'Bearer legacy-synthetic-key'
                   for path, headers, body in endpoint.requests)
    finally:
        fence.close()


def test_vault_write_interruption_resumes_without_rotation(legacy, tmp_path, endpoint, vault, monkeypatch):
    _, root, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault)
    try:
        plan = conversion(backup, fence, bindings, vault)
        original = vault.ensure
        def interrupted(*args):
            original(*args)
            raise OSError('Simulated interrupted migration')
        monkeypatch.setattr(vault, 'ensure', interrupted)
        with pytest.raises(OSError): restore(backup, fence, plan)
        assert json.loads((root / 'migration.json').read_text())['phase'] == 'importing'
        with pytest.raises(ValueError): AgentAppData(root, namespace='process-app', projects=AppProjects([]))
        assert read_key(vault, bindings[0]['ref'], bindings[0]['endpoint']) == 'legacy-synthetic-key'
        changed = [{**bindings[0], 'ref': 'node-secret://different'}]
        with pytest.raises(ValueError, match='different migration'):
            restore(backup, fence, conversion(backup, fence, changed, vault))
        monkeypatch.setattr(vault, 'ensure', original)
        receipt = restore(backup, fence, conversion(backup, fence, bindings, vault))
        assert receipt['conversations'] == 2
    finally:
        fence.close()


def test_conflicting_existing_credential_is_not_overwritten(legacy, tmp_path, endpoint, vault):
    _, root, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault)
    try:
        vault.ensure(bindings[0]['ref'], bindings[0]['endpoint'], 'previous-shared-key')
        with pytest.raises(ValueError, match='not replaced'):
            restore(backup, fence, conversion(backup, fence, bindings, vault))
        assert read_key(vault, bindings[0]['ref'], bindings[0]['endpoint']) == 'previous-shared-key'
        abort_pending_import(fence=fence)
        assert read_key(vault, bindings[0]['ref'], bindings[0]['endpoint']) == 'previous-shared-key'
    finally:
        fence.close()


@pytest.mark.parametrize('extra', [{'SCRAPER_API_KEY': 'separate-secret'}, {'LLM_API_BASE': 'https://proxy.example/v1'}])
def test_unmapped_secret_or_fallback_blocks_before_vault_writes(legacy, tmp_path, endpoint, vault, extra):
    _, root, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault, extra_keys=extra)
    try:
        plan = conversion(backup, fence, bindings, vault)
        with pytest.raises(ValueError, match='explicit conversion'): restore(backup, fence, plan)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()
    finally:
        fence.close()


@pytest.mark.parametrize('change', [{'endpoint': 'https://wrong.example/v1'}, {'provider': 'not-a-provider'},
                                  {'alias': 'allocator'}, {'ref': 'node-secret://../bad'}, {'source': '/not-in-backup'}])
def test_invalid_binding_rejected_without_provisioning(legacy, tmp_path, endpoint, vault, change):
    _, root, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault)
    try:
        with pytest.raises(ValueError): conversion(backup, fence, [{**bindings[0], **change}], vault)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()
    finally:
        fence.close()


def test_vault_checks_persisted_node_before_provisioning(tmp_path, vault):
    (vault.state_dir / 'node_id').write_text('another-node')
    with pytest.raises(ValueError, match='another Fleet node'):
        vault.ensure('node-secret://provider', 'https://api.example/v1', 'test-key')
    assert not (vault.state_dir / 'apps').exists()


def test_original_model_service_connector_consumes_converted_reference(legacy, tmp_path, vault, monkeypatch):
    received = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            received.append((self.path, self.headers.get('Authorization')))
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"original-model"}]}')
    with serve(Engine) as base:
        _, root, fence, backup, bindings = stage(legacy, tmp_path, SimpleNamespace(url=base), vault)
        try:
            restore(backup, fence, conversion(backup, fence, bindings, vault))
            monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
            monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
            connector = connector_module.Connector(tmp_path / 'connector')
            connector.configure({'engine': 'api', 'endpoint': bindings[0]['endpoint'], 'secret_ref': bindings[0]['ref']})
            assert connector.discover()['models'] == [{'id': 'original-model'}]
            assert received == [('/byok/v1/models', 'Bearer legacy-synthetic-key')]
            assert 'legacy-synthetic-key' not in connector.path.read_text()
        finally:
            fence.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('dotenv', [False, True], ids=['settings', 'dotenv'])
async def test_packaged_agent_validates_and_uses_migrated_credential(
        release, legacy, tmp_path, endpoint, vault, model_dependency, model_endpoint, monkeypatch, dotenv):
    config, root, fence, backup, bindings = stage(legacy, tmp_path, endpoint, vault,
                                                target=tmp_path / 'data' / 'agent', dotenv=dotenv)
    try:
        plan = conversion(backup, fence, bindings, vault)
        restore(backup, fence, plan)
        descriptor = plan.describe()
        config['values']['agent']['models'] = descriptor['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        config['credentials']['provider'] = {'endpoint': bindings[0]['endpoint'],
            'key': read_key(vault, bindings[0]['ref'], bindings[0]['endpoint'])}
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
        wrong = copy.deepcopy(config)
        wrong['credentials']['provider']['endpoint'] = 'https://different.example/v1'
        with release_process(tmp_path, release, wrong) as (process, base):
            assert await asyncio.to_thread(process.wait, timeout=20) != 0
        assert 'preserve its migrated model bindings' in (tmp_path / 'release.log').read_text()
        assert not endpoint.requests
        with release_process(tmp_path, release, config) as (process, base):
            await ready(process, base, tmp_path)
            result = await request(base, '/rpc', {'method': 'chat', 'args': {
                'chat_id': 'chat-b', 'message': [{'role': 'user', 'content': 'Continue'}]}})
            assert result['success'] and result['result']['success'], result
            assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
        assert process.returncode == 0
        assert endpoint.requests and all(headers['Authorization'] == 'Bearer legacy-synthetic-key'
                                         for _, headers, _ in endpoint.requests)
    finally:
        fence.close()
