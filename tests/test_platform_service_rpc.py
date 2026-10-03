"""A real authenticated local bus can serve platform RPCs without Agent code."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import uuid

import pytest


@pytest.fixture
def local_store_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == '/api/store/packages/platform-test/download'
            data = json.dumps({'type': 'agent', 'name': 'platform-test',
                               'version': '1', 'content': '# Test recipe'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("with_child", [False, True])
def test_platform_process_without_agent(tmp_path, monkeypatch, with_child, local_store_server):
    server = Path(sys.executable).parent / 'nats-server'
    binary = str(server) if server.is_file() else shutil.which('nats-server')
    if not binary:
        pytest.skip('local nats-server binary required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = uuid.uuid4().hex
    url = f'nats://127.0.0.1:{port}'
    seed = 'platform-test-' + uuid.uuid4().hex
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('FLEET_', 'PANTHEON_', 'NATS_'))}
    # The RPC fixture owns its credentials; do not inherit empty or real
    # developer keys which take precedence over its project .env.
    for key in ('OPENAI_API_KEY', 'CUSTOM_OPENAI_API_KEY'):
        env.pop(key, None)
    env.update(HOME=str(tmp_path), PYTHONPATH=str(root), NATS_SERVERS=url,
               NATS_TOKEN=token, NATS_ENABLE_JETSTREAM='false',
               PANTHEON_REMOTE_BACKEND='nats', PANTHEON_HUB_URL=local_store_server)
    monkeypatch.setenv('NATS_ENABLE_JETSTREAM', 'false')
    code = '''
import importlib.abc, runpy, sys
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == p or fullname.startswith(p + '.') for p in
               ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team',
                'pantheon.factory', 'pantheon.internal.learning_system', 'pantheon.internal.memory')):
            raise AssertionError('Platform imported Agent: ' + fullname)
sys.meta_path.insert(0, NoAgent())
from pantheon.utils.oauth import codex
codex.CALLBACK_PORT = 0
runpy.run_module('pantheon.platform', run_name='__main__')
'''
    if with_child:
        child_pid = tmp_path / 'agent-child.pid'
        child_code = "import os,time; from pathlib import Path; Path('agent-child.pid').write_text(str(os.getpid())); time.sleep(60)"
        code = code.replace("runpy.run_module('pantheon.platform', run_name='__main__')", """
import asyncio
from pantheon.platform.service import PlatformService
from pantheon.platform.bootstrap import serve
asyncio.run(serve(PlatformService(id_hash=sys.argv[2]), log_level='WARNING',
    agent_command=[sys.executable, '-c', %r]))
""" % child_code)
    procs = []
    logpath = tmp_path / 'platform.log'
    with logpath.open('w') as log:
        try:
            procs.append(subprocess.Popen([binary, '-a', '127.0.0.1', '-p', str(port),
                '--user', 'agent', '--pass', token], stdout=log, stderr=log))
            procs.append(subprocess.Popen([sys.executable, '-c', code, '--id-hash', seed,
                '--log-level', 'WARNING'], cwd=tmp_path, env=env, stdout=log, stderr=log))

            async def verify():
                from nats.errors import NoRespondersError
                from pantheon.remote.backend.nats import NATSBackend
                from pantheon.utils.misc import generate_service_id
                backend = NATSBackend([url], user='agent', password=token,
                                      max_reconnect_attempts=0, connect_timeout=.3)
                shutdown_waiter = None
                try:
                    deadline = asyncio.get_running_loop().time() + 20
                    while True:
                        assert all(p.poll() is None for p in procs), logpath.read_text()
                        try:
                            service = await asyncio.wait_for(
                                backend.connect(generate_service_id(seed), timeout=2), .5)
                            info = await service.invoke('platform_info', {})
                            break
                        except (TimeoutError, ConnectionError, OSError, NoRespondersError):
                            if asyncio.get_running_loop().time() >= deadline:
                                pytest.fail(logpath.read_text())
                            await asyncio.sleep(.1)
                    installed = await service.invoke('install_store_package', {'package_id': 'platform-test'})
                    assert installed['success'], installed
                    records = await service.invoke('get_installed_store_packages', {})
                    assert records['installs']['platform-test']['version'] == '1'
                    skills = await service.invoke('get_local_skills', {})
                    assert skills['success'] and skills['skills'], skills
                    removed = await service.invoke('uninstall_store_package', {'package_id': 'platform-test'})
                    assert removed['success'], removed
                    assert not (tmp_path / '.pantheon/agents/platform-test.md').exists()
                    if with_child:
                        assert child_pid.exists(), logpath.read_text()
                        os.kill(int(child_pid.read_text()), signal.SIGTERM)
                        deadline = asyncio.get_running_loop().time() + 5
                        while 'Agent exited' not in logpath.read_text():
                            assert asyncio.get_running_loop().time() < deadline, logpath.read_text()
                            await asyncio.sleep(.05)
                        assert procs[-1].poll() is None
                        info = await service.invoke('platform_info', {})
                    with pytest.raises(Exception, match='not found'):
                        await service.invoke('_restart_in_place', {})
                    pong = await service.invoke('_ping', {})
                    assert pong['activity_scope'] == 'platform'
                    assert 'active_threads' not in pong
                    assert info['api_version'] == 1
                    assert 'chat' not in info['methods']
                    assert 'call_app_service' in info['methods']
                    apps = await service.invoke('get_toolsets', {})
                    assert apps['success']
                    assert any(a['app_id'] == 'shell' for a in apps['services'])
                    project = tmp_path / 'rpc-project'
                    project.mkdir()
                    result = await service.invoke('register_project',
                        {'path': str(project), 'name': 'Independent project'})
                    assert result['success']
                    result = await service.invoke('set_active_project', {'path': str(project)})
                    assert result['project']['name'] == 'Independent project'
                    result = await service.invoke('get_active_project', {})
                    assert result['active']['path'] == str(project)
                    assert result['home']['path'] == str(tmp_path)
                    assert not (project / '.pantheon').exists()
                    (project / '.env').write_text('OPENAI_API_KEY=rpc-test-secret\n')
                    result = await service.invoke('saved_models', {'saved_models': {'openai': ['openai/rpc-model']}})
                    assert result['saved_models']['openai'] == ['rpc-model']
                    result = await service.invoke('saved_models', {})
                    assert result['saved_models']['openai'] == ['rpc-model']
                    result = await service.invoke('check_api_keys', {})
                    assert result['keys']['OPENAI_API_KEY']['configured']
                    assert 'rpc-test-secret' not in json.dumps(result)
                    result = await service.invoke('get_project_settings', {})
                    assert result['project']['models']['saved_models']['openai'] == ['rpc-model']
                    login = await service.invoke('oauth_start', {'provider': 'codex'})
                    assert login['success'], login
                    waiting = asyncio.create_task(service.invoke('oauth_wait', {
                        'session_id': login['session_id'], 'provider': 'codex', 'timeout_seconds': 300}))
                    await asyncio.sleep(.05)
                    assert (await asyncio.wait_for(service.invoke('platform_info', {}), 1))['api_version'] == 1
                    cancelled = await service.invoke('oauth_cancel', {'session_id': login['session_id']})
                    assert cancelled['success']
                    assert not (await asyncio.wait_for(waiting, 2))['success']
                    result = await service.invoke('fleet_app_lifecycle', {'node_id': 'absent'})
                    assert result == {'success': False, 'error': 'Fleet is not connected'}
                    # Leave a real accepted login wait in the worker when its
                    # client disappears and SIGTERM arrives. Shutdown must not
                    # spend 300 seconds draining this RPC or orphan its server.
                    login = await service.invoke('oauth_start', {})
                    shutdown_waiter = asyncio.create_task(service.invoke('oauth_wait', {
                        'session_id': login['session_id'], 'timeout_seconds': 300}))
                    await asyncio.sleep(.1)
                finally:
                    if shutdown_waiter is not None:
                        shutdown_waiter.cancel()
                        await asyncio.gather(shutdown_waiter, return_exceptions=True)
                    if backend._nc is not None:
                        await backend._nc.close()
            asyncio.run(verify())
        finally:
            for process in reversed(procs):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            assert procs[-1].returncode == 0, logpath.read_text()
