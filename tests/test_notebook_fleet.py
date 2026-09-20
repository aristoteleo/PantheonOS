"""Exercise the real notebook engine over the portable node's HTTP boundary."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import Request, urlopen

from pantheon.apps.portable import execution_package, portable_backend
from pantheon.apps.schema import parse_manifest

SOURCE = Path(__file__).parents[1] / 'apps/notebook'


def test_notebook_has_both_legacy_and_portable_entries():
    manifest = json.loads((SOURCE / 'app.json').read_text())
    assert portable_backend(manifest)
    assert parse_manifest(manifest).entry.fleetBackend == 'fleet_backend/__init__.py'
    assert ':' in manifest['entry']['backend']
    for platform in ('darwin-arm64', 'windows-amd64', 'linux-amd64'):
        with execution_package(SOURCE, platform) as root:
            staged = json.loads((root / 'app.json').read_text())
            assert staged['entry']['backend'] == 'fleet_backend/__init__.py'
            assert (root / 'fleet_backend/_vendor/pantheon/toolset.py').is_file()
            assert not (root / 'fleet_backend/_vendor/pantheon/agent.py').exists()
            definition = json.loads((root / 'fleet.json').read_text())
            assert definition['requires']['os'] == [platform.split('-')[0]]


def test_notebook_executes_reads_interrupts_and_cleans_up_on_node(tmp_path, monkeypatch):
    spec = tmp_path / 'jupyter' / 'kernels' / 'python3'
    spec.mkdir(parents=True)
    (spec / 'kernel.json').write_text(json.dumps({
        'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
        'display_name': 'Python 3', 'language': 'python'}))
    monkeypatch.setenv('JUPYTER_PATH', str(tmp_path / 'jupyter'))
    workspace = tmp_path / 'existing-workspace'
    workspace.mkdir()
    (workspace / 'existing.txt').write_text('preserved')
    with execution_package(SOURCE, 'darwin-arm64', workspace=str(workspace)) as root:
        definition = json.loads((root / 'fleet.json').read_text())
        assert definition['components'][0]['argv'][-2:] == ['--workspace', str(workspace)]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        data = tmp_path / 'data'
        # -I prevents the controller checkout from satisfying runtime imports.
        # host.py needs its sibling app_runtime, as it would through launch.py.
        boot = "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        with (tmp_path / 'backend.log').open('w') as log:
            proc = subprocess.Popen([sys.executable, '-I', '-c', boot, str(root / '.fleet-runtime'),
                str(root / '.fleet-runtime/host.py'), 'start', '--package', str(root), '--data', str(data),
                '--workspace', str(workspace)], stdout=log, stderr=log,
                env={**os.environ, 'PANTHEON_PORT_HTTP': str(port), 'PANTHEON_INSTANCE_GENERATION': '1'})
            base = f'http://127.0.0.1:{port}'
            def post(path, payload):
                with urlopen(Request(base + path, data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'}), timeout=40) as response:
                    return json.load(response)
            def rpc(method, **args):
                response = post('/rpc', {'method': method, 'args': args})
                assert response['success'], response
                return response['result']
            kernel_pid = None
            try:
                for _ in range(150):
                    try:
                        with urlopen(base + '/health', timeout=.5) as response:
                            assert json.load(response)['ready']
                        break
                    except OSError:
                        assert proc.poll() is None, (tmp_path / 'backend.log').read_text()
                        time.sleep(.1)
                else:
                    raise AssertionError((tmp_path / 'backend.log').read_text())
                assert rpc('execution_host')['workspace'] == str(workspace)
                assert 'existing.txt' in [e['name'] for e in post('/_fleet/fs', {'op': 'ls', 'path': ''})['entries']]
                assert rpc('create_notebook', notebook_path='node.ipynb')['success']
                result = rpc('add_cell', notebook_path='node.ipynb', cell_type='code', execute=True,
                    content="import os,platform,json; print(json.dumps({'pid':os.getpid(),'os':platform.system(),'cwd':os.getcwd()}))",
                    context_variables={'workdir': '/not-the-node'}, session_id='other-host')
                assert result['success'] and result['execution']['success'], result
                outputs = result['execution']['outputs']
                text = ''.join(o.get('text', '') for o in outputs)
                location = json.loads(text.strip())
                assert Path(location['cwd']).resolve() == workspace.resolve()
                kernel_pid = location['pid']
                assert (workspace / 'node.ipynb').is_file()
                assert rpc('read_notebook', notebook_path='node.ipynb')['success']
                relative_status = rpc('manage_kernel', notebook_path='node.ipynb', action='status')
                absolute_status = rpc('manage_kernel', notebook_path=str(workspace / 'node.ipynb'), action='status')
                assert absolute_status['kernel_session_id'] == relative_status['kernel_session_id']
                # Widgets use the same portable RPC boundary; no Jupyter HTTP
                # server or controller-local Python imports are needed.
                result = rpc('add_cell', notebook_path='node.ipynb', execute=True,
                    content="import ipywidgets as w\nlabel=w.Label(value='before')\nbutton=w.Button()\nbutton.on_click(lambda _: setattr(label, 'value', 'after'))\nprint(button.model_id)")
                assert result['execution']['success'], result
                comm = ''.join(o.get('text', '') for o in result['execution']['outputs']).strip()
                connected = rpc('widget_channel', notebook_path='node.ipynb')
                generation = connected['generation']
                assert comm in rpc('widget_channel', notebook_path='node.ipynb', action='info', generation=generation)['comms']
                sent = rpc('widget_channel', notebook_path='node.ipynb', action='send', generation=generation,
                    message={'msg_type': 'comm_msg', 'msg_id': uuid.uuid4().hex,
                             'content': {'comm_id': comm, 'data': {'method': 'custom', 'content': {'event': 'click'}}}})
                assert sent['success'], sent
                check = rpc('add_cell', notebook_path='node.ipynb', execute=True, content="assert label.value == 'after'")
                assert check['execution']['success'], check
                poll = rpc('widget_channel', notebook_path='node.ipynb', action='poll', generation=generation, cursor=0)
                assert poll['success'] and poll['frames'], poll
                # A long cell must not serialize the entire App RPC queue.
                added = rpc('add_cell', notebook_path='node.ipynb', content='import time; time.sleep(30)', cell_type='code')
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(rpc, 'execute_cell', notebook_path='node.ipynb', cell_id=added['cell_id'])
                    time.sleep(.8)
                    started = time.monotonic()
                    assert rpc('manage_kernel', notebook_path='node.ipynb', action='interrupt')['success']
                    future.result(10)
                    assert time.monotonic() - started < 10
                denied = rpc('create_notebook', notebook_path='../outside.ipynb')
                assert not denied['success']
                assert not (tmp_path / 'outside.ipynb').exists()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
                    raise
            if kernel_pid:
                import psutil
                assert not psutil.pid_exists(kernel_pid), 'Stopping the App must stop its kernel'
            assert (workspace / 'node.ipynb').is_file(), 'Stopping must preserve notebook files'
