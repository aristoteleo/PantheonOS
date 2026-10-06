"""Saved Hub preset -> PlatformService -> actual complete Agent on native Fleet.

Hub authentication/database/routes and Fleet/App processes are real. HTTP delivery
uses an in-process ASGI transport and the upstream model is deterministic. This
does not claim remote provisioning, rendered UI or physical cross-host acceptance.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import nats
import pytest

from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.app_preset import fetch_hub_preset
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from pantheon.platform.service import PlatformService
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile_agent import product_configuration


@asynccontextmanager
async def owner_hub(tmp_path, user_id, monkeypatch):
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from pantheon_hub.api.fleet_apps import create_fleet_apps_router
    from pantheon_hub.core.database import Base, User
    from pantheon_hub.core.security import create_access_token
    from pantheon_hub.utils.auth import create_auth_dependencies
    import pantheon_hub.utils.auth as auth

    monkeypatch.setattr(auth, 'db_user_to_pydantic',
        lambda user: SimpleNamespace(id=user.id, username=user.username))
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path/'hub.db'))
    try:
        session = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session() as database:
            database.add(User(id=user_id, username='native-owner', email='native@example.test'))
            await database.commit()
        class DB:
            SessionLocal = session
            async def get_user_by_id(self, database, identity):
                return await database.get(User, identity)
        config = SimpleNamespace(session_secret_key='native-startup-test-secret-at-least-32-characters')
        database = DB()
        owner, _ = create_auth_dependencies(config, database)
        app = FastAPI()
        app.include_router(create_fleet_apps_router(config, database, owner))
        def token(scope=None):
            claims = {'sub': user_id, 'user_id': user_id}
            if scope:
                claims['scope'] = scope
            return create_access_token(claims, config.session_secret_key)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='https://hub.test') as client:
            yield client, token
    finally:
        await engine.dispose()


async def startup_settled(service):
    async with asyncio.timeout(900):
        while True:
            result = await service.platform_app_preset_status()
            if result['state'] != 'pending':
                return result
            await asyncio.sleep(.1)


@pytest.mark.asyncio
async def test_saved_complete_agent_platform_start_restart_and_explicit_stop(
        tmp_path, binaries, model_endpoint, monkeypatch):
    release = os.environ.get('AGENT_STARTUP_RELEASE')
    hub_source = os.environ.get('AGENT_STARTUP_HUB_SOURCE')
    if not release or not hub_source:
        pytest.skip('Supply complete native Agent release and matching Hub source')
    monkeypatch.syspath_prepend(hub_source)
    from pantheon_hub.core.app_startup import configure_app_startup
    model_endpoint.context_length = 131072
    model_endpoint.tool_prompt_prefix = 'platform startup turn '
    catalog = tmp_path/'catalog'; catalog.mkdir()

    def configure(setup):
        agent = setup['agent']
        agent.pop('dependencies')
        agent['settings'] = {'default_template_auto_update': False}
        agent['models']['fleet_tiers'] = {tier: 'fleet-model://local/example%3A8b'
                                        for tier in ('low', 'normal', 'high')}
        setup['model_apps']['connector']['models'][0]['context_limit'] = 131072
        complete = dict(protocol=1, preset='general-team', agent=agent,
            models=setup['models'], model_apps=setup['model_apps'],
            files={name: {'state': 'unconfigured'} for name in ('sampling', 'image_generation')},
            desktop={'user_seed': 'platform-startup', 'catalog': [{'path': str(catalog), 'scope': 'user'}],
                     'store': {'origin': 'https://store.invalid'}, 'data': {'mode': 'loopback'}},
            evolution={'execution': 'node', 'options': {'num_workers': 1, 'llm_weight': 0,
                'function_weight': 1, 'evaluation_timeout': 30, 'mutation_timeout': 60,
                'max_tool_calls_per_mutation': 8, 'max_mutation_turns': 8}})
        setup.clear(); setup.update(complete)

    _, _, bundled, spec = await product_configuration(tmp_path, binaries, None,
        model_endpoint, monkeypatch, release_root=Path(release), configure=configure)
    workspace = tmp_path/'workspace'
    (workspace/'shared.txt').write_text('PLATFORM_STARTUP_FILES')
    async with LocalFleet(tmp_path/'profile', bundled, workspace=workspace) as runtime:
        info, children = runtime.coordinates, list(runtime._children)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                               inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id,
                                       str(workspace), connection=nc)
        staging = LocalAppProfile(runtime, spec, resolver)
        services = []
        try:
            # Provision only immutable artifacts, node-vault references and the
            # explicit owner recipe. LocalAppProfile.advance is never called.
            await staging._open()
            await staging._stage()
            assert not (await staging.wire.status(info.node_id))['instances']
            recipe = staging._record['recipe']
            assert len(recipe['apps']) == 12 and len(recipe['model_apps']) == 1
            assert len(json.dumps(recipe).encode()) > 64 * 1024
            user_id = (runtime.root/'owner.key').read_text().strip()
            async with owner_hub(tmp_path, user_id, monkeypatch) as (hub, token):
                path = '/api/fleet/apps/startup/default'
                response = await hub.put(path, headers={'Authorization': 'Bearer '+token()},
                    json={'revision': 0, 'recipe': recipe})
                assert response.status_code == 200, response.text
                env = {'PANTHEON_NODE_APPS': 'platform', 'PANTHEON_HUB_URL': 'https://hub.test',
                       'FLEET_KEY': token('fleet'),
                       'PANTHEON_PLATFORM_STATE_DIR': str(tmp_path/'persistent/platform-private')}
                monkeypatch.setenv('PANTHEON_APP_PRESETS_ENABLED', 'true')
                configure_app_startup(env)
                monkeypatch.setattr('pantheon.apps.resolver.get_shared_resolver', lambda: resolver)
                reads = []
                async def load():
                    value = await fetch_hub_preset(env['PANTHEON_APP_PRESET_URL'],
                        hub=env['PANTHEON_HUB_URL'], token=env['FLEET_KEY'],
                        owner=info.fleet_id, transport=hub._transport)
                    assert value == recipe
                    reads.append(value['operation_id'])
                    return value
                async def platform():
                    home = tmp_path/f'platform-home-{len(services)}'
                    home.mkdir()
                    monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
                    service = PlatformService(workspace_path=str(workspace), app_preset_source=load,
                        owner_state_directory=env['PANTHEON_PLATFORM_STATE_DIR'])
                    # Explicit test infrastructure, with production coordinators,
                    # maintenance, bootstrap dispatch and durable journals intact.
                    factory = service._dependency_starter
                    def starter():
                        value = factory()
                        value.authority = staging.authority
                        return value
                    service._dependency_starter = starter
                    service._model_services_manager = lambda: staging.manager
                    service._app_preset.interval = .1
                    services.append(service)
                    await service.run(remote=False)
                    return service
                service = await platform()
                result = await startup_settled(service)
                assert result['state'] == 'ready', result
                assert result['operation_id'] == recipe['operation_id']
                assert 'chat' not in (await service.platform_info())['methods']
                bootstrap = service._model_service_bootstrap()
                deployment = service._app_deployments()
                consumer_id = bootstrap.child_id(recipe, 'consumers')
                completed = deployment.inspect(owner=info.fleet_id, operation_id=consumer_id)
                assert completed['state'] == 'ready'
                client = await resolver._ensure_client()
                ids = {'agent': 'agent', 'files': 'file-manager', 'notebook': 'integrated-notebook'}
                async def invoke(alias, method, **arguments):
                    prepared = dict(completed['prepared'][alias])
                    node = prepared.pop('node_id')
                    prepared['generation'] += 1
                    value = await client.invoke(node, ids[alias], prepared, method, arguments, 100)
                    assert 'error' not in value, value
                    value = value['response']
                    assert value['success'] is True, value
                    result = value['result']
                    assert not result.get('error') and result.get('success') is not False, result
                    return result
                assert 'PLATFORM_STARTUP_FILES' in json.dumps(await invoke('files', 'read_file', file_path='shared.txt'))
                assert (await invoke('notebook', 'execution_host'))['workspace'] == str(workspace)
                chat = (await invoke('agent', 'create_chat', chat_name='Platform startup', project_name='Shared'))['chat_id']
                agents = (await invoke('agent', 'get_agents', chat_id=chat))['agents']
                assert len(agents) == 3
                identities = {a['instance']['instance_id'] for a in agents}
                for turn in (1, 2):
                    await invoke('agent', 'chat', chat_id=chat,
                        message=[{'role': 'user', 'content': f'platform startup turn {turn}'}])
                    snapshot = await invoke('agent', 'open_agent_history', chat_id=chat)
                    history = await invoke('agent', 'read_agent_history', chat_id=chat,
                        snapshot_id=snapshot['snapshot_id'], part=0)
                    assert 'platform startup turn 1' in history['json_fragment']
                    assert 'PROFILE_TOOL_OK' in history['json_fragment']
                    await invoke('agent', 'release_agent_history', chat_id=chat, snapshot_id=snapshot['snapshot_id'])
                    if turn == 1:
                        before = await staging.wire.status(info.node_id)
                        await service.cleanup()
                        service = await platform()
                        assert (await startup_settled(service))['state'] == 'ready'
                        after = await staging.wire.status(info.node_id)
                        assert after['operations'] == before['operations']
                        # Catalog/status RPCs update usage calls/idle times.
                        # This is not a new process or a lifecycle operation;
                        # retain every other field, including process resources.
                        def running_instances(state):
                            return {key: {k: v for k, v in item.items() if k != 'usage'}
                                    for key, item in state['instances'].items()}
                        assert running_instances(after) == running_instances(before)
                        assert identities == {a['instance']['instance_id'] for a in
                            (await invoke('agent', 'get_agents', chat_id=chat))['agents']}
                # An explicitly stopped Agent stays stopped after platform
                # replacement. Inspection still identifies the original operation.
                await service.cleanup()
                binding = completed['prepared']['agent']
                state = await staging.wire.status(info.node_id)
                instance = state['instances'][binding['instance_id']]
                operation = await staging.wire.submit(info.node_id, 'stop', instance['digest'],
                    scope=instance['scope'], generation=instance['generation'])
                async with asyncio.timeout(90):
                    while (await staging.wire.status(info.node_id))['operations'][operation['request']['operation_id']]['state'] in ('queued', 'running'):
                        await asyncio.sleep(.1)
                before = await staging.wire.status(info.node_id)
                service = await platform()
                result = await startup_settled(service)
                assert result['state'] == 'needs_attention', result
                assert result['operation_id'] == recipe['operation_id']
                assert (await staging.wire.status(info.node_id))['operations'] == before['operations']
                assert reads == [recipe['operation_id']] * 3
                assert all(not list(home.iterdir()) for home in tmp_path.glob('platform-home-*'))
                assert list((tmp_path/'persistent/platform-private').glob('*/model-startup/*.json'))
        finally:
            for service in services:
                await service.cleanup()
            try:
                state = await staging.wire.status(info.node_id)
                for item in sorted(state['instances'].values(), key=lambda i: i.get('app_id') != 'agent'):
                    if item['state'] == 'stopped':
                        continue
                    operation = await staging.wire.submit(info.node_id, 'stop', item['digest'],
                        scope=item['scope'], generation=item['generation'])
                    async with asyncio.timeout(90):
                        while (await staging.wire.status(info.node_id))['operations'][operation['request']['operation_id']]['state'] in ('queued', 'running'):
                            await asyncio.sleep(.1)
                assert all(item['state'] == 'stopped' for item in
                           (await staging.wire.status(info.node_id))['instances'].values())
            finally:
                await resolver.close()
    assert_stopped(children, info)
