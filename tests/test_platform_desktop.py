"""Production desktop -> authenticated WebSocket NATS -> independent platform.

Hub discovery/model-directory responses and App placement are local fixtures.
Platform RPC, Desktop document/presence, Files and UI are production code. Agent
implementation imports and browser chunks are forbidden throughout the gate.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

from test_dependency_owner_host import credentials


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_production_desktop_without_agent(tmp_path):
    script = os.environ.get('PANTHEON_TEST_PLATFORM_DESKTOP')
    if not script:
        pytest.skip('Supply production desktop build and PANTHEON_TEST_PLATFORM_DESKTOP browser script')
    binary = shutil.which('nats-server') or str(Path(sys.executable).parent/'nats-server')
    assert Path(binary).is_file(), 'nats-server required'
    port, ws_port = free_port(), free_port()
    op, account, accjwt, creds = credentials(['service.desktop-gate.>', '_INBOX.>', '$JS.API.>'])
    jwt = re.search(r'BEGIN NATS USER JWT-----\n(.*?)\n', creds)[1]
    seed = re.search(r'BEGIN USER NKEY SEED-----\n(.*?)\n', creds)[1]
    (tmp_path/'operator.jwt').write_text(op)
    (tmp_path/'nats.conf').write_text(
        f'host: 127.0.0.1\nport: {port}\noperator: "{tmp_path / "operator.jwt"}"\n'
        f'resolver: MEMORY\nresolver_preload: {{ {account}: "{accjwt}" }}\n'
        f'websocket {{ host: 127.0.0.1; port: {ws_port}; no_tls: true }}\n')
    from pantheon.utils.misc import generate_service_id
    info = dict(service_id='absent-legacy-agent', platform_service_id=generate_service_id('platform-desktop-gate'),
                nats_url=f'ws://127.0.0.1:{ws_port}', nats_jwt=jwt, nats_seed=seed,
                nats_subject_prefix='service.desktop-gate', browser_stream_base='http://127.0.0.1:1')
    requests = []
    model = dict(id='gate-model', name='Independent platform model', operations=['text'], context=8192, tools=True)
    row = dict(deployment_id='desktop-gate', name='Shared model provider', node_id='fixture',
               node_name='Gate node', engine='api', mode='attached', state='ready', revision=1, models=[model])

    class Hub(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split('?')[0]
            requests.append((self.command, path))
            values = {
                '/api/chatroom': info,
                '/api/chatroom/pod-status': dict(has_assignment=True, nats_healthy=True, phase='running', chatroom_id=info['service_id']),
                '/api/auth/nats-credentials': dict(jwt=jwt, seed=seed, nats_url=info['nats_url']),
                '/api/model-services': {'deployments': [row]},
                '/api/model-services/routes': {'routes': []},
                '/api/auth/prewarm': {'success': True},
                '/api/chatroom/heartbeat': {'success': True},
                '/api/billing/pricing': {},
            }
            if path.startswith('/api/model-services'):
                assert self.headers.get('Authorization') == 'Bearer fixture-owner'
            raw = json.dumps(values.get(path, {'detail': 'Fixture does not implement ' + path})).encode()
            self.send_response(200 if path in values else 404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_POST = do_GET

    hub = ThreadingHTTPServer(('127.0.0.1', 0), Hub)
    thread = threading.Thread(target=hub.serve_forever, daemon=True)
    thread.start()
    workspace = tmp_path/'workspace'
    workspace.mkdir()
    (workspace/'independent-platform.txt').write_text('Files remain available without Agent.\n')
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('FLEET_', 'PANTHEON_', 'NATS_'))}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(root), NATS_SERVERS=f'nats://127.0.0.1:{port}',
               NATS_JWT=jwt, NATS_SEED=seed, NATS_SUBJECT_PREFIX=info['nats_subject_prefix'],
               NATS_ENABLE_JETSTREAM='false', PANTHEON_REMOTE_BACKEND='nats',
               PANTHEON_HUB_URL=f'http://127.0.0.1:{hub.server_port}', FLEET_KEY='fixture-owner')
    procs = []
    logpath = tmp_path/'platform.log'
    with logpath.open('w') as log:
        try:
            procs.append(subprocess.Popen([binary, '-c', str(tmp_path/'nats.conf')], stdout=log, stderr=log))
            procs.append(subprocess.Popen([sys.executable, str(root/'tests/platform_desktop_host.py')],
                cwd=workspace, env=env, stdout=log, stderr=log))
            deadline = time.monotonic() + 20
            while not (workspace/'services.json').exists():
                assert all(p.poll() is None for p in procs), logpath.read_text()[-12000:]
                assert time.monotonic() < deadline, logpath.read_text()[-12000:]
                time.sleep(.1)
            browser_env = {**os.environ, 'PLATFORM_DESKTOP_HUB': env['PANTHEON_HUB_URL']}
            result = subprocess.run(['node', script], env=browser_env, text=True, capture_output=True, timeout=150)
            assert result.returncode == 0, result.stdout + result.stderr + '\nHOST:\n' + logpath.read_text()[-18000:]
            assert ('GET', '/api/model-services') in requests
            assert requests.count(('GET', '/api/model-services')) >= 2
            assert (workspace/'New Folder').is_dir()
            assert 'Desktop platform imported Agent:' not in logpath.read_text()
            assert all(p.poll() is None for p in procs), logpath.read_text()[-12000:]
        finally:
            for proc in reversed(procs):
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            hub.shutdown()
            hub.server_close()
            thread.join(timeout=3)
