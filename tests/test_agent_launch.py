"""Production composition from the same prepared snapshot used by other Apps."""
import json
from pathlib import Path

import pytest

from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.chatroom.app_models import AppModels
from test_agent_application import PLUGIN_KEYS, TEMPLATE
from test_agent_model_scope import endpoint as model_endpoint


def prepared(root, url):
    return {
        'protocol': 1, 'owner': 'owner', 'node_id': 'node', 'instance_id': 'agent-app',
        'revision': 'a' * 64, 'generation': 1, 'component': 'backend',
        'credentials': {
            'model': {'endpoint': url + '/byok/v1', 'key': 'process-fixture'},
            'allocator': {'endpoint': 'https://allocator.example/rpc', 'key': 'c' * 64},
        },
        'values': {'agent': {
            'protocol': 1, 'namespace': 'process-app',
            'projects': [{'id': 'shared', 'name': 'Shared', 'path': str(root / 'workspace')}],
            'active_project': 'shared', 'default_project': 'shared',
            'settings': {**{key: {'enabled': False} for key in PLUGIN_KEYS},
                         'default_template_auto_update': False},
            'models': {'providers': {'openai': 'model'}},
            'dependencies': {'allocator': 'allocator', 'profiles': {'toolsets': {}, 'mcp_servers': {}}},
        }},
    }


def snapshot(value):
    return RuntimeConfiguration(values=value['values'], credentials={
        name: RuntimeCredential(**credential) for name, credential in value['credentials'].items()},
        **{key: value[key] for key in ('owner', 'node_id', 'instance_id', 'revision', 'generation', 'component')})


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [False, True], ids=['byok', 'platform-budget'])
async def test_configured_application_uses_model_binding_and_no_ambient_settings(tmp_path, model_endpoint, monkeypatch, budget):
    monkeypatch.setenv('OPENAI_API_KEY', 'unrelated-key')
    monkeypatch.setenv('LLM_FORCE_PROXY', 'true')
    def forbidden(*args, **kwargs):
        raise AssertionError('ambient resolver used')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.setattr('pantheon.agent._resolve_model_tag', forbidden)
    value = prepared(tmp_path, model_endpoint.url)
    if budget:
        value['values']['agent']['models']['platform_budget'] = 'budget'
        value['credentials']['budget'] = {'endpoint': model_endpoint.url + '/budget/v1', 'key': 'budget-fixture'}
    app = ConfiguredAgentApplication('agent', data_dir=tmp_path / 'data', configuration=snapshot(value))
    try:
        await app.run_setup()
        listing = await app.list_available_models()
        assert listing['current_provider'] == ('openrouter' if budget else 'openai')
        template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [], 'model': 'normal'}]}
        created = await app.create_chat('Configured', project_name='Shared', template_obj=template)
        assert created['success'], created
        agent = (await app.get_team_for_chat(created['chat_id'])).team_agents[0]
        assert agent.model_scope is app.app_models.scope
        assert (await agent.run('Reply once')).content == 'scoped reply'
        assert app._validate_model_provider('codex/unbound')[0] is False
        assert model_endpoint.requests[0][1]['Authorization'] == ('Bearer budget-fixture' if budget else 'Bearer process-fixture')
        assert model_endpoint.requests[0][0].startswith('/budget/v1/' if budget else '/byok/v1/')
    finally:
        await app.cleanup()


def test_private_saved_settings_override_deployment_defaults_and_env_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv('BORROWED_SECRET', 'ambient-secret')
    config = tmp_path / 'configuration' / '.pantheon'
    config.mkdir(parents=True)
    (config / 'settings.json').write_text(json.dumps({'models': {'provider_priority': ['gemini']}}))
    (config.parent / '.env').write_text('EXPANDED=${BORROWED_SECRET}\nOWN=${OPENAI_API_KEY}\n')
    models = AppModels(tmp_path, defaults={'models': {'provider_priority': ['anthropic']}},
                       config={'providers': {'openai': 'key'}}, credentials={
                           'key': RuntimeCredential('https://models.example/v1', 'own-key')})
    assert models.settings.get('models.provider_priority') == ['gemini']
    assert models.settings.get_env('EXPANDED') == ''
    assert models.settings.get_env('OWN') == 'own-key'
    models.settings.reload(env_override=False)
    assert models.settings.get_env('EXPANDED') == ''
    assert models.settings.get('models.provider_priority') == ['gemini']


def test_oauth_file_binding_never_imports_another_apps_login(tmp_path, monkeypatch):
    from pantheon.utils.oauth import codex, gemini
    ambient = tmp_path / 'ambient.json'
    ambient.write_text(json.dumps({'tokens': {'refresh_token': 'unrelated-refresh-token'}}))
    monkeypatch.setattr(codex, 'AUTH_FILE', ambient)
    monkeypatch.setattr(gemini, 'AUTH_FILE', ambient)
    config = {'oauth': ['codex', 'gemini-cli']}
    a = AppModels(tmp_path/'a', defaults={}, config=config, credentials={})
    b = AppModels(tmp_path/'b', defaults={}, config=config, credentials={})
    assert a.selector.list_available_models()['available_providers'] == []
    path = tmp_path/'a'/'oauth'/'codex.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'tokens': {'refresh_token': 'app-refresh-token'}}))
    assert a.selector.detect_available_provider() == 'codex'
    assert b.selector.detect_available_provider() is None
    path.unlink()
    assert a.selector.detect_available_provider() is None


@pytest.mark.asyncio
async def test_ollama_discovery_and_inference_base_have_same_explicit_origin(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"models":[{"name":"private-model"}]}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setenv('HTTP_PROXY', 'http://wrong.invalid')
    try:
        models = AppModels(tmp_path, defaults={}, config={'ollama': base+'/v1'}, credentials={})
        await models.refresh()
        assert models.resolve('normal') == ['ollama/private-model']
        assert models.validate('ollama/private-model') == (True, '')
        assert models.settings.get_api_key('OLLAMA_API_BASE') == base+'/v1'
        await models.refresh()
        assert calls == ['/api/tags']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('mutation', ['identity', 'credential', 'schema', 'profile', 'raw-key', 'default-profile'])
def test_invalid_configuration_does_not_create_app_data(tmp_path, mutation):
    value = prepared(tmp_path, 'http://127.0.0.1:12345')
    spec = value['values']['agent']
    if mutation == 'identity':
        value['node_id'] = ''
    elif mutation == 'credential':
        spec['models']['providers']['openai'] = 'missing'
    elif mutation == 'schema':
        spec['protocol'] = True
    elif mutation == 'profile':
        spec['dependencies']['profiles']['toolsets']['shell'] = {'alias': 'shell', 'functions': []}
    elif mutation == 'default-profile':
        spec['dependencies']['defaults'] = {'toolsets': [], 'mcp_servers': ['unapproved']}
    else:
        spec['settings']['api_keys'] = {'OPENAI_API_KEY': 'value-secret'}
    with pytest.raises(ValueError) as error:
        ConfiguredAgentApplication('agent', data_dir=tmp_path / 'data', configuration=snapshot(value))
    assert 'value-secret' not in str(error.value)
    assert not (tmp_path / 'data').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('use_defaults', [False, True], ids=['recipe-tools', 'deployment-tools'])
async def test_launch_binds_each_logical_agent_and_verifies_outputs_at_its_files_service(tmp_path, monkeypatch, use_defaults):
    """Real launch/factory/provisioner/providers; transport authority is a fixture."""
    import hashlib
    import time
    from pantheon.apps.dependency_client import DependencyClient
    value = prepared(tmp_path, 'http://127.0.0.1:12345')
    spec = value['values']['agent']
    function = lambda name, arg: {'name': name, 'description': name,
        'parameters': {'type': 'object', 'properties': {arg: {'type': 'string'}},
                       'required': [arg], 'additionalProperties': False}}
    spec['dependencies']['profiles']['toolsets'] = {
        'shell': {'alias': 'shell', 'functions': [function('execute', 'command')]},
        'file_manager': {'alias': 'files', 'functions': [function('stat_path', 'file_path')]},
    }
    if use_defaults:
        spec['dependencies']['defaults'] = {'toolsets': ['shell', 'file_manager'], 'mcp_servers': []}
    spec['settings']['task_system']['enabled'] = True
    consumer = {key: value[key] for key in ('node_id', 'instance_id', 'revision', 'generation')}
    owners, requests, bearer_owner = set(), [], {}

    def invoke(client, method, args, **kwargs):
        assert client.credential.key != 'ambient-owner-key'
        requests.append((method, args))
        if method == 'bind_dependencies':
            assert set(args) == {'owner_ref', 'operation_id', 'aliases'}
            assert set(args['aliases']) == {'shell', 'files'}
            owners.add(args['owner_ref'])
            grants = {}
            for alias in args['aliases']:
                provider = dict(node_id='workspace', instance_id=alias, revision='b'*64,
                                generation=2, component='backend', port='http')
                token = hashlib.sha256((args['owner_ref']+alias).encode()).hexdigest()
                bearer_owner[token] = args['owner_ref']
                prefix = hashlib.sha256(f'{alias}:backend:http:2'.encode()).hexdigest()[:32]
                grants[alias] = {'endpoint': f'https://{prefix}.apps.test/rpc', 'access_token': token,
                    'grant_id': hashlib.sha256(token.encode()).hexdigest(), 'expires': int(time.time())+800,
                    'consumer': {**consumer, 'fleet_id': 'owner'},
                    'provider': {**provider, 'fleet_id': 'owner'}}
            result = {'protocol': 1, 'owner_ref': args['owner_ref'], 'operation_id': args['operation_id'],
                      'consumer': consumer, 'bindings': grants}
        elif method == 'execute':
            assert set(args) == {'command'}
            result = {'session': bearer_owner[client.credential.key]}
        else:
            assert method == 'stat_path' and set(args) == {'file_path'}
            result = {'success': True, 'exists': True, 'is_dir': False,
                      'node_id': 'workspace', 'path': '/shared/report.txt', 'store_path': 'report.txt'}
        return {'success': True, 'result': result}

    monkeypatch.setenv('FLEET_KEY', 'ambient-owner-key')
    monkeypatch.setattr(DependencyClient, 'invoke', invoke)
    app = ConfiguredAgentApplication('agent', data_dir=tmp_path/'data', configuration=snapshot(value))
    try:
        await app.run_setup()
        agents = []
        for number in range(2):
            template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0],
                'toolsets': [] if use_defaults else ['shell', 'file_manager']}]}
            chat = await app.create_chat(str(number), project_name='Shared', template_obj=template)
            assert chat['success'], chat
            agent = (await app.get_team_for_chat(chat['chat_id'])).team_agents[0]
            agents.append(agent)
            reply = await agent.call_tool('shell__execute', {'command': 'pwd'})
            assert reply['session'] == str(agent.id)
            context = {'chat_id': chat['chat_id'], 'project_root': str(tmp_path/'workspace')}
            metadata = await agent.call_tool('task__register_output', {'path': 'report.txt'}, context)
            assert metadata['success'], metadata
            assert metadata['source'] == {'node_id': 'workspace', 'path': '/shared/report.txt'}
            mismatch = await agent.call_tool('task__register_output', {
                'path': '/shared/report.txt', 'node_id': 'other-node'}, context)
            assert mismatch.get('code') == 'output_verification_unavailable'
        assert len(owners) == 2
        assert not (tmp_path/'data'/'report.txt').exists()
    finally:
        await app.cleanup()
    with pytest.raises(RuntimeError, match='closed'):
        await agents[0].providers['shell'].call_tool('execute', {'command': 'pwd'})


@pytest.mark.asyncio
async def test_default_team_reports_all_missing_bindings_before_allocating(tmp_path, model_endpoint):
    app = ConfiguredAgentApplication('agent', data_dir=tmp_path/'data',
        configuration=snapshot(prepared(tmp_path, model_endpoint.url)))
    try:
        await app.run_setup()
        created = await app.create_chat('Default', project_name='Shared')
        assert created['success'], created
        with pytest.raises(ValueError, match='Configure these App bindings') as failure:
            await app.get_team_for_chat(created['chat_id'])
        for name in ('file_manager', 'shell', 'integrated_notebook', 'web', 'desktop', 'evolution'):
            assert name in str(failure.value)
        assert 'execution configuration is invalid' not in str(failure.value)
        assert not model_endpoint.requests
    finally:
        await app.cleanup()
