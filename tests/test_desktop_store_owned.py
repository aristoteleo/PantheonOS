"""Store calls use a Desktop's declared identity, not another App's CLI login."""
import asyncio
import builtins
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest

from pantheon.apps.builtin.desktop.data_server import DataServerConfig, LiveViewDataServer
from pantheon.apps.builtin.desktop.files_binding import DesktopFilesBinding
from pantheon.apps.builtin.desktop.store_binding import DesktopStoreBinding
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


@pytest.fixture
def store():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append((self.path, self.headers.get('Authorization')))
            if self.path.endswith('/redirect'):
                self.send_response(302)
                self.send_header('Location', '/credential-must-not-reach-here')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            value, status = {'success': True, 'path': self.path}, 200
            if self.path.endswith('/denied'):
                value, status = {'detail': 'Sign in again'}, 401
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(origin=f'http://127.0.0.1:{server.server_port}', calls=calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.asyncio
async def test_desktops_use_separate_explicit_store_identities_and_drain_http(tmp_path, store, monkeypatch):
    clients = []
    original_client = httpx.AsyncClient
    def tracked_client(**kwargs):
        result = original_client(**kwargs)
        clients.append(result)
        return result
    monkeypatch.setattr(httpx, 'AsyncClient', tracked_client)
    monkeypatch.setenv('PANTHEON_HUB_URL', 'https://wrong-store.invalid')
    monkeypatch.setenv('PANTHEON_STORE_TOKEN', 'ambient-private-token')
    # Explicit clients must not inherit a proxy or another App's trust file.
    monkeypatch.setenv('ALL_PROXY', 'http://wrong-proxy.invalid:80')
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path/'nonexistent-ambient-ca.pem'))
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name in ('pantheon.agent', 'pantheon.settings', 'pantheon.store.auth'):
            raise AssertionError('Bound Store read ambient configuration: ' + name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    def desktop(token):
        return DesktopToolSet(files_binding=DesktopFilesBinding(workspace=tmp_path,
            app_roots=[(tmp_path/'apps', 'user')], data_roots=[tmp_path],
            server=LiveViewDataServer(config=DataServerConfig())),
            store_binding=DesktopStoreBinding(store.origin, token))
    first, second, anonymous = desktop('owner-a'), desktop('owner-b'), desktop('')
    try:
        results = await asyncio.gather(
            first.desktop_app_store('search', query='one'),
            second.desktop_app_store('inspect', repository_id='two'),
            anonymous.desktop_app_store('community', app_id='browser'))
        assert all(result['success'] for result in results), results
        assert {token for _, token in store.calls} == {'Bearer owner-a', 'Bearer owner-b', None}
        denied = await first.desktop_app_store('inspect', repository_id='denied')
        assert denied == {'success': False, 'error': 'Sign in again'}
        assert len([p for p, _ in store.calls if p.endswith('/denied')]) == 1
        redirect = await second.desktop_app_store('inspect', repository_id='redirect')
        assert not redirect['success'] and 'redirected' in redirect['error']
        assert not any(path == '/credential-must-not-reach-here' for path, _ in store.calls)
        assert all(client.is_closed for client in clients)
        await first.cleanup()
        # Cleanup of one Desktop must not remove another Desktop's Store access.
        assert (await second.desktop_app_store('inspect', repository_id='still-works'))['success']
        assert store.calls[-1][1] == 'Bearer owner-b'
    finally:
        await first.cleanup()
        await second.cleanup()
        await anonymous.cleanup()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize('origin', [None, '', 'file:///tmp/store', 'http://store.test',
    'https://token@store.test', 'https://store.test/path', 'https://store.test?q=a',
    'https://store.test#fragment', 'https://store.test:0', 'https://store.test:99999',
    'https://store.test\n'])
def test_invalid_store_origins_are_rejected(origin):
    with pytest.raises(ValueError):
        DesktopStoreBinding(origin)


def test_store_binding_redacts_token_and_retains_explicit_anonymous_identity():
    binding = DesktopStoreBinding('https://store.test/', 'private-token')
    assert binding.origin == 'https://store.test'
    assert 'private-token' not in repr(binding)
    assert DesktopStoreBinding('http://[::1]:1234').token == ''
    with pytest.raises(ValueError):
        DesktopStoreBinding('https://store.test', 'token\r\nInjected: header')
    with pytest.raises(ValueError):
        DesktopStoreBinding('https://store.test', tls_context=False)
