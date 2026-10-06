"""Full ordinary Fleet tools: owned real transport, no ambient identity fallback."""
import asyncio
import base64
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.fleet.build_managed import build
from pantheon.apps.builtin.fleet.fleet import FleetToolSet
from pantheon.apps.builtin.fleet.managed import create_service, Controller
from pantheon.apps.local_agent import native_platform
from pantheon.apps.runtime_config import RuntimeConfiguration, RuntimeCredential
from pantheon.apps.tool_profiles import compile_tool_profile
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries, assert_stopped


def test_fleet_package_preserves_tools_and_is_importable_without_agent(tmp_path):
    package = build(tmp_path/'release', native_platform())
    manifest = json.loads((package/'app.json').read_text())
    assert {tool['name'] for tool in manifest['provides']['tools']} == FleetToolSet().functions.keys()
    profile, policy, declaration = compile_tool_profile(manifest, alias='fleet', uses=['fleet-management@1'])
    assert {'fleet_hpc', 'fleet_hpc_cluster', 'fleet_hpc_service', 'fleet_update_nodes',
            'run_on_node', 'transfer'} <= {item['name'] for item in profile['functions']}
    vendor = package/'backend/_vendor'
    assert not (vendor/'pantheon/agent.py').exists()
    assert not (vendor/'pantheon/settings.py').exists()
    assert not (vendor/'pantheon/chatroom').exists()
    code = '''import sys
sys.path.insert(0, sys.argv[1])
from pantheon.apps.builtin.fleet.managed import create_service
from pantheon.apps.builtin.fleet.fleet import FleetToolSet
from pantheon.apps.builtin.fleet import hpc, update
assert 'pantheon.agent' not in sys.modules
assert 'pantheon.settings' not in sys.modules
assert 'fleet_hpc_cluster' in FleetToolSet().functions
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(vendor)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_prepared_fleet_real_node_execution_and_disconnect(tmp_path, binaries, monkeypatch):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    state = tmp_path/'private'; state.mkdir()
    for key in ('FLEET_CONTROLLER_URL', 'FLEET_KEY', 'FLEET_NATS_URL', 'FLEET_CREDS'):
        monkeypatch.setenv(key, 'must-not-borrow')
    def forbidden(*args, **kwargs):
        pytest.fail('Prepared Fleet borrowed the global resolver')
    monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', forbidden)
    async with LocalFleet(tmp_path/'profile', binaries, workspace=workspace) as runtime:
        info, children = runtime.coordinates, list(runtime._children)
        config = RuntimeConfiguration({'fleet': {'bus': {'auth': 'creds-base64'},
            'controller_ca_pem': info.ca_certificate.read_text()}}, {
            'fleet': RuntimeCredential(info.nats, base64.b64encode(info.credentials.read_bytes()).decode()),
            'controller': RuntimeCredential(info.controller, (runtime.root/'owner.key').read_text().strip())},
            'fleet-tools', 'a'*64, 1, 'backend', info.fleet_id, info.node_id)
        service = await create_service(config, workspace, state)
        connection = service._nc
        try:
            await service.run_setup()
            assert (await service.fleet_status())['nodes_total'] == 1
            assert await service._resolve_local_node([]) == info.node_id
            result = await service.run_on_node(info.node_id, 'printf OWNED_FLEET_OK')
            assert result['success'] and 'OWNED_FLEET_OK' in result['stdout'], result
            assert isinstance(await service._owned_controller.mint_join_token(), str)
            # Reuse original update and HPC validation, bound to this resolver.
            result = await service.fleet_update_nodes(['foreign-node'], tag='test')
            assert not result['success'] and 'not in' in result['error']
            result = await service.fleet_hpc_cluster(info.node_id, 'status', cluster_id='test')
            assert not result['success']  # no signed-in cluster in this isolated node
            assert list(state.glob('.app-bus-*.creds'))
            await connection.close()
            result = await service.fleet_status()
            assert not result['success'] and 'unavailable' in result['error']
        finally:
            await service.cleanup()
        assert connection.is_closed and not list(state.glob('.app-bus-*.creds'))
        assert service._owned_controller._http.is_closed
        assert not (await service.run_on_node(info.node_id, 'true'))['success']
    assert_stopped(children, info)


@pytest.mark.asyncio
async def test_owned_fleet_shutdown_joins_transfer_tasks():
    nc = SimpleNamespace(is_connected=True, jetstream=lambda: object())
    resolver = SimpleNamespace(_explicit_connection=True, _nc=nc, _fleet='owned', close=AsyncMock())
    controller = SimpleNamespace(close=AsyncMock())
    service = FleetToolSet(resolver=resolver, controller=controller)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def transfer():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            await release.wait()
            finished.set()
    worker = asyncio.create_task(transfer())
    service._transfer_tasks['test'] = worker
    await entered.wait()
    closing = asyncio.create_task(service.cleanup())
    await asyncio.sleep(0)
    assert not closing.done() and not resolver.close.called
    release.set()
    await closing
    assert finished.is_set() and worker.done()
    resolver.close.assert_awaited_once()
    controller.close.assert_awaited_once()


@pytest.mark.parametrize('endpoint,ca', [('http://remote.invalid', None),
    ('https://user:pass@example.com', None), ('https://example.com/path', None),
    ('https://example.com', 'not a certificate'), ('http://127.0.0.1', 'not a certificate')])
def test_controller_rejects_ambiguous_authority(endpoint, ca):
    with pytest.raises(ValueError):
        Controller(RuntimeCredential(endpoint, 'key'), ca)


@pytest.mark.asyncio
async def test_explicit_hpc_and_update_do_not_use_environment(monkeypatch):
    from pantheon.apps.builtin.fleet import hpc, update
    from test_fleet_hpc import Resolver, record
    def forbidden(*args, **kwargs):
        pytest.fail('Explicit management used ambient Controller')
    monkeypatch.setattr(hpc, 'mint_join_token', forbidden)
    monkeypatch.setattr(update, 'latest_release', forbidden)
    resolver = Resolver([record('login')])
    mint = AsyncMock(side_effect=['owned-a', 'owned-b'])
    result = await hpc.launch(resolver, 'login', partition='cpu', count=2, token_factory=mint)
    assert result['success'] and mint.await_count == 2
    assert [data['request']['join_token'] for _, _, data in resolver._client.sent] == ['owned-a', 'owned-b']
    lookup = AsyncMock(return_value='fleet-owned-version')
    result = await update.update_nodes(resolver, release_lookup=lookup)
    assert result['tag'] == 'fleet-owned-version'
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_fleet_package_native_install_rpc_stop_reopen(tmp_path, binaries):
    import nats
    from pantheon.apps.client import AppClient
    from pantheon.apps.credentials import RemoteAppCredentialVault
    from pantheon.apps.lifecycle import FleetLifecycle, ConfigurationBusy
    from pantheon.apps.resolver import AppInstanceResolver
    package = build(tmp_path/'fleet-release', native_platform())
    workspace = tmp_path/'workspace'; workspace.mkdir()
    async with LocalFleet(tmp_path/'profile', binaries, workspace=workspace) as runtime:
        info, children = runtime.coordinates, list(runtime._children)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                               inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(workspace), connection=nc)
        wire, client = FleetLifecycle(resolver), AppClient(nc, info.fleet_id)
        digest, current = None, None
        async def action(name, generation=0, **kwargs):
            receipt = await wire.submit(info.node_id, name, digest, generation=generation, **kwargs)
            async with asyncio.timeout(180):
                while True:
                    status = await wire.status(info.node_id)
                    op = status['operations'][receipt['request']['operation_id']]
                    if op['state'] == 'succeeded':
                        return next((i for i in status['instances'].values() if i['digest'] == digest), None)
                    assert op['state'] in ('queued', 'running'), op
                    await asyncio.sleep(.1)
        async def invoke(method, **args):
            response = await client.invoke(info.node_id, 'fleet', {
                'instance_id': current['instance_id'], 'revision': digest,
                'generation': current['generation']}, method, args, 30)
            assert not response.get('error'), response
            assert response['response'].get('success'), response
            return response['response']['result']
        try:
            vault = RemoteAppCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id)
            refs = {}
            for alias, endpoint, key in (
                    ('fleet', info.nats, base64.b64encode(info.credentials.read_bytes()).decode()),
                    ('controller', info.controller, (runtime.root/'owner.key').read_text().strip())):
                ref = 'node-secret://management-'+alias
                await vault.ensure_async(ref, endpoint, key)
                refs[alias] = {'ref': ref, 'endpoint': endpoint}
            digest = await wire.stage(info.node_id, package)
            current = await action('install')
            previous = None
            for cycle in (1, 2):
                operation = 'prepare-fleet-'+str(cycle)
                prepared = await action('prepare_start', current['generation'] if current else 0, operation_id=operation)
                async with asyncio.timeout(10):
                    while True:
                        try:
                            await wire.configure(info.node_id, instance_id=prepared['instance_id'], revision=digest,
                                generation=prepared['generation'], preparation_id=operation,
                                components={'backend': {'values': {'fleet': {'bus': {'auth': 'creds-base64'},
                                    'controller_ca_pem': info.ca_certificate.read_text()}}, 'credentials': refs}})
                            break
                        except ConfigurationBusy:
                            await asyncio.sleep(.05)
                current = await action('start', prepared['generation'], start_preparation_id=operation)
                assert current['state'] == 'ready' and previous in (None, current['instance_id'])
                previous = current['instance_id']
                assert (await invoke('fleet_status'))['nodes_total'] == 1
                result = await invoke('run_on_node', node_id=info.node_id, code='printf PACKAGED_FLEET_OK')
                assert result['success'] and 'PACKAGED_FLEET_OK' in result['stdout'], result
                result = await invoke('fleet_list_apps', node_id=info.node_id)
                assert result['success'] and any(i['app_id'] == 'fleet' for i in result['instances']), result
                current = await action('stop', current['generation'])
                assert current['state'] == 'stopped'
                assert not list((runtime.root/'node').rglob('.app-bus-*.creds'))
        finally:
            try:
                if digest:
                    status = await wire.status(info.node_id)
                    instance = next((i for i in status['instances'].values() if i['digest'] == digest), None)
                    if instance and instance['state'] != 'stopped':
                        await action('stop', instance['generation'])
            finally:
                await resolver.close()
    assert_stopped(children, info)


@pytest.mark.asyncio
async def test_fleet_inventory_uses_exact_owned_desktop_generation(monkeypatch):
    from datetime import datetime, timezone
    from pantheon.apps.builtin.fleet.inventory import inventory_from_records
    import builtins
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == 'pantheon.apps.proxy':
            pytest.fail('Owned Fleet inventory imported the ambient proxy')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    invoke = AsyncMock(return_value={'response': {'success': True, 'result': {
        'success': True, 'instances': [{'app_id': 'browser', 'kind': 'window', 'window_id': 'w1'}]}}})
    resolver = SimpleNamespace(_ensure_client=AsyncMock(), _client=SimpleNamespace(invoke=invoke))
    record = {'node_id': 'node-owned', 'name': 'Owned', 'last_seen': datetime.now(timezone.utc).isoformat(),
              'state': {'status': 'online', 'instances': [{'app_id': 'desktop', 'health': 'healthy',
                  'instance_id': 'desktop-owned', 'revision': 'a'*64, 'generation': 4}]}}
    result = await inventory_from_records([record], resolver=resolver)
    assert [i['app_id'] for i in result['instances']] == ['desktop', 'browser']
    invoke.assert_awaited_once_with('node-owned', 'desktop', {
        'instance_id': 'desktop-owned', 'revision': 'a'*64, 'generation': 4}, 'fleet_instances', {}, 4)
    invoke.return_value = {'error': 'stale generation'}
    result = await inventory_from_records([record], resolver=resolver)
    assert [i['app_id'] for i in result['instances']] == ['desktop']
    assert result['warnings'][0]['error'] == 'stale generation'
