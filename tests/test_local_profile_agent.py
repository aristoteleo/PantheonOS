"""Full packaged Agent launched/reopened by the product local-profile owner."""
import asyncio
import json
from pathlib import Path
import platform
import subprocess
import sys

import nats
import pytest

from pantheon.apps.agent_deployment import compose_deployment
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.models.connector_package import build_package as build_connector
from pantheon.platform.dependency_package import build_package as build_allocator
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from pantheon.platform.model_dependency_package import build_package as build_access
from test_agent_application import TEMPLATE
from test_agent_launch import prepared
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import settle


@pytest.mark.asyncio
async def test_product_profile_full_agent_chat_tools_and_clean_reopen(tmp_path, binaries, release, model_endpoint, monkeypatch):
    target = sys.platform + '-' + {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    paths = {'agent': release[0], 'allocator': build_allocator(tmp_path/'allocator', target),
        'model-access': build_access(tmp_path/'access', target), 'connector': build_connector(tmp_path/'connector', target),
        'shell': tmp_path/'shell'}
    source = Path(__file__).resolve().parents[1]
    build = await asyncio.to_thread(subprocess.run, [sys.executable, str(source/'apps/shell/build_managed.py'),
        '--output', str(paths['shell']), '--os', sys.platform, '--arch', target.split('-')[1]],
        cwd=source, capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    packages = {name: {'path': str(path), 'revision': build_artifact(path)[1]} for name, path in paths.items()}
    model_endpoint.tool_command = 'printf PROFILE_TOOL_OK'
    (tmp_path/'workspace').mkdir()
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://must-not-join.invalid')
    monkeypatch.setenv('FLEET_KEY', 'must-not-borrow')
    monkeypatch.setenv('NATS_SERVERS', 'nats://127.0.0.1:1')
    spec = chat_id = logical_id = None
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', binaries, workspace=tmp_path/'workspace') as runtime:
            info = runtime.coordinates
            children = list(runtime._children)
            if spec is None:
                owner_credential = {'ref': 'node-secret://owner-placeholder', 'endpoint': info.controller}
                agent = prepared(tmp_path, model_endpoint.url)['values']['agent']
                agent['models'] = {'fleet_tiers': {'normal': 'fleet-model://local/example%3A8b'}}
                agent['dependencies']['profiles']['toolsets']['shell'] = {'alias': 'shell', 'functions': [{
                    'name': 'run_command', 'description': 'Execute in this Agent shell', 'parameters': {
                        'type': 'object', 'properties': {'command': {'type': 'string'}, 'timeout': {'type': 'integer'}},
                        'required': ['command'], 'additionalProperties': False}}]}
                recipe = compose_deployment(owner=info.fleet_id, operation_id='template', agent=agent,
                    targets={name: dict(node_id=info.node_id, revision=packages[name]['revision'], scope=name, generation=0)
                             for name in ('agent', 'allocator', 'model-access')},
                    tools={'shell': {'app_id': 'shell', 'provider': {'$app': 'shell', 'component': 'backend', 'port': 'http'},
                        'methods': {'run_command': {'arguments': ['command', 'timeout'], 'bound': {}}},
                        'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}},
                    models={'deployments': {'local': {'$model': 'connector'}}, 'routes': {}, 'allow_wake': False},
                    credentials={'agent': {}, 'allocator': {'hub': owner_credential, 'controller': owner_credential},
                                 'model-access': {'hub': owner_credential}},
                    provider_apps={'shell': dict(node_id=info.node_id, revision=packages['shell']['revision'],
                        scope='shared-shell', generation=0, components={}, bindings={})},
                    local_transport={'origin': info.controller, 'trust_roots_pem': info.ca_certificate.read_text(),
                                     'directory_root': str(runtime.root/'app-profile/models')})
                def template(value):
                    if value == owner_credential: return {'$local': 'owner_credential'}
                    context = {'controller': info.controller, 'trust_roots_pem': info.ca_certificate.read_text(),
                               'directory_root': str(runtime.root/'app-profile/models'), 'workspace': str(runtime.workspace)}
                    for name, actual in context.items():
                        if value == actual: return {'$local': name}
                    if isinstance(value, dict): return {k: template(v) for k, v in value.items()}
                    if isinstance(value, list): return [template(v) for v in value]
                    return value
                spec = dict(protocol=1, packages=packages, apps={name: {'package': name, 'scope': app['scope'],
                    'components': template(app['components']), 'bindings': app['bindings']} for name, app in recipe['apps'].items()},
                    model_apps={'connector': {'deployment_id': 'local', 'name': 'Profile model',
                        'models': [{'id': 'example:8b', 'context_limit': 4096}], 'app': {'package': 'connector',
                        'scope': 'model-local', 'components': {'backend': {'values': {'connector': {
                            'engine': 'ollama', 'endpoint': model_endpoint.url}}}}, 'bindings': {}}}})
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(runtime.workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                result = await settle(session, 'advance')
                assert result['state'] == 'ready' and result['cycle'] == cycle
                ready = session.deploy.inspect(owner=info.fleet_id,
                    operation_id=session._consumer_id(session._record['recipe']))
                identity = {**ready['prepared']['agent'], 'generation': ready['prepared']['agent']['generation'] + 1}
                identity.pop('node_id')
                client = await resolver._ensure_client()
                async def invoke(method, **args):
                    response = await client.invoke(info.node_id, 'agent', identity, method, args, 45)
                    assert 'error' not in response, response
                    assert response['response'].get('success') is not False, response
                    return response['response']['result']
                if chat_id is None:
                    template_obj = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': ['shell'], 'model': 'normal'}]}
                    chat = await invoke('create_chat', chat_name='Product profile', project_name='Shared', template_obj=template_obj)
                    chat_id = chat['chat_id']
                agents = await invoke('get_agents', chat_id=chat_id)
                current = agents['agents'][0]['instance']['instance_id']
                assert logical_id in (None, current)
                logical_id = current
                assert (await invoke('chat', chat_id=chat_id, message=[{'role': 'user', 'content': 'profile turn ' + str(cycle)}]))['success']
                snapshot = await invoke('open_agent_history', chat_id=chat_id)
                history = await invoke('read_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'], part=0)
                assert 'profile turn 1' in history['json_fragment'] and 'PROFILE_TOOL_OK' in history['json_fragment']
                await invoke('release_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'])
                assert (await settle(session, 'stop'))['state'] == 'stopped'
            finally:
                state = await session.wire.status(info.node_id)
                for item in sorted(state['instances'].values(), key=lambda i: i.get('app_id') != 'agent'):
                    if item['state'] == 'stopped': continue
                    op = await session.wire.submit(info.node_id, 'stop', item['digest'], scope=item['scope'], generation=item['generation'])
                    async with asyncio.timeout(90):
                        while (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]['state'] in ('queued', 'running'):
                            await asyncio.sleep(.1)
                await resolver.close()
        assert_stopped(children, info)
    # Actual public command reopens this same profile and resumes the existing
    # Agent over its generation-bound RPC. It never constructs a second runtime
    # in the terminal process or opens the Agent's data lock there.
    manifest_path = tmp_path/'profile.json'
    manifest_path.write_text(json.dumps(spec)); manifest_path.chmod(0o600)
    proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'local',
        '--profile', str(tmp_path/'profile'), '--workspace', str(tmp_path/'workspace'),
        '--manifest', str(manifest_path), '--controller', str(binaries.controller),
        '--broker', str(binaries.broker), '--runner', str(binaries.runner),
        '--agent', 'agent', '--chat-id', chat_id, '-i', 'profile turn 3',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(120): out, err = await proc.communicate()
        assert proc.returncode == 0, err.decode()
        assert json.loads(out) == {'chat_id': chat_id, 'response': 'scoped reply'}
        state = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
        assert state['phase'] == 'stopped' and state['cycle'] == 3
    finally:
        if proc.returncode is None:
            proc.kill(); await proc.wait()
    calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
    assert len(calls) == 6
    assert 'profile turn 1' in json.dumps(calls[-1]['messages'])
    sessions = set()
    for index in (1, 3, 5):
        output = json.loads([m for m in calls[index]['messages'] if m['role'] == 'tool'][-1]['content'])
        assert output['output'] == 'PROFILE_TOOL_OK'
        sessions.add(output['shell_id'])
    assert len(sessions) == 3
