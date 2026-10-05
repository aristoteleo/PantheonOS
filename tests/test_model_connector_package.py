"""The existing model Connector accepts the ordinary prepared App lifecycle."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.request import Request, urlopen

import httpx
import pytest

from pantheon.apps.lifecycle import build_artifact
from pantheon.models.connector_package import build_package
from test_model_services import connector_module
from test_model_platform_budget import vault, provision, payload, KEY
from test_model_dependency import model_endpoint

spec = importlib.util.spec_from_file_location('prepared_connector', Path(__file__).parents[1] / 'apps/model-service/prepared.py')
prepared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepared)


def configuration(value, credentials=None):
    return SimpleNamespace(values={'connector': value}, credentials=credentials or {})


def test_initialization_is_one_time_and_never_reconfigures_a_retained_service(tmp_path, monkeypatch):
    connector = connector_module.Connector(tmp_path)
    value = {'engine': 'api', 'endpoint': 'https://models.test/v1', 'secret_ref': 'node-secret://models'}
    prepared.initialize(connector, configuration(value))
    original = connector.path.read_bytes(), connector.path.stat().st_mtime_ns
    def forbidden(*args, **kwargs):
        raise AssertionError('Matching startup must not reconfigure an existing service')
    monkeypatch.setattr(connector, 'configure', forbidden)
    connector.accepting = False
    prepared.initialize(connector, configuration(value))
    assert not connector.accepting
    assert (connector.path.read_bytes(), connector.path.stat().st_mtime_ns) == original
    with pytest.raises(ValueError, match='conflicts with retained'):
        prepared.initialize(connector, configuration({**value, 'endpoint': 'https://another.test/v1'}))
    assert (connector.path.read_bytes(), connector.path.stat().st_mtime_ns) == original


@pytest.mark.parametrize('change', ['inline-key', 'credential-file', 'managed', 'credentials', 'extra-value', 'not-mapping'])
def test_prepared_configuration_rejects_ambient_or_extra_authority(tmp_path, change):
    connector = connector_module.Connector(tmp_path)
    config = configuration({'engine': 'api', 'endpoint': 'https://models.test/v1'})
    if change == 'inline-key': config.values['connector']['api_key'] = KEY
    elif change == 'credential-file': config.values['connector']['credential_file'] = '/private/key'
    elif change == 'managed': config.values['connector']['managed'] = {}
    elif change == 'credentials': config.credentials['hub'] = {'endpoint': 'https://hub.test', 'key': KEY}
    elif change == 'extra-value': config.values['unrelated'] = {}
    else: config.values['connector'] = [1, 2]
    with pytest.raises(ValueError) as error:
        prepared.initialize(connector, config)
    assert KEY not in str(error.value) and not connector.path.exists()


@pytest.mark.parametrize('platform', ['linux-amd64', 'darwin-arm64', 'windows-amd64'])
def test_artifact_uses_original_server_and_shared_configuration_reader(tmp_path, platform):
    root = Path(__file__).parents[1]
    package = build_package(tmp_path / 'package', platform)
    second = build_package(tmp_path / 'second', platform)
    assert build_artifact(package)[1] == build_artifact(second)[1]
    assert (package / 'server.py').read_bytes() == (root / 'apps/model-service/server.py').read_bytes()
    assert (package / '_fleet_runtime_config.py').read_bytes() == (root / 'pantheon/apps/runtime_config.py').read_bytes()
    definition = json.loads((package / 'fleet.json').read_text())
    assert definition['components'][0]['configuration'] == {'values': {'connector': {'required': True}}}
    assert definition['version'] == json.loads((package / 'app.json').read_text())['version'] == '0.1.25'
    assert (package / 'image_api.py').is_file()
    assert 'prepared.py' in definition['components'][0]['argv'][1]
    assert not (package / 'requirements.txt').exists()
    with pytest.raises(FileExistsError): build_package(package, platform)


@contextmanager
def process(tmp_path, package, value, *, vault=None, stale=False):
    snapshot = dict(protocol=1, owner='owner' if vault is None else vault.owner,
        node_id='node' if vault is None else vault.node_id, instance_id='connector', revision='a'*64,
        generation=2, component='backend', values={'connector': value}, credentials={})
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(snapshot))
    path.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'PYTHON'))}
    env.update({env_name: str(snapshot[name]) for name, env_name in {
        'owner': 'PANTHEON_FLEET_ID', 'node_id': 'PANTHEON_NODE_ID', 'instance_id': 'PANTHEON_INSTANCE_ID',
        'revision': 'PANTHEON_APP_REVISION', 'generation': 'PANTHEON_INSTANCE_GENERATION',
        'component': 'PANTHEON_COMPONENT_NAME'}.items()})
    if stale: env['PANTHEON_INSTANCE_GENERATION'] = '3'
    if vault is not None:
        env.update(PANTHEON_FLEET_EXECUTABLE=str(vault.executable),
                   PANTHEON_MODEL_CREDENTIALS=str(vault.state_dir / 'apps' / vault.owner / 'model-credentials'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env.update(PANTHEON_PORT_HTTP=str(port), PANTHEON_APP_CONFIG=str(path), PANTHEON_APP_RPC_TOKEN='fixture-rpc')
    with (tmp_path / 'process.log').open('a') as log:
        child = subprocess.Popen([sys.executable, str(package / 'prepared.py'), 'start', '--data', str(tmp_path / 'data')],
                                 cwd=tmp_path, env=env, stdout=log, stderr=log)
        try:
            yield child, f'http://127.0.0.1:{port}'
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)


def ready(child, base):
    for _ in range(200):
        assert child.poll() is None
        try:
            with urlopen(base + '/health', timeout=.2) as response:
                assert response.status == 200
            return
        except OSError:
            time.sleep(.025)
    raise AssertionError('Prepared connector did not become ready')


@pytest.mark.asyncio
async def test_budget_descriptor_starts_original_connector_without_configure_rpc(vault, tmp_path, model_endpoint):
    descriptor = await provision(vault, lambda _: httpx.Response(200, json=payload(model_endpoint.url + '/v1')))
    package = build_package(tmp_path / 'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    original = None
    for iteration in range(2):
        with process(tmp_path, package, descriptor['connector'], vault=vault) as (child, base):
            ready(child, base)
            req = Request(base + '/rpc', data=json.dumps({'method': 'discover', 'args': {}}).encode(),
                          headers={'Content-Type': 'application/json', 'X-Fleet-RPC-Token': 'fixture-rpc'})
            # Owner RPC authentication uses the original Connector's header.
            with urlopen(req, timeout=3) as response:
                found = json.load(response)
            assert found['models'][0]['id'] == 'example:8b'
            req = Request(base + '/v1/chat/completions', data=json.dumps({'model': 'example:8b',
                          'messages': [{'role': 'user', 'content': 'Reply once'}], 'stream': True}).encode(),
                          headers={'Content-Type': 'application/json', 'X-Model-Config': found['config_revision'],
                                   'X-Model-Request': f'prepared-budget-{iteration}'})
            with urlopen(req, timeout=3) as response:
                assert b'scoped reply' in response.read()
        state = tmp_path / 'data/connector.json'
        current = state.read_bytes(), state.stat().st_mtime_ns
        assert original is None or current == original
        original = current
    assert KEY not in (tmp_path / 'process.log').read_text()
    assert KEY not in (tmp_path / 'data/connector.json').read_text()


def test_stale_generation_does_not_initialize_connector(tmp_path):
    package = build_package(tmp_path / 'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    with process(tmp_path, package, {'engine': 'api', 'endpoint': 'https://models.test/v1'}, stale=True) as (child, _):
        assert child.wait(timeout=5) != 0
    assert not (tmp_path / 'data/connector.json').exists()
    assert 'invalid or stale' in (tmp_path / 'process.log').read_text()
