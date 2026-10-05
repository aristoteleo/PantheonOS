"""Actual local owner, Controller, Runner and two native Apps; no Hub fixture."""
import asyncio
import base64
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys

import nats
import pytest

from pantheon.apps.client import AppClient
from pantheon.apps.dependency_assembly import DependencyAuthority, DependencyStarter
from pantheon.apps.dependency_client import DependencyClient, DependencyCallError
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact, CHUNK_SIZE
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries


CONSUMER = '''
import hmac, json, os, ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from sdk.runtime_config import load_runtime_configuration
from sdk.dependency_client import DependencyClient, DependencyCallError
cfg=load_runtime_configuration(required=True)
client=DependencyClient(cfg.credentials['shell'], ssl.create_default_context(cadata=cfg.values['trust_roots_pem']))
assert not any(k in os.environ for k in ('FLEET_KEY','FLEET_APP_GATEWAY_KEY'))
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'ready')
 def do_POST(self):
  if not hmac.compare_digest(self.headers.get('X-Fleet-RPC-Token',''),os.environ['PANTHEON_APP_RPC_TOKEN']):
   self.send_response(403);self.end_headers();return
  q=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  try:
   result=client.invoke('run_command',q['args'],timeout_seconds=5)
  except DependencyCallError as e:
   result={'success':False,'status':e.status,'outcome_unknown':e.outcome_unknown}
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps(result).encode())
ThreadingHTTPServer(('127.0.0.1',int(os.environ['PANTHEON_PORT_HTTP'])),Handler).serve_forever()
'''


def consumer_package(path, root):
    path.mkdir()
    sdk = path / 'sdk'
    sdk.mkdir()
    (sdk / '__init__.py').write_text('')
    for name in ('runtime_config.py', 'dependency_client.py'):
        shutil.copy2(root / 'pantheon/apps' / name, sdk / name)
    (path / 'consumer.py').write_text(CONSUMER)
    manifest = {'apiVersion': 2, 'id': 'local-rpc-consumer', 'version': '1.0.0',
                'runtime': 'process', 'execution': {'protocol': 1, 'manifest': 'fleet.json'},
                'dependencies': {'shell': {'range': '*', 'uses': ['shell@1']}}}
    definition = {'protocol': 1, 'app_id': manifest['id'], 'version': manifest['version'],
                  'components': [{'name': 'backend', 'runtime': 'process',
                    'argv': ['python3', '${PACKAGE}/consumer.py'], 'ports': {'http': 0},
                    'configuration': {'values': {'trust_roots_pem': {'required': True}},
                                      'credentials': {'shell': {'required': True}}},
                    'readiness': {'argv': ['python3', '-c',
                        "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PANTHEON_PORT_HTTP'],timeout=1).read()"],
                        'timeout_seconds': 10}}]}
    for name, value in (('app.json', manifest), ('fleet.json', definition)):
        (path / name).write_text(json.dumps(value))


@pytest.mark.asyncio
async def test_real_local_app_dependency_and_revocation(tmp_path, binaries):
    root = Path(__file__).resolve().parents[1]
    shell = tmp_path / 'shell'
    built = await asyncio.to_thread(subprocess.run, [sys.executable,
        str(root / 'apps/shell/build_managed.py'), '--output', str(shell), '--os', sys.platform,
        '--arch', {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]],
        cwd=root, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    consumer = tmp_path / 'consumer'
    consumer_package(consumer, root)
    async with LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path) as runtime:
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        client = AppClient(nc, info.fleet_id)
        class Wire(FleetLifecycle):
            async def _request(self, node, method, **kwargs):
                value = await client.lifecycle(node, method, **kwargs)
                assert 'error' not in value, value
                return value
        wire = Wire(None)
        async def wait_operation(receipt):
            for _ in range(400):
                state = await wire.status(info.node_id)
                op = state['operations'][receipt['request']['operation_id']]
                if op['state'] == 'succeeded':
                    return next((i for i in state['instances'].values()
                                 if i['digest'] == receipt['request']['digest']), None)
                assert op['state'] in ('queued', 'running'), op
                await asyncio.sleep(.05)
            pytest.fail('App operation did not complete')
        async def action(digest, name, generation=0, **kwargs):
            return await wait_operation(await wire.submit(info.node_id, name, digest,
                generation=generation, **kwargs))
        async def stage(path):
            data, digest = build_artifact(path)
            for offset in range(0, len(data), CHUNK_SIZE):
                await wire._request(info.node_id, 'stage', digest=digest, offset=offset,
                    data=base64.b64encode(data[offset:offset+CHUNK_SIZE]).decode())
            return digest
        async def invoke(app, instance, method, args):
            value = await client.invoke(info.node_id, app, {
                'instance_id': instance['instance_id'], 'revision': instance['digest'],
                'generation': instance['generation']}, method, args, 10)
            assert 'error' not in value, value
            return value['response']
        try:
            shell_digest, consumer_digest = await stage(shell), await stage(consumer)
            provider = await action(shell_digest, 'start')
            acquired = await invoke('shell', provider, 'resource_session_acquire',
                {'owner_ref': 'local-consumer', 'lease_id': 'a'*64, 'kind': 'shell', 'ttl_seconds': 300})
            assert acquired['success'], acquired
            session = acquired['result']['session_id']
            await action(consumer_digest, 'install')
            prepared = await action(consumer_digest, 'prepare_start',
                                    operation_id='local-prepare')
            identity = {'node_id': info.node_id, 'instance_id': prepared['instance_id'],
                        'revision': consumer_digest, 'generation': prepared['generation']}
            issuer = DependencyAuthority(credential=RuntimeCredential(info.controller,
                (runtime.root / 'owner.key').read_text().strip()), tls_context=info.tls_context(),
                rpc_origin=info.controller)
            starter = DependencyStarter(wire, tmp_path / 'assembly', issuer)
            result = await starter.start(consumer=identity, preparation_id='local-prepare',
                operation_id='local-start', components={'backend': {'values': {
                    'trust_roots_pem': info.ca_certificate.read_text()}}}, bindings={'shell': {
                    'app_id': 'shell', 'component': 'backend', 'provider': {
                        'node_id': info.node_id, 'instance_id': provider['instance_id'],
                        'revision': shell_digest, 'generation': provider['generation'],
                        'component': 'backend', 'port': 'http'},
                    'methods': {'run_command': {'arguments': ['command', 'timeout'],
                                              'bound': {'shell_id': session}}}}})
            running = await wait_operation(result['operation'])
            called = await invoke('local-rpc-consumer', running, 'exercise',
                                  {'command': 'printf LOCAL_DEPENDENCY_OK', 'timeout': 3})
            assert called['success'] and 'LOCAL_DEPENDENCY_OK' in called['result']['output'], called
            forbidden = await invoke('local-rpc-consumer', running, 'exercise',
                                     {'command': 'printf WRONG_SESSION', 'shell_id': 'foreign'})
            assert forbidden == {'success': False, 'status': 403, 'outcome_unknown': False}
            record = json.loads(next((tmp_path / 'assembly').glob('*.json')).read_text())
            assert record['grants'] == {} and 'access_token' not in json.dumps(record)
            grant = record['renewals']['shell']['grant_id']
            await issuer.renew(grant)
            await issuer.revoke(grant)
            denied = await invoke('local-rpc-consumer', running, 'exercise',
                                  {'command': 'printf MUST_NOT_EXECUTE'})
            assert denied == {'success': False, 'status': 401, 'outcome_unknown': False}
            # A different still-valid grant must also stop at the exact consumer
            # lifetime boundary, even while the shared provider remains ready.
            second = await issuer.issue({**record['plan']['requests']['shell'],
                'operation_id': 'local-lifetime', 'preparation_id': ''})
            stale = DependencyClient(RuntimeCredential(second['endpoint'], second['access_token']),
                                     info.tls_context())
            await action(consumer_digest, 'stop', running['generation'])
            with pytest.raises(DependencyCallError) as error:
                await asyncio.to_thread(stale.invoke, 'run_command', {'command': 'printf STALE'},
                                        timeout_seconds=5)
            assert error.value.status == 409 and not error.value.outcome_unknown
            await issuer.revoke(second['grant_id'])
            assert (await invoke('shell', provider, 'resource_session_release',
                {'owner_ref': 'local-consumer', 'lease_id': 'a'*64}))['success']
        finally:
            try:
                state = await wire.status(info.node_id)
                for instance in state['instances'].values():
                    if instance['state'] in ('ready', 'prepared', 'failed'):
                        await action(instance['digest'], 'stop', instance['generation'])
            finally:
                await nc.close()
