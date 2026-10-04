"""Captured budget state -> original Fleet vault/Connector -> migrated Agent.

Hub provisioning, control directory and model replies are local fixtures.
"""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler

import pytest

from pantheon.chatroom.migration_credentials import ModelCredentialConversion
from pantheon.chatroom.migration_models import ModelSelectionConversion
from pantheon.chatroom.migration_import import import_backup
from pantheon.chatroom.migration import fence_legacy
from pantheon.chatroom.migration_backup import backup_legacy
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.models.client import model_ref
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_agent_migration_handoff import capture, attach
from test_agent_migration_environment import backed_up
from test_agent_launch import prepared, snapshot
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_model_services import deployment, connector_module, serve
from test_agent_release import release, release_process, ready
from test_agent_native_process import request as rpc_request


@pytest.fixture
def endpoint():
    calls = []
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path.endswith('/v1/models')
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"example:8b"},{"id":"openrouter/vendor/model"}]}')
        def do_POST(self):
            calls.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"budget reply"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    with serve(Proxy) as url:
        yield SimpleNamespace(url=url, requests=calls)


def choice(enabled=True):
    return dict(protocol=1, source='legacy-local-browser', service_id='original-service', enabled=enabled)


def capture_budget(legacy, tmp_path, base, *, enabled=True, mode='direct', credentials=True, byok=True):
    env = {'LLM_FORCE_PROXY': 'true' if enabled else 'false', 'PLATFORM_MODEL_MODE': mode}
    if byok:
        env.update(OPENAI_API_KEY='preserved-byok', OPENAI_API_BASE=base + '/byok/v1')
    if credentials:
        env.update(PANTHEON_PLATFORM_PROXY_BASE=base + '/budget', PANTHEON_PLATFORM_PROXY_KEY='budget-virtual-key')
    settings, path, dotenv = capture(legacy, tmp_path, env)
    if not byok:
        config = json.loads(path.read_text()); config['api_keys'] = {}
        path.write_text(json.dumps(config)); dotenv.write_text('')
    source, _ = attach(legacy, settings)
    connector = dict(engine='api', endpoint=base + '/budget/v1', secret_ref='node-secret://budget')
    receipt = dict(protocol=1, owner='owner', node_id='node', source='platform-budget', model_mode=mode, connector=connector)
    return source, {'choice': choice(enabled), 'provisioned': receipt if credentials else None}


def keys(backup, fence, vault, source, base, budget):
    return ModelCredentialConversion(backup['directory'], digest=backup['sha256'], fence=fence, vault=vault,
        bindings=[dict(provider='openai', source=str(source), alias='byok',
                       endpoint=base + '/byok/v1', ref='node-secret://byok')], platform_budget=budget)


def selections(legacy, backup, fence, budget_choice, reference):
    member = json.loads((Path(legacy['home_memory']) / 'chat-b.json').read_text())['extra_data']['team_template']['agents'][0]
    return ModelSelectionConversion(backup['directory'], digest=backup['sha256'], fence=fence,
        owner='owner', node_id='node', selections=[dict(conversation_id=cid, config_id=member['id'],
            source=member['model'], target=reference) for cid in ('chat-a', 'chat-b')],
        fleet_tiers={tier: reference for tier in ('normal', 'high', 'low')},
        budget_choice=budget_choice, source_service_id=budget_choice['service_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('execution', ['source', 'package'])
@pytest.mark.parametrize('enabled,mode,route', [(True, 'direct', False), (True, 'openrouter', False),
                                             (True, 'direct', True), (False, 'openrouter', False)])
async def test_budget_state_preserves_byok_and_saved_agent_uses_original_connector(
        legacy, tmp_path, vault, endpoint, model_dependency, model_endpoint, monkeypatch, enabled, mode, route,
        execution, request):
    package = request.getfixturevalue('release') if execution == 'package' else None
    source, budget = capture_budget(legacy, tmp_path, endpoint.url, enabled=enabled, mode=mode, credentials=enabled)
    native_id = 'openrouter/vendor/model' if mode == 'openrouter' and enabled else 'example:8b'
    reference = 'fleet-route://local' if route else model_ref('mac', native_id)
    connector_config = budget['provisioned']['connector'] if enabled else dict(
        engine='api', endpoint=endpoint.url + '/byok/v1', secret_ref='node-secret://byok')
    model_dependency.connector.configure(connector_config)
    model_dependency.deployment.update(engine='api', config_revision=model_dependency.connector.revision, node_id='node')
    model_dependency.deployment['binding']['node_id'] = 'node'
    model_dependency.control.policies['agent']['deployments']['mac']['node_id'] = 'node'
    model_dependency.deployment['models'][0]['id'] = native_id
    root = tmp_path / 'data/agent'
    with fence_legacy(legacy, operation='budget-agent', target=root, namespace='process-app') as fence:
        backup = backup_legacy(legacy, fence=fence, directory=tmp_path / 'backup')
        conversion = keys(backup, fence, vault, source, endpoint.url, budget)
        selection = selections(legacy, backup, fence, budget['choice'], reference)
        if enabled:
            before = selection.describe()['selection_sha256']
            await selection.review_budget(model_dependency.control.client, budget['provisioned'])
            assert selection.describe()['selection_sha256'] != before
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                                model_credentials=conversion, model_selection=selection)
        assert import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                             model_credentials=conversion, model_selection=selection) == receipt
        assert receipt['model_bindings']['credentials'] == {}
        assert receipt['model_bindings']['provisioning']['platform_budget']['choice']['enabled'] is enabled
        assert read_key(vault, 'node-secret://byok', endpoint.url + '/byok/v1') == 'preserved-byok'
        config = prepared(tmp_path, endpoint.url)
        config['values']['agent'].update({k: legacy[k] for k in ('projects', 'active_project', 'default_project')})
        config['values']['agent']['models'] = selection.describe()['models']
        config['credentials'].pop('model')
        config['credentials']['model_services'] = model_dependency.credential
        monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
        monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps/owner/model-credentials'))
        monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
        if package is None:
            app = ConfiguredAgentApplication('agent', data_dir=root, configuration=snapshot(config),
                                             dependency_ca_file=tmp_path / 'cert.pem')
            try:
                await app.run_setup()
                assert (await app.chat(chat_id='chat-b', message=[{'role': 'user', 'content': 'Continue'}]))['success']
            finally:
                await app.cleanup()
        else:
            with release_process(tmp_path, package, config) as (process, address):
                await ready(process, address, tmp_path)
                result = await rpc_request(address, '/rpc', {'method': 'chat', 'args': {
                    'chat_id': 'chat-b', 'message': [{'role': 'user', 'content': 'Continue'}]}})
                assert result['success'] and result['result']['success'], result
                assert (await rpc_request(address, '/_fleet/drain', {}))['safe_to_stop']
            assert process.returncode == 0
            assert 'budget-virtual-key' not in (tmp_path / 'release.log').read_text()
        assert len(endpoint.requests) == 1 and not model_endpoint.requests
        path, headers, body = endpoint.requests[0]
        assert path == ('/budget/v1/chat/completions' if enabled else '/byok/v1/chat/completions')
        assert headers['Authorization'] == 'Bearer ' + ('budget-virtual-key' if enabled else 'preserved-byok')
        assert body['model'] == native_id
        assert 'budget-virtual-key' not in json.dumps(receipt) + json.dumps(config) + model_dependency.connector.path.read_text()
        assert 'preserved-byok' not in json.dumps(receipt) + json.dumps(config)


@pytest.mark.parametrize('change', ['choice', 'owner', 'node', 'mode', 'endpoint', 'missing', 'shared-ref', 'extra'])
def test_invalid_budget_pairing_rejected_before_target_or_vault(legacy, tmp_path, vault, change):
    base = 'https://proxy.example'
    source, budget = capture_budget(legacy, tmp_path, base)
    if change == 'choice': budget['choice']['enabled'] = False
    elif change in ('owner', 'mode'): budget['provisioned'][change if change == 'owner' else 'model_mode'] = 'different'
    elif change == 'node': budget['provisioned']['node_id'] = 'different'
    elif change == 'endpoint': budget['provisioned']['connector']['endpoint'] = 'https://elsewhere.example/v1'
    elif change == 'missing': budget['provisioned'] = None
    elif change == 'shared-ref': budget['provisioned']['connector']['secret_ref'] = 'node-secret://byok'
    else: budget['choice']['key'] = 'must-not-enter-audit'
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        with pytest.raises(ValueError) as error: keys(backup, fence, vault, source, base, budget)
        assert 'budget-virtual-key' not in str(error.value)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


@pytest.mark.parametrize('change', ['missing', 'enabled', 'service'])
def test_budget_conversion_requires_matching_model_selection_audit(legacy, tmp_path, vault, change):
    base = 'https://proxy.example'
    source, budget = capture_budget(legacy, tmp_path, base)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = keys(backup, fence, vault, source, base, budget)
        observed = choice()
        if change == 'enabled': observed['enabled'] = False
        if change == 'service': observed['service_id'] = 'different-service'
        selected = None if change == 'missing' else selections(legacy, backup, fence, observed, model_ref('budget', 'model'))
        with pytest.raises(ValueError, match='same confirmed budget choice'):
            import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                          model_credentials=conversion, model_selection=selected)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


def test_enabled_budget_needs_publication_review_before_import(legacy, tmp_path, vault):
    base = 'https://proxy.example'
    source, budget = capture_budget(legacy, tmp_path, base)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = keys(backup, fence, vault, source, base, budget)
        selected = selections(legacy, backup, fence, budget['choice'], model_ref('budget', 'model'))
        with pytest.raises(ValueError, match='Review all model selections'):
            import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                          model_credentials=conversion, model_selection=selected)
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


def directory(budget):
    row = deployment()
    config = connector_module.validate_config(budget['provisioned']['connector'])
    row.update(engine='api', node_id='node', config_revision=connector_module.configuration_revision(config))
    row['binding']['node_id'] = 'node'
    row['models'][0]['context'] = 8192
    route = dict(route_id='budget-route', name='Budget', revision=1,
        candidates=[dict(deployment_id='mac', model_id='example:8b')],
        requires=dict(operation='text', tools=True, context=8192),
        transport='relay_allowed', fallback='none', selection='ordered')
    rows, routes = [row], [route]
    async def deployments(): return deepcopy(rows)
    async def route_directory(): return deepcopy(routes)
    return SimpleNamespace(deployments=deployments, routes=route_directory), rows, routes


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['endpoint', 'node', 'engine', 'tools', 'context', 'fallback', 'unavailable'])
async def test_budget_review_rejects_wrong_publication_and_hidden_fallbacks(legacy, tmp_path, vault, change):
    source, budget = capture_budget(legacy, tmp_path, 'https://proxy.example')
    client, rows, routes = directory(budget)
    if change == 'endpoint': rows[0]['config_revision'] = 'e' * 64
    elif change == 'node': rows[0]['node_id'] = rows[0]['binding']['node_id'] = 'other'
    elif change == 'engine': rows[0]['engine'] = 'ollama'
    elif change == 'tools': rows[0]['models'][0]['tools'] = False
    elif change == 'context': rows[0]['models'][0]['context'] = None
    elif change == 'fallback':
        other = deepcopy(rows[0]); other.update(deployment_id='byok', config_revision='d' * 64)
        other['binding']['instance_id'] = 'byok-instance'; rows.append(other)
        routes[0]['candidates'].append(dict(deployment_id='byok', model_id='example:8b'))
    else:
        async def unavailable(): raise RuntimeError('private-transport-information')
        client.deployments = unavailable
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        selected = selections(legacy, backup, fence, budget['choice'], 'fleet-route://budget-route')
        before = selected.describe(), selected.audit()
        with pytest.raises(ValueError, match='budget Connector') as error:
            await selected.review_budget(client, budget['provisioned'])
        assert 'private-transport-information' not in str(error.value)
        assert (selected.describe(), selected.audit()) == before
        assert not root.exists() and not (vault.state_dir / 'apps').exists()


@pytest.mark.asyncio
async def test_budget_review_covers_existing_template_and_plugin_references(legacy, tmp_path, vault):
    _, budget = capture_budget(legacy, tmp_path, 'https://proxy.example')
    client, _, _ = directory(budget)
    with backed_up(legacy, tmp_path) as (_, fence, backup):
        selected = selections(legacy, backup, fence, budget['choice'], model_ref('mac', 'example:8b'))
        await selected.review_budget(client, budget['provisioned'])
        assert selected.template_model('/template.md', 'inherit', '') == ('', None)
        assert selected.template_model('/template.md', 'tier', 'normal') == ('normal', None)
        with pytest.raises(ValueError, match='missing from the budget'):
            selected.template_model('/template.md', 'existing', 'fleet-model://byok/model')
        with pytest.raises(ValueError, match='missing from the budget'):
            selected.convert_settings('/settings.json', {'memory_system': {'selection_model': 'fleet-route://unreviewed'}})


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_budget_only_source_does_not_require_a_synthetic_byok_binding(legacy, tmp_path, vault, enabled):
    _, budget = capture_budget(legacy, tmp_path, 'https://proxy.example', enabled=enabled, credentials=enabled, byok=False)
    client, _, _ = directory(budget) if enabled else (None, None, None)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = ModelCredentialConversion(backup['directory'], digest=backup['sha256'], fence=fence,
                                               vault=vault, bindings=[], platform_budget=budget)
        selected = selections(legacy, backup, fence, budget['choice'], model_ref('mac', 'example:8b'))
        if enabled: await selected.review_budget(client, budget['provisioned'])
        receipt = import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                                model_credentials=conversion, model_selection=selected)
        assert receipt['model_bindings']['credentials'] == {}
        assert root.exists()
        if enabled:
            assert read_key(vault, 'node-secret://budget', 'https://proxy.example/budget/v1') == 'budget-virtual-key'
        else:
            assert not (vault.state_dir / 'apps').exists()


@pytest.mark.asyncio
async def test_rotated_budget_key_is_not_overwritten_and_candidate_stays_unstartable(legacy, tmp_path, vault):
    base = 'https://proxy.example'
    source, budget = capture_budget(legacy, tmp_path, base)
    vault.ensure('node-secret://budget', base + '/budget/v1', 'different-current-key')
    client, _, _ = directory(budget)
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        conversion = keys(backup, fence, vault, source, base, budget)
        selected = selections(legacy, backup, fence, budget['choice'], model_ref('mac', 'example:8b'))
        await selected.review_budget(client, budget['provisioned'])
        with pytest.raises(ValueError, match='conflicts'):
            import_backup(backup['directory'], digest=backup['sha256'], fence=fence,
                          model_credentials=conversion, model_selection=selected)
        assert read_key(vault, 'node-secret://budget', base + '/budget/v1') == 'different-current-key'
        assert json.loads((root / 'migration.json').read_text())['phase'] == 'importing'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['codex', 'gemini-cli'])
async def test_budget_review_cannot_silently_rebill_legacy_oauth_models(legacy, tmp_path, provider):
    _, budget = capture_budget(legacy, tmp_path, 'https://proxy.example')
    source = Path(legacy['home_memory']) / 'chat-b.json'
    value = json.loads(source.read_text())
    value['extra_data']['team_template']['agents'][0]['model'] = provider + '/model'
    source.write_text(json.dumps(value))
    with backed_up(legacy, tmp_path) as (root, fence, backup):
        selected = selections(legacy, backup, fence, budget['choice'], model_ref('mac', 'example:8b'))
        with pytest.raises(ValueError, match='separate billing'):
            await selected.review_budget(None, budget['provisioned'])
        assert not root.exists()
