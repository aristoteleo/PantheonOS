"""Full release on real profile Fleet, original models, and generic restart.

The only service fixture is the upstream model. No Hub, directory, lifecycle,
grant or Agent substitutes; this is not yet the shipped CLI/Desktop launcher.
"""
import asyncio
import base64
from contextlib import AsyncExitStack
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import socket
import sys

import nats
import pytest

from pantheon.apps.agent_deployment import compose_selected_deployment, plan_local_agent_restart
from pantheon.apps.dependency_assembly import DependencyAuthority, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_restart import plan_restart
from pantheon.apps.lifecycle import CHUNK_SIZE, ConfigurationBusy, FleetLifecycle, build_artifact
from pantheon.apps.resolver import AppInstanceResolver, NotJoinedError
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.connector_package import build_package as build_connector
from pantheon.models.credentials import RemoteModelCredentialVault
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.manager import ModelServiceManager
from pantheon.platform.dependency_package import build_package as build_allocator
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.model_dependency_package import build_package as build_access
from test_agent_application import TEMPLATE
from test_agent_launch import prepared
from test_agent_release import release
from test_local_fleet import binaries
from test_local_model_http import model_endpoint


@pytest.mark.asyncio
@pytest.mark.parametrize('restart_profile', [False, True], ids=['agent-generation', 'whole-profile'])
async def test_local_full_agent_chat_restart_and_shared_models(tmp_path, binaries, release, model_endpoint, monkeypatch, restart_profile):
    target = sys.platform + '-' + {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    packages = {'agent': release[0], 'allocator': build_allocator(tmp_path / 'allocator', target),
                'model-access': build_access(tmp_path / 'access', target),
                'connector': build_connector(tmp_path / 'connector', target)}
    source = Path(__file__).resolve().parents[1]
    shell = tmp_path / 'shell'
    built = await asyncio.to_thread(subprocess.run, [sys.executable, str(source / 'apps/shell/build_managed.py'),
        '--output', str(shell), '--os', sys.platform, '--arch', target.split('-')[1]],
        cwd=source, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    packages['shell'] = shell
    model_endpoint.tool_command = 'printf LOCAL_AGENT_TOOL_OK'
    (tmp_path / 'workspace').mkdir()
    # Ambient installed-Fleet settings cannot redirect an explicit local owner.
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://must-not-join.invalid')
    monkeypatch.setenv('FLEET_KEY', 'must-not-borrow')
    monkeypatch.setenv('NATS_SERVERS', 'nats://127.0.0.1:1')
    async with AsyncExitStack() as profiles:
        runtime = await profiles.enter_async_context(LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path))
        info = runtime.coordinates
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
        wire = FleetLifecycle(resolver)
        client = await resolver._ensure_client()
        async def action(digest, name, generation=0, **kwargs):
            receipt = await wire.submit(info.node_id, name, digest, generation=generation, **kwargs)
            async with asyncio.timeout(180):
                while True:
                    state = await wire.status(info.node_id)
                    op = state['operations'][receipt['request']['operation_id']]
                    if op['state'] == 'succeeded':
                        return next((i for i in state['instances'].values()
                            if i['digest'] == digest and i['scope'] == kwargs.get('scope', '')), None)
                    assert op['state'] in ('queued', 'running'), op
                    await asyncio.sleep(.1)
        async def configure(**kwargs):
            async with asyncio.timeout(5):
                while True:
                    try:
                        return await wire.configure(info.node_id, **kwargs)
                    except ConfigurationBusy:
                        await asyncio.sleep(.05)
        async def stage(path):
            data, digest = await asyncio.to_thread(build_artifact, path)
            for offset in range(0, len(data), CHUNK_SIZE):
                await wire._request(info.node_id, 'stage', digest=digest, offset=offset,
                    data=base64.b64encode(data[offset:offset + CHUNK_SIZE]).decode())
            return digest
        async def invoke(identity, method, **args):
            value = await client.invoke(info.node_id, 'agent', identity, method, args, 45)
            assert 'error' not in value, value
            response = value['response']
            assert response.get('success') is not False, response
            return response['result']
        try:
            digests = {name: await stage(path) for name, path in packages.items()}
            digest = digests['connector']
            await action(digest, 'install', scope='model-local')
            provider = await action(digest, 'prepare_start', operation_id='prepare-model', scope='model-local')
            await configure(instance_id=provider['instance_id'], revision=digest, generation=provider['generation'],
                preparation_id='prepare-model', components={'backend': {'values': {'connector': {
                    'engine': 'ollama', 'endpoint': model_endpoint.url}}}})
            provider = await action(digest, 'start', provider['generation'],
                start_preparation_id='prepare-model', scope='model-local')
            directory = LocalModelDirectory(tmp_path / 'directory', owner=info.fleet_id)
            await directory.initialize()
            manager = ModelServiceManager(client=directory, resolver=resolver)
            row = await manager.register_prepared(deployment_id='local', name='Local models',
                binding={'node_id': info.node_id, 'instance_id': provider['instance_id'], 'revision': digest,
                    'generation': provider['generation'], 'component': 'backend', 'port': 'http'},
                configuration={'engine': 'ollama', 'endpoint': model_endpoint.url},
                models=[{'id': 'example:8b', 'context_limit': 4096}])
            await directory.hub_request('PUT', '/api/model-services/routes/local', {
                'route_id': 'local', 'name': 'Local model', 'allowed_nodes': [info.node_id],
                'candidates': [{'deployment_id': 'local', 'model_id': 'example:8b'}], 'requires': {'tools': True}})
            key = (runtime.root / 'owner.key').read_text().strip()
            credential = RuntimeCredential(info.controller, key)
            ref = 'node-secret://local-owner'
            await RemoteModelCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id).ensure_async(
                ref, info.controller, key)
            vault = {'ref': ref, 'endpoint': info.controller}
            agent = prepared(tmp_path, model_endpoint.url)['values']['agent']
            agent['models'] = {}
            agent['dependencies']['profiles']['toolsets']['shell'] = {'alias': 'shell', 'functions': [{
                'name': 'run_command', 'description': 'Execute in this Agent shell', 'parameters': {
                    'type': 'object', 'properties': {'command': {'type': 'string'}, 'timeout': {'type': 'integer'}},
                    'required': ['command'], 'additionalProperties': False}}]}
            selection = await compose_selected_deployment(directory, spec={
                'owner': info.fleet_id, 'operation_id': 'local-agent', 'agent': agent,
                'tools': {'shell': {'app_id': 'shell',
                    'provider': {'$app': 'shell', 'component': 'backend', 'port': 'http'},
                    'methods': {'run_command': {'arguments': ['command', 'timeout'], 'bound': {}}},
                    'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}},
                'targets': {name: {'node_id': info.node_id, 'revision': digests[name], 'scope': name,
                                  'generation': 0} for name in ('agent', 'allocator', 'model-access')},
                'provider_apps': {'shell': {'node_id': info.node_id, 'revision': digests['shell'],
                    'scope': 'shared-shell', 'generation': 0, 'components': {}, 'bindings': {}}},
                'credentials': {'agent': {}, 'allocator': {'hub': vault, 'controller': vault},
                                'model-access': {'hub': vault}},
                'local_transport': {'origin': info.controller, 'trust_roots_pem': info.ca_certificate.read_text(),
                                    'directory_root': str(directory.root)}},
                fleet_tiers={'normal': 'fleet-route://local'})
            authority = DependencyAuthority(credential=credential, tls_context=info.tls_context(), rpc_origin=info.controller)
            deploy = AppDeployment(DependencyStarter(wire, tmp_path / 'starts', authority), tmp_path / 'deployments')
            async def advance(recipe):
                async with asyncio.timeout(240):
                    while True:
                        result = await deploy.advance(**recipe)
                        if result['state'] == 'ready':
                            return result
                        assert result['state'] not in ('failed', 'blocked'), result
                        await asyncio.sleep(.1)
            recipe = selection['recipe']
            result = await advance(recipe)
            shared_shell = result['prepared']['shell']
            chat_id = logical_id = None
            template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
            for cycle in range(2):
                identity = {**result['prepared']['agent'], 'generation': result['prepared']['agent']['generation'] + 1}
                identity.pop('node_id')
                assert (await invoke(identity, 'get_agent_app_info'))['protocol'] == 1
                if chat_id is None:
                    created = await invoke(identity, 'create_chat', chat_name='Local profile', project_name='Shared', template_obj=template)
                    assert created['success'], created
                    chat_id = created['chat_id']
                else:
                    listing = await invoke(identity, 'list_chats', project_name='Shared')
                    assert chat_id in {c['id'] for c in listing['chats']}
                agents = await invoke(identity, 'get_agents', chat_id=chat_id)
                current = agents['agents'][0]['instance']['instance_id']
                assert logical_id in (None, current)
                logical_id = current
                answer = await invoke(identity, 'chat', chat_id=chat_id,
                    message=[{'role': 'user', 'content': 'local prompt ' + str(cycle)}])
                assert answer['success'], answer
                snapshot = await invoke(identity, 'open_agent_history', chat_id=chat_id)
                assert snapshot['parts'] == 1
                history = await invoke(identity, 'read_agent_history', chat_id=chat_id,
                    snapshot_id=snapshot['snapshot_id'], part=0)
                assert 'scoped reply' in history['json_fragment'] and 'local prompt 0' in history['json_fragment']
                assert 'LOCAL_AGENT_TOOL_OK' in history['json_fragment']
                await invoke(identity, 'release_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'])
                if cycle == 0:
                    for name in ('agent', 'allocator', 'model-access'):
                        item = result['prepared'][name]
                        await action(digests[name], 'stop', item['generation'] + 1, scope=name)
                    state = await wire.status(info.node_id)
                    assert state['instances'][provider['instance_id']]['state'] == 'ready'
                    assert state['instances'][shared_shell['instance_id']]['state'] == 'ready'
                    assert await directory.deployment('local') == row
                    if restart_profile:
                        await action(digests['shell'], 'stop', shared_shell['generation'] + 1, scope='shared-shell')
                        stopped = await manager.set_running('local', False)
                        old_info, old_ca = info, info.ca_certificate.read_bytes()
                        await resolver.close()
                        await profiles.aclose()
                        with socket.socket() as unavailable:
                            unavailable.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                            unavailable.bind(('127.0.0.1', int(old_info.controller.rsplit(':', 1)[1])))
                            runtime = await profiles.enter_async_context(LocalFleet(tmp_path / 'profile', binaries, workspace=tmp_path))
                        info = runtime.coordinates
                        assert info.controller != old_info.controller
                        assert (info.fleet_id, info.node_id) == (old_info.fleet_id, old_info.node_id)
                        assert info.ca_certificate.read_bytes() == old_ca
                        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                            inbox_prefix=('_INBOX_' + info.fleet_id).encode())
                        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
                        wire = FleetLifecycle(resolver)
                        client = await resolver._ensure_client()
                        directory = LocalModelDirectory(directory.root, owner=info.fleet_id)
                        assert await directory.deployment('local') == stopped
                        provider = await action(digest, 'prepare_start', stopped['binding']['generation'],
                            operation_id='prepare-model-restored', scope='model-local')
                        await configure(instance_id=provider['instance_id'], revision=digest, generation=provider['generation'],
                            preparation_id='prepare-model-restored', components={'backend': {'values': {'connector': {
                                'engine': 'ollama', 'endpoint': model_endpoint.url}}}})
                        provider = await action(digest, 'start', provider['generation'],
                            start_preparation_id='prepare-model-restored', scope='model-local')
                        manager = ModelServiceManager(client=directory, resolver=resolver)
                        row = await manager.rebind_prepared(previous=stopped,
                            binding={**row['binding'], 'generation': provider['generation']},
                            configuration={'engine': 'ollama', 'endpoint': model_endpoint.url})
                        key = (runtime.root / 'owner.key').read_text().strip()
                        credential = RuntimeCredential(info.controller, key)
                        ref = 'node-secret://local-owner-' + hashlib.sha256(info.controller.encode()).hexdigest()[:16]
                        await RemoteModelCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id).ensure_async(
                            ref, info.controller, key)
                        vault = {'ref': ref, 'endpoint': info.controller}
                        authority = DependencyAuthority(credential=credential, tls_context=info.tls_context(), rpc_origin=info.controller)
                    # A new coordinator reopens durable deployment journals;
                    # the existing generic planner preserves the model provider.
                    deploy = AppDeployment(DependencyStarter(wire, tmp_path / 'starts', authority), tmp_path / 'deployments')
                    if restart_profile:
                        selection = await plan_local_agent_restart(directory, deploy, owner=info.fleet_id,
                            source_operation_id='local-agent', operation_id='local-agent-restart', owner_credential=vault,
                            local_transport={'origin': info.controller, 'trust_roots_pem': info.ca_certificate.read_text(),
                                             'directory_root': str(directory.root)})
                        recipe = selection['recipe']
                    else:
                        recipe = await plan_restart(deploy, owner=info.fleet_id, source_operation_id='local-agent',
                            operation_id='local-agent-restart', apps=['agent', 'allocator', 'model-access'])
                    result = await advance(recipe)
                    old = await client.invoke(info.node_id, 'agent', identity, 'list_chats', {}, 5)
                    assert 'error' in old, old
            calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
            assert len(calls) == 4, calls
            assert 'local prompt 0' in json.dumps(calls[-1]['messages'])
            sessions = set()
            for index in (1, 3):
                returned = [m for m in calls[index]['messages'] if m['role'] == 'tool']
                output = json.loads(returned[-1]['content'])
                assert output['success'] is True and output['status'] == 'completed'
                assert output['output'] == 'LOCAL_AGENT_TOOL_OK'
                sessions.add(output['shell_id'])
            assert len(sessions) == 2, 'A new consumer generation must not reuse the previous generation\'s Shell session'
            assert key not in json.dumps(model_endpoint.requests)
        finally:
            try:
                if nc.is_connected:
                    state = await wire.status(info.node_id)
                    instances = sorted(state['instances'].values(), key=lambda i: i.get('app_id') != 'agent')
                    for item in instances:
                        if item['state'] in ('ready', 'prepared', 'failed'):
                            await action(item['digest'], 'stop', item['generation'], scope=item['scope'])
            finally:
                await resolver.close()
                with pytest.raises(NotJoinedError):
                    await resolver._ensure_client()
