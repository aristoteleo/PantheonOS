"""Unmodified General Team on the complete ordinary local App composition.

The upstream text engine is deterministic; App packages, grants, processes,
workspace files and profile close/reopen are real. Provider-specific exhaustive
functionality and paid image inference remain separate acceptance gates.
"""
import asyncio
import json
import os

import nats
import pytest

from pantheon.apps.builtin.desktop.build_managed import build as desktop
from pantheon.apps.builtin.evolution.build_managed import build as evolution
from pantheon.apps.builtin.file.build_managed import build as files
from pantheon.apps.builtin.fleet.build_managed import build as fleet
from pantheon.apps.builtin.notebook.build_managed import build as notebook
from pantheon.apps.builtin.web.build_managed import build as web
from pantheon.apps.local_agent import native_platform
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.apps.tool_profiles import compile_tool_profile
from pantheon.chatroom.package import build_package as build_agent
from pantheon.models.connector_package import build_package as build_connector
from pantheon.models.management_package import build_package as build_management
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from pantheon.platform.model_dependency_package import build_package as build_access
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile_agent import product_configuration
from test_local_profile_evolution import configure_evolution
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
async def test_general_team_all_providers_chat_and_clean_reopen(
        tmp_path, binaries, release, model_endpoint, image_engine, monkeypatch):
    target = native_platform()
    model_endpoint.tool_prompt_prefix = 'general team turn '
    model_endpoint.context_length = 131072
    builders = {'files': files, 'notebook': notebook, 'web': web,
                'evolution': evolution, 'desktop': desktop, 'fleet': fleet, 'model-management': build_management}
    names = {'files': 'file_manager', 'notebook': 'integrated_notebook',
             'web': 'web', 'evolution': 'evolution', 'desktop': 'desktop', 'fleet': 'fleet',
             'model-management': 'model_services'}
    packages, contracts, dependencies = {}, {}, {
        'shell': {'range': '^0.6.0', 'uses': ['shell@1'], 'binding': 'runtime'}}
    for alias, builder in builders.items():
        kwargs = {'model_sampling': True, 'image_generation': True} if alias == 'files' else {}
        packages[alias] = builder(tmp_path/(alias+'-release'), target, **kwargs)
        manifest = json.loads((packages[alias]/'app.json').read_text())
        uses = [f"{i['name']}@{i.get('version', 1)}" for i in manifest['provides']['interfaces']]
        _, _, dependencies[manifest['id']] = compile_tool_profile(manifest, alias=alias, uses=uses)
        contracts[names[alias]] = {'app': alias, 'uses': uses}
    contracts['file_manager']['service_methods'] = ['stat_path']
    dependencies['file-manager']['binding'] = 'startup'
    files_manifest = json.loads((packages['files']/'app.json').read_text())
    _, files_policy, _ = compile_tool_profile(files_manifest,
        alias='files', uses=contracts['file_manager']['uses'])
    packages['files-models'] = build_access(tmp_path/'files-model-access', target)
    packages['image-connector'] = build_connector(tmp_path/'image-connector', target)
    agent = build_agent(tmp_path/'complete-agent', target, version='0.7.0',
        frontend=os.environ['AGENT_APP_BUILD_DIR'], transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies=dependencies)
    catalog = tmp_path/'catalog'; catalog.mkdir()
    trust = {'$local': 'trust_roots_pem'}
    project = {'$local': 'workspace'}
    model = 'fleet-model://local/example%3A8b'

    def configure(setup):
        configure_evolution(setup)
        # Do not replace the canonical team or disable its plugins. Automatic
        # external template updates are unrelated to this isolated product gate.
        setup['agent']['settings'] = {'default_template_auto_update': False}
        # Original background plugins choose low/high as well as normal. The
        # owner explicitly binds all tiers; no runtime inference fallback.
        setup['agent']['models']['fleet_tiers'] = {tier: model for tier in ('low', 'normal', 'high')}
        setup['model_apps']['connector']['models'][0]['context_limit'] = model_endpoint.context_length
        setup['agent']['dependencies']['defaults'] = {
            'toolsets': [], 'mcp_servers': [], 'primary_toolsets': ['fleet', 'model_services']}
        # Background memory/learning and GUI clients outlive individual turns.
        # Their startup grant belongs to the App; runtime Files grants still
        # belong to each logical Agent and are retired independently.
        files_binding = {'credential': 'files', 'profile': 'file_manager'}
        setup['agent']['auxiliary'] = {'toolsets': {'file_manager': files_binding}}
        setup['agent']['view_dependencies'] = {'shared': {'toolsets': {'file_manager': files_binding}}}
        setup['extra_bindings'] = {'files': {**files_policy, 'component': 'backend'}}
        setup['model_apps']['image-connector'] = {'deployment_id': 'images', 'name': 'Image test engine',
            'models': [{'id': 'chosen-image-model', 'operations': ['image']}], 'app': {
                'scope': 'model-images', 'components': {'backend': {'values': {'connector': {
                    'engine': 'api', 'endpoint': image_engine[0]}}}}, 'bindings': {}}}
        setup['agent']['dependencies']['profiles']['toolsets'] = {}
        setup['tools'] = {}
        setup['tool_contracts'] = {**contracts, 'shell': {'app': 'shell', 'uses': ['shell@1'],
            'resource': {'kind': 'shell', 'arguments': {'run_command': 'shell_id'}}}}
        def provider(scope, values, bindings=None, credentials=None):
            backend = {'values': values}
            if credentials: backend['credentials'] = credentials
            return {'scope': scope, 'components': {'backend': backend}, 'bindings': bindings or {}}
        setup['providers']['files-models'] = provider('files-models', {'model_services': {
            'protocol': 1, 'http_origin': {'$local': 'controller'}, 'trust_roots_pem': trust,
            'directory_root': {'$local': 'directory_root'}, 'policies': {'files': {
                'consumer': {'$app': 'files'}, 'deployments': {'local': {'$model': 'connector'},
                    'images': {'$model': 'image-connector'}},
                'routes': {}, 'allow_wake': False}}}}, credentials={'hub': {'$local': 'owner_credential'}})
        setup['providers']['files'] = provider('shared-files', {
            'files': {'workspace': project},
            'sampling': {'credential': 'models', 'model': model, 'max_tokens': 256,
                         'max_requests_per_call': 2, 'trust_roots_pem': trust},
            'image_generation': {'credential': 'models', 'model': 'fleet-model://images/chosen-image-model', 'aliases': {},
                                 'timeout_seconds': 30, 'trust_roots_pem': trust}}, bindings={
            'models': {'app_id': 'model-services-control', 'component': 'backend',
                'provider': {'$app': 'files-models', 'component': 'backend', 'port': 'http'},
                'methods': {'model_services_control': {'arguments': ['operation', 'arguments'],
                                                      'bound': {'policy_id': 'files'}}}}})
        setup['providers']['notebook'] = provider('shared-notebook', {'notebook': {
            'workspace': project, 'execution_timeout': 60, 'execution_logging': True}})
        setup['providers']['fleet'] = provider('shared-fleet-management', {'fleet': {
            'bus': {'auth': 'creds-base64'}, 'controller_ca_pem': trust}},
            credentials={'fleet': {'$local': 'fleet_credential'}, 'controller': {'$local': 'owner_credential'}})
        setup['providers']['model-management'] = provider('shared-model-management', {'model_management': {
            'directory_root': {'$local': 'directory_root'},
            'bus': {'auth': 'creds-base64'}, 'controller_ca_pem': trust}},
            credentials={'fleet': {'$local': 'fleet_credential'}, 'controller': {'$local': 'owner_credential'}})
        setup['providers']['web'] = {'scope': 'shared-web', 'components': {}, 'bindings': {}}
        setup['providers']['desktop'] = provider('shared-desktop', {'desktop': {
            'user_seed': 'general-team', 'fleet': {'auth': 'creds-base64'},
            'events': {'auth': 'creds-base64'}, 'event_prefix': {'$local': 'fleet_event_prefix'},
            'catalog': [{'path': str(catalog), 'scope': 'user'}], 'data_roots': [project],
            'store': {'origin': 'https://store.invalid'}, 'data': {'mode': 'loopback'}}},
            credentials={'fleet': {'$local': 'fleet_credential'}, 'events': {'$local': 'fleet_credential'}})

    _, _, bundled, spec = await product_configuration(tmp_path, binaries, (agent, release[1]),
        model_endpoint, monkeypatch, provider_packages=packages, configure=configure)
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
                generated = await invoke('files', 'generate_image', prompt='Generate a test square')
                assert generated['success'] and (workspace/generated['images'][0]).is_file()
                overview = await invoke('model-management', 'model_services_overview')
                assert {d['deployment_id'] for d in overview['deployments']} == {'local', 'images'}
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
    calls = [body for path, _, body in model_endpoint.requests if path == '/v1/chat/completions']
    full = [body for body in calls if any(t['function']['name'] == 'evolution__evolve'
                                         for t in body.get('tools', []))]
    assert full
    names = {t['function']['name'] for t in full[0]['tools']}
    assert {'shell__run_command', 'file_manager__generate_image', 'file_manager__observe_images',
            'web__web_crawl', 'desktop__desktop_call', 'evolution__evolve',
            'model_services__model_services_overview'} <= names
