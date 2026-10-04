"""Packaged GUI -> native Agent -> TLS grant fixture -> real Files tools.

The provider executes on temporary disk. Fleet gateway issuance is covered
separately; this is not a deployed Fleet or native Desktop acceptance claim.
"""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading
from types import SimpleNamespace

import pytest

from pantheon.apps.builtin.file import FileManagerToolSet
from pantheon.apps.builtin.file_transfer import FileTransferToolSet
from test_agent_application import TEMPLATE
from test_agent_dependency_bindings import endpoint as tls_material
from test_agent_model_scope import endpoint as model_endpoint
from test_agent_native_process import native_process, request
from test_agent_launch import prepared


@pytest.fixture
def files_provider(tmp_path, tls_material, monkeypatch):
    root = tmp_path/'workspace'
    root.mkdir()
    (root/'fixture.py').write_text('print("original file")\n')
    monkeypatch.setattr('pantheon.settings.get_settings', lambda: SimpleNamespace(
        max_file_read_lines=800, max_file_read_chars=50_000))
    monkeypatch.setattr('pantheon.apps.builtin.fleet.local_node.local_node_id', lambda: 'files-test')
    files, transfer = FileManagerToolSet('files', root), FileTransferToolSet('transfer', root)
    providers = {'a'*64: (files, ['list_files', 'get_cwd', 'read_file', 'write_file', 'move_file', 'delete_path']),
                 'b'*64: (transfer, ['open_file_for_read', 'read_chunk_at', 'close_file',
                                    'open_file_for_write', 'write_chunk'])}
    calls = []
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever)
    worker.start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                provider, methods = providers[self.headers['Authorization'].removeprefix('Bearer ')]
                method, args = request['method'], request['args']
                assert method in methods
                signature = inspect.signature(getattr(provider, method))
                signature.bind(**args)
                for key in ('file_path', 'sub_dir', 'old_path', 'new_path', 'path'):
                    if key in args:
                        path = Path(args[key])
                        assert (path if path.is_absolute() else root/path).resolve().is_relative_to(root)
                calls.append(('files' if provider is files else 'transfer', method, args))
                future = asyncio.run_coroutine_threadsafe(getattr(provider, method)(**args), loop)
                result = future.result(timeout=10)
                raw = json.dumps({'success': True, 'result': result}).encode()
                self.send_response(200)
            except Exception as error:
                raw = json.dumps({'success': False, 'error': type(error).__name__}).encode()
                self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path/'cert.pem', tmp_path/'key.pem')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    bindings = {}
    for token, (provider, methods) in providers.items():
        service = 'file_manager' if provider is files else 'file_transfer'
        functions = []
        for method in methods:
            parameters = inspect.signature(getattr(provider, method)).parameters
            functions.append({'name': method, 'parameters': {'type': 'object',
                'properties': {name: {} for name in parameters},
                'required': [name for name, param in parameters.items() if param.default is inspect.Parameter.empty],
                'additionalProperties': False}})
        bindings[service] = {'credential': service, 'functions': functions}
    value = SimpleNamespace(root=root, calls=calls, bindings=bindings,
        credentials={service: {'endpoint': f'https://127.0.0.1:{server.server_port}/rpc',
                              'key': token} for service, token in [('file_manager', 'a'*64), ('file_transfer', 'b'*64)]})
    try:
        yield value
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        for handle in list(transfer._handles):
            asyncio.run_coroutine_threadsafe(transfer.close_file(handle), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=5)
        loop.close()
        assert not thread.is_alive() and not worker.is_alive()


@pytest.mark.asyncio
async def test_packaged_file_panel_reads_edits_reopens_and_uploads(tmp_path, model_endpoint, files_provider, monkeypatch):
    script = os.environ.get('PANTHEON_TEST_AGENT_GUI')
    if not script:
        pytest.skip('Supply production Agent GUI build and browser gate script')
    value = prepared(tmp_path, model_endpoint.url)
    value['credentials'].update(files_provider.credentials)
    value['values']['agent']['view_dependencies'] = {'shared': {'toolsets': files_provider.bindings}}
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path/'cert.pem'))
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': []}]}
    with native_process(tmp_path, model_endpoint.url, configuration=value) as (child, base):
        for _ in range(200):
            try:
                await request(base, '/health')
                break
            except OSError:
                assert child.poll() is None, (tmp_path/'process.log').read_text()[-12000:]
                await asyncio.sleep(.05)
        else:
            pytest.fail('HTTP Agent never became ready')
        created = await request(base, '/rpc', dict(method='create_chat', args=dict(
            chat_name='Files GUI', project_name='Shared', template_obj=template)))
        assert created['success'] and created['result']['success']
        skill = await request(base, '/rpc', dict(method='agent_skill_files', args=dict(
            operation='write', scope='project', path='owned-gui-skill/SKILL.md',
            content='# Original GUI Skill\n\nKeep this body.')))
        assert skill['success'] and skill['result']['success']
        env = {**os.environ, 'PANTHEON_AGENT_TEST_SKILLS': '1',
               'PANTHEON_AGENT_TEST_URL': base, 'PANTHEON_AGENT_TEST_FILES': '1',
               'PANTHEON_AGENT_TEST_CHAT': created['result']['chat_id']}
        result = await asyncio.to_thread(subprocess.run, ['node', script], env=env,
            capture_output=True, text=True, timeout=150)
        assert result.returncode == 0, result.stdout + result.stderr
    skill_content = (tmp_path/'data/agent/configuration/.pantheon/skills/owned-gui-skill/SKILL.md').read_text()
    assert 'name: Edited GUI Skill' in skill_content and 'Keep this body.' in skill_content
    assert (files_provider.root/'fixture.py').read_text() == 'print("edited in Agent App")\n'
    assert (files_provider.root/'uploaded.txt').read_text() == 'uploaded through scoped Files\n'*4000
    assert not list(files_provider.root.glob('.pantheon-upload-*'))
    assert {'list_files', 'open_file_for_read', 'read_chunk_at', 'close_file', 'write_file'} <= {
        method for _, method, _ in files_provider.calls}
    assert {'open_file_for_write', 'write_chunk', 'move_file'} <= {
        method for _, method, _ in files_provider.calls}
