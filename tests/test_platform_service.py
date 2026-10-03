"""Platform operations must remain usable when the Agent package is absent."""

import asyncio
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from pantheon.platform.service import PlatformService


ROOT = Path(__file__).resolve().parents[1]


def test_platform_starts_and_serves_model_directory_without_agent(tmp_path):
    # An import blocker is stronger than checking sys.modules after startup:
    # it also catches lazy Agent imports during real directory RPC execution.
    code = '''
import asyncio, importlib.abc, sys
class NoAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        for prefix in ('pantheon.agent', 'pantheon.chatroom', 'pantheon.team',
                       'pantheon.factory', 'pantheon.internal.memory'):
            if fullname == prefix or fullname.startswith(prefix + '.'):
                raise AssertionError('Platform imported Agent: ' + fullname)
sys.meta_path.insert(0, NoAgent())
import httpx
from pantheon.platform.service import PlatformService
from pantheon.models.client import ModelServices
from pantheon.models.manager import ModelServiceManager
async def check():
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.path == '/api/model-services'
        assert request.headers['Authorization'] == 'Bearer test-user-token'
        return httpx.Response(200, json={'deployments': [
            {'deployment_id': 'local-model', 'models': [{'id': 'small'}]}]})
    client = ModelServices(hub='https://hub.test', token='test-user-token',
                           transport=httpx.MockTransport(respond))
    service = PlatformService()
    service._model_services = ModelServiceManager(client=client)
    try:
        await service.run(remote=False)
        info = await service.platform_info()
        assert 'fleet_app_lifecycle' in info['methods']
        assert 'chat' not in info['methods']
        result = await service.model_services_list()
        assert result['deployments'][0]['deployment_id'] == 'local-model'
        assert len(requests) == 1
    finally:
        await client.aclose()
        await service.cleanup()
asyncio.run(check())
'''
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('FLEET_', 'PANTHEON_', 'NATS_'))}
    env.update(PYTHONPATH=str(ROOT), HOME=str(tmp_path))
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_extracted_wire_arguments_match_pre_migration_contract():
    baseline = json.loads((ROOT / 'docs/agent-app-rpc-inventory.json').read_text())
    service = PlatformService()
    from pantheon.apps.builtin.llm_playground.service import PlaygroundToolSet
    playground = PlaygroundToolSet()
    import inspect
    for row in baseline['methods']:
        if not row['extracted']:
            continue
        owner = playground if row['owner'] == 'playground' else service
        method = owner.functions[row['method']][0]
        if row['owner'] == 'playground':
            assert row['method'] not in service.functions
        node = ast.parse(__import__('textwrap').dedent(inspect.getsource(method))).body[0]
        assert ast.unparse(node.args) == row['signature'], row['method']


def test_legacy_chatroom_keeps_all_original_rpc_signatures():
    import inspect
    import textwrap
    from pantheon.chatroom.room import ChatRoom
    baseline = json.loads((ROOT / 'docs/agent-app-rpc-inventory.json').read_text())
    for row in baseline['methods']:
        method = getattr(ChatRoom, row['method'])
        assert method._is_tool
        node = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
        assert ast.unparse(node.args) == row['signature'], row['method']


@pytest.mark.parametrize('failure', [None, TimeoutError('unknown outcome')])
def test_app_call_routes_explicit_workspace_and_never_replays_timeout(monkeypatch, failure):
    from pantheon.apps import resolver as resolver_module
    from pantheon.apps.proxy import ToolsetProxy
    from unittest.mock import Mock
    resolver = SimpleNamespace(
        resolves=lambda name: name == 'shell',
        project_scope=lambda path: 'project:' + path,
        ensure_instance=AsyncMock(return_value='shell-service'),
        invalidate=Mock(),
    )
    proxy = SimpleNamespace(invoke=AsyncMock(return_value={'stdout': 'ok'}, side_effect=failure))
    monkeypatch.setattr(resolver_module, 'get_shared_resolver', lambda: resolver)
    monkeypatch.setattr(ToolsetProxy, 'from_toolset', lambda sid: proxy)
    result = asyncio.run(PlatformService().call_app_service('run_command',
        {'command': 'pwd'}, 'shell', workdir='/workspace/project-a'))
    resolver.ensure_instance.assert_awaited_once_with('shell',
        scope='project:/workspace/project-a', workdir='/workspace/project-a')
    proxy.invoke.assert_awaited_once_with('run_command', {'command': 'pwd'})
    resolver.invalidate.assert_not_called()
    assert result == ({'success': False, 'error': 'unknown outcome'} if failure else {'stdout': 'ok'})


def test_fleet_lifecycle_preserves_target_generation_and_lease(monkeypatch):
    from pantheon.apps import resolver
    from pantheon.apps.lifecycle import FleetLifecycle

    monkeypatch.setattr(resolver, 'get_shared_resolver', lambda: object())
    status = AsyncMock(return_value={'instances': {}})
    usage = AsyncMock(return_value={'leased': True})
    submit = AsyncMock(return_value={'operation_id': 'op-1'})
    monkeypatch.setattr(FleetLifecycle, 'status', status)
    monkeypatch.setattr(FleetLifecycle, 'usage', usage)
    monkeypatch.setattr(FleetLifecycle, 'submit', submit)

    async def check():
        service = PlatformService()
        assert (await service.fleet_app_lifecycle('mac', 'status'))['success']
        await service.fleet_app_lifecycle('mac', 'lease', generation=7,
            instance_id='app-1', revision='digest-1', lease_id='consumer-1', release=True)
        await service.fleet_app_lifecycle('hpc-job', 'stop', digest='digest-2',
            scope='session-1', generation=3, operation_id='op-1')
    asyncio.run(check())
    status.assert_awaited_once_with('mac')
    usage.assert_awaited_once_with('mac', 'lease', instance_id='app-1',
        revision='digest-1', generation=7, lease_id='consumer-1',
        release=True, keep_alive=False)
    submit.assert_awaited_once_with('hpc-job', 'stop', 'digest-2',
        scope='session-1', generation=3, operation_id='op-1')


def test_platform_does_not_claim_success_without_fleet(monkeypatch):
    from pantheon.apps import resolver
    monkeypatch.setattr(resolver, 'get_shared_resolver', lambda: None)
    result = asyncio.run(PlatformService().fleet_app_lifecycle('missing', 'start'))
    assert result == {'success': False, 'error': 'Fleet is not connected'}


def test_platform_default_workspace_is_not_process_cwd(monkeypatch, tmp_path):
    from pantheon.platform import apps_api
    invoke = AsyncMock(return_value={'success': True})
    monkeypatch.setattr(apps_api, 'invoke_app_tool', invoke)
    service = PlatformService(workspace_path=str(tmp_path))
    asyncio.run(service.call_app_service('list_files', {}, 'file_manager'))
    invoke.assert_awaited_once_with('list_files', {}, 'file_manager', workdir=str(tmp_path))


def test_no_responder_recovery_invalidates_only_requested_project(monkeypatch):
    from nats.errors import NoRespondersError
    from pantheon.apps import resolver as resolver_module
    from pantheon.apps.proxy import ToolsetProxy
    from unittest.mock import Mock
    resolver = SimpleNamespace(
        resolves=lambda name: True,
        project_scope=lambda path: 'project:' + path,
        ensure_instance=AsyncMock(side_effect=['old', 'new']), invalidate=Mock())
    old = SimpleNamespace(has_instance_binding=False, invoke=AsyncMock(side_effect=NoRespondersError()))
    new = SimpleNamespace(invoke=AsyncMock(return_value={'stdout': 'ok'}))
    monkeypatch.setattr(resolver_module, 'get_shared_resolver', lambda: resolver)
    monkeypatch.setattr(ToolsetProxy, 'from_toolset', lambda sid: old if sid == 'old' else new)
    result = asyncio.run(PlatformService().call_app_service('run_command',
        {'command': 'pwd'}, 'shell', workdir='/workspace/project-a'))
    assert result == {'stdout': 'ok'}
    resolver.invalidate.assert_called_once_with('shell', scope='project:/workspace/project-a')
    assert resolver.ensure_instance.await_count == 2


def test_cleanup_releases_only_host_connections():
    async def check():
        service = PlatformService()
        refresh = asyncio.create_task(asyncio.sleep(100))
        service._fleet_session_task = refresh
        fleet = SimpleNamespace(cleanup=AsyncMock())
        service._fleet_ts = fleet
        await service.cleanup()
        await service.cleanup()
        assert refresh.cancelled()
        fleet.cleanup.assert_awaited_once()
    asyncio.run(check())
