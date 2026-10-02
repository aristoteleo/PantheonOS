"""A real authenticated local bus can serve platform RPCs without Agent code."""

import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

import pytest


def test_platform_process_without_agent(tmp_path, monkeypatch):
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
    env.update(HOME=str(tmp_path), PYTHONPATH=str(root), NATS_SERVERS=url,
               NATS_TOKEN=token, NATS_ENABLE_JETSTREAM='false',
               PANTHEON_REMOTE_BACKEND='nats')
    monkeypatch.setenv('NATS_ENABLE_JETSTREAM', 'false')
    code = '''
import importlib.abc, runpy, sys
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if any(fullname == p or fullname.startswith(p + '.') for p in
               ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team',
                'pantheon.factory', 'pantheon.internal.memory')):
            raise AssertionError('Platform imported Agent: ' + fullname)
sys.meta_path.insert(0, NoAgent())
runpy.run_module('pantheon.platform', run_name='__main__')
'''
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
                    result = await service.invoke('fleet_app_lifecycle', {'node_id': 'absent'})
                    assert result == {'success': False, 'error': 'Fleet is not connected'}
                finally:
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
