"""Production desktop -> authenticated WebSocket NATS -> independent platform.

Hub discovery/model-directory and Controller join are fixtures. Native mode
uses the real Fleet binary, registry, placement and supervised App processes.
Agent imports and browser chunks are forbidden throughout the gate.
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
import psutil

from test_dependency_owner_host import credentials, jetstream_credentials


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize('native_fleet', [False, True], ids=['local-services', 'native-fleet'])
def test_production_desktop_without_agent(tmp_path, native_fleet):
    script = os.environ.get('PANTHEON_TEST_PLATFORM_DESKTOP')
    if not script:
        pytest.skip('Supply production desktop build and PANTHEON_TEST_PLATFORM_DESKTOP browser script')
    fleet = os.environ.get('PANTHEON_TEST_DESKTOP_FLEET')
    if native_fleet and not fleet:
        pytest.skip('Supply a freshly built Fleet binary in PANTHEON_TEST_DESKTOP_FLEET')
    binary = shutil.which('nats-server') or str(Path(sys.executable).parent/'nats-server')
    assert Path(binary).is_file(), 'nats-server required'
    port, ws_port = free_port(), free_port()
    subjects = ['service.desktop-gate.>', '_INBOX.>', '$JS.API.>']
    if native_fleet:
        subjects += ['fleet.desktop-gate.>', '_INBOX_desktop-gate.>',
                     '$KV.FLEET_desktop-gate_NODES.>', '$JS.ACK.>']
    system_config = ''
    if native_fleet:
        op, account, accjwt, creds, system, sysjwt = jetstream_credentials(subjects)
        system_config = f', {system}: "{sysjwt}"'
    else:
        op, account, accjwt, creds = credentials(subjects)
    jwt = re.search(r'BEGIN NATS USER JWT-----\n(.*?)\n', creds)[1]
    seed = re.search(r'BEGIN USER NKEY SEED-----\n(.*?)\n', creds)[1]
    (tmp_path/'operator.jwt').write_text(op)
    (tmp_path/'nats.conf').write_text(
        f'host: 127.0.0.1\nport: {port}\noperator: "{tmp_path / "operator.jwt"}"\n'
        f'resolver: MEMORY\nresolver_preload: {{ {account}: "{accjwt}"{system_config} }}\n'
        f'websocket {{ host: 127.0.0.1; port: {ws_port}; no_tls: true }}\n'
        + (f'jetstream {{ store_dir: "{tmp_path / "jetstream"}" }}\n' if native_fleet else ''))
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
            if path == '/controller/join':
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                assert body.get('key') == 'fixture-owner'
            values = {
                '/api/chatroom': info,
                '/api/chatroom/pod-status': dict(has_assignment=True, nats_healthy=True, phase='running', chatroom_id=info['service_id']),
                '/api/auth/nats-credentials': dict(jwt=jwt, seed=seed, nats_url=info['nats_url']),
                '/api/model-services': {'deployments': [row]},
                '/api/model-services/routes': {'routes': []},
                '/api/model-services/modal-gpu': {'services': []},
                '/api/auth/prewarm': {'success': True},
                '/api/chatroom/heartbeat': {'success': True},
                '/api/billing/pricing': {},
                '/controller/join': dict(fleet_id='desktop-gate', nats_url=f'nats://127.0.0.1:{port}', creds=creds),
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
    if native_fleet:
        state = tmp_path/'fleet-state'
        guard = tmp_path/'import-guard'
        guard.mkdir()
        (guard/'sitecustomize.py').write_text('from platform_no_agent import install\ninstall()\n')
        env.update(PANTHEON_TEST_DESKTOP_FLEET=fleet,
                   FLEET_CONTROLLER_URL=f'http://127.0.0.1:{hub.server_port}/controller',
                   PANTHEON_USER_SEED='desktop-gate', PANTHEON_FLEET_STATE_DIR=str(state),
                   PYTHONPATH=os.pathsep.join((str(guard), str(root/'tests'), str(root))),
                   PANTHEON_TEST_IMPORT_AUDIT=str(tmp_path/'import-audit'))
    procs = []
    logpath = tmp_path/'platform.log'
    with logpath.open('w') as log:
        try:
            procs.append(subprocess.Popen([binary, '-c', str(tmp_path/'nats.conf')], stdout=log, stderr=log))
            if native_fleet:
                procs.append(subprocess.Popen([fleet, 'up', '--controller', env['FLEET_CONTROLLER_URL'],
                    '--key', 'fixture-owner', '--state-dir', str(state), '--workdir', str(workspace),
                    '--name', 'Gate workspace',
                    '--kind', 'sandbox', '--caps', 'proc,fs:workspace,display,net',
                    '--no-dataplane', '--no-files', '--no-capture-setup', '--no-auto-update'],
                    cwd=workspace, env=env, stdout=log, stderr=log))
                deadline = time.monotonic() + 30
                while not (state/'runtime.json').exists():
                    assert all(p.poll() is None for p in procs), logpath.read_text()[-12000:]
                    assert time.monotonic() < deadline, logpath.read_text()[-12000:]
                    time.sleep(.1)
                row.update(node_id=json.loads((state/'runtime.json').read_text())['node_id'],
                           node_name='Gate workspace')
            procs.append(subprocess.Popen([sys.executable, str(root/'tests/platform_desktop_host.py')],
                cwd=workspace, env=env, stdout=log, stderr=log))
            deadline = time.monotonic() + 20
            while not (workspace/'services.json').exists():
                assert all(p.poll() is None for p in procs), logpath.read_text()[-12000:]
                assert time.monotonic() < deadline, logpath.read_text()[-12000:]
                time.sleep(.1)
            browser_env = {**os.environ, 'PLATFORM_DESKTOP_HUB': env['PANTHEON_HUB_URL'],
                           'PLATFORM_DESKTOP_NATIVE_FLEET': '1' if native_fleet else ''}
            result = subprocess.run(['node', script], env=browser_env, text=True, capture_output=True, timeout=150)
            assert result.returncode == 0, result.stdout + result.stderr + '\nHOST:\n' + logpath.read_text()[-18000:]
            assert ('GET', '/api/model-services') in requests
            assert requests.count(('GET', '/api/model-services')) >= 2
            assert (workspace/'New Folder').is_dir()
            assert 'Desktop platform imported Agent:' not in logpath.read_text()
            assert all(p.poll() is None for p in procs), logpath.read_text()[-12000:]
            if native_fleet:
                children = psutil.Process(procs[1].pid).children(recursive=True)
                apps = {}
                for child in children:
                    args = child.cmdline()
                    if '--app-id' in args:
                        apps[args[args.index('--app-id')+1]] = child.pid
                assert {'desktop', 'file-manager', 'file-transfer'} <= apps.keys(), apps
                audit = (tmp_path/'import-audit').read_text()
                assert 'imported Agent:' not in audit, audit
                assert all(f'{pid} guard installed' in audit for pid in apps.values()), audit
                assert (workspace/'terminal-proof.txt').read_text() == 'terminal-without-agent\n'
        finally:
            descendants = {}
            for proc in procs:
                if proc.poll() is None:
                    for child in psutil.Process(proc.pid).children(recursive=True):
                        descendants[child.pid] = child
            for proc in reversed(procs):
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            _, remaining = psutil.wait_procs(list(descendants.values()), timeout=3)
            for child in remaining:
                child.kill()
            hub.shutdown()
            hub.server_close()
            thread.join(timeout=3)
            assert not remaining, f'Fleet shutdown left child processes: {[p.pid for p in remaining]}'
