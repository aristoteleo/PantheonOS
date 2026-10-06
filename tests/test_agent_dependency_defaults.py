"""Owner defaults reach ordinary Agent instance assembly, without global MCP.

The MCP server and Agent are real; the RPC grant/transport in this test is an
explicit in-process fixture. Deployed authorization has separate Fleet gates.
"""
import asyncio
from copy import deepcopy
import sqlite3

from fastmcp import Client
import pytest

from pantheon.apps.agent_defaults import dependency_defaults, with_dependency_defaults
from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory.bindings import AgentToolBindings
from pantheon.factory.instance_store import AgentInstanceStore
from pantheon.factory.instances import AgentInstanceBinding, _config
from pantheon.factory.provisioned_instances import ProvisionedAgentInstanceFactory
from test_agent_dependency_bindings import forbid_ambient_tools
from test_agent_instance_factory import RECIPE
from test_agent_model_scope import scopes
from test_scoped_mcp_app import mcp, contract, config


@pytest.mark.parametrize('value', [None, {}, {'mcp_servers': []},
    {'toolsets': [], 'mcp_servers': 'mcp'},
    {'toolsets': ['shell', 'shell'], 'mcp_servers': []},
    {'toolsets': ['mcp:docs'], 'mcp_servers': []},
    {'toolsets': ['mcp'], 'mcp_servers': []},
    {'toolsets': ['think'], 'mcp_servers': []},
    {'toolsets': ['docs'], 'mcp_servers': ['docs']},
    {'toolsets': [], 'mcp_servers': ['bad__name']},
    {'toolsets': [], 'mcp_servers': ['x'] * 65},
    {'toolsets': [], 'mcp_servers': [], 'endpoint': 'https://ambient.test'},
    {'toolsets': [], 'mcp_servers': [], 'mcp_unified_precedence': 1},
    {'toolsets': [], 'mcp_servers': [], 'mcp_unified_precedence': 'true'},
])
def test_reject_invalid_default_contract(value):
    with pytest.raises(ValueError):
        dependency_defaults(value)


def test_defaults_snapshot_preserves_recipe_and_mcp_spelling():
    raw = {'toolsets': ['shell'], 'mcp_servers': ['docs']}
    defaults = dependency_defaults(raw, profiles={'toolsets': {'shell': {}}, 'mcp_servers': {'docs': {}}})
    raw['mcp_servers'].append('unreviewed')
    original = _config({**RECIPE, 'toolsets': ['mcp:docs'], 'mcp_servers': []})[0]
    before = deepcopy(original)
    actual = with_dependency_defaults(original, defaults)
    assert original == before
    assert actual['toolsets'] == ['mcp:docs', 'shell'] and actual['mcp_servers'] == []
    with pytest.raises(ValueError, match='approved'):
        dependency_defaults(raw, profiles={'toolsets': {'shell': {}}, 'mcp_servers': {'docs': {}}})


@pytest.mark.parametrize('toolsets,mcps,automatic,policy,expected', [
    (['shell','mcp:docs'], ['docs'], True, True, (['shell'], ['mcp'])),
    (['mcp','mcp:docs'], ['docs'], False, True, ([], ['mcp'])),
    (['mcp:docs'], ['mcp'], False, True, ([], ['mcp'])),
    (['shell','mcp:docs'], [], False, True, (['shell','mcp:docs'], [])),
    ([], ['docs'], False, True, ([], ['docs'])),
    (['mcp:docs'], ['docs'], True, False, (['mcp:docs'], ['docs','mcp'])),
])
def test_migration_policy_preserves_legacy_unification_without_changing_normal_defaults(
        toolsets, mcps, automatic, policy, expected):
    recipe = {'toolsets':toolsets, 'mcp_servers':mcps, 'instructions':'Keep mcp:docs in this prompt'}
    original = deepcopy(recipe)
    defaults = dependency_defaults({'toolsets':[], 'mcp_servers':['mcp'] if automatic else [],
                                   'mcp_unified_precedence':policy})
    result = with_dependency_defaults(recipe, defaults)
    assert (result['toolsets'], result['mcp_servers']) == expected
    assert recipe == original and result['instructions'] == original['instructions']


@pytest.mark.asyncio
async def test_default_mcp_survives_reopen_and_changes_durable_revision_without_changing_member(
        tmp_path, mcp, scopes, forbid_ambient_tools):
    server, state = mcp
    exports = await contract(server)
    host = ScopedMCP(exports, config(), client_factory=lambda _: Client(server))
    await host.start()
    loop = asyncio.get_running_loop()
    class Link(DependencyClient):
        def invoke(self, name, args=None, *, timeout_seconds=60):
            result = asyncio.run_coroutine_threadsafe(host.call(name, args), loop).result(timeout_seconds)
            return {'success': True, 'result': result}
    functions = [{'name': name, 'description': spec['description'], 'parameters': spec['parameters']}
                 for name, spec in exports.items()]
    intents, clients = [], []
    class Provisioner:
        async def bind(self, intent):
            intents.append(intent)
            providers = {}
            for name in intent.config['mcp_servers']:
                assert name == 'mcp'
                provider = DependencyToolProvider(name, Link(RuntimeCredential('https://localhost/rpc', 'a' * 64)), functions)
                clients.append(provider)
                providers[name] = provider
            return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings({}, providers))
    recipe = {**RECIPE, 'toolsets': [], 'mcp_servers': []}
    original = deepcopy(recipe)
    defaults = {'toolsets': [], 'mcp_servers': ['mcp']}
    identities = []
    try:
        # Same inputs recover the same durable operation after restart. Removing
        # the default is a new revision of the same member, not a new session.
        for enabled in (True, True, False):
            store = AgentInstanceStore(tmp_path/'instances', namespace='app-defaults')
            selected = deepcopy(defaults) if enabled else {'toolsets': [], 'mcp_servers': []}
            factory = ProvisionedAgentInstanceFactory(store, Provisioner(), model_scope=scopes(),
                                                       default_dependencies=selected)
            selected['mcp_servers'].append('mutated-after-launch')
            try:
                agent = (await factory({'member': recipe}, conversation_id='chat'))[0]
                same = (await factory({'member': recipe}, conversation_id='chat'))[0]
                assert same is agent
                identities.append(deepcopy(agent._instance_identity))
                menu = [t['function']['name'] for t in await agent.get_tools_for_llm()]
                assert ('mcp__increment' in menu) is enabled
                assert not any('forbidden' in name for name in menu)
                if enabled:
                    result = await agent.call_tool('mcp__increment', {'amount': 3})
                    assert result['structuredContent'] == {'count': state.count}
                else:
                    assert not factory.bindings_for(agent).mcp_servers
            finally:
                await factory.shutdown()
        assert recipe == original
        assert state.count == 6
        assert identities[0] == identities[1]
        assert identities[0]['instance_id'] == identities[2]['instance_id']
        assert identities[0]['config_revision'] != identities[2]['config_revision']
        assert intents[0].operation_id == intents[1].operation_id != intents[2].operation_id
        assert len({id(c) for c in clients}) == 2
        with sqlite3.connect(tmp_path/'instances/instances.sqlite3') as db:
            assert db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0] == 2
    finally:
        await host.close()


def test_owner_preset_retains_defaults_and_rejects_unbound_profiles(tmp_path):
    from pantheon.apps.agent_deployment import compose_deployment
    from pantheon.apps.dependency_assembly import AssemblyError
    from test_agent_deployment_recipe import inputs
    from test_agent_dependency_bindings import FUNCTION
    spec = inputs(tmp_path)
    deps = spec['agent']['dependencies']
    deps['defaults'] = {'toolsets': [], 'mcp_servers': ['mcp']}
    with pytest.raises(AssemblyError, match='approved'):
        compose_deployment(**spec)
    deps['profiles']['mcp_servers']['mcp'] = {'alias': 'reviewed-mcp', 'functions': [FUNCTION]}
    # Ordinary deployment validation defers grant schemas to the allocator;
    # only the explicit selection/profile correspondence is checked here.
    spec['tools']['reviewed-mcp'] = {}
    before = deepcopy(spec)
    recipe = compose_deployment(**spec)
    assert recipe['apps']['agent']['components']['backend']['values']['agent']['dependencies'] == deps
    assert spec == before


@pytest.mark.parametrize('names', ['fleet', ['fleet', 'fleet'], ['mcp'], ['bad__name'], [None]])
def test_primary_defaults_reject_invalid_names(names):
    with pytest.raises(ValueError):
        dependency_defaults({'toolsets': [], 'mcp_servers': [], 'primary_toolsets': names})


def test_primary_defaults_require_approved_profiles():
    value = {'toolsets': [], 'mcp_servers': [], 'primary_toolsets': ['fleet']}
    with pytest.raises(ValueError, match='approved'):
        dependency_defaults(value, profiles={'toolsets': {}, 'mcp_servers': {}})
    with pytest.raises(ValueError, match='conflict'):
        dependency_defaults({**value, 'mcp_servers': ['fleet']})


@pytest.mark.asyncio
async def test_primary_management_follows_team_order_with_durable_member_identity(tmp_path, scopes, forbid_ambient_tools):
    calls = []
    functions = [{'name': 'fleet_status', 'description': 'Inspect owned Fleet',
                  'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]
    class Provisioner:
        async def bind(self, intent):
            calls.append(intent)
            tools = {name: DependencyToolProvider(name,
                DependencyClient(RuntimeCredential('https://localhost/rpc', 'a'*64)), functions)
                for name in intent.config['toolsets']}
            return AgentInstanceBinding(**intent.identity(), tools=AgentToolBindings(tools))
    recipes = {'alice': {**RECIPE, 'name': 'Alice', 'toolsets': [], 'mcp_servers': []},
               'bob': {**RECIPE, 'name': 'Bob', 'toolsets': [], 'mcp_servers': []}}
    original = deepcopy(recipes)
    snapshots = []
    for order in (('alice', 'bob'), ('alice', 'bob'), ('bob', 'alice')):
        factory = ProvisionedAgentInstanceFactory(AgentInstanceStore(tmp_path/'roles', namespace='roles'),
            Provisioner(), model_scope=scopes(), default_dependencies={
                'toolsets': [], 'mcp_servers': [], 'primary_toolsets': ['fleet']})
        try:
            # Runtime preflight and factory reservation both prepare configs;
            # applying the policy twice must preserve the same revision.
            prepared = factory.prepare_configs({key: recipes[key] for key in order})
            assert factory.prepare_configs(prepared) == prepared
            agents = await factory(prepared, conversation_id='team')
            snapshots.append({a.name: deepcopy(a._instance_identity) for a in agents})
            for index, agent in enumerate(agents):
                tools = await agent.get_tools_for_llm()
                assert ('fleet__fleet_status' in {t['function']['name'] for t in tools}) is (index == 0)
                assert bool(factory.bindings_for(agent).toolsets) is (index == 0)
        finally:
            await factory.shutdown()
    assert recipes == original
    assert snapshots[0] == snapshots[1]
    for name in ('Alice', 'Bob'):
        assert snapshots[0][name]['instance_id'] == snapshots[2][name]['instance_id']
        assert snapshots[0][name]['config_revision'] != snapshots[2][name]['config_revision']
    assert [tuple(i.config['toolsets']) for i in calls] == [('fleet',), (), ('fleet',), (), ('fleet',), ()]
