"""Actual paired Agent with Shell and Model Connector on another native Runner.

Both Runners share a host; separate identities/state/workspaces are real, but
loopback owner/engine transport is not physical cross-host acceptance.
"""
import asyncio
import json
from pathlib import Path

import nats
import pytest

from pantheon.apps.agent_deployment import compose_deployment
from pantheon.apps.credentials import RemoteAppCredentialVault
from pantheon.apps.dependency_assembly import DependencyAuthority, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_stop import AppDeploymentStop
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.models.bootstrap import ModelServiceBootstrap
from pantheon.models.local_directory import LocalModelDirectory
from pantheon.models.manager import ModelServiceManager
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import local_values
from native_fleet_peer import fleet_peer
from test_agent_application import TEMPLATE
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile_agent import product_configuration


async def ready(call, expected):
    # Observe original operation identities through the actual 600-second hook
    # budget; never resubmit a cold install because a short poll expired.
    async with asyncio.timeout(660):
        while True:
            value = await call()
            if value['state'] == expected:
                return value
            assert value['state'] not in ('failed', 'cancelled', 'unknown'), value
            await asyncio.sleep(.1)


@pytest.mark.asyncio
async def test_agent_cross_runner_models_and_separate_shell_lifetimes(tmp_path, binaries, release, model_endpoint, monkeypatch):
    _, setup_path, bundled, spec = await product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch)
    setup = json.loads(setup_path.read_text())
    async with LocalFleet(tmp_path/'profile', bundled, workspace=tmp_path/'workspace') as runtime:
        info = runtime.coordinates
        children = list(runtime._children)
        async with fleet_peer(runtime, tmp_path/'peer', tmp_path/'peer-workspace') as peer:
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                    inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(runtime.workspace), connection=nc)
            wire = FleetLifecycle(resolver)
            credential = RuntimeCredential(info.controller, (runtime.root/'owner.key').read_text().strip())
            authority = DependencyAuthority(credential=credential, tls_context=info.tls_context(), rpc_origin=info.controller)
            deploy = AppDeployment(DependencyStarter(wire, tmp_path/'starts', authority), tmp_path/'deployments')
            directory = LocalModelDirectory(tmp_path/'models', owner=info.fleet_id)
            await directory.initialize()
            bootstrap = ModelServiceBootstrap(deploy, ModelServiceManager(client=directory, resolver=resolver), tmp_path/'model-starts')
            stop = AppDeploymentStop(deploy, tmp_path/'stops')
            ref = {'ref': 'node-secret://two-node-owner', 'endpoint': info.controller}
            targets = {name: dict(node_id=info.node_id, revision=spec['packages'][name]['revision'], scope=name, generation=0)
                       for name in ('agent', 'allocator', 'model-access')}
            providers = {'shell': dict(node_id=peer.node_id, revision=spec['packages']['shell']['revision'], generation=0,
                **local_values(setup['providers']['shell'], {'workspace': str(tmp_path/'peer-workspace')}))}
            recipe = compose_deployment(owner=info.fleet_id, operation_id='two-runner-agent', targets=targets,
                agent=local_values(setup['agent'], {'workspace': str(runtime.workspace)}),
                tools=setup['tools'], models=setup['models'], credentials={
                    'agent': {}, 'allocator': {'hub': ref, 'controller': ref}, 'model-access': {'hub': ref}},
                provider_apps=providers, local_transport=dict(origin=info.controller,
                    trust_roots_pem=info.ca_certificate.read_text(), directory_root=str(directory.root)))
            recipe.update(kind='model-services', model_apps={'connector': {
                **setup['model_apps']['connector'], 'app': dict(node_id=peer.node_id,
                    revision=spec['packages']['connector']['revision'], generation=0,
                    **setup['model_apps']['connector']['app'])}})
            try:
                await RemoteAppCredentialVault(wire, owner=info.fleet_id, node_id=info.node_id).ensure_async(
                    ref['ref'], credential.endpoint, credential.key)
                for name, package in spec['packages'].items():
                    node = peer.node_id if name in ('shell', 'connector') else info.node_id
                    payload, revision = await asyncio.to_thread(build_artifact, Path(package['path']), package['platform'])
                    assert revision == package['revision']
                    await wire.stage_exact(node, payload, revision)
                await ready(lambda: bootstrap.advance(**recipe), 'ready')
                consumer_id = bootstrap.child_id(recipe, 'consumers')
                started = deploy.inspect(owner=info.fleet_id, operation_id=consumer_id)
                identities = {}
                for name, prepared in started['prepared'].items():
                    identities[name] = {**prepared, 'generation': prepared['generation']+1}
                agent = identities['agent']; shell = identities['shell']
                assert agent['node_id'] == info.node_id and shell['node_id'] == peer.node_id
                primary = await wire.status(info.node_id)
                remote = await wire.status(peer.node_id)
                assert shell['instance_id'] not in primary['instances']
                assert agent['instance_id'] not in remote['instances']
                shared = {key: (i['digest'], i['generation']) for key, i in remote['instances'].items()}
                assert {i['app_id'] for i in remote['instances'].values()} == {'shell', 'model-service'}
                client = await resolver._ensure_client()
                async def invoke(method, **args):
                    response = await client.invoke(info.node_id, 'agent', {k:v for k,v in agent.items() if k != 'node_id'}, method, args, 60)
                    assert 'error' not in response, response
                    assert response['response'].get('success') is not False, response
                    return response['response']['result']
                template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
                chats = [(await invoke('create_chat', chat_name=name, project_name='Shared', template_obj=template))['chat_id']
                         for name in ('First owner', 'Second owner')]
                sessions = {}
                async def command(chat, script):
                    model_endpoint.tool_command = script
                    before = len(model_endpoint.requests)
                    result = await invoke('chat', chat_id=chat, message=[{'role':'user','content':'Execute the selected test command'}])
                    assert result['success'], result
                    calls = model_endpoint.requests[before:]
                    messages = [m for path, _, body in calls if path == '/v1/chat/completions'
                                for m in body['messages'] if m['role'] == 'tool']
                    assert messages, calls
                    result = json.loads(messages[-1]['content'])
                    assert result.get('success') is True and result.get('status') == 'completed', result
                    assert isinstance(result.get('shell_id'), str) and result['shell_id'], result
                    assert sessions.setdefault(chat, result['shell_id']) == result['shell_id']
                    return result['output']
                assert 'first-owner' in await command(chats[0], 'export OWNER_MARK=first-owner; echo "$OWNER_MARK"')
                assert 'unshared' in await command(chats[1], 'echo "${OWNER_MARK:-unshared}"')
                assert str(tmp_path/'peer-workspace') in await command(chats[1], 'pwd')
                assert 'first-owner' in await command(chats[0], 'echo "$OWNER_MARK"')
                assert sessions[chats[0]] != sessions[chats[1]]
                async def assert_released(chat):
                    response = await client.invoke(peer.node_id, 'shell',
                        {k:v for k,v in shell.items() if k != 'node_id'},
                        'get_shell_output', {'shell_id': sessions[chat], 'timeout': 1}, 10)
                    assert 'error' not in response, response
                    assert response['response']['success'] is True, response
                    assert response['response']['result']['success'] is False, response
                    assert response['response']['result']['error'] == 'Shell not found', response
                assert (await invoke('delete_chat', chat_id=chats[0]))['success']
                await assert_released(chats[0])
                assert 'second-still-alive' in await command(chats[1], 'echo second-still-alive')
                await ready(lambda: stop.advance(owner=info.fleet_id, operation_id='stop-agent-group',
                    source_operation_id=consumer_id, apps=['agent','allocator','model-access']), 'stopped')
                await assert_released(chats[1])
                remote = await wire.status(peer.node_id)
                for key, (revision, generation) in shared.items():
                    item = remote['instances'][key]
                    assert (item['digest'], item['generation'], item['state']) == (revision, generation, 'ready')
            finally:
                cleanup_errors = []
                try:
                    for node in (info.node_id, peer.node_id):
                        try:
                            state = await wire.status(node)
                        except Exception as exc:
                            cleanup_errors.append(exc)
                            continue
                        for item in sorted(state['instances'].values(), key=lambda i:i['app_id']!='agent'):
                            if item['state'] == 'stopped': continue
                            try:
                                receipt = await wire.submit(node, 'stop', item['digest'], scope=item['scope'], generation=item['generation'])
                                async def operation():
                                    return (await wire.status(node))['operations'][receipt['request']['operation_id']]
                                await ready(operation, 'succeeded')
                            except Exception as exc:
                                cleanup_errors.append(exc)
                finally:
                    await resolver.close()
                if cleanup_errors:
                    raise ExceptionGroup('Owned two-Runner Apps failed cleanup', cleanup_errors)
        assert peer.process.returncode is not None
    assert_stopped(children, info)
