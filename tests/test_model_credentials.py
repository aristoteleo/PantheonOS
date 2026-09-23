"""Node-only named keys: no config secrets, cross-endpoint forwarding or key RPC."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock

import pytest

from test_model_services import connector_module, serve
from http.server import BaseHTTPRequestHandler


def test_named_credential_configuration_keeps_legacy_identity(tmp_path):
    legacy = connector_module.validate_config({'engine': 'api', 'endpoint': 'https://api.example'})
    assert legacy == {'engine': 'api', 'endpoint': 'https://api.example/v1', 'credential_file': ''}
    config = {**legacy, 'secret_ref': 'node-secret://provider'}
    connector = connector_module.Connector(tmp_path)
    connector.configure(config)
    assert connector_module.Connector(tmp_path).config == config
    for ref in ('key', '../key', 'node-secret://key/path', 'node-secret://key?foo', 'node-secret://KEY'):
        with pytest.raises(ValueError): connector_module.validate_config({**config, 'secret_ref': ref})
    with pytest.raises(ValueError, match='not both'):
        connector_module.validate_config({**config, 'credential_file': '/private/key'})
    with pytest.raises(ValueError, match='attached'):
        connector.configuration(config, managed={})


@pytest.mark.asyncio
async def test_old_node_rejects_named_credentials_before_installing():
    from pantheon.models.manager import ModelServiceManager
    client, resolver = AsyncMock(), AsyncMock()
    manager = ModelServiceManager(client=client, resolver=resolver)
    manager.node = AsyncMock(return_value={'capability': {'runtimes': {'app-rpc-auth': '1'}}})
    manager.ensure = AsyncMock()
    with pytest.raises(ValueError, match='Update Fleet'):
        await manager.attach('deployment', 'API', 'node', 'api', 'https://api.example', secret_ref='node-secret://provider')
    assert not client.method_calls and not manager.ensure.mock_calls


@pytest.mark.parametrize('output,returncode', [(b'private-provider-key', 1), (b'{"key":"line\\nkey"}', 0),
    (b'{"key":""}', 0), (b'[]', 0), (b'x' * 65537, 0)])
def test_reader_errors_never_include_output(tmp_path, monkeypatch, output, returncode):
    connector = connector_module.Connector(tmp_path)
    credentials = connector.module('credentials')
    monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', str(tmp_path / 'fleet'))
    monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(tmp_path / 'store'))
    monkeypatch.setattr(credentials.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, returncode, output, b'private-provider-key'))
    with pytest.raises(ValueError) as caught: credentials.read('node-secret://provider', 'https://api.example/v1')
    assert str(caught.value) == credentials.ERROR


def test_real_node_credential_pipe_discovery_rotation_and_revocation(tmp_path, monkeypatch):
    binary = os.environ.get('FLEET_CREDENTIAL_TEST_BINARY')
    if not binary:
        pytest.skip('Opt-in real Fleet credential pipe; build Fleet and set FLEET_CREDENTIAL_TEST_BINARY')
    received = []
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            received.append(self.headers.get('Authorization'))
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"synthetic-api-model"}]}')
    state = tmp_path / 'node'
    store = state / 'apps' / 'test-fleet' / 'model-credentials'
    monkeypatch.setenv('PANTHEON_FLEET_EXECUTABLE', binary)
    monkeypatch.setenv('PANTHEON_MODEL_CREDENTIALS', str(store))
    connector = connector_module.Connector(tmp_path / 'connector')
    def provision(endpoint, key, replace=False):
        args=[binary, 'credentials', 'put', '--state-dir', str(state), '--fleet', 'test-fleet',
              '--name', 'provider', '--endpoint', endpoint, '--stdin'] + (['--replace'] if replace else [])
        result=subprocess.run(args, input=key.encode(), capture_output=True, check=True)
        assert key.encode() not in result.stdout + result.stderr
    with serve(Engine) as endpoint:
        provision(endpoint, 'synthetic-key-one')
        connector.configure(dict(engine='api', endpoint=endpoint, secret_ref='node-secret://provider'))
        revision = connector.revision
        assert connector.discover()['models'] == [{'id': 'synthetic-api-model'}]
        provision(endpoint, 'synthetic-key-two', replace=True)
        assert connector.discover()['models'] == [{'id': 'synthetic-api-model'}]
        assert connector.revision == revision
        assert received == ['Bearer synthetic-key-one', 'Bearer synthetic-key-two']
        assert 'synthetic-key' not in connector.path.read_text()
        # A changed API prefix must fail before any socket is opened.
        connector.configure(dict(engine='api', endpoint=endpoint + '/other', secret_ref='node-secret://provider'))
        with pytest.raises(ValueError, match='not authorized'): connector.discover()
        assert len(received) == 2
        connector.configure(dict(engine='api', endpoint=endpoint, secret_ref='node-secret://provider'))
        subprocess.run([binary, 'credentials', 'delete', '--state-dir', str(state), '--fleet', 'test-fleet', '--name', 'provider'], capture_output=True, check=True)
        with pytest.raises(ValueError, match='unavailable'): connector.discover()
        assert len(received) == 2
