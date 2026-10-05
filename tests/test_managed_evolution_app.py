"""Public Evolution App admission, actual isolated tools and owned shutdown."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.builtin.evolution.managed import create_service
from pantheon.apps.builtin.evolution.build_managed import build
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.apps.toolset_backend import register_toolset
from pantheon.chatroom.execution_service import AgentExecutions, ExecutionJournal
from pantheon.evolution.sandbox.package import build_package
from test_agent_execution_runner import BoundClient
from test_evolution_remote_sandbox import LocalPlacement
from test_evolution_tools_package import EVALUATOR

CONFIG = {'agent_credential': 'agent', 'execution': 'isolated', 'placement': {},
          'options': {'llm_weight': 0, 'function_weight': 1, 'evaluation_timeout': 30, 'mutation_timeout': 60}}
CREDENTIALS = {'agent': RuntimeCredential('https://agent.example/rpc', 'a' * 64)}


class Client(BoundClient):
    def __init__(self, service):
        super().__init__(service)
        self.closed = False
    async def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['code', 'codebase'])
async def test_public_evolve_status_report_and_reopen(tmp_path, monkeypatch, kind):
    work, state = tmp_path / 'work', tmp_path / 'state'
    work.mkdir(); state.mkdir()
    codebase = work / 'codebase'; codebase.mkdir()
    (codebase / 'main.py').write_text('x=1')
    package = build_package(tmp_path / 'tools')
    placements = []
    def factory(root, *, operation_id):
        value = LocalPlacement(package, root)
        placements.append(value)
        return value
    async def engine(spec, invoke):
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        await invoke('evolution', 'submit', {'summary': 'Improved'})
        return {'content': 'Submitted'}
    agent = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    client = Client(agent)
    def forbid(*_, **__):
        raise AssertionError('Embedded Agent or controller evaluation')
    monkeypatch.setattr('pantheon.agent.Agent.__init__', forbid)
    monkeypatch.setattr('pantheon.evolution.HybridEvaluator.evaluate', forbid)
    service = create_service(CONFIG, CREDENTIALS, work, state, sandbox_factory=factory, client_factory=lambda _: client)
    ctx = AppContext('evolution', work, state, None)
    await register_toolset(ctx, service)
    try:
        response = await ctx._methods['evolve'](type=kind, code='x=1', codebase_path='codebase',
            evaluator_code=EVALUATOR, objective='Improve', iterations=1, async_mode=True)
        identity = response['evolution_id']
        await asyncio.wait_for(asyncio.shield(service.manager.get_session(identity).task), 30)
        status = await ctx._methods['evolution_manage'](evolution_id=identity, include_code=True)
        assert status['status'] == 'completed' and status['result']['files']['main.py'] == 'x=8', status
        report = await ctx._methods['get_evolution_html_report'](evolution_id=identity)
        assert report['success'] and '<html' in str(report).lower(), report
        assert len(placements) == 2 and all(p.process.returncode == 0 for p in placements)
        await ctx.before_stop()
        assert client.closed
        restored = create_service(CONFIG, CREDENTIALS, work, state, sandbox_factory=factory,
                                  client_factory=lambda _: Client(agent))
        assert restored.manager is not service.manager
        assert restored.manager.get_session(identity).status == 'completed'
        assert not restored._owned_tasks and len(placements) == 2
        await restored.cleanup()
    finally:
        await ctx._cleanup()
        await agent.close()
        await asyncio.gather(*(p.close() for p in placements), return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_keeps_placement_credentials_until_run_termination(tmp_path, monkeypatch):
    work, state = tmp_path / 'work', tmp_path / 'state'
    work.mkdir(); state.mkdir()
    config = {k: v for k, v in CONFIG.items() if k != 'placement'} | {'execution': 'node'}
    client = AsyncMock()
    service = create_service(config, CREDENTIALS, work, state, client_factory=lambda _: client)
    owner = AsyncMock()
    service._placement_owner = owner
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def running(identity, *args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await stopped.wait()
    monkeypatch.setattr(service, '_run_evolution', running)
    ctx = AppContext('evolution', work, state, None)
    await register_toolset(ctx, service)
    await ctx._methods['evolve'](type='code', code='x=1', evaluator_code=EVALUATOR, objective='test', iterations=1)
    await entered.wait()
    closing = asyncio.create_task(ctx.before_stop())
    await asyncio.sleep(.02)
    assert not closing.done()
    client.close.assert_not_awaited(); owner.close.assert_not_awaited()
    stopped.set()
    await closing
    client.close.assert_awaited_once(); owner.close.assert_awaited_once()
    await ctx._cleanup()


def test_package_runs_without_checkout_or_embedded_agent(tmp_path):
    package = build(tmp_path / 'app', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    manifest = json.loads((package / 'app.json').read_text())
    assert manifest['dependencies'] == {'agent': {'uses': ['agent-execution@1']}}
    assert not list(package.rglob('agent.py')) and not list(package.rglob('mutation_worker.py'))
    work, state = tmp_path / 'work', tmp_path / 'state'
    work.mkdir(); state.mkdir()
    script = '''
import asyncio, importlib.abc, sys
from pathlib import Path
class Deny(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name == 'pantheon.agent' or name.startswith(('pantheon.chatroom','openai','anthropic','litellm')):
   raise AssertionError('Embedded dependency: '+name)
sys.meta_path.insert(0,Deny())
sys.path.insert(0,sys.argv[1])
from pantheon.apps.builtin.evolution.managed import create_service
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.apps.builtin.desktop.app_runtime import AppContext
from pantheon.apps.toolset_backend import register_toolset
async def run():
 service=create_service({'agent_credential':'agent','execution':'node','options':{}},
  {'agent':RuntimeCredential('https://agent.example/rpc','a'*64)},sys.argv[2],sys.argv[3])
 ctx=AppContext('evolution',Path(sys.argv[2]),Path(sys.argv[3]),None)
 await register_toolset(ctx,service)
 assert 'evolve' in ctx._methods
 await ctx.before_stop()
 await ctx._cleanup()
asyncio.run(run())
'''
    result = subprocess.run([os.environ.get('PANTHEON_TEST_EVOLUTION_PYTHON', sys.executable), '-I', '-c', script, str(package / 'backend/_vendor'),
                             str(work), str(state)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('changes', [{'agent_credential': []}, {'options': {'num_workers': True}},
                                     {'options': {'llm_weight': float('nan')}}, {'execution': 'automatic'}])
def test_invalid_preparation_rejected_before_execution(changes, tmp_path):
    with pytest.raises(ValueError):
        create_service({**CONFIG, **changes}, CREDENTIALS, tmp_path, tmp_path)


@pytest.mark.asyncio
async def test_workspace_boundary_and_cleanup_failure_do_not_release_dependencies(tmp_path, monkeypatch):
    from pantheon.evolution.lifetime import EvolutionCleanupError
    work, state = tmp_path / 'work', tmp_path / 'state'
    work.mkdir(); state.mkdir()
    config = {k: v for k, v in CONFIG.items() if k != 'placement'} | {'execution': 'node'}
    client, owner = AsyncMock(), AsyncMock()
    service = create_service(config, CREDENTIALS, work, state, client_factory=lambda _: client)
    service._placement_owner = owner
    ctx = AppContext('evolution', work, state, None)
    await register_toolset(ctx, service)
    with pytest.raises(ValueError, match='prepared workspace'):
        await ctx._methods['evolve'](type='codebase', codebase_path='../state',
                                   evaluator_code=EVALUATOR, objective='test')
    assert not service._owned_sessions
    async def failed(*args):
        raise EvolutionCleanupError([RuntimeError('container stop unconfirmed')])
    monkeypatch.setattr(service, '_run_evolution', failed)
    response = await ctx._methods['evolve'](type='code', code='x=1', evaluator_code=EVALUATOR, objective='test')
    with pytest.raises(EvolutionCleanupError):
        await service.manager.get_session(response['evolution_id']).task
    with pytest.raises(Exception):
        await ctx.before_stop()
    client.close.assert_not_awaited(); owner.close.assert_not_awaited()
    # No external resources were created by the failed test operation.


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['node', pytest.param('isolated', marks=pytest.mark.skipif(
    not os.environ.get('PANTHEON_TEST_MODAL_IMAGE'), reason='Explicit prepared Modal image required'))])
async def test_separate_controller_package_uses_prepared_https_execution_dependency(tmp_path, mode):
    """Real package, TLS, SDK, Shell/Python and reports; grant/model are fixtures."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import hmac
    import ssl
    import threading
    from types import SimpleNamespace
    from pantheon.apps.modal_app_transport import ModalAppTransport
    from pantheon.platform.local_tls import prepare_tls
    from test_evolution_tools_package import BOOT
    loop = asyncio.get_running_loop()
    calls, failures = [], []
    async def engine(spec, invoke):
        calls.append(spec)
        if not spec['tools']:
            return {'content': 'Verified improvement'}
        await invoke('shell', 'run_command', {'command': "printf 'x=8' > main.py"})
        python = await invoke('python', 'run_python_code', {'code': 'print(6*7)'})
        assert '42' in str(python), python
        await invoke('evolution', 'submit', {'summary': 'Verified code'})
        return {'content': 'Submitted'}
    agent = AgentExecutions(ExecutionJournal(tmp_path / 'agent'), engine)
    methods = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            try:
                assert self.path == '/rpc'
                assert hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + 'a' * 64)
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                name = body['method'].removeprefix('agent_execution_')
                assert name in ('submit','poll','claim','reply','cancel','read_result','release')
                assert 'consumer_id' not in body['args']
                methods.append(name)
                operation = getattr(agent, name)(consumer_id='packaged-controller', **body['args'])
                result = asyncio.run_coroutine_threadsafe(operation, loop).result(30)
                raw = json.dumps({'success': True, 'result': result}).encode()
                self.send_response(200)
            except Exception as exc:
                failures.append(repr(exc))
                raw = b'fixture failure'
                self.send_response(500)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
    tlsdir = tmp_path / 'tls'; tlsdir.mkdir()
    ca, certificate = prepare_tls(tlsdir)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    package = build(tmp_path / 'app', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    work, state = tmp_path / 'work', tmp_path / 'state'
    work.mkdir(); state.mkdir()
    values = {k: v for k, v in CONFIG.items() if k != 'placement'} | {
        'execution': 'node', 'agent_ca_pem': ca.read_text()}
    credentials = {'agent': {'endpoint': f'https://127.0.0.1:{server.server_port}/rpc', 'key': 'a'*64}}
    modal_credential = None
    if mode == 'isolated':
        import modal.config
        from pantheon.apps.lifecycle import build_artifact
        image = json.loads(Path(os.environ['PANTHEON_TEST_MODAL_IMAGE']).read_text())
        assert build_artifact(build_package(tmp_path / 'tools'))[1] == image['artifact_sha256']
        modal_credential = RuntimeCredential('https://api.modal.com', json.dumps({
            'token_id': modal.config.config.get('token_id'), 'token_secret': modal.config.config.get('token_secret')}))
        credentials['modal'] = {'endpoint': modal_credential.endpoint, 'key': modal_credential.key}
        values.update(execution='isolated', placement={'kind':'modal', 'image':image,
            'app_name':'pantheon-agent-extraction-acceptance', 'timeout':180, 'cpu':1,
            'memory':1024, 'gpu':None, 'credential':'modal'})
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'protocol': 1, 'generation': 1, 'owner': 'owner', 'node_id': 'node',
        'instance_id': 'evolution', 'revision': 'revision', 'component': 'backend',
        'credentials': credentials,
        'values': {'evolution': values}}))
    config.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if key in ('PATH','LANG','TMPDIR')}
    env.update(PANTHEON_APP_CONFIG=str(config), PANTHEON_FLEET_ID='owner', PANTHEON_NODE_ID='node',
        PANTHEON_INSTANCE_ID='evolution', PANTHEON_APP_REVISION='revision', PANTHEON_COMPONENT_NAME='backend',
        PANTHEON_INSTANCE_GENERATION='1')
    process = pipe = None
    try:
        process = await asyncio.create_subprocess_exec(os.environ.get('PANTHEON_TEST_EVOLUTION_PYTHON', sys.executable), '-I', '-c', BOOT,
            str(package / '.fleet-runtime/app_runtime.py'), '--app-dir', str(package), '--app-id', 'evolution',
            '--workspace', str(work), '--state-dir', str(state), cwd=tmp_path, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async def chunks(reader):
            while value := await reader.read(8192): yield value
        pipe = ModalAppTransport(SimpleNamespace(object_id='controller-process',
            stdin=SimpleNamespace(write=process.stdin.write, drain=SimpleNamespace(aio=process.stdin.drain)),
            stdout=chunks(process.stdout), stderr=chunks(process.stderr)))
        await pipe.ready(timeout=15)
        accepted = await pipe.invoke('evolve', {'type':'code', 'code':'x=1', 'evaluator_code':EVALUATOR,
            'objective':'Improve score', 'iterations':1})
        identity = accepted['evolution_id']
        async with asyncio.timeout(30):
            while True:
                status = await pipe.invoke('evolution_manage', {'evolution_id':identity, 'include_code':True})
                if status['status'] in ('completed','failed','cancelled'): break
                await asyncio.sleep(.05)
        assert status['status'] == 'completed', (status, failures, pipe.stderr_tail.decode())
        assert status['result']['files']['main.py'] == 'x=8', status
        assert (await pipe.invoke('get_evolution_html_report', {'evolution_id':identity}))['success']
        assert 'submit' in methods and 'reply' in methods and 'release' in methods and calls
        assert not failures
        await pipe.shutdown()
        assert await asyncio.wait_for(process.wait(), 15) == 0, pipe.stderr_tail.decode()
        if modal_credential is not None:
            import sqlite3
            import modal
            from pantheon.apps.modal_credentials import ModalCredentialOwner
            control = ModalCredentialOwner(modal_credential)
            remote = await control.start()
            containers = []
            try:
                for journal in (state / 'receipts').rglob('*.sqlite3'):
                    with sqlite3.connect(journal) as db:
                        if db.execute("SELECT 1 FROM sqlite_master WHERE name='modal_app'").fetchone():
                            containers.extend(db.execute('SELECT backend_id,phase FROM modal_app').fetchall())
                assert len(containers) == 2
                for identity, phase in containers:
                    assert phase == 'stopped'
                    sandbox = await modal.Sandbox.from_id.aio(identity, client=remote)
                    assert type(await sandbox.poll.aio()) is int
                print(json.dumps({'containers': containers, 'receipt_dir': str(state / 'receipts')}))
            finally:
                await control.close()
    finally:
        if process is not None:
            if process.returncode is None and pipe is not None:
                try:
                    await asyncio.wait_for(pipe.shutdown(), 20)
                    await asyncio.wait_for(process.wait(), 20)
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
            elif process.returncode is None:
                process.kill()
            await process.wait()
        if pipe is not None: await pipe.disconnect()
        await agent.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close(); thread.join(2)
        config.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_invalid_remote_resources_do_not_open_modal_credentials(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pantheon.apps.builtin.evolution import managed
    from pantheon.apps.modal_credentials import ModalCredentialOwner
    values = {**CONFIG, 'placement': {'kind': 'modal', 'image': {
        'app_id':'evolution-tools', 'version':'0.1.0', 'artifact_sha256':'a'*64,
        'image_id':'im-pinned', 'base_image_id':'im-base'}, 'app_name':'test',
        'timeout':180, 'cpu':float('nan'), 'memory':1024, 'gpu':None, 'credential':'modal'}}
    snapshot = SimpleNamespace(values={'evolution':values}, credentials={**CREDENTIALS,
        'modal': RuntimeCredential('https://api.modal.com', json.dumps({'token_id':'id','token_secret':'secret'}))})
    monkeypatch.setattr(managed, 'load_runtime_configuration', lambda **_: snapshot)
    start = AsyncMock()
    monkeypatch.setattr(ModalCredentialOwner, 'start', start)
    with pytest.raises(ValueError, match='CPU'):
        await managed.register(AppContext('evolution', tmp_path, tmp_path, None))
    start.assert_not_awaited()
