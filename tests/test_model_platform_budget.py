"""Owner Hub acquisition -> real Fleet vault -> unchanged Model Service.

Hub/LiteLLM responses are controlled fixtures. No paid inference or user data.
"""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import httpx
import pytest

from pantheon.models.credentials import LocalModelCredentialVault
from pantheon.models.platform_budget import provision_platform_budget
from pantheon.models.client import model_ref
from test_agent_application import TEMPLATE
from test_agent_launch import prepared
from test_agent_native_process import native_process, request
from test_model_dependency import model_dependency, model_endpoint, tls_material
from test_model_services import connector_module, serve


OWNER = 'f_' + hashlib.sha256(b'budget-owner').hexdigest()[:16]
KEY = 'fixture-per-user-virtual-key'


@pytest.fixture
def vault(tmp_path):
    binary = os.environ.get('AGENT_MIGRATION_FLEET')
    if not binary:
        pytest.skip('Build Fleet and set AGENT_MIGRATION_FLEET for real node vault acceptance')
    state = tmp_path / 'node'; state.mkdir(mode=0o700)
    (state / 'node_id').write_text('budget-node\n')
    return LocalModelCredentialVault(binary, state_dir=state, owner=OWNER, node_id='budget-node')


def payload(endpoint='https://hub.test/litellm/v1', mode='direct'):
    return {'fleet_id': OWNER, 'api_base_url': endpoint, 'model_mode': mode, 'virtual_key': KEY}


def provision(vault, handler):
    return provision_platform_budget(hub='https://hub.test', token='owner-login', vault=vault,
                                     ref='node-secret://platform-budget', transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['direct', 'openrouter'])
async def test_budget_connector_uses_original_key_and_unmodified_model_id(vault, tmp_path, monkeypatch, mode):
    calls = []
    model = 'vendor/native-name' if mode == 'direct' else 'openrouter/vendor/model'
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            assert self.path == '/litellm/v1/models'
            assert self.headers['Authorization'] == 'Bearer ' + KEY
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': model}]}).encode())
        def do_POST(self):
            calls.append((self.path, self.headers['Authorization'],
                          json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            assert self.headers.get('X-Pantheon-App-Token') is None
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"budget reply"}}]}\n\ndata: [DONE]\n\n')
    def hub(request):
        assert str(request.url) == 'https://hub.test/api/users/me/llm-proxy'
        assert request.headers['authorization'] == 'Bearer owner-login'
        return httpx.Response(200, json=payload(upstream + '/litellm/v1', mode))
    monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
    monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps' / OWNER / 'model-credentials'))
    with serve(Proxy) as upstream:
        first = await provision(vault, hub)
        credential_files = list((vault.state_dir / 'apps' / OWNER / 'model-credentials').glob('*.json'))
        before = {p: p.read_bytes() for p in credential_files}
        assert before
        assert await provision(vault, hub) == first
        assert {p: p.read_bytes() for p in credential_files} == before
        assert KEY not in json.dumps(first) and 'owner-login' not in json.dumps(first)
        assert first['model_mode'] == mode
        connector = connector_module.Connector(tmp_path / 'connector')
        connector.configure(first['connector'])
        assert connector.discover()['models'] == [{'id': model}]
        with serve(connector_module.handler(connector)) as url:
            async with httpx.AsyncClient(timeout=10) as client:
                result = await client.post(url + '/v1/chat/completions',
                    headers={'X-Model-Request': 'budget-request', 'X-Model-Config': connector.revision},
                    json={'model': model, 'messages': [{'role': 'user', 'content': 'one test'}], 'stream': True})
            assert result.status_code == 200 and 'budget reply' in result.text and '[DONE]' in result.text
        assert KEY not in connector.path.read_text()
    assert calls == [('/litellm/v1/chat/completions', 'Bearer ' + KEY,
                      {'model': model, 'messages': [{'role': 'user', 'content': 'one test'}], 'stream': True})]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['owner', 'missing-owner', 'mode', 'key', 'endpoint', 'prefix',
                                     'redirect', 'expired', 'oversize', 'duplicate', 'transport'])
async def test_invalid_hub_response_never_writes_node_vault(vault, failure):
    requests = []
    def hub(request):
        requests.append(request)
        data = payload()
        if failure == 'owner': data['fleet_id'] = 'f_' + '0' * 16
        elif failure == 'missing-owner': del data['fleet_id']
        elif failure == 'mode': data['model_mode'] = 'unknown'
        elif failure == 'key': data['virtual_key'] = KEY + '\n'
        elif failure == 'endpoint': data['api_base_url'] = 'https://user:secret@hub.test/v1'
        elif failure == 'prefix': data['api_base_url'] = 'https://hub.test/litellm'
        elif failure == 'redirect': return httpx.Response(302, headers={'Location': 'https://elsewhere.test/' + KEY})
        elif failure == 'expired': return httpx.Response(401, text=KEY)
        elif failure == 'oversize': return httpx.Response(200, content=b'x' * 32769)
        elif failure == 'duplicate': return httpx.Response(200, text='{"virtual_key":"' + KEY + '","virtual_key":"other"}')
        elif failure == 'transport': raise httpx.ConnectError(KEY, request=request)
        return httpx.Response(200, json=data)
    with pytest.raises(ValueError) as error:
        await provision(vault, hub)
    assert KEY not in str(error.value) and 'owner-login' not in str(error.value)
    assert len(requests) == 1
    assert not (vault.state_dir / 'apps').exists()


@pytest.mark.asyncio
async def test_existing_reference_is_not_rotated_or_retargeted(vault):
    await provision(vault, lambda request: httpx.Response(200, json=payload()))
    path = next((vault.state_dir / 'apps' / OWNER / 'model-credentials').glob('*.json'))
    before = path.read_bytes()
    for changed in ({'virtual_key': 'rotated-key'}, {'api_base_url': 'https://new.test/v1'}):
        with pytest.raises(ValueError, match='conflicts'):
            await provision(vault, lambda request: httpx.Response(200, json={**payload(), **changed}))
        assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_changed_node_rejected_before_hub_request(vault):
    (vault.state_dir / 'node_id').write_text('other-node')
    def forbidden(request): raise AssertionError('Do not acquire credentials for another node')
    with pytest.raises(ValueError, match='another Fleet node'):
        await provision(vault, forbidden)


def test_owner_helper_does_not_import_agent():
    result = subprocess.run([sys.executable, '-c',
        'import sys; import pantheon.models.platform_budget; '
        'assert not any(k == "pantheon.chatroom" or k.startswith("pantheon.chatroom.") for k in sys.modules)'],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_cancelled_provision_waits_for_local_vault_mutation(vault, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    def ensure(*args):
        entered.set()
        assert finish.wait(5)
    monkeypatch.setattr(vault, 'ensure', ensure)
    task = asyncio.create_task(provision(vault, lambda _: httpx.Response(200, json=payload())))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(.02)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize('unsafe', ['public-token', 'symlink-token', 'existing-output'])
def test_cli_rejects_unsafe_files_before_network(vault, tmp_path, monkeypatch, unsafe, capsys):
    from pantheon.models import platform_budget
    token = tmp_path / 'login-token'
    token.write_text('private-owner-login')
    token.chmod(0o600)
    output = tmp_path / 'descriptor.json'
    if unsafe == 'public-token': token.chmod(0o644)
    elif unsafe == 'symlink-token':
        link = tmp_path / 'token-link'; link.symlink_to(token); token = link
    else: output.write_text('existing-descriptor')
    async def forbidden(**kwargs): raise AssertionError('Unsafe input reached Hub provisioning')
    monkeypatch.setattr(platform_budget, 'provision_platform_budget', forbidden)
    monkeypatch.setattr(sys, 'argv', ['platform-budget', '--hub', 'https://hub.test',
        '--token-file', str(token), '--fleet-executable', str(vault.executable),
        '--state-dir', str(vault.state_dir), '--owner', OWNER, '--node-id', vault.node_id,
        '--ref', 'node-secret://platform-budget', '--output', str(output)])
    with pytest.raises(SystemExit) as error:
        platform_budget.main()
    assert error.value.code == 1
    logs = capsys.readouterr()
    assert 'private-owner-login' not in logs.out + logs.err
    if unsafe == 'existing-output': assert output.read_text() == 'existing-descriptor'
    else: assert not output.exists()


@pytest.mark.asyncio
async def test_native_agent_uses_budget_connector_without_budget_credentials(
        vault, tmp_path, monkeypatch, model_dependency, model_endpoint):
    descriptor = await provision(vault, lambda _: httpx.Response(
        200, json=payload(model_endpoint.url + '/v1', 'openrouter')))
    monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(vault.executable))
    monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(vault.state_dir / 'apps' / OWNER / 'model-credentials'))
    connector = model_dependency.connector
    connector.configure(descriptor['connector'])
    model_dependency.deployment.update(engine='api', config_revision=connector.revision)
    config = prepared(tmp_path, model_endpoint.url)
    config['credentials']['models'] = model_dependency.credential
    config['values']['agent']['models'] = {'model_services': 'models'}
    assert KEY not in json.dumps(config) and 'owner-login' not in json.dumps(config)
    assert 'platform_budget' not in config['values']['agent']['models']
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [],
                                      'model': model_ref('mac', 'example:8b')}]}
    with native_process(tmp_path, model_endpoint.url, configuration=config) as (child, base):
        for _ in range(300):
            try:
                await request(base, '/health')
                break
            except OSError:
                assert child.poll() is None
                await asyncio.sleep(.05)
        else:
            pytest.fail('Agent did not start')
        async def rpc(method, **args):
            response = await request(base, '/rpc', {'method': method, 'args': args})
            assert response['success'], response
            return response['result']
        chat = await rpc('create_chat', chat_name='Platform budget through Model Service',
                         project_name='Shared', template_obj=template)
        result = await rpc('chat', chat_id=chat['chat_id'], message=[{'role': 'user', 'content': 'Reply once'}])
        assert result['success'], result
        history = await rpc('open_agent_history', chat_id=chat['chat_id'])
        data = await rpc('read_agent_history', chat_id=chat['chat_id'], snapshot_id=history['snapshot_id'], part=0)
        assert 'scoped reply' in data['json_fragment']
        assert (await request(base, '/_fleet/drain', {}))['safe_to_stop']
    assert child.returncode == 0
    calls = [r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']
    assert len(calls) == 1 and calls[0][1]['Authorization'] == 'Bearer ' + KEY
    assert calls[0][2]['model'] == 'example:8b'
    assert model_dependency.data_calls == ['/v1/chat/completions']
    assert KEY not in json.dumps(model_dependency.controls)
    assert KEY not in (tmp_path / 'process.log').read_text()
