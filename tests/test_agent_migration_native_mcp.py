"""Actual migration CLI and packaged Agent call captured MCP after Fleet restarts."""
import asyncio
import json
from pathlib import Path
import signal
import sys

import nats
import pytest

from pantheon.apps.local_agent import native_platform
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.chatroom.package import build_package
from pantheon.chatroom.migration_mcp_deployment import dependency_inputs
from pantheon.platform.mcp_package import build_migration_package
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_agent_application import TEMPLATE
from test_agent_migration import legacy
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import settle
from test_local_profile_agent import product_configuration
from test_mcp_configuration_migration import captured


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'environment': {
    'MCP_KEY': '${ORIGINAL_MCP_KEY}', 'MODE': 'read'}, 'enable_mcp_tools': True}], indirect=True)
async def test_owner_cli_mcp_credentials_tools_and_history_survive_full_native_reopen(
        tmp_path, legacy, captured, binaries, release, model_endpoint, monkeypatch):
    handoff, before, script = captured
    document = json.loads(Path(handoff['source']).read_text())
    contract = document['contract']
    transport = release[0]/'backend/_vendor/pantheon/models/fleet-app-transport'
    mcp_package = await asyncio.to_thread(build_migration_package, tmp_path/'mcp', native_platform(),
        contract=contract, credential_slots=['mcp-env'], transport=transport)
    additions = dependency_inputs(contract, name='mcp', aliases={'mcp': 'mcp-shared'})
    agent_package = await asyncio.to_thread(build_package, tmp_path/'mcp-agent', native_platform(),
        version='0.7.0', frontend=release[0]/'frontend', transport=transport, dependencies={
            'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'}, **additions['dependencies']})
    credential = {'ref': 'node-secret://mcp-env', 'endpoint': 'https://mcp.example/v1'}
    def configure(setup):
        setup['agent'].update({k: legacy[k] for k in ('projects', 'active_project', 'default_project')})
        setup['agent']['dependencies']['profiles']['mcp_servers'] = additions['profiles']['mcp_servers']
        setup['agent']['dependencies']['defaults'] = {
            'toolsets': [], 'mcp_servers': ['mcp'], 'mcp_unified_precedence': True}
        setup['tools'].update(additions['tools'])
        setup['providers']['mcp'] = {'scope': 'migrated-mcp', 'bindings': {}, 'components': {
            'backend': {'values': {'mcp': {'protocol': 1, 'servers': {'docs': {
                'transport': 'stdio', 'command': [sys.executable, str(script)], 'cwd': str(script.parent),
                'env': {'MODE': 'read'}, 'env_credentials': {'MCP_KEY': {
                    'credential': 'mcp-env', 'endpoint': credential['endpoint']}}}}}},
                'credentials': {'mcp-env': credential}}}}
    bundle, setup_path, bundled, spec = await product_configuration(tmp_path, binaries,
        (agent_package, release[1]), model_endpoint, monkeypatch,
        provider_packages={'mcp': mcp_package}, configure=configure)
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': [],
        'mcp_servers': ['mcp'], 'model': 'normal'}]}
    for filename in ('chat-a.meta.json', 'chat-b.json'):
        path = Path(legacy['home_memory'])/filename
        value = json.loads(path.read_text())
        value.setdefault('extra_data', {})['team_template'] = template
        value['extra_data']['project'] = legacy['projects'][0]
        path.write_text(json.dumps(value))
    workspace = Path(legacy['projects'][0]['path'])
    request = dict(protocol=1, operation='native-mcp', app='agent', legacy=legacy,
        backup=str(tmp_path/'backup'), mcp_configuration={
            'app': 'mcp', 'targets': {'docs': {'command': [sys.executable, str(script)], 'cwd': str(script.parent)}},
            'aliases': {'mcp': 'mcp-shared'}, 'enable_mcp': True,
            'environments': {'docs': {'literals': ['MODE'], 'credentials': {'MCP_KEY': {
                'source': handoff['source'], 'alias': 'mcp-env', **credential}}}}})
    request_path = tmp_path/'migration-request.json'
    request_path.write_text(json.dumps(request)); request_path.chmod(0o600)
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon.chatroom.migration_profile',
        '--bundle', str(bundle), '--setup', str(setup_path), '--profile', str(tmp_path/'profile'),
        '--workspace', str(workspace), '--request', str(request_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    communication = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 180)
    except TimeoutError:
        process.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 30)
        except TimeoutError:
            process.kill()
            stdout, stderr = await communication
        pytest.fail('Migration did not complete: ' + stderr.decode())
    assert process.returncode == 0, stderr.decode()
    result = json.loads(stdout)
    assert result['state'] == 'imported' and result['conversations'] == 2
    root = Path(result['data_root'])
    audit = json.loads((root/'migration-mcp-bindings.json').read_text())
    assert audit['protocol'] == 2
    assert 'original-key' not in stdout.decode() + stderr.decode() + json.dumps(audit)
    assert not (root/'configuration/.pantheon/mcp.json').exists()
    assert not (root/'agent-data-format.json').exists()
    assert not model_endpoint.requests
    model_endpoint.tool_name = 'mcp__docs_check'
    model_endpoint.tool_arguments = {}
    generations = []
    logical_id = None
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', bundled, workspace=workspace) as runtime:
            info, children = runtime.coordinates, list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                                            str(workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                assert (await settle(session, 'advance'))['state'] == 'ready'
                generations.append(session.app_binding('mcp')['generation'])
                agent = await session.bind_rpc('agent', 'agent')
                async def invoke(method, **arguments):
                    response = await agent(method, arguments, 60)
                    assert response.get('success') is not False and 'error' not in response, response
                    return response
                member = (await invoke('get_agents', chat_id='chat-b'))['agents'][0]['instance']['instance_id']
                assert logical_id in (None, member)
                logical_id = member
                calls_before = len(model_endpoint.requests)
                await invoke('chat', chat_id='chat-b', message=[{'role': 'user', 'content': 'MCP turn ' + str(cycle)}])
                snapshot = await invoke('open_agent_history', chat_id='chat-b')
                history = await invoke('read_agent_history', chat_id='chat-b', snapshot_id=snapshot['snapshot_id'], part=0)
                for marker in ('saved answer', 'MCP turn 1', 'source marker', 'key_matches', 'owner_present'):
                    assert marker in history['json_fragment']
                # Confirm a successful real tool result rather than merely its
                # requested name in the transcript, including the private key.
                tool_messages = [m for _, _, body in model_endpoint.requests[calls_before:] if 'messages' in body
                                 for m in body['messages'] if m['role'] == 'tool']
                assert any('"key_matches": true' in str(m['content'])
                           and '"owner_present": false' in str(m['content']) for m in tool_messages), tool_messages
                await invoke('release_agent_history', chat_id='chat-b', snapshot_id=snapshot['snapshot_id'])
                assert (await settle(session, 'stop'))['state'] == 'stopped'
            finally:
                await resolver.close()
        assert_stopped(children, info)
    assert generations[1] > generations[0]
    assert len([r for r in model_endpoint.requests if r[0] == '/v1/chat/completions']) == 4
