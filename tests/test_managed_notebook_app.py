"""Ordinary Notebook packaging, explicit state and real kernel lifetime gates."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid
from unittest.mock import AsyncMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import psutil

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.notebook.build_managed import build
from pantheon.apps.builtin.notebook.managed import create_service
from pantheon.apps.toolset_backend import register_toolset
from pantheon.apps.lifecycle import build_artifact
from test_local_fleet import binaries

CONFIG = {'execution_timeout': 60, 'execution_logging': True}


def service_at(tmp_path):
    workspace, state = tmp_path / 'workspace', tmp_path / 'state'
    workspace.mkdir(exist_ok=True)
    state.mkdir(exist_ok=True)
    return create_service(CONFIG, workspace, state)


def test_package_preserves_gui_tools_and_excludes_embedded_agent(tmp_path):
    package = build(tmp_path / 'package', 'darwin-arm64')
    manifest = json.loads((package / 'app.json').read_text())
    source = json.loads((Path(__file__).parents[1] / 'apps/notebook/app.json').read_text())
    assert manifest['surface'] == source['surface']
    assert (package / manifest['entry']['frontend']).is_file()
    assert {t['name'] for t in manifest['provides']['tools']} == {t['name'] for t in source['provides']['tools']} | {'execution_host'}
    assert manifest['provides']['interfaces'][0]['name'] == 'notebook'
    assert not list(package.rglob('agent.py')) and not list(package.rglob('settings.py'))
    build_artifact(package)
    for method in manifest['provides']['tools']:
        for param in method.get('params', []):
            assert param.get('default') != 'not_defined'
    script = '''import asyncio, importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name in ('pantheon.agent','pantheon.settings','pantheon.factory','pantheon.chatroom','pantheon.remote.factory'):
   raise AssertionError('Ambient import: '+name)
sys.meta_path.insert(0, Boundary())
sys.path.insert(0, sys.argv[1])
from pantheon.apps.builtin.notebook.managed import create_service
async def run():
 service = create_service({'execution_timeout':60,'execution_logging':True}, sys.argv[2], sys.argv[3])
 await service.run(remote=False, cleanup_on_exit=False)
 assert service.worker is None
 assert service.kernel_toolset.execution_timeout == 60
 assert 'file_log' in service.kernel_toolset._iopub_handlers
 await service.cleanup()
asyncio.run(run())
'''
    service = service_at(tmp_path)
    result = subprocess.run([sys.executable, '-I', '-c', script, str(package / 'backend/_vendor'),
                             service.workdir, str(service.persistence_dir)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_corrupt_contexts_refuse_start_and_are_not_overwritten(tmp_path):
    service = service_at(tmp_path)
    service.persistence_file.write_text('corrupt original')
    ctx = AppContext('integrated-notebook', Path(service.workdir), service.persistence_dir, None)
    with pytest.raises(RuntimeError, match='could not be loaded'):
        await register_toolset(ctx, service)
    assert not ctx._methods
    assert service.persistence_file.read_text() == 'corrupt original'


@pytest.mark.asyncio
async def test_failed_save_does_not_skip_kernel_cleanup_or_report_success(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    ctx = AppContext('integrated-notebook', Path(service.workdir), service.persistence_dir, None)
    await register_toolset(ctx, service)
    close = AsyncMock()
    monkeypatch.setattr(service.kernel_toolset, 'cleanup', close)
    monkeypatch.setattr(service, '_save_contexts', AsyncMock(side_effect=OSError('disk failure')))
    with pytest.raises(ExceptionGroup, match='shutdown incomplete'):
        await ctx.before_stop()
    close.assert_awaited_once()
    with pytest.raises(RuntimeError, match='stopping'):
        await ctx._methods['list_notebooks']()


@pytest.mark.asyncio
async def test_kernel_cleanup_attempts_every_session_and_reports_failure(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    kernels = service.kernel_toolset
    kernels.sessions = {'one': object(), 'two': object()}
    stop = AsyncMock(side_effect=[{'success': False}, {'success': True}])
    monkeypatch.setattr(kernels, 'shutdown_session', stop)
    with pytest.raises(RuntimeError, match='Could not stop 1'):
        await kernels.cleanup()
    assert [c.args for c in stop.await_args_list] == [('one',), ('two',)]


@pytest.mark.parametrize('config', [None, {}, {**CONFIG, 'execution_timeout': True},
    {**CONFIG, 'execution_timeout': 0}, {**CONFIG, 'execution_logging': 'false'}, {**CONFIG, 'extra': 1}])
def test_configuration_is_explicit(config, tmp_path):
    with pytest.raises(ValueError, match='explicit'):
        create_service(config, tmp_path, tmp_path)


def test_packaged_rpc_kernels_widgets_stop_and_reopen(tmp_path):
    package = build(tmp_path / 'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    workspace, data, home = tmp_path / 'workspace', tmp_path / 'data', tmp_path / 'home'
    workspace.mkdir(); home.mkdir()
    # Keep the test kernel in the same controlled interpreter as the backend.
    kernels = tmp_path / 'jupyter/kernels/python3'
    kernels.mkdir(parents=True)
    (kernels / 'kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
        'display_name': 'Python 3', 'language': 'python'}))
    config_path = tmp_path / 'config.json'
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, HOME=str(home), JUPYTER_PATH=str(tmp_path / 'jupyter'),
        PANTHEON_APP_RPC_TOKEN=token, PANTHEON_APP_CONFIG=str(config_path),
        PANTHEON_FLEET_ID='owner', PANTHEON_NODE_ID='node', PANTHEON_INSTANCE_ID='notebook',
        PANTHEON_APP_REVISION='revision', PANTHEON_COMPONENT_NAME='backend', PANTHEON_PORT_HTTP='0')
    env.pop('PYTHONPATH', None)
    boot = "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    def request(path, payload=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['X-Fleet-RPC-Token'] = token
        with urlopen(Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                             headers=headers), timeout=30) as response:
            return json.load(response)
    def rpc(method, **args):
        result = request('/rpc', {'method': method, 'args': args})
        assert result['success'], result
        return result['result']
    previous_kernel = previous_widget = None
    for generation in (1, 2):
        config_path.write_text(json.dumps({'protocol': 1, 'generation': generation, 'owner': 'owner',
            'node_id': 'node', 'instance_id': 'notebook', 'revision': 'revision', 'component': 'backend',
            'values': {'notebook': CONFIG}, 'credentials': {}}))
        env['PANTHEON_INSTANCE_GENERATION'] = str(generation)
        log_path = tmp_path / f'backend-{generation}.log'
        kernel_pid = None
        with log_path.open('w') as log:
            proc = subprocess.Popen([sys.executable, '-I', '-c', boot, str(package / '.fleet-runtime'),
                str(package / '.fleet-runtime/host.py'), 'start', '--package', str(package), '--data', str(data),
                '--workspace', str(workspace)], stdout=log, stderr=log, env=env)
            try:
                descriptor = data / 'backend-endpoint.json'
                for _ in range(200):
                    assert proc.poll() is None, log_path.read_text()
                    if descriptor.exists():
                        info = json.loads(descriptor.read_text())
                        base = f"http://127.0.0.1:{info['port']}"
                        try:
                            if request('/health').get('ready'):
                                break
                        except OSError:
                            pass
                    time.sleep(.05)
                else:
                    raise AssertionError(log_path.read_text())
                with pytest.raises(HTTPError) as unauthorized:
                    request('/rpc', {'method': 'list_notebooks', 'args': {}}, auth=False)
                assert unauthorized.value.code == 403
                assert rpc('execution_host')['workspace'] == str(workspace)
                if generation == 1:
                    assert rpc('create_notebook', notebook_path='node.ipynb')['success']
                else:
                    assert rpc('read_notebook', notebook_path='node.ipynb')['success']
                    assert not rpc('widget_channel', notebook_path='node.ipynb')['success']
                execution = rpc('add_cell', notebook_path='node.ipynb', execute=True,
                    content="import os,json\nprint(json.dumps({'pid':os.getpid(),'cwd':os.getcwd()}))")['execution']
                assert execution['success'], execution
                identity = json.loads(''.join(o.get('text', '') for o in execution['outputs']).strip())
                kernel_pid = identity['pid']
                assert Path(identity['cwd']).resolve() == workspace.resolve()
                assert kernel_pid != previous_kernel
                status = rpc('manage_kernel', notebook_path='node.ipynb', action='status')
                assert status['kernel_session_id'] == rpc('manage_kernel', notebook_path=str(workspace / 'node.ipynb'), action='status')['kernel_session_id']
                with pytest.raises(HTTPError):
                    request('/rpc', {'method': 'list_notebooks', 'args': {'context_variables': {'workdir': '/tmp'}}})
                widget = rpc('add_cell', notebook_path='node.ipynb', execute=True,
                    content="import ipywidgets as w\nlabel=w.Label(value='before')\nbutton=w.Button()\nbutton.on_click(lambda _: setattr(label,'value','after'))\nprint(button.model_id)")['execution']
                assert widget['success'], widget
                comm = ''.join(o.get('text', '') for o in widget['outputs']).strip()
                connected = rpc('widget_channel', notebook_path='node.ipynb')
                widget_generation = connected['generation']
                assert widget_generation != previous_widget
                sent = rpc('widget_channel', notebook_path='node.ipynb', action='send', generation=widget_generation,
                    message={'msg_type': 'comm_msg', 'msg_id': uuid.uuid4().hex,
                        'content': {'comm_id': comm, 'data': {'method': 'custom', 'content': {'event': 'click'}}}})
                assert sent['success']
                check = rpc('add_cell', notebook_path='node.ipynb', execute=True, content="assert label.value == 'after'")
                assert check['execution']['success']
                assert rpc('widget_channel', notebook_path='node.ipynb', action='poll', generation=widget_generation, cursor=0)['frames']
                # Interrupt remains concurrent with execution before shutdown.
                added = rpc('add_cell', notebook_path='node.ipynb', content="from pathlib import Path\nimport time\nPath('running').touch()\ntime.sleep(30)")
                with ThreadPoolExecutor() as pool:
                    pending = pool.submit(rpc, 'execute_cell', notebook_path='node.ipynb', cell_id=added['cell_id'])
                    for _ in range(150):
                        if (workspace / 'running').exists():
                            break
                        time.sleep(.02)
                    assert (workspace / 'running').exists()
                    assert rpc('manage_kernel', notebook_path='node.ipynb', action='interrupt')['success']
                    pending.result(10)
                (workspace / 'running').unlink()
                # A stop cannot overtake an accepted cell or acknowledge while
                # its output still needs to be written to the notebook.
                held = rpc('add_cell', notebook_path='node.ipynb', content=
                    "from pathlib import Path\nimport time\nPath('running').touch()\n"
                    "while not Path('release').exists(): time.sleep(.02)\nprint('DRAINED_OUTPUT')")
                with ThreadPoolExecutor() as pool:
                    pending = pool.submit(rpc, 'execute_cell', notebook_path='node.ipynb', cell_id=held['cell_id'])
                    try:
                        for _ in range(150):
                            if (workspace / 'running').exists():
                                break
                            time.sleep(.02)
                        assert (workspace / 'running').exists()
                        draining = request('/_fleet/drain', {})
                        assert not draining['safe_to_stop'] and draining['status'] == 'waiting'
                        assert psutil.pid_exists(kernel_pid)
                        with pytest.raises(HTTPError):
                            rpc('add_cell', notebook_path='node.ipynb', content='must not run', execute=True)
                    finally:
                        (workspace / 'release').touch()
                    completed = pending.result(10)
                    assert completed['success']
                saved = json.loads((workspace / 'node.ipynb').read_text())
                assert 'DRAINED_OUTPUT' in ''.join(''.join(o.get('text', '')) for c in saved['cells'] for o in c.get('outputs', []))
                (workspace / 'running').unlink()
                (workspace / 'release').unlink()
                stopped = request('/_fleet/drain', {})
                assert stopped['safe_to_stop'], stopped
                assert not psutil.pid_exists(kernel_pid)
                with pytest.raises(HTTPError):
                    request('/rpc', {'method': 'list_notebooks', 'args': {}})
                previous_kernel, previous_widget = kernel_pid, widget_generation
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    raise
                if kernel_pid:
                    assert not psutil.pid_exists(kernel_pid)
        assert proc.returncode == 0, log_path.read_text()
    assert (workspace / 'node.ipynb').is_file()
    assert list(data.rglob('*.jsonl')), 'Execution logging must remain available without Agent settings'


@pytest.mark.asyncio
async def test_actual_fleet_prepared_notebook_start_stop_reopen(tmp_path, binaries, monkeypatch):
    """Runs through actual Controller/Runner and independently installed Python."""
    import base64
    import platform
    import shlex
    import nats
    from pantheon.apps.client import AppClient
    from pantheon.apps.lifecycle import FleetLifecycle, CHUNK_SIZE, ConfigurationBusy
    from pantheon.apps.resolver import AppInstanceResolver
    from pantheon.platform.local_fleet import LocalFleet
    from test_local_fleet import assert_stopped

    target = os.environ.get('PANTHEON_TEST_NOTEBOOK_INSTALL')
    if not target:
        pytest.skip('Prepare Notebook dependencies and supply PANTHEON_TEST_NOTEBOOK_INSTALL')
    binding = json.loads((Path(target) / 'python-environment.json').read_text())
    cache = Path(binding['python']).parent.parent.parent
    assert cache.stat().st_mode & 0o077 == 0
    launchers = tmp_path / 'node-bin'
    launchers.mkdir()
    python = launchers / 'python3'
    python.write_text('#!/bin/sh\nexport PANTHEON_PYTHON_CACHE=' + shlex.quote(str(cache)) +
                      '\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o700)
    monkeypatch.setenv('PATH', str(launchers) + os.pathsep + os.environ['PATH'])
    target_platform = ('darwin' if sys.platform == 'darwin' else 'linux') + '-' + {
        'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    package = build(tmp_path / 'notebook', target_platform)
    artifact, digest = build_artifact(package)
    async with LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path) as runtime:
        info, children = runtime.coordinates, list(runtime._children)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
        wire, client = FleetLifecycle(resolver), AppClient(nc, info.fleet_id)
        async def action(name, generation=0, **kwargs):
            receipt = await wire.submit(info.node_id, name, digest, scope='shared-notebook', generation=generation, **kwargs)
            async with asyncio.timeout(120):
                while True:
                    state = await wire.status(info.node_id)
                    op = state['operations'][receipt['request']['operation_id']]
                    if op['state'] == 'succeeded':
                        return next((i for i in state['instances'].values() if i['digest'] == digest), None)
                    assert op['state'] in ('queued', 'running'), op
                    await asyncio.sleep(.1)
        async def invoke(instance, method, **args):
            response = await client.invoke(info.node_id, 'integrated-notebook', {
                'instance_id': instance['instance_id'], 'revision': digest, 'generation': instance['generation']}, method, args, 40)
            assert 'error' not in response and response['response']['success'], response
            return response['response']['result']
        try:
            for offset in range(0, len(artifact), CHUNK_SIZE):
                await wire._request(info.node_id, 'stage', digest=digest, offset=offset,
                    data=base64.b64encode(artifact[offset:offset + CHUNK_SIZE]).decode())
            await action('install')
            generation, instance_id, previous_kernel = 0, None, None
            for lifetime in (1, 2):
                preparation = f'notebook-start-{lifetime}'
                prepared = await action('prepare_start', generation, operation_id=preparation)
                async with asyncio.timeout(5):
                    while True:
                        try:
                            await wire.configure(info.node_id, instance_id=prepared['instance_id'], revision=digest,
                                generation=prepared['generation'], preparation_id=preparation,
                                components={'backend': {'values': {'notebook': CONFIG}}})
                            break
                        except ConfigurationBusy:
                            await asyncio.sleep(.05)
                instance = await action('start', prepared['generation'], start_preparation_id=preparation)
                assert instance['state'] == 'ready'
                assert instance_id in (None, instance['instance_id'])
                instance_id = instance['instance_id']
                host = await invoke(instance, 'execution_host')
                assert Path(host['python']).is_relative_to(cache)
                workspace = Path(host['workspace'])
                if lifetime == 1:
                    assert (await invoke(instance, 'create_notebook', notebook_path='native.ipynb'))['success']
                else:
                    assert (await invoke(instance, 'read_notebook', notebook_path='native.ipynb'))['success']
                executed = await invoke(instance, 'add_cell', notebook_path='native.ipynb', execute=True,
                    content="import os,sys,json,ipywidgets\nprint(json.dumps({'pid':os.getpid(),'python':sys.executable}))")
                assert executed['execution']['success'], executed
                value = json.loads(''.join(o.get('text', '') for o in executed['execution']['outputs']).strip())
                kernel_pid = value['pid']
                assert kernel_pid != previous_kernel
                assert Path(value['python']).is_relative_to(cache)
                stopped = await action('stop', instance['generation'])
                assert stopped['state'] == 'stopped'
                assert not psutil.pid_exists(kernel_pid)
                assert (workspace / 'native.ipynb').is_file()
                previous_kernel, generation = kernel_pid, stopped['generation']
            logs = list(runtime.root.rglob('dependencies.log'))
            assert logs and all('Reusing installed Python dependencies' in p.read_text() for p in logs)
        finally:
            state = await wire.status(info.node_id)
            for instance in state['instances'].values():
                if instance['digest'] == digest and instance['state'] != 'stopped':
                    await action('stop', instance['generation'])
            await resolver.close()
    assert_stopped(children, info)
