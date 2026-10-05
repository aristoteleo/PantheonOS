"""Agent -> Evolution -> Agent with real local Fleet-issued dependency grants.

Only the model engine is scripted. Packages, install hooks, Controller/NATS,
Runner, allocator, model publication, RPC grants and tool processes are real.
This joint gate does not claim complete General Team or paid-model acceptance.
"""
import asyncio
from http.server import BaseHTTPRequestHandler
import json
import os
import platform
import sys
from types import SimpleNamespace

import nats
import pytest

from pantheon.apps.agent_execution_client import execution_method_rules
from pantheon.apps.builtin.evolution.build_managed import build as build_evolution
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.chatroom.package import build_package as build_agent
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_agent_application import TEMPLATE
from test_agent_release import release
from test_evolution_tools_package import EVALUATOR
from test_local_fleet import binaries, assert_stopped
from test_local_profile_agent import product_configuration
from test_model_services import serve


EVOLVE_ARGUMENTS = {
    'type': 'code', 'code': 'x = 1', 'evaluator_code': EVALUATOR,
    'objective': 'Increase x to eight and verify it', 'iterations': 1,
    'islands': 1, 'model': 'normal', 'async_mode': False, 'timeout': 60,
}


@pytest.fixture
def composition_model():
    calls = []

    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/v1/models':
                value = {'data': [{'id': 'example:8b'}]}
            elif self.path == '/api/ps':
                value = {'models': []}
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/api/show':
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'capabilities': ['completion', 'tools'],
                    'model_info': {'general.architecture': 'llama', 'llama.context_length': 32768}}).encode())
                return
            if self.path != '/v1/chat/completions':
                self.send_response(404); self.end_headers(); return
            calls.append(body)
            names = {tool['function']['name'] for tool in body.get('tools', [])}
            results = [m for m in body['messages'] if m['role'] == 'tool']
            if 'evolution__evolve' in names:
                actions = [('evolution__evolve', EVOLVE_ARGUMENTS)]
                content = 'EVOLUTION_PROFILE_OK'
            else:
                actions = [
                    ('shell__run_command', {'command': "printf 'x = 8' > main.py"}),
                    ('python__run_python_code', {'code': 'print(6 * 7)'}),
                    ('evolution__run_evaluator', {}),
                    ('evolution__submit', {'summary': 'Verified eight through Fleet'}),
                ]
                content = 'Submitted verified eight'
            if len(results) < len(actions):
                name, args = actions[len(results)]
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0,
                    'id': 'call_' + str(len(calls)), 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(args)}}]}
                finish = 'tool_calls'
            else:
                delta, finish = {'role': 'assistant', 'content': content}, 'stop'
            events = [
                {'id': 'composition', 'object': 'chat.completion.chunk', 'created': 0,
                 'model': body['model'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'composition', 'object': 'chat.completion.chunk', 'created': 0,
                 'model': body['model'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                 'usage': {'prompt_tokens': 30, 'completion_tokens': 10, 'total_tokens': 40}},
            ]
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            for event in events:
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')

    with serve(Engine) as url:
        yield SimpleNamespace(url=url, calls=calls)


def configure_evolution(setup):
    # The original Agent runtime remains the same. Its additional tool is a
    # runtime allocation; Evolution's startup grant points back to that Agent.
    properties = {
        'type': {'type': 'string'}, 'code': {'type': 'string'},
        'evaluator_code': {'type': 'string'}, 'objective': {'type': 'string'},
        'iterations': {'type': 'integer'}, 'islands': {'type': 'integer'},
        'model': {'type': 'string'}, 'async_mode': {'type': 'boolean'},
        'timeout': {'type': 'integer'},
    }
    functions = [{'name': 'evolve', 'description': 'Evolve code through the independent Evolution App',
        'parameters': {'type': 'object', 'properties': properties,
                       'required': ['type', 'evaluator_code', 'objective'], 'additionalProperties': False}}]
    setup['agent']['dependencies']['profiles']['toolsets']['evolution'] = {
        'alias': 'evolution', 'functions': functions}
    setup['tools']['evolution'] = {'app_id': 'evolution',
        'provider': {'$app': 'evolution', 'component': 'backend', 'port': 'http'},
        'methods': {'evolve': {'arguments': list(properties), 'bound': {}}}}
    setup['providers']['evolution'] = {
        'scope': 'evolution-controller',
        'components': {'backend': {'values': {'evolution': {
            'agent_credential': 'agent', 'agent_ca_pem': {'$local': 'trust_roots_pem'},
            'execution': 'node', 'options': {'num_workers': 1, 'llm_weight': 0, 'function_weight': 1,
                'evaluation_timeout': 30, 'mutation_timeout': 60,
                'max_tool_calls_per_mutation': 8, 'max_mutation_turns': 8}}}}},
        'bindings': {'agent': {'app_id': 'agent', 'component': 'backend',
            'provider': {'$app': 'agent', 'component': 'backend', 'port': 'http'},
            'methods': execution_method_rules('evolution-controller')}},
    }


async def settled(session, action):
    async with asyncio.timeout(240):
        while True:
            value = await getattr(session, action)()
            if value['state'] in ('ready', 'stopped'):
                return value
            await asyncio.sleep(.05)


@pytest.mark.asyncio
async def test_real_fleet_agent_evolution_callback_stop_and_reopen(
        tmp_path, binaries, release, composition_model, monkeypatch):
    target = sys.platform + '-' + {'arm64': 'arm64', 'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    agent = build_agent(tmp_path/'paired-agent', target, version='0.7.0',
        frontend=os.environ['AGENT_APP_BUILD_DIR'], transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies={
            'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'},
            'evolution': {'range': '^0.7.0', 'uses': ['evolution@1'], 'binding': 'runtime'}})
    package = build_evolution(tmp_path/'evolution-release', target)
    _, _, bundled, spec = await product_configuration(tmp_path, binaries, (agent, release[1]),
        composition_model, monkeypatch, provider_packages={'evolution': package}, configure=configure_evolution)
    chat_id = evolution_id = None
    evolution_ids = set()
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', bundled, workspace=tmp_path/'workspace') as runtime:
            info, children = runtime.coordinates, list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                                           str(runtime.workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                assert (await settled(session, 'advance'))['state'] == 'ready'
                ready = session.deploy.inspect(owner=info.fleet_id,
                    operation_id=session._consumer_id(session._record['recipe']))
                client = await resolver._ensure_client()

                async def invoke(app, method, **args):
                    identity = {**ready['prepared'][app], 'generation': ready['prepared'][app]['generation'] + 1}
                    identity.pop('node_id')
                    response = await client.invoke(info.node_id, app, identity, method, args, 100)
                    assert 'error' not in response, response
                    assert response['response'].get('success') is not False, response
                    return response['response']['result']

                if cycle == 2:
                    snapshot = await invoke('agent', 'open_agent_history', chat_id=chat_id)
                    history = await invoke('agent', 'read_agent_history', chat_id=chat_id,
                                           snapshot_id=snapshot['snapshot_id'], part=0)
                    assert 'EVOLUTION_PROFILE_OK' in history['json_fragment']
                    await invoke('agent', 'release_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'])
                    status = await invoke('evolution', 'evolution_manage', evolution_id=evolution_id, include_code=True)
                    assert status['status'] == 'completed' and status['result']['files']['main.py'] == 'x = 8', status
                    assert len(composition_model.calls) == 7, 'Reopening must not repeat model or tool execution'

                # A fresh run after full profile restart must use the new
                # generation's grants, not just display a previously saved result.
                template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0],
                    'toolsets': ['evolution'], 'model': 'normal'}]}
                chat_id = (await invoke('agent', 'create_chat', chat_name='Evolution composition',
                    project_name='Shared', template_obj=template))['chat_id']
                response = await invoke('agent', 'chat', chat_id=chat_id,
                    message=[{'role': 'user', 'content': 'Improve the code using Evolution'}])
                assert response['success'], response
                outer = [c for c in composition_model.calls if any(
                    t['function']['name'] == 'evolution__evolve' for t in c.get('tools', []))]
                assert len(outer) == 2 * cycle, composition_model.calls
                output = json.loads([m for m in outer[-1]['messages'] if m['role'] == 'tool'][-1]['content'])
                assert output['success'] and output['status'] == 'completed', output
                evolution_id = output['evolution_id']
                assert evolution_id not in evolution_ids
                evolution_ids.add(evolution_id)
                status = await invoke('evolution', 'evolution_manage', evolution_id=evolution_id, include_code=True)
                assert status['status'] == 'completed' and status['result']['files']['main.py'] == 'x = 8', status
                assert len(composition_model.calls) == 7 * cycle, composition_model.calls
                # Ordinary before_stop drains Evolution before retiring its
                # Agent execution dependency and the original model services.
                assert (await settled(session, 'stop'))['state'] == 'stopped'
                states = await session.wire.status(info.node_id)
                assert all(i['state'] == 'stopped' and not i.get('resources') for i in states['instances'].values())
            finally:
                # Preserve normal stop assertions above; on failure, terminate
                # only this isolated test profile's admitted native instances.
                states = await session.wire.status(info.node_id)
                for item in sorted(states['instances'].values(), key=lambda i: i.get('app_id') != 'evolution'):
                    if item['state'] == 'stopped':
                        continue
                    op = await session.wire.submit(info.node_id, 'stop', item['digest'],
                        scope=item['scope'], generation=item['generation'])
                    async with asyncio.timeout(90):
                        while (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]['state'] in ('queued', 'running'):
                            await asyncio.sleep(.1)
                await resolver.close()
        assert_stopped(children, info)
