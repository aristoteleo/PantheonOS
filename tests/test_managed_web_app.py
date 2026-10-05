"""Web package boundary and actual browser-rendered RPC/lifecycle acceptance."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import platform
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import nats

from pantheon.apps.client import AppClient
from pantheon.apps.builtin.web import WebToolSet
from pantheon.apps.builtin.web.build_managed import build
from pantheon.apps.lifecycle import build_artifact, FleetLifecycle, CHUNK_SIZE
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries, assert_stopped
from test_model_services import serve


def test_package_preserves_tools_without_agent_or_ambient_state(tmp_path):
    package = build(tmp_path / 'web', 'darwin-arm64')
    manifest = json.loads((package / 'app.json').read_text())
    assert manifest['runtime'] == 'process' and manifest['surface'] == 'headless'
    assert {t['name'] for t in manifest['provides']['tools']} == {'duckduckgo_search', 'web_crawl'}
    for method in manifest['provides']['tools']:
        assert method['params'][0]['required'] is True
        assert 'default' not in method['params'][0]
    assert {i['name'] for i in manifest['provides']['interfaces']} == {'web-search', 'web-crawl'}
    assert not list(package.rglob('agent.py')) and not list(package.rglob('settings.py'))
    build_artifact(package)
    state = tmp_path / 'state'
    state.mkdir()
    code = '''import asyncio, importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name in ('pantheon.agent', 'pantheon.settings', 'pantheon.factory', 'pantheon.remote', 'pantheon.chatroom', 'pantheon.models'):
   raise AssertionError('Web imported Agent or ambient state: '+name)
sys.meta_path.insert(0, Boundary())
sys.path.insert(0, sys.argv[1])
from pantheon.apps.builtin.web.managed import create_service
from pathlib import Path
async def run():
 service = create_service(sys.argv[2])
 from crawl4ai.async_database import async_db_manager
 assert Path(async_db_manager.db_path).is_relative_to(Path(sys.argv[2]))
 assert not service._crawler_options['config'].ignore_https_errors
 await service.run(remote=False, cleanup_on_exit=False)
 assert service.worker is None
 await service.cleanup()
asyncio.run(run())
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(package / 'backend/_vendor'), str(state)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_search_preserves_result_contract_and_drains_worker_on_cancel(monkeypatch):
    import ddgs
    started, release = threading.Event(), threading.Event()
    calls = []
    class Search:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def text(self, query, **kwargs):
            calls.append((query, kwargs))
            started.set()
            assert release.wait(10)
            return iter([{'title': 'fixture', 'href': 'https://example.org', 'body': 'result'}])
    monkeypatch.setattr(ddgs, 'DDGS', Search)
    service = WebToolSet('web')
    pending = asyncio.create_task(service.duckduckgo_search('test', 3, 'w', context_variables={}))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        pending.cancel()
        await asyncio.sleep(.02)
        pending.cancel()
        await asyncio.sleep(.02)
        assert not pending.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert calls == [('test', {'max_results': 3, 'timelimit': 'w'})]
    assert (await service.duckduckgo_search('again', context_variables={}))[0]['body'] == 'result'


def test_native_web_rpc_uses_prepared_browser_and_drains(tmp_path):
    """Opt-in: a real environment prepared by this App's ordinary install hook.

    No external search engine is contacted. Pages are served locally and require
    JavaScript execution, so a static HTTP fetch cannot satisfy this gate.
    """
    target = os.environ.get('PANTHEON_TEST_WEB_INSTALL')
    if not target:
        pytest.skip('Set PANTHEON_TEST_WEB_INSTALL to a prepared Web App installation')
    package = build(tmp_path / 'web', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    install = Path(target)
    binding = json.loads((install / 'python-environment.json').read_text())
    assert binding['runtime_resources'] == {'protocol': 1, 'playwright': ['chromium']}
    prepared = subprocess.run([sys.executable, str(package / '.fleet-runtime/install.py'),
                              '--package', str(package), '--install', str(install)],
                             capture_output=True, text=True, timeout=90)
    assert prepared.returncode == 0 and json.loads(prepared.stdout)['status'] == 'succeeded', prepared.stdout
    assert 'Reused' in json.loads(prepared.stdout)['message']
    assert json.loads((install / 'python-environment.json').read_text()) == binding
    entered, release = threading.Event(), threading.Event()
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/hold':
                entered.set()
                assert release.wait(20)
            html = ('<html><body><main><h1>Web App acceptance</h1>'
                    '<p id="result">before script</p></main><script>'
                    'document.getElementById("result").textContent="RENDERED_' + self.path[1:] + '";'
                    '</script></body></html>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        def log_message(self, *_):
            pass
    fixture = ThreadingHTTPServer(('127.0.0.1', 0), Page)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    thread.start()
    data = tmp_path / 'data'
    home = tmp_path / 'home'
    home.mkdir()
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, HOME=str(home), PANTHEON_APP_RPC_TOKEN=token,
               PANTHEON_PORT_HTTP='0', PANTHEON_INSTANCE_GENERATION='web-acceptance',
               PLAYWRIGHT_BROWSERS_PATH=str(tmp_path / 'wrong-ambient-browser-path'))
    env.pop('PYTHONPATH', None)
    process = None
    def request(path, payload=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth:
            headers['X-Fleet-RPC-Token'] = token
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers=headers)
        with urlopen(req, timeout=45) as response:
            return json.load(response)
    with (tmp_path / 'host.log').open('w') as log:
        try:
            process = subprocess.Popen([sys.executable, str(package / '.fleet-runtime/launch.py'),
                '--install', str(install), str(package / '.fleet-runtime/host.py'), 'start',
                '--package', str(package), '--data', str(data)], env=env, stdout=log, stderr=log)
            endpoint = data / 'backend-endpoint.json'
            deadline = time.monotonic() + 45
            while not endpoint.is_file():
                assert process.poll() is None, (tmp_path / 'host.log').read_text()
                assert time.monotonic() < deadline, (tmp_path / 'host.log').read_text()
                time.sleep(.05)
            base = 'http://127.0.0.1:' + str(json.loads(endpoint.read_text())['port'])
            health = request('/health')
            assert health['ready'] and health['methods'] == ['duckduckgo_search', 'web_crawl']
            with pytest.raises(HTTPError) as denied:
                request('/rpc', {'method': 'web_crawl', 'args': {'urls': []}}, auth=False)
            assert denied.value.code == 403
            page = f'http://127.0.0.1:{fixture.server_port}'
            def crawl(urls):
                return request('/rpc', {'method': 'web_crawl', 'args': {'urls': urls}})['result']
            result = crawl([page + '/one', page + '/two'])
            assert len(result) == 2 and 'RENDERED_one' in result[0] and 'RENDERED_two' in result[1]
            assert 'RENDERED_single' in crawl(page + '/single')[0]
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(crawl, [page + '/hold'])
                assert entered.wait(10)
                assert request('/health')['ready']
                draining = request('/_fleet/drain', {})
                assert not draining['safe_to_stop'] and draining['status'] == 'waiting'
                release.set()
                assert 'RENDERED_hold' in pending.result(timeout=30)[0]
            assert request('/_fleet/drain', {})['safe_to_stop']
            with pytest.raises(HTTPError):
                crawl([page + '/after-stop'])
            # Crawl4AI may bypass page caching; the database path is verified
            # by the isolated import gate, while logs must actually be local.
            assert (data / '.crawl4ai/crawler.log').is_file()
            assert not (home / '.crawl4ai').exists()
            process.terminate()
            assert process.wait(timeout=15) == 0
        finally:
            release.set()
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            fixture.shutdown()
            fixture.server_close()
            thread.join(timeout=5)


@pytest.mark.asyncio
async def test_web_package_on_real_fleet_node_and_clean_reopen(tmp_path, binaries, monkeypatch):
    """The same package through actual Controller/NATS/Runner, with no Agent."""
    target = os.environ.get('PANTHEON_TEST_WEB_INSTALL')
    if not target:
        pytest.skip('Prepare Web dependencies and supply PANTHEON_TEST_WEB_INSTALL')
    binding = json.loads((Path(target) / 'python-environment.json').read_text())
    cache = Path(binding['python']).parent.parent.parent
    assert cache.stat().st_mode & 0o077 == 0, 'Native gate requires an operator-owned private dependency cache'
    # Both LocalFleet and Runner strip ambient deployment settings. Supply an
    # explicit node Python launcher (as a cluster module would), not an App env
    # override. App execution still uses the independently installed venv.
    launchers = tmp_path / 'node-bin'
    launchers.mkdir()
    python = launchers / 'python3'
    python.write_text('#!/bin/sh\nexport PANTHEON_PYTHON_CACHE=' + shlex.quote(str(cache)) +
                      '\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o700)
    monkeypatch.setenv('PATH', str(launchers) + os.pathsep + os.environ['PATH'])
    target_platform = ('darwin' if sys.platform == 'darwin' else 'linux') + '-' + {
        'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    package = build(tmp_path / 'web', target_platform)
    artifact, digest = build_artifact(package)
    class Page(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body><p id="result">Waiting</p><script>'
                b'document.getElementById("result").textContent="FLEET_WEB_RENDERED";'
                b'</script></body></html>')
    with serve(Page) as page:
        async with LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path) as runtime:
            info = runtime.coordinates
            children = list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
            wire = FleetLifecycle(resolver)
            client = AppClient(nc, info.fleet_id)
            async def action(name, generation=0):
                receipt = await wire.submit(info.node_id, name, digest, scope='shared-web', generation=generation)
                async with asyncio.timeout(120):
                    while True:
                        state = await wire.status(info.node_id)
                        operation = state['operations'][receipt['request']['operation_id']]
                        if operation['state'] == 'succeeded':
                            return next(i for i in state['instances'].values() if i['digest'] == digest)
                        assert operation['state'] in ('queued', 'running'), operation
                        await asyncio.sleep(.1)
            try:
                for offset in range(0, len(artifact), CHUNK_SIZE):
                    await wire._request(info.node_id, 'stage', digest=digest, offset=offset,
                        data=base64.b64encode(artifact[offset:offset + CHUNK_SIZE]).decode())
                instance_id = None
                generation = 0
                for _ in range(2):
                    instance = await action('start', generation)
                    assert instance['state'] == 'ready' and instance['generation'] > generation
                    assert instance_id in (None, instance['instance_id'])
                    instance_id, generation = instance['instance_id'], instance['generation']
                    value = await client.invoke(info.node_id, 'web', {
                        'instance_id': instance_id, 'revision': digest, 'generation': generation},
                        'web_crawl', {'urls': [page]}, 45)
                    assert 'error' not in value and value['response']['success'], value
                    assert 'FLEET_WEB_RENDERED' in value['response']['result'][0]
                    stopped = await action('stop', generation)
                    assert stopped['state'] == 'stopped'
                    generation = stopped['generation']
                logs = list(runtime.root.rglob('dependencies.log'))
                assert logs and all('Reusing installed Python dependencies' in p.read_text() for p in logs)
                environments = list(runtime.root.rglob('python-environment.json'))
                assert environments and all(Path(json.loads(p.read_text())['python']).is_relative_to(cache)
                                             for p in environments)
            finally:
                state = await wire.status(info.node_id)
                for instance in state['instances'].values():
                    if instance['digest'] == digest and instance['state'] != 'stopped':
                        await action('stop', instance['generation'])
                await resolver.close()
        assert_stopped(children, info)
