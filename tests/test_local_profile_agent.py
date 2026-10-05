"""Full packaged Agent launched/reopened by the product local-profile owner."""
import asyncio
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import nats
import httpx
import pytest

from pantheon.apps.local_agent import build_bundle, read_bundle, compose_profile
from pantheon.apps.release_set import index_packages
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


async def product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch,
                                *, provider_packages=None, configure=None):
    target = sys.platform + '-' + {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    paths = {'agent': release[0], 'allocator': build_allocator(tmp_path/'allocator', target),
        'model-access': build_access(tmp_path/'access', target), 'connector': build_connector(tmp_path/'connector', target),
        'shell': tmp_path/'shell'}
    paths.update(provider_packages or {})
    source = Path(__file__).resolve().parents[1]
    build = await asyncio.to_thread(subprocess.run, [sys.executable, str(source/'apps/shell/build_managed.py'),
        '--output', str(paths['shell']), '--os', sys.platform, '--arch', target.split('-')[1]],
        cwd=source, capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    model_endpoint.tool_command = 'printf PROFILE_TOOL_OK'
    (tmp_path/'workspace').mkdir()
    monkeypatch.setenv('FLEET_CONTROLLER_URL', 'https://must-not-join.invalid')
    monkeypatch.setenv('FLEET_KEY', 'must-not-borrow')
    monkeypatch.setenv('NATS_SERVERS', 'nats://127.0.0.1:1')
    # Build the product distribution once. Both direct host and actual terminal
    # command below use the same compiler, not a fixture-built deployment recipe.
    shutil.copytree(paths['agent'], tmp_path/'agent-release')
    paths['agent'] = tmp_path/'agent-release'
    index_packages(tmp_path, {name: {target: path} for name, path in paths.items()})
    bundle = build_bundle(tmp_path/'product', release=tmp_path, binaries=binaries, target=target)
    bundled_binaries, entries = read_bundle(bundle)
    agent = prepared(tmp_path, model_endpoint.url)['values']['agent']
    agent['projects'][0]['path'] = {'$local': 'workspace'}
    agent['models'] = {'fleet_tiers': {'normal': 'fleet-model://local/example%3A8b'}}
    agent['dependencies']['profiles']['toolsets']['shell'] = {'alias': 'shell', 'functions': [{
        'name': 'run_command', 'description': 'Execute in this Agent shell', 'parameters': {
            'type': 'object', 'properties': {'command': {'type': 'string'}, 'timeout': {'type': 'integer'}},
            'required': ['command'], 'additionalProperties': False}}]}
    setup = {'protocol': 1, 'agent': agent,
        'tools': {'shell': {'app_id': 'shell', 'provider': {'$app': 'shell', 'component': 'backend', 'port': 'http'},
            'methods': {'run_command': {'arguments': ['command', 'timeout'], 'bound': {}}},
            'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}},
        'models': {'deployments': {'local': {'$model': 'connector'}}, 'routes': {}, 'allow_wake': False},
        'providers': {'shell': {'scope': 'shared-shell', 'components': {}, 'bindings': {}}},
        'model_apps': {'connector': {'deployment_id': 'local', 'name': 'Profile model',
            'models': [{'id': 'example:8b', 'context_limit': 4096}], 'app': {
            'scope': 'model-local', 'components': {'backend': {'values': {'connector': {
                'engine': 'ollama', 'endpoint': model_endpoint.url}}}}, 'bindings': {}}}}}
    if configure is not None:
        configure(setup)
    setup_path = tmp_path/'setup.json'
    setup_path.write_text(json.dumps(setup)); setup_path.chmod(0o600)
    spec = compose_profile(entries, setup)
    return bundle, setup_path, bundled_binaries, spec


@pytest.mark.asyncio
async def test_product_profile_full_agent_chat_tools_and_clean_reopen(tmp_path, binaries, release, model_endpoint, monkeypatch):
    bundle, setup_path, bundled_binaries, spec = await product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch)
    chat_id = logical_id = None
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', bundled_binaries, workspace=tmp_path/'workspace') as runtime:
            info = runtime.coordinates
            children = list(runtime._children)
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
        '--agent', 'agent', '--chat-id', chat_id, '-i', 'profile turn 3', '--stream',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(120): out, err = await proc.communicate()
        assert proc.returncode == 0, err.decode()
        lines = [json.loads(line) for line in out.splitlines()]
        assert lines[-1] == {'kind': 'result', 'chat_id': chat_id, 'response': 'scoped reply'}
        events = [line['event'] for line in lines if line['kind'] == 'event']
        assert {'chunk', 'step', 'chat_finished'} <= {event['type'] for event in events}
        assert 'PROFILE_TOOL_OK' in json.dumps(events)
        state = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
        assert state['phase'] == 'stopped' and state['cycle'] == 3
    finally:
        if proc.returncode is None:
            proc.kill(); await proc.wait()
    interactive = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'cli',
        '--profile', str(tmp_path/'profile'), '--workspace', str(tmp_path/'workspace'),
        '--bundle', str(bundle), '--setup', str(setup_path),
        '--chat-id', chat_id,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(120):
            out, err = await interactive.communicate(b'/chats\nprofile turn 4\nprofile turn 5\n/quit\n')
        assert interactive.returncode == 0, err.decode()
        text = out.decode()
        assert text.count('scoped reply') == 2 and 'PROFILE_TOOL_OK' in text and chat_id in text
        state = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
        assert state['phase'] == 'stopped' and state['cycle'] == 4
    finally:
        if interactive.returncode is None:
            interactive.kill(); await interactive.wait()
    calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
    assert len(calls) == 10
    assert 'profile turn 1' in json.dumps(calls[-1]['messages'])
    sessions = set()
    for index in (1, 3, 5, 7, 9):
        output = json.loads([m for m in calls[index]['messages'] if m['role'] == 'tool'][-1]['content'])
        assert output['output'] == 'PROFILE_TOOL_OK'
        sessions.add(output['shell_id'])
    assert len(sessions) == 4


@pytest.mark.asyncio
async def test_desktop_product_gui_reload_and_owner_pipe_shutdown(tmp_path, binaries, release, model_endpoint, monkeypatch):
    script = os.environ.get('PANTHEON_TEST_LOCAL_DESKTOP_GUI')
    if not script: pytest.skip('Supply the production local Desktop GUI browser gate')
    from pantheon.platform.local_desktop import PREFIX
    bundle, setup_path, _, spec = await product_configuration(tmp_path, binaries, release, model_endpoint, monkeypatch)
    chat_id = None
    identities = []
    for cycle in (1, 2):
        child = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'local',
            '--profile', str(tmp_path/'profile'), '--workspace', str(tmp_path/'workspace'),
            '--bundle', str(bundle), '--setup', str(setup_path), '--desktop-agent', 'agent',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(120):
                while line := await child.stdout.readline():
                    text = line.decode()
                    if not text.startswith(PREFIX): continue
                    record = json.loads(text[len(PREFIX):])
                    assert not record['value'].get('needs_attention'), record
                    if record['kind'] == 'ready': break
                else: pytest.fail('Desktop launcher exited before ready: ' + (await child.stderr.read()).decode())
            identity = record['value']; url = identity['url']
            identities.append(identity)
            assert identity['revision'] == spec['packages']['agent']['revision']
            headers = {'Origin': url.split('/view/')[0], 'X-Pantheon-View':'1'}
            async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
                if chat_id is None:
                    template_obj = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets':['shell'], 'model':''}]}
                    response = await http.post(url+'rpc', headers=headers, json={'method':'create_chat','timeout_s':30,
                        'args':{'chat_name':'Native Desktop','project_name':'Shared','template_obj':template_obj}})
                    chat_id = response.json()['result']['chat_id']
                    assert (await http.post(url+'state',headers=headers,json={'chatId':chat_id})).status_code == 200
                else:
                    assert (await http.post(url+'state',headers=headers,json={'read':True})).json()['result']['chatId'] == chat_id
            env = {**os.environ, 'PANTHEON_LOCAL_DESKTOP_URL':url, 'PANTHEON_LOCAL_DESKTOP_CYCLE':str(cycle)}
            browser = await asyncio.create_subprocess_exec('node', script, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                async with asyncio.timeout(90): out, err = await browser.communicate()
                assert browser.returncode == 0, out.decode() + err.decode()
            finally:
                if browser.returncode is None: browser.kill(); await browser.wait()
            if cycle == 1:
                child.stdin.write(b'{"protocol":1,"command":"stop"}\n')
                await child.stdin.drain()
            else:
                child.stdin.close()  # Native owner loss uses the same ordered drain.
            async with asyncio.timeout(120): out, err = await child.communicate()
            assert child.returncode == 0, err.decode() + out.decode()
            statuses = [json.loads(line[len(PREFIX):]) for line in out.decode().splitlines() if line.startswith(PREFIX)]
            assert statuses[-1]['value']['state'] == 'stopped'
            state = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
            assert state['phase'] == 'stopped' and state['cycle'] == cycle
            async with httpx.AsyncClient(trust_env=False) as http:
                with pytest.raises(httpx.ConnectError): await http.get(url)
        finally:
            if child.returncode is None:
                child.stdin.close()
                try: await asyncio.wait_for(child.wait(), 30)
                except asyncio.TimeoutError: child.kill(); await child.wait()
    assert identities[0]['instance_id'] == identities[1]['instance_id']
    assert identities[0]['generation'] < identities[1]['generation']
    assert identities[0]['url'] != identities[1]['url']
    calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
    assert len(calls) == 4
    assert 'desktop view turn 1' in json.dumps(calls[-1]['messages'])
    assert 'PROFILE_TOOL_OK' in json.dumps(calls[-1]['messages'])
