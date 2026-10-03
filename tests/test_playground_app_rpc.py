"""Real authenticated App RPC and provider HTTP without importing Agent code."""
import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import uuid

import pytest


@pytest.fixture
def model_http():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, self.headers.get('Authorization'), body))
            response = [
                {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1,
                 'model': body['model'], 'choices': [{'index': 0,
                    'delta': {'role': 'assistant', 'content': 'APP_RESPONSE'}, 'finish_reason': None}]},
                {'id': 'test', 'object': 'chat.completion.chunk', 'created': 1,
                 'model': body['model'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}},
            ]
            payload = ''.join('data: ' + json.dumps(row) + '\n\n' for row in response) + 'data: [DONE]\n\n'
            payload = payload.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_apphost_serves_playground_without_agent(tmp_path, monkeypatch, model_http):
    binary = Path(sys.executable).parent / 'nats-server'
    binary = str(binary) if binary.is_file() else shutil.which('nats-server')
    if not binary:
        pytest.skip('local nats-server required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token, seed = uuid.uuid4().hex, uuid.uuid4().hex
    url = f'nats://127.0.0.1:{port}'
    base, calls = model_http
    (tmp_path / '.env').write_text(
        f'OPENAI_API_BASE={base}/own/v1\nOPENAI_API_KEY=fixture-own\n'
        f'PANTHEON_PLATFORM_PROXY_BASE={base}/budget/v1\n'
        'PANTHEON_PLATFORM_PROXY_KEY=fixture-budget\n')
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('FLEET_', 'PANTHEON_', 'NATS_', 'PLATFORM_MODEL_',
                                'OPENAI_', 'LLM_', 'CUSTOM_OPENAI_'))}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(root), NATS_SERVERS=url,
               NATS_TOKEN=token, NATS_ENABLE_JETSTREAM='false', PANTHEON_REMOTE_BACKEND='nats')
    monkeypatch.setenv('NATS_ENABLE_JETSTREAM', 'false')
    code = '''
import importlib.abc, runpy, sys
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == p or fullname.startswith(p + '.') for p in
               ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team',
                'pantheon.factory', 'pantheon.internal.learning_system', 'pantheon.internal.memory')):
            raise AssertionError('Playground imported Agent: ' + fullname)
sys.meta_path.insert(0, NoAgent())
runpy.run_module('pantheon.apphost', run_name='__main__')
'''
    processes = []
    logpath = tmp_path / 'playground.log'
    with logpath.open('w') as log:
        try:
            processes.append(subprocess.Popen([binary, '-a', '127.0.0.1', '-p', str(port),
                '--user', 'agent', '--pass', token], stdout=log, stderr=log))
            processes.append(subprocess.Popen([sys.executable, '-c', code, '--app-id', 'llm-playground',
                '--workdir', str(tmp_path), '--id-hash', seed],
                cwd=tmp_path, env=env, stdout=log, stderr=log))

            async def check():
                from nats.errors import NoRespondersError
                from pantheon.remote.backend.nats import NATSBackend
                from pantheon.utils.misc import generate_service_id
                backend = NATSBackend([url], user='agent', password=token,
                                      max_reconnect_attempts=0, connect_timeout=.3)
                try:
                    deadline = asyncio.get_running_loop().time() + 20
                    while True:
                        assert all(p.poll() is None for p in processes), logpath.read_text()
                        try:
                            service = await asyncio.wait_for(
                                backend.connect(generate_service_id(seed), timeout=2), .5)
                            await service.invoke('llm_playground_status', {'request_id': 'readiness'})
                            break
                        except (TimeoutError, ConnectionError, OSError, NoRespondersError):
                            assert asyncio.get_running_loop().time() < deadline, logpath.read_text()
                            await asyncio.sleep(.1)
                    for source, model in [('openai', 'openai/example'), ('platform', 'openrouter/acme/example')]:
                        result = await asyncio.wait_for(service.invoke('llm_playground_run', {
                            'request_id': 'wire-' + source, 'source': source, 'model': model,
                            'prompt': 'test', 'system': 'be brief', 'max_tokens': 8}), 15)
                        assert result['success'], result
                        assert result['output'] == 'APP_RESPONSE', result
                        assert result['usage']['prompt_tokens'] == 2, result
                        assert 'fixture-' not in json.dumps(result)
                    assert len(calls) == 2
                    assert calls[0][0:2] == ('/own/v1/chat/completions', 'Bearer fixture-own')
                    assert calls[0][2]['model'] == 'example'
                    assert calls[1][0:2] == ('/budget/v1/chat/completions', 'Bearer fixture-budget')
                    assert calls[1][2]['model'] == 'openrouter/acme/example'
                    for _, _, body in calls:
                        assert body['messages'] == [{'role': 'system', 'content': 'be brief'},
                                                    {'role': 'user', 'content': 'test'}]
                        assert 'tools' not in body
                    await service.invoke('llm_playground_cancel', {'request_id': 'never-send-this'})
                    with pytest.raises(Exception, match='cancelled'):
                        await service.invoke('llm_playground_run', {'request_id': 'never-send-this',
                            'source': 'openai', 'model': 'openai/example', 'prompt': 'test'})
                    assert len(calls) == 2
                    asset = await service.invoke('llm_playground_upload',
                        {'data': base64.b64encode(b'fixture audio').decode(), 'name': 'input.wav'})
                    data = await service.invoke('llm_playground_media', {'asset_id': asset['id']})
                    assert base64.b64decode(data['data']) == b'fixture audio'
                    assert data['done']
                    assert (await service.invoke('_ping', {}))['activity_scope'] == 'app'
                    with pytest.raises(Exception, match='not found'):
                        await service.invoke('_restart_in_place', {})
                finally:
                    if backend._nc:
                        await backend._nc.close()
            asyncio.run(check())
            processes[-1].send_signal(signal.SIGINT)
            assert processes[-1].wait(timeout=10) in (0, 130), logpath.read_text()
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
