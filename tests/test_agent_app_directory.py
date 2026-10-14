"""Installed Apps in the Agent's App directory (docs/agent-app-directory.md)."""
import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.desktop import agent_apps
from apps.desktop.agent_apps import AgentLeases, action_function, check_args, offer
from apps.desktop.app_placement import AppPlacement

TOOL_APP = {
    'id': 'graph-tools', 'name': 'Graph Tools', 'version': '1.0.0', 'apiVersion': 2,
    'description': 'Network analysis.\nMore detail.',
    'entry': {'backend': 'backend/__init__.py'},
    'provides': {'tools': [
        {'name': 'centrality', 'description': 'Rank nodes.', 'params': [
            {'name': 'graph', 'type': 'str'}, {'name': 'top', 'type': 'int', 'required': False, 'default': 10}]},
        {'name': 'internal', 'hidden': True, 'params': []},
    ]},
    'actions': [{'name': 'focusNode', 'description': 'Centre a node.', 'params': {'id': 'node id'}},
                {'name': 'fit'}],
}
VIEWER = {'id': 'viewer', 'name': 'Viewer', 'version': '1.0.0', 'apiVersion': 2,
          'entry': {'frontend': 'frontend/index.js'}}


def test_an_app_offers_its_declared_tools_and_actions_only():
    entry = offer(TOOL_APP)
    assert entry['app_id'] == 'graph-tools'
    assert [f['name'] for f in entry['tools']] == ['centrality']
    assert entry['tools'][0]['parameters']['required'] == ['graph']
    assert [f['name'] for f in entry['actions']] == ['focusNode', 'fit']
    assert entry['actions'][0]['parameters']['properties'] == {'id': {'description': 'node id'}}
    assert 'Window action' in entry['actions'][1]['description']
    assert offer(VIEWER) is None
    clash = {**TOOL_APP, 'actions': [{'name': 'centrality'}]}
    assert offer(clash) is None
    assert action_function({'name': '$close'}) is None


def test_only_declared_arguments_pass():
    tool = offer(TOOL_APP)['tools'][0]
    assert check_args(tool, {'graph': 'g'}) == {'graph': 'g'}
    with pytest.raises(ValueError, match='Unknown arguments'):
        check_args(tool, {'graph': 'g', 'path': '/etc'})
    with pytest.raises(ValueError, match='Missing arguments'):
        check_args(tool, {'top': 3})
    assert check_args(offer(TOOL_APP)['actions'][1], None) == {}


@pytest.mark.asyncio
async def test_agent_lease_is_renewed_while_used_and_released_ten_minutes_after(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(agent_apps.time, 'monotonic', lambda: clock[0])
    calls = []
    leases = AgentLeases(AsyncMock(side_effect=lambda b, lease, release: calls.append((lease, release))))
    binding = {'node_id': 'n', 'instance_id': 'i', 'revision': 'r', 'generation': 1}
    await leases.use(binding, 'abcdef0123456789')
    await leases.use(binding, 'abcdef0123456789')
    assert calls == [('agent-abcdef0123456789', False)]
    clock[0] += agent_apps.RENEW_SECONDS
    await leases.tick()
    assert calls[-1] == ('agent-abcdef0123456789', False) and len(calls) == 2
    clock[0] += agent_apps.AGENT_LEASE_SECONDS
    await leases.tick()
    assert calls[-1] == ('agent-abcdef0123456789', True)
    await leases.tick()
    assert len(calls) == 3
    with pytest.raises(ValueError):
        AgentLeases.lease_id('../x')
    await leases.close()


def test_policy_is_kept_beside_the_default_node(tmp_path):
    placement = AppPlacement(SimpleNamespace(records=tmp_path, lock=nullcontext), None)
    assert placement.policy('viewer') == 'on_demand'
    placement.set_policy('viewer', 'resident')
    placement.save('viewer', 'mac')
    assert placement.policy('viewer') == 'resident' and placement.default('viewer') == 'mac'
    assert placement.defaults() == {'viewer': {'node_id': 'mac', 'policy': 'resident'}}
    with pytest.raises(ValueError):
        placement.set_policy('viewer', 'sometimes')


class Placement:
    def __init__(self, manifests, policies=None):
        self.manifests, self.policies = manifests, policies or {}
        self.ensure = AsyncMock(return_value={'node_id': 'n', 'instance_id': 'i', 'revision': 'r', 'generation': 2})
        self.call = AsyncMock(return_value={'success': True, 'result': {'ranked': ['a']}})

    def resolve(self, app_id, revision=None):
        return None, self.manifests[app_id], None

    def policy(self, app_id):
        return self.policies.get(app_id, 'on_demand')


def desktop(monkeypatch, placement, windows=None):
    from apps.desktop.toolset import DesktopToolSet
    ts = DesktopToolSet('agent-apps')
    store = SimpleNamespace(current=lambda: None, session=SimpleNamespace(windows=windows or {}))
    monkeypatch.setattr(ts, '_desktop', lambda: store)
    monkeypatch.setattr(ts, '_app_placement', lambda: placement)
    monkeypatch.setattr(ts, '_app_scope_roots', lambda: [])
    ts._app_usage = AsyncMock()
    return ts


@pytest.mark.asyncio
async def test_directory_lists_installed_apps_with_something_to_offer(monkeypatch):
    inventory = {'apps': [
        {'id': 'graph-tools', 'effective': True, 'manifest': TOOL_APP},
        {'id': 'graph-tools', 'effective': False, 'manifest': TOOL_APP},
        {'id': 'viewer', 'effective': True, 'manifest': VIEWER},
        {'id': 'cytoscape', 'effective': True, 'manifest': {**TOOL_APP, 'id': 'cytoscape'}},
        {'id': 'fleet', 'effective': True, 'manifest': {**TOOL_APP, 'id': 'fleet'}},
    ]}
    monkeypatch.setattr('apps.desktop.store_manager.AppStoreManager',
                        lambda roots: SimpleNamespace(inventory=lambda **kw: inventory))
    ts = desktop(monkeypatch, Placement({}, {'cytoscape': 'disabled'}))
    reply = await ts.agent_app_directory(exclude=['fleet'])
    assert reply['success'] and [a['app_id'] for a in reply['apps']] == ['graph-tools']


@pytest.mark.asyncio
async def test_a_tool_call_starts_the_app_holds_the_agent_lease_and_calls_it(monkeypatch):
    placement = Placement({'graph-tools': TOOL_APP})
    ts = desktop(monkeypatch, placement)
    reply = await ts.agent_app_call('graph-tools', 'centrality', {'graph': 'g'}, holder='abcdef0123456789')
    assert reply == {'success': True, 'result': {'ranked': ['a']}}
    placement.ensure.assert_awaited_once()
    placement.call.assert_awaited_once_with('graph-tools', placement.ensure.return_value, 'centrality',
                                            {'graph': 'g'}, 600)
    ts._app_usage.assert_awaited_once_with(placement.ensure.return_value,
                                           lease_id='agent-abcdef0123456789', release=False)
    await ts._agent_leases().close()


@pytest.mark.asyncio
async def test_a_resident_app_is_kept_alive_and_a_disabled_one_refused(monkeypatch):
    placement = Placement({'graph-tools': TOOL_APP}, {'graph-tools': 'resident'})
    ts = desktop(monkeypatch, placement)
    assert (await ts.agent_app_call('graph-tools', 'centrality', {'graph': 'g'}))['success']
    ts._app_usage.assert_awaited_once_with(placement.ensure.return_value, keep_alive=True)
    placement.policies['graph-tools'] = 'disabled'
    reply = await ts.agent_app_call('graph-tools', 'centrality', {'graph': 'g'})
    assert not reply['success'] and 'disabled' in reply['error']


@pytest.mark.asyncio
async def test_undeclared_tools_and_arguments_are_refused_before_anything_starts(monkeypatch):
    placement = Placement({'graph-tools': TOOL_APP})
    ts = desktop(monkeypatch, placement)
    hidden = await ts.agent_app_call('graph-tools', 'internal', {})
    assert not hidden['success'] and hidden['tools'] == ['centrality']
    extra = await ts.agent_app_call('graph-tools', 'centrality', {'graph': 'g', 'shell': 'rm'})
    assert not extra['success'] and 'Unknown arguments' in extra['error']
    bad_holder = await ts.agent_app_call('graph-tools', 'centrality', {'graph': 'g'}, holder='../x')
    assert not bad_holder['success']
    placement.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_action_runs_in_the_apps_window_opening_one_when_needed(monkeypatch):
    placement = Placement({'graph-tools': TOOL_APP})
    ts = desktop(monkeypatch, placement, windows={'win-7': {'app_id': 'pkg:graph-tools'}})
    ts.desktop_call = AsyncMock(return_value={'success': True, 'result': 'centred'})
    ts.desktop_open = AsyncMock()
    reply = await ts.agent_app_call('graph-tools', 'focusNode', {'id': 'TP53'})
    assert reply == {'success': True, 'result': 'centred', 'window_id': 'win-7'}
    ts.desktop_call.assert_awaited_once_with('win-7', 'focusNode', {'id': 'TP53'})
    ts.desktop_open.assert_not_awaited()

    ts = desktop(monkeypatch, placement)
    ts.desktop_open = AsyncMock(return_value={'success': True, 'window_id': 'win-9'})
    ts.desktop_call = AsyncMock(side_effect=[
        {'success': False, 'error': "window 'win-9' (Graph) is not accepting calls"},
        {'success': True, 'result': 'fit'}])
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())
    reply = await ts.agent_app_call('graph-tools', 'fit')
    assert reply['success'] and reply['window_id'] == 'win-9' and ts.desktop_call.await_count == 2
    ts.desktop_open.assert_awaited_once_with(app='graph-tools')
    placement.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_desktop_an_action_says_so(monkeypatch):
    ts = desktop(monkeypatch, Placement({'graph-tools': TOOL_APP}))
    ts.desktop_open = AsyncMock(return_value={'success': False, 'error': 'no desktop to ask — no viewport'})
    reply = await ts.agent_app_call('graph-tools', 'fit')
    assert not reply['success'] and 'no desktop' in reply['error']


# ---- the Agent's side ---------------------------------------------------------

class FakeDesktop:
    toolset_name = 'desktop'

    def __init__(self, apps):
        self.apps, self.calls, self.fail = apps, [], False

    async def list_tools(self):
        return []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == 'agent_app_directory':
            if self.fail:
                raise RuntimeError('desktop is starting')
            return {'success': True, 'apps': [a for a in self.apps if a['app_id'] not in args['exclude']]}
        return {'success': True, 'result': f"{args['app_id']}.{args['name']}"}


async def core_agent(desktop):
    from pantheon.agent import Agent
    from pantheon.agent_app_directory import AppDirectory
    agent = Agent(name='leader', instructions='Test', model='gpt-4o-mini')
    agent.enable_core_tools()
    agent.app_directory = AppDirectory(desktop, exclude=['fleet'])
    return agent


@pytest.mark.asyncio
async def test_installed_apps_join_call_app_tool_and_route_through_the_desktop():
    from pantheon.agent_app_directory import provider_name
    assert provider_name('game-of-life') == 'game_of_life' and provider_name('--') is None
    desk = FakeDesktop([offer(TOOL_APP), offer({**TOOL_APP, 'id': 'fleet'})])
    agent = await core_agent(desk)
    index = await agent._app_tool_index()
    assert index['graph_tools'] == ['centrality', 'focusNode', 'fit'] and 'fleet' not in index
    tools = await agent.get_tools_for_llm()
    listing = next(t for t in tools if t['function']['name'] == 'call_app_tool')['function']['description']
    assert '- graph_tools (Network analysis.): centrality, focusNode, fit' in listing
    described = await agent._base_functions['describe_app']('graph_tools')
    assert described['tools'][0]['name'] == 'centrality'
    assert await agent.call_tool('graph_tools__focusNode', {'id': 'TP53'}) == {
        'success': True, 'result': 'graph-tools.focusNode'}
    name, args = desk.calls[-1]
    assert name == 'agent_app_call' and args['app_id'] == 'graph-tools' and len(args['holder']) == 16
    listed = [c for c in desk.calls if c[0] == 'agent_app_directory']
    await agent._app_tool_index()
    assert len([c for c in desk.calls if c[0] == 'agent_app_directory']) == len(listed) == 1


@pytest.mark.asyncio
async def test_a_desktop_that_is_not_up_is_asked_again_later_or_when_an_app_is_named():
    desk = FakeDesktop([offer(TOOL_APP)])
    desk.fail = True
    agent = await core_agent(desk)
    assert 'graph_tools' not in await agent._app_tool_index()
    await agent._app_tool_index()
    assert len(desk.calls) == 1  # not on every model call
    desk.fail = False
    reply = await agent._base_functions['call_app_tool']('graph_tools', 'fit', {})
    assert reply == {'success': True, 'result': 'graph-tools.fit'}
    assert 'graph_tools' in await agent._app_tool_index()
