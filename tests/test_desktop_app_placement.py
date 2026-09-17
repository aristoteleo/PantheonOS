import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from apps.desktop.app_placement import AppPlacement


def node(id='mac', **kw):
    return {'node_id': id, 'name': id, 'status': 'online', 'kind': 'machine', 'os': 'darwin', 'arch': 'arm64',
            'caps': ['proc'], 'runtimes': {'app-lifecycle': '1', 'app-services': '1', 'app-rpc': '1'}, **kw}


@pytest.fixture
def placement(tmp_path):
    p = AppPlacement(SimpleNamespace(records=tmp_path, lock=nullcontext), None)
    p.nodes = AsyncMock(return_value=[node(), node('workspace', kind='sandbox', os='linux')])
    return p


MANIFEST = {'entry': {'backend': 'backend/__init__.py'}}


@pytest.mark.asyncio
async def test_missing_preference_falls_back_but_never_to_offline_or_incompatible_nodes(placement):
    placement.save('example', 'removed-mac')
    result = await placement.target('example', MANIFEST)
    assert result['node_id'] == 'workspace'
    assert 'no longer' in result['fallback_reason']
    placement.nodes.return_value[1]['runtimes'].pop('app-rpc')
    assert (await placement.target('example', MANIFEST))['node_id'] == 'mac'
    placement.nodes.return_value[0]['status'] = 'offline'
    with pytest.raises(RuntimeError, match='No compatible'):
        await placement.target('example', MANIFEST)

@pytest.mark.asyncio
async def test_auto_prefers_workspace_and_unavailable_preference_falls_back(placement):
    assert (await placement.target('example', MANIFEST))['node_id'] == 'workspace'
    placement.save('example', 'mac')
    assert AppPlacement(placement.manager, None).default('example') == 'mac'
    assert (await placement.target('example', MANIFEST))['node_id'] == 'mac'
    assert (await placement.target('example', MANIFEST, 'workspace'))['node_id'] == 'workspace'
    placement.nodes.return_value[0]['status'] = 'offline'
    fallback = await placement.target('example', MANIFEST)
    assert fallback['node_id'] == 'workspace'
    assert fallback['preferred_node_id'] == 'mac'
    assert 'offline' in fallback['fallback_reason']
    assert placement.default('example') == 'mac'
    with pytest.raises(ValueError, match='offline'):
        await placement.target('example', MANIFEST, 'mac')
    placement.nodes.return_value[0]['status'] = 'online'
    assert (await placement.target('example', MANIFEST))['node_id'] == 'mac'
    placement.save('example', '')
    assert (await placement.target('example', MANIFEST))['node_id'] == 'workspace'


@pytest.mark.asyncio
async def test_rejects_foreign_nodes_old_runners_and_platform_mismatches(placement):
    with pytest.raises(ValueError, match='no longer'):
        await placement.target('example', MANIFEST, 'foreign')
    placement.nodes.return_value[0]['runtimes'].pop('app-rpc')
    with pytest.raises(ValueError, match='Update Fleet'):
        await placement.target('example', MANIFEST, 'mac')
    native = {'execution': {'protocol': 1, 'platform_manifests': {'linux-amd64': 'fleet.linux-amd64.json'}}}
    with pytest.raises(ValueError, match='operating system'):
        await placement.target('office', native, 'mac')
    with pytest.raises(ValueError, match='Invalid App id'):
        placement.save('../escape', 'mac')


@pytest.mark.asyncio
async def test_calls_keep_pinned_node_and_generation_despite_changed_default(placement, monkeypatch):
    binding = dict(node_id='mac', instance_id='instance', revision='digest', generation=2)
    client = SimpleNamespace(invoke=AsyncMock(return_value={'response': {'success': True, 'result': {'node': 'mac'}}}))
    state = {'instances': {'instance': {'app_id': 'example', 'digest': 'digest', 'generation': 2}}}
    lifecycle = SimpleNamespace(_client=AsyncMock(return_value=client), status=AsyncMock(return_value=state))
    monkeypatch.setattr('apps.desktop.app_placement.FleetLifecycle', lambda _: lifecycle)
    placement.save('example', 'workspace')
    result = await placement.call('example', binding, 'edit', {'value': 1}, 30)
    assert result['result']['node'] == 'mac'
    client.invoke.assert_awaited_once_with('mac', 'example', binding, 'edit', {'value': 1}, 30)
    state['instances']['instance']['generation'] = 3
    with pytest.raises(ValueError, match='instance changed'):
        await placement.call('example', binding, 'edit', {}, 30)
    assert client.invoke.await_count == 1
    state['instances']['instance']['app_id'] = 'other'
    with pytest.raises(ValueError, match='does not belong'):
        await placement.call('example', binding, 'edit', {}, 30)


@pytest.mark.asyncio
async def test_failed_install_does_not_start_and_recovered_instance_not_restarted(placement, monkeypatch):
    operation = {'request': {'operation_id': 'install'}}
    placement.install = AsyncMock(return_value=dict(operation=operation, node_id='mac', node_name='Mac', digest='digest', component='backend', app_revision=None))
    state = {'operations': {'install': {'state': 'failed', 'error': 'dependency failed'}}, 'instances': {}}
    lifecycle = SimpleNamespace(status=AsyncMock(return_value=state), submit=AsyncMock())
    monkeypatch.setattr('apps.desktop.app_placement.FleetLifecycle', lambda _: lifecycle)
    with pytest.raises(RuntimeError, match='dependency failed'):
        await placement.ensure('example')
    lifecycle.submit.assert_not_awaited()
    state['operations']['install'] = {'state': 'succeeded'}
    state['instances']['instance'] = {'digest': 'digest', 'scope': 'app', 'state': 'recovered', 'generation': 2}
    with pytest.raises(RuntimeError, match='recovery'):
        await placement.ensure('example')
    lifecycle.submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_inventory_places_window_with_its_backend_node(monkeypatch):
    from datetime import datetime, timezone
    from apps.fleet.inventory import inventory_from_records
    from pantheon.apps.proxy import ToolsetProxy
    now = datetime.now(timezone.utc).isoformat()
    records = [{'node_id': 'workspace', 'name': 'Workspace', 'kind': 'sandbox', 'last_seen': now,
                'state': {'status': 'online', 'instances': [{'app_id': 'desktop', 'health': 'healthy', 'service_id': 'desktop-service'}]}},
               {'node_id': 'mac', 'name': 'My Mac', 'kind': 'machine', 'last_seen': now, 'state': {'status': 'online'}}]
    reply = {'success': True, 'instances': [{'app_id': 'spatial3d', 'kind': 'window', 'backend_node_id': 'mac'},
                                          {'app_id': 'files', 'kind': 'window'}]}
    monkeypatch.setattr(ToolsetProxy, 'from_toolset', lambda _: SimpleNamespace(invoke=AsyncMock(return_value=reply)))
    result = await inventory_from_records(records)
    windows = {i['app_id']: i for i in result['instances'] if i['kind'] == 'window'}
    assert windows['spatial3d']['node_id'] == 'mac'
    assert windows['spatial3d']['node_name'] == 'My Mac'
    assert windows['files']['node_id'] == 'workspace'


@pytest.mark.asyncio
async def test_tool_call_honors_window_binding_and_refuses_conflicting_node(monkeypatch):
    from apps.desktop.toolset import DesktopToolSet
    ts = DesktopToolSet('placement-test')
    binding = dict(node_id='mac', instance_id='instance', revision='digest', generation=2)
    placement = SimpleNamespace(ensure=AsyncMock(), call=AsyncMock(return_value={'success': True, 'result': 42}))
    monkeypatch.setattr(ts, '_app_placement', lambda: placement)
    monkeypatch.setattr(ts, '_desktop_window', lambda _: {'app_id': 'pkg:spatial3d', 'args': {'appInstance': binding}})
    result = await ts.app_call('spatial3d', 'datasets', window_id='win-1')
    assert result['result'] == 42
    placement.ensure.assert_not_awaited()
    placement.call.assert_awaited_once_with('spatial3d', binding, 'datasets', None, 60.0)
    result = await ts.app_call('spatial3d', 'edit', window_id='win-1', node_id='workspace')
    assert not result['success'] and 'conflicts' in result['error']
    assert placement.call.await_count == 1
