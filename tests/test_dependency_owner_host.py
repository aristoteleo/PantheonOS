"""The packaged owner process speaks real HTTPS and authenticated NATS.

Controller, Hub and node replies are deterministic fixtures. The production
package, join, RPC client, policy assembly, journals and HTTP host are exercised;
this does not claim an enrolled Fleet or deployed Agent/GUI acceptance.
"""
import asyncio
import base64
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx
import nats
import nkeys
import pytest
import pytest_asyncio

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.platform.dependency_host import DependencyBindingHost
from pantheon.platform.dependency_package import build_package
from test_live_dependency_bindings import fixture


def credentials(subjects=None):
    return _credentials(subjects)[:4]


def jetstream_credentials(subjects):
    """Include a separate system account required by operator-mode JetStream."""
    return _credentials(subjects, jetstream=True)


def _credentials(subjects=None, *, jetstream=False):
    def pair(kind):
        seed = nkeys.encode_seed(os.urandom(32), kind)
        return nkeys.from_seed(seed), seed.decode()
    operator, _ = pair(nkeys.PREFIX_BYTE_OPERATOR)
    account, _ = pair(nkeys.PREFIX_BYTE_ACCOUNT)
    user, seed = pair(nkeys.PREFIX_BYTE_USER)
    def jwt(issuer, subject, kind, **details):
        claim = dict(iat=int(time.time())-1, iss=issuer.public_key.decode(),
                     sub=subject.public_key.decode(), name='fixture',
                     nats=dict(type=kind, version=2, **details))
        claim['jti'] = base64.b32encode(hashlib.sha256(json.dumps(claim).encode()).digest()).decode().rstrip('=')
        def b64(raw):
            return base64.urlsafe_b64encode(raw).decode().rstrip('=')
        raw = b64(b'{"typ":"JWT","alg":"ed25519-nkey"}') + '.' + b64(json.dumps(claim).encode())
        return raw + '.' + b64(issuer.sign(raw.encode()))
    system, _ = pair(nkeys.PREFIX_BYTE_ACCOUNT)
    opjwt = jwt(operator, operator, 'operator',
                **({'system_account': system.public_key.decode()} if jetstream else {}))
    limits = dict(subs=-1, data=-1, payload=-1,
                  imports=-1, exports=-1, conn=-1, leaf=-1, wildcards=True)
    if jetstream:
        limits.update(mem_storage=64*1024*1024, disk_storage=64*1024*1024,
                      streams=32, consumer=128)
    accjwt = jwt(operator, account, 'account', limits=limits)
    subjects = subjects or ['fleet.owner.>', '_INBOX_owner.>']
    userjwt = jwt(account, user, 'user', pub={'allow': subjects},
                  sub={'allow': subjects}, subs=-1, data=-1, payload=-1)
    creds = f'-----BEGIN NATS USER JWT-----\n{userjwt}\n------END NATS USER JWT------\n\n-----BEGIN USER NKEY SEED-----\n{seed}\n------END USER NKEY SEED------\n'
    return opjwt, account.public_key.decode(), accjwt, creds, system.public_key.decode(), jwt(operator, system, 'account')


@pytest_asyncio.fixture
async def wire(tmp_path, monkeypatch):
    f = fixture(tmp_path / 'fixture', monkeypatch)
    # The independent child uses the real clock when checking provider TTLs.
    f.clock.now = int(time.time())
    binary = shutil.which('nats-server')
    if not binary:
        pytest.skip('nats-server required')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    op, account, accjwt, creds = credentials()
    (tmp_path / 'operator.jwt').write_text(op)
    (tmp_path / 'user.creds').write_text(creds)
    (tmp_path / 'nats.conf').write_text(
        f'host: 127.0.0.1\nport: {port}\noperator: "{tmp_path / "operator.jwt"}"\n'
        f'resolver: MEMORY\nresolver_preload: {{ {account}: "{accjwt}" }}\n')
    log = (tmp_path / 'nats.log').open('w+')
    proc = subprocess.Popen([binary, '-c', str(tmp_path / 'nats.conf')], stdout=log, stderr=log)
    nc = None
    server = None
    thread = None
    async def ignore_error(exc):
        pass
    try:
        for _ in range(60):
            if proc.poll() is not None:
                log.seek(0)
                pytest.fail('NATS fixture exited: ' + log.read())
            try:
                nc = await nats.connect(servers=[f'nats://127.0.0.1:{port}'],
                    user_credentials=str(tmp_path / 'user.creds'), inbox_prefix=b'_INBOX_owner',
                    max_reconnect_attempts=0, connect_timeout=.1, error_cb=ignore_error)
                break
            except Exception:
                await asyncio.sleep(.05)
        assert nc is not None, 'authenticated fixture join failed'
        calls = []
        async def node_call(msg):
            request = json.loads(msg.data)
            node = msg.subject.split('.')[3]
            calls.append((node, request))
            try:
                assert request['type'] == 'app_lifecycle' and request['protocol'] == 1
                method = request['method']
                if method == 'status':
                    result = await f.lifecycle.status(node)
                elif method == 'app_manifest':
                    manifest = copy.deepcopy(f.manifests[request['revision']])
                    manifest.setdefault('version', '1.0.0')
                    result = dict(protocol=1, revision=request['revision'], manifest=manifest,
                                  definition={'app_id': manifest['id'], 'version': manifest['version']})
                elif method == 'invoke':
                    binding = {k: request[k] for k in ('instance_id', 'revision', 'generation')}
                    binding.update(node_id=node, component='backend', port='http')
                    receipt = await f.lifecycle.resource_session(request['app_id'], binding,
                        request['payload']['method'], request['payload']['args'])
                    result = {'response': {'success': True, 'result': receipt}}
                else:
                    raise AssertionError(method)
            except Exception as exc:
                result = {'error': type(exc).__name__}
            await msg.respond(json.dumps(result).encode())
        await nc.subscribe('fleet.owner.node.*.cmd', cb=node_call)
        await nc.flush()

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'owner fixture')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
                .not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                .sign(key, hashes.SHA256()))
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        (tmp_path / 'cert.pem').write_text(pem)
        (tmp_path / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(fleet='owner', joins=0, requests=[])
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.dispatch()
            def do_PATCH(self):
                self.dispatch()
            def do_DELETE(self):
                self.dispatch()
            def dispatch(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or 'null')
                status = 200
                try:
                    state.requests.append((self.path, self.headers.get('Authorization'), body))
                    if self.path == '/controller/join':
                        assert body == {'key': 'controller-only-key'}
                        state.joins += 1
                        result = dict(fleet_id=state.fleet, nats_url=f'nats://127.0.0.1:{port}', creds=creds)
                    else:
                        assert self.headers.get('Authorization') == 'Bearer hub-only-key'
                        base = '/hub/api/fleet/apps/dependency-grants'
                        assert self.path.startswith(base)
                        if self.command == 'POST':
                            operation = f.authority.issue(body)
                        elif self.command == 'PATCH':
                            operation = f.authority.renew(self.path[len(base)+1:], body['ttl_seconds'])
                        else:
                            operation = f.authority.revoke(self.path[len(base)+1:])
                            status = 204
                        result = asyncio.run_coroutine_threadsafe(operation, loop).result(10)
                    raw = json.dumps(result).encode() if status != 204 else b''
                except Exception:
                    status, raw = 403, b'private control failure'
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(tmp_path / 'cert.pem', tmp_path / 'key.pem')
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'https://127.0.0.1:{server.server_port}'
        state.config = RuntimeConfiguration(
            values={'dependency_binding': {'protocol': 1, 'trust_roots_pem': pem,
                'policies': {'approved': {'consumer': f.consumer, 'bindings': f.bindings}}}},
            credentials={'hub': RuntimeCredential(base+'/hub', 'hub-only-key'),
                         'controller': RuntimeCredential(base+'/controller', 'controller-only-key')},
            owner='owner', node_id='owner-node', instance_id='allocator',
            revision='d'*64, generation=1, component='backend')
        state.f, state.calls, state.tls = f, calls, ssl.create_default_context(cadata=pem)
        yield state
    finally:
        if server:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)
        if nc:
            await nc.close()
        proc.terminate()
        await asyncio.to_thread(proc.wait, 5)
        log.close()


def request(owner='one', operation='one'):
    return dict(policy_id='approved', owner_ref=owner, operation_id=operation, aliases=['shell', 'files'])


@pytest.mark.asyncio
async def test_host_real_control_lifetime_recovery_and_isolation(tmp_path, wire, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://do-not-use.invalid')
    monkeypatch.setenv('FLEET_KEY', 'ambient-secret')
    host = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'data')
    await host.start()
    credential_file = host.lifecycle._credentials_path
    assert credential_file.stat().st_mode & 0o077 == 0
    try:
        first = await host.bind_dependencies(**request())
        second = await host.bind_dependencies(**request('two', 'two'))
        assert len(wire.f.receipts) == 2
        assert len(wire.f.issued) == 4
        assert first['bindings']['shell'] != second['bindings']['shell']
        assert await host.bind_dependencies(**request()) == first
        with pytest.raises(AssemblyError):
            await host.bind_dependencies(**{**request(), 'aliases': ['unapproved']})
        duplicate = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'data')
        with pytest.raises(AssemblyError, match='writer'):
            await duplicate.start()
        assert wire.joins == 1
        assert all('ambient-secret' not in str(item) for item in wire.requests)
        assert 'hub-only-key' not in json.dumps(first)
        # Close waits for admitted calls; it does not retire healthy resources.
        original = host.service.bind_dependencies
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)
        host.service.bind_dependencies = waiting
        active = asyncio.create_task(host.bind_dependencies(**request()))
        await entered.wait()
        closing = asyncio.create_task(host.close())
        await asyncio.sleep(.01)
        assert not closing.done()
        with pytest.raises(AssemblyError, match='not accepting'):
            await host.bind_dependencies(**request())
        release.set()
        assert await active == first
        await closing
    finally:
        await host.close()
    assert not credential_file.exists()
    assert wire.f.authority.revoke.await_count == 0
    replacement = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'data')
    await replacement.start()
    try:
        assert await replacement.bind_dependencies(**request()) == first
        assert len(wire.f.receipts) == 2 and len(wire.f.issued) == 4
        # Exercise the real HTTPS PATCH/DELETE authority as well as issuance.
        grant = first['bindings']['files']
        renewed = await replacement.owner.authority.renew(grant['grant_id'], 900)
        assert renewed['grant_id'] == grant['grant_id']
        await replacement.owner.authority.revoke(grant['grant_id'])
        wire.f.authority.revoke.assert_awaited_once_with(grant['grant_id'])
    finally:
        await replacement.close()
    foreign = DependencyBindingHost(configuration=replace(wire.config, instance_id='other'),
                                    data_dir=tmp_path / 'data')
    with pytest.raises(AssemblyError, match='another deployment'):
        await foreign.start()
    assert foreign._lock is None


@pytest.mark.asyncio
async def test_close_releases_stopped_consumers_before_dropping_authority(tmp_path, wire):
    host = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'data')
    await host.start()
    credential_file = host.lifecycle._credentials_path
    try:
        await host.bind_dependencies(**request())
        await host.bind_dependencies(**request('two', 'two'))
        assert len(wire.f.receipts) == 2
        provider_before = copy.deepcopy(wire.f.states['provider-node'])
        wire.f.states['consumer-node']['instances']['consumer'].update(state='stopped', generation=3)
        await host.close()
        assert {r['state'] for r in wire.f.receipts.values()} == {'released'}
        assert wire.f.authority.revoke.await_count == 4
        assert wire.f.states['provider-node'] == provider_before
        assert not credential_file.exists() and host._lock is None
        before = len(wire.calls)
        await host.close()
        assert len(wire.calls) == before
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['consumer-unavailable', 'release-lost'])
async def test_failed_close_keeps_writer_and_recovers_original_receipts(tmp_path, wire, failure):
    host = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'data')
    await host.start()
    credential_file = host.lifecycle._credentials_path
    original_status = wire.f.lifecycle.status.side_effect
    original_session = wire.f.lifecycle.resource_session.side_effect
    try:
        await host.bind_dependencies(**request())
        original_ids = set(wire.f.receipts)
        wire.f.states['consumer-node']['instances']['consumer'].update(state='stopped', generation=3)
        if failure == 'consumer-unavailable':
            async def unavailable(node):
                if node == 'consumer-node':
                    raise OSError('private transport detail')
                return await original_status(node)
            wire.f.lifecycle.status.side_effect = unavailable
        else:
            async def lost(*args):
                result = await original_session(*args)
                if args[2] == 'resource_session_release':
                    raise OSError('lost acknowledgement with private details')
                return result
            wire.f.lifecycle.resource_session.side_effect = lost
        with pytest.raises(AssemblyError, match='shutdown requires recovery') as error:
            await host.close()
        assert 'private' not in str(error.value)
        assert credential_file.exists() and host._lock is not None
        with pytest.raises(AssemblyError, match='not accepting'):
            await host.bind_dependencies(**request('new', 'new'))
        duplicate = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'data')
        with pytest.raises(AssemblyError, match='writer'):
            await duplicate.start()
        if failure == 'consumer-unavailable':
            assert {r['state'] for r in wire.f.receipts.values()} == {'active'}
        wire.f.lifecycle.status.side_effect = original_status
        wire.f.lifecycle.resource_session.side_effect = original_session
        await host.close()
        assert set(wire.f.receipts) == original_ids
        assert {r['state'] for r in wire.f.receipts.values()} == {'released'}
        assert not credential_file.exists() and host._lock is None
    finally:
        wire.f.lifecycle.status.side_effect = original_status
        wire.f.lifecycle.resource_session.side_effect = original_session
        await host.close()


@pytest.mark.asyncio
async def test_foreign_fleet_join_fails_closed(tmp_path, wire):
    wire.fleet = 'someone-else'
    host = DependencyBindingHost(configuration=wire.config, data_dir=tmp_path / 'foreign')
    with pytest.raises(AssemblyError, match='join'):
        await host.start()
    assert host.lifecycle._credentials_path is None and host._lock is None
    assert not wire.calls


@pytest.mark.parametrize('change', ['http', 'missing-controller', 'policy', 'protocol', 'extra', 'bad-ca'])
def test_bad_owner_snapshot_is_rejected_before_storage(tmp_path, change):
    consumer = dict(node_id='node', instance_id='app', revision='a'*64, generation=1)
    provider = {**consumer, 'component': 'backend', 'port': 'http'}
    bindings = {'files': {'app_id': 'files', 'provider': provider,
                          'methods': {'read': {'arguments': ['path'], 'bound': {}}}}}
    config = RuntimeConfiguration(
        values={'dependency_binding': {'protocol': 1, 'policies': {'policy': {
            'consumer': consumer, 'bindings': bindings}}}},
        credentials={name: RuntimeCredential('https://example.test', 'private-key') for name in ('hub', 'controller')},
        instance_id='allocator', revision='a'*64, generation=1, component='backend', owner='owner', node_id='node')
    if change == 'http':
        config.credentials['controller'] = RuntimeCredential('http://example.test', 'private-key')
    elif change == 'missing-controller':
        del config.credentials['controller']
    elif change == 'policy':
        config.values['dependency_binding']['policies']['policy']['consumer']['generation'] = 0
    elif change == 'protocol':
        config.values['dependency_binding']['protocol'] = True
    elif change == 'extra':
        config.values['unapproved'] = 'private-key'
    else:
        config.values['dependency_binding']['trust_roots_pem'] = 'private-key'
    with pytest.raises(AssemblyError) as error:
        DependencyBindingHost(configuration=config, data_dir=tmp_path / 'absent')
    assert 'private-key' not in str(error.value)
    assert not (tmp_path / 'absent').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('consumer_stop', ['live', 'stopped', 'release-lost'])
async def test_packaged_http_host_is_authenticated_and_registers_only_allocator(tmp_path, wire, consumer_stop):
    package = build_package(tmp_path / 'package', 'darwin-arm64' if sys.platform == 'darwin' else 'linux-amd64')
    from pantheon.apps.schema import parse_manifest
    from pantheon.apps.lifecycle import build_artifact
    manifest = json.loads((package / 'app.json').read_text())
    assert parse_manifest(manifest).id == 'dependency-binding'
    definition = json.loads((package / 'fleet.json').read_text())
    assert definition['hooks']['before_stop']['component'] == 'backend'
    payload, digest = build_artifact(package)
    assert payload and len(digest) == 64
    cfg = wire.config
    resolved = dict(protocol=1, values=cfg.values,
        credentials={k: {'endpoint': v.endpoint, 'key': v.key} for k, v in cfg.credentials.items()},
        **{k: getattr(cfg, k) for k in ('owner', 'node_id', 'instance_id', 'revision', 'generation', 'component')})
    config_path = tmp_path / 'prepared.json'
    config_path.write_text(json.dumps(resolved))
    data = tmp_path / 'process-data'
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PANTHEON_', 'FLEET_', 'NATS_', 'PYTHONPATH'))}
    env.update(PANTHEON_APP_CONFIG=str(config_path), PANTHEON_FLEET_ID=cfg.owner,
        PANTHEON_NODE_ID=cfg.node_id, PANTHEON_INSTANCE_ID=cfg.instance_id,
        PANTHEON_APP_REVISION=cfg.revision, PANTHEON_INSTANCE_GENERATION=str(cfg.generation),
        PANTHEON_COMPONENT_NAME='backend', PANTHEON_PORT_HTTP='0', PANTHEON_APP_RPC_TOKEN='node-rpc-secret')
    log = (tmp_path / 'owner-process.log').open('w+')
    original_session = wire.f.lifecycle.resource_session.side_effect
    # A fresh interpreter loads only the built package. Reject Agent imports.
    boot = '''import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, *args):
  if name.startswith(('pantheon.agent', 'pantheon.chatroom', 'pantheon.factory', 'pantheon.team')):
   raise AssertionError('Agent import in owner service')
sys.meta_path.insert(0, Guard())
sys.path.insert(0, sys.argv[1])
sys.argv = ['host.py', *sys.argv[2:]]
runpy.run_module('host', run_name='__main__')
'''
    proc = subprocess.Popen([sys.executable, '-c', boot, str(package / '.fleet-runtime'),
        'start', '--package', str(package), '--data', str(data)], env=env, cwd=tmp_path,
        stdout=log, stderr=log)
    try:
        endpoint = data / 'backend-endpoint.json'
        for _ in range(200):
            if proc.poll() is not None:
                log.seek(0)
                pytest.fail('packaged owner exited: ' + log.read())
            if endpoint.exists():
                break
            await asyncio.sleep(.05)
        assert endpoint.exists()
        address = 'http://127.0.0.1:' + str(json.loads(endpoint.read_text())['port'])
        async with httpx.AsyncClient(base_url=address, trust_env=False, timeout=20) as client:
            health = (await client.get('/health')).json()
            assert health['methods'] == ['bind_dependencies', 'retire_dependencies'] and health['ready']
            body = {'method': 'bind_dependencies', 'args': request()}
            assert (await client.post('/rpc', json=body)).status_code == 403
            assert (await client.post('/_fleet/drain')).status_code == 403
            client.headers['X-Fleet-RPC-Token'] = 'node-rpc-secret'
            bad = await client.post('/rpc', json={'method': 'fleet_app_lifecycle', 'args': {}})
            assert bad.status_code == 400
            first = await client.post('/rpc', json=body)
            assert first.status_code == 200, first.text
            assert (await client.post('/rpc', json=body)).json() == first.json()
            assert len(wire.f.receipts) == 1
            assert 'hub-only-key' not in first.text and 'controller-only-key' not in first.text
            # Run the real lifecycle probe with the Runner's assigned port.
            # Corrupt mutable endpoint metadata must not redirect its token.
            hook_env = {**env, 'PANTHEON_PORT_HTTP': str(json.loads(endpoint.read_text())['port'])}
            endpoint.write_text(json.dumps({'port': 1, 'generation': 'wrong'}))
            for action in ('ready', 'drain'):
                if action == 'drain' and consumer_stop != 'live':
                    wire.f.states['consumer-node']['instances']['consumer'].update(state='stopped', generation=3)
                    if consumer_stop == 'release-lost':
                        async def lost(*args):
                            result = await original_session(*args)
                            if args[2] == 'resource_session_release':
                                raise OSError('lost release acknowledgement')
                            return result
                        wire.f.lifecycle.resource_session.side_effect = lost
                        waiting = (await client.post('/_fleet/drain')).json()
                        assert waiting['safe_to_stop'] is False, waiting
                        assert 'requires recovery' in waiting['message']
                        assert proc.poll() is None
                        wire.f.lifecycle.resource_session.side_effect = original_session
                hook = await asyncio.create_subprocess_exec(sys.executable,
                    str(package / '.fleet-runtime' / 'host.py'), action,
                    '--package', str(package), '--data', str(data), env=hook_env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(hook.communicate(), 10)
                assert hook.returncode == 0, stderr.decode()
                if action == 'drain':
                    assert json.loads(stdout)['safe_to_stop']
                    expected = 'active' if consumer_stop == 'live' else 'released'
                    assert {r['state'] for r in wire.f.receipts.values()} == {expected}
            assert (await client.post('/rpc', json=body)).status_code == 400
    finally:
        wire.f.lifecycle.resource_session.side_effect = original_session
        proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, 10)
        except subprocess.TimeoutExpired:
            proc.kill()
            await asyncio.to_thread(proc.wait, 5)
        log.close()
