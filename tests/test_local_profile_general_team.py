"""Unmodified General Team on the complete ordinary local App composition.

The upstream text engine is deterministic; App packages, grants, processes,
workspace files and profile close/reopen are real. Provider-specific exhaustive
functionality and paid image inference remain separate acceptance gates.
"""
import asyncio
import json
import os
import sys

import httpx

import nats
import pytest

from pathlib import Path
from pantheon.apps.general_agent_release import build_release
from pantheon.apps.local_agent import native_platform
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile_agent import product_configuration
from test_model_api_images import upstream
from test_model_services import serve


async def settled(session, action):
    # This cold-install gate includes all tool environments and a browser;
    # provider readiness remains bounded by Fleet's individual deadlines.
    async with asyncio.timeout(900):
        while True:
            result = await getattr(session, action)()
            if result['state'] in ('ready', 'stopped'):
                return result
            await asyncio.sleep(.1)


@pytest.fixture
def image_engine():
    calls = []
    class Engine(upstream(calls)):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"chosen-image-model"}]}')
    with serve(Engine) as endpoint:
        yield endpoint, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('files_models', ['configured', 'unconfigured'])
async def test_general_team_all_providers_chat_and_clean_reopen(
        tmp_path, binaries, release, model_endpoint, image_engine, monkeypatch, files_models):
    target = native_platform()
    model_endpoint.tool_prompt_prefix = 'general team turn '
    model_endpoint.context_length = 131072
    product_release = await asyncio.to_thread(build_release, tmp_path/'complete-release', target,
        version='0.7.0', frontend=os.environ['AGENT_APP_BUILD_DIR'],
        notebook_frontend=Path(__file__).resolve().parents[1]/'apps/notebook/frontend',
        transport=os.environ['AGENT_RELEASE_TRANSPORT'], model_aliases=['connector', 'image-connector'])
    catalog = tmp_path/'catalog'; catalog.mkdir()
    model = 'fleet-model://local/example%3A8b'

    def configure(setup):
        agent = setup['agent']
        agent.pop('dependencies')
        # Keep the canonical team and all original plugins. The product preset
        # supplies the graph; the owner supplies models, projects and policies.
        agent['settings'] = {'default_template_auto_update': False}
        agent['models']['fleet_tiers'] = {tier: model for tier in ('low', 'normal', 'high')}
        setup['model_apps']['connector']['models'][0]['context_limit'] = model_endpoint.context_length
        setup['model_apps']['image-connector'] = {'deployment_id': 'images', 'name': 'Image test engine',
            'models': [{'id': 'chosen-image-model', 'operations': ['image']}], 'app': {
                'scope': 'model-images', 'components': {'backend': {'values': {'connector': {
                    'engine': 'api', 'endpoint': image_engine[0]}}}}, 'bindings': {}}}
        selected = {'protocol': 1, 'preset': 'general-team', 'agent': agent,
            'models': {**setup['models'], 'deployments': {'local': {'$model': 'connector'},
                                                       'images': {'$model': 'image-connector'}}},
            'model_apps': setup['model_apps'],
            'files': {
                'sampling': {'model': model, 'max_tokens': 256, 'max_requests_per_call': 2},
                'image_generation': {'model': 'fleet-model://images/chosen-image-model',
                                     'aliases': {}, 'timeout_seconds': 30}},
            'notebook': {'execution_timeout': 60, 'execution_logging': True},
            'evolution': {'execution': 'node', 'options': {'num_workers': 1, 'llm_weight': 0,
                'function_weight': 1, 'evaluation_timeout': 30, 'mutation_timeout': 60,
                'max_tool_calls_per_mutation': 8, 'max_mutation_turns': 8}},
            'desktop': {'user_seed': 'general-team', 'catalog': [{'path': str(catalog), 'scope': 'user'}],
                        'store': {'origin': 'https://store.invalid'}, 'data': {'mode': 'loopback'}}}
        setup.clear()
        if files_models == 'unconfigured':
            selected['files'] = {name: {'state': 'unconfigured'} for name in ('sampling', 'image_generation')}
            del selected['model_apps']['image-connector']
            del selected['models']['deployments']['images']
        setup.update(selected)

    bundle, setup_path, bundled, spec = await product_configuration(tmp_path, binaries, release,
        model_endpoint, monkeypatch, release_root=product_release, configure=configure)
    workspace = tmp_path/'workspace'
    (workspace/'shared.txt').write_text('GENERAL_TEAM_WORKSPACE')
    chat_id = None
    logical = None
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', bundled, workspace=workspace) as runtime:
            info, children = runtime.coordinates, list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                   inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                                           str(workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                assert (await settled(session, 'advance'))['state'] == 'ready'
                apps = {alias: await session.bind_rpc(alias, app_id) for alias, app_id in {
                    'agent': 'agent', 'files': 'file-manager', 'notebook': 'integrated-notebook',
                    'desktop': 'desktop', 'model-management': 'model-services-management'}.items()}
                async def invoke(alias, method, **args):
                    result = await apps[alias](method, args, 100)
                    assert not result.get('error') and result.get('success') is not False, result
                    return result
                assert (await invoke('notebook', 'execution_host'))['workspace'] == str(workspace)
                assert 'GENERAL_TEAM_WORKSPACE' in json.dumps(await invoke('files', 'read_file', file_path='shared.txt'))
                if files_models == 'configured':
                    generated = await invoke('files', 'generate_image', prompt='Generate a test square')
                    assert generated['success'] and (workspace/generated['images'][0]).is_file()
                else:
                    for method, args in [('generate_image', {'prompt': 'Not configured'}),
                                         ('observe_images', {'question': 'What?', 'image_paths': ['missing.png']})]:
                        result = await apps['files'](method, args, 10)
                        assert result['success'] is False and result['code'] == 'model_not_configured', result
                    assert not image_engine[1]
                overview = await invoke('model-management', 'model_services_overview')
                assert {d['deployment_id'] for d in overview['deployments']} == (
                    {'local', 'images'} if files_models == 'configured' else {'local'})
                assert overview['modal_available'] is False
                if chat_id is None:
                    chat_id = (await invoke('agent', 'create_chat', chat_name='Complete General Team',
                                            project_name='Shared'))['chat_id']
                agents = (await invoke('agent', 'get_agents', chat_id=chat_id))['agents']
                assert len(agents) == 3
                identities = {a['instance']['instance_id'] for a in agents}
                assert logical in (None, identities)
                logical = identities
                result = await invoke('agent', 'chat', chat_id=chat_id,
                    message=[{'role': 'user', 'content': 'general team turn '+str(cycle)}])
                assert result['success'], result
                snapshot = await invoke('agent', 'open_agent_history', chat_id=chat_id)
                history = await invoke('agent', 'read_agent_history', chat_id=chat_id,
                                       snapshot_id=snapshot['snapshot_id'], part=0)
                assert 'general team turn 1' in history['json_fragment']
                assert 'PROFILE_TOOL_OK' in history['json_fragment']
                await invoke('agent', 'release_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'])
                assert (await invoke('desktop', 'desktop_app_lifecycle', node_id=info.node_id))['success']
                agent_identity = session.app_binding('agent')
                assert (await settled(session, 'stop'))['state'] == 'stopped'
                agent_data = runtime.root/'node/apps'/info.fleet_id/'data'/agent_identity['instance_id']
                backend_log = (agent_data/'backend.log').read_text()
                # Check completed background work, not only the absence of errors.
                assert backend_log.count('agent=memory-extractor model=fleet-model://local/') >= cycle
                notes = agent_data/'agent/configuration/.pantheon/memory-store/memory-runtime/session-notes'
                note = (notes/(chat_id + '.md')).read_text()
                assert 'title: General Team verification' in note
                assert 'Verified PROFILE_TOOL_OK.' in note
                for failure in ('Memory extraction failed', 'Session note extraction failed',
                                'configured Fleet model tier is unavailable'):
                    assert failure not in backend_log, failure
            finally:
                # Failed startup is not a completed profile that stop() can
                # close. Retire actual test-owned instances before their bus.
                try:
                    state = await session.wire.status(info.node_id)
                    for item in sorted(state['instances'].values(), key=lambda i: i.get('app_id') != 'agent'):
                        if item['state'] == 'stopped':
                            continue
                        op = await session.wire.submit(info.node_id, 'stop', item['digest'],
                            scope=item['scope'], generation=item['generation'])
                        async with asyncio.timeout(90):
                            while (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]['state'] in ('queued', 'running'):
                                await asyncio.sleep(.1)
                finally:
                    await resolver.close()
        assert_stopped(children, info)
    # Public terminal and native Desktop control entry points consume exactly
    # the same compact owner setup, with no fixture template or manual graph.
    launch = [sys.executable, '-m', 'pantheon']
    options = ['--profile', str(tmp_path/'profile'), '--workspace', str(workspace),
               '--bundle', str(bundle), '--setup', str(setup_path)]
    cli = await asyncio.create_subprocess_exec(*launch, 'cli', *options,
        '--chat-id', chat_id, '-i', 'general team turn cli', '--stream',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(180): out, err = await cli.communicate()
        assert cli.returncode == 0, err.decode()
        records = [json.loads(line) for line in out.splitlines()]
        assert records[-1] == {'kind': 'result', 'chat_id': chat_id, 'response': 'scoped reply'}
        assert 'PROFILE_TOOL_OK' in json.dumps([r for r in records if r['kind'] == 'event'])
        state = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
        assert state['phase'] == 'stopped' and state['cycle'] == 3
    finally:
        if cli.returncode is None:
            cli.terminate()
            try: await asyncio.wait_for(cli.wait(), 120)
            except asyncio.TimeoutError: cli.kill(); await cli.wait()

    from pantheon.platform.local_desktop import PREFIX
    child = await asyncio.create_subprocess_exec(*launch, 'local', *options, '--desktop-agent', 'agent',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(180):
            while line := await child.stdout.readline():
                text = line.decode()
                if not text.startswith(PREFIX): continue
                record = json.loads(text[len(PREFIX):])
                assert not record['value'].get('needs_attention'), record
                if record['kind'] == 'ready': break
            else: pytest.fail('Desktop profile exited before readiness')
        identity = record['value']
        assert identity['revision'] == spec['packages']['agent']['revision']
        url = identity['url']
        headers = {'Origin': url.split('/view/')[0], 'X-Pantheon-View': '1'}
        async with httpx.AsyncClient(trust_env=False, timeout=100) as http:
            async def invoke_view(method, **args):
                response = await http.post(url+'rpc', headers=headers,
                    json={'method': method, 'timeout_s': 100, 'args': args})
                response.raise_for_status()
                result = response.json()['result']
                assert result.get('success') is not False, result
                return result
            agents = (await invoke_view('get_agents', chat_id=chat_id))['agents']
            assert {a['instance']['instance_id'] for a in agents} == logical
            assert (await invoke_view('chat', chat_id=chat_id,
                message=[{'role': 'user', 'content': 'general team turn desktop'}]))['success']
            snapshot = await invoke_view('open_agent_history', chat_id=chat_id)
            history = await invoke_view('read_agent_history', chat_id=chat_id,
                snapshot_id=snapshot['snapshot_id'], part=0)
            assert 'general team turn cli' in history['json_fragment']
            assert 'general team turn desktop' in history['json_fragment']
            assert 'PROFILE_TOOL_OK' in history['json_fragment']
            await invoke_view('release_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'])
        child.stdin.close()
        async with asyncio.timeout(180): out, err = await child.communicate()
        assert child.returncode == 0, err.decode()
        state = json.loads((tmp_path/'profile/app-profile/current.json').read_text())
        assert state['phase'] == 'stopped' and state['cycle'] == 4
        async with httpx.AsyncClient(trust_env=False) as http:
            with pytest.raises(httpx.ConnectError): await http.get(url)
    finally:
        if child.returncode is None:
            child.stdin.close()
            try: await asyncio.wait_for(child.wait(), 120)
            except asyncio.TimeoutError: child.kill(); await child.wait()

    calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
    full = [body for body in calls if any(t['function']['name'] == 'evolution__evolve'
                                         for t in body.get('tools', []))]
    assert full
    names = {t['function']['name'] for t in full[0]['tools']}
    assert {'shell__run_command', 'file_manager__generate_image', 'file_manager__observe_images',
            'web__web_crawl', 'desktop__desktop_call', 'evolution__evolve',
            'model_services__model_services_overview'} <= names
