"""Captured MCP configuration through the ordinary deployment/allocator path.

Node scheduling and grant issuance use deterministic fixtures. The App packages,
deployment coordinator, allocator, Agent routing and stdio MCP are real; this
does not claim a live Fleet installation or admit a legacy Agent data import.
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.agent_deployment import compose_deployment
from pantheon.apps.builtin.mcp.scoped import ScopedMCP
from pantheon.apps.dependency_assembly import AssemblyError, _methods
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.live_dependencies import LiveDependencyOwner, ScopedDependencyBindings
from pantheon.apps.resource_sessions import ResourceSessionOwner
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.migration_mcp_configuration import MCPConfigurationConversion
from pantheon.chatroom.migration_mcp_deployment import dependency_inputs
from pantheon.chatroom.migration_mcp_tools import compile_mcp_tools
from pantheon.chatroom.package import build_package as build_agent
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory import create_agent
from pantheon.factory.bindings import AgentToolBindings
from pantheon.platform.dependency_package import build_package as build_allocator
from pantheon.platform.model_dependency_package import build_package as build_models
from pantheon.platform.mcp_package import build_migration_package
from test_agent_deployment_recipe import inputs
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_agent_release import release
from test_app_deployment import Nodes, Authority, finish_deployment
from test_mcp_configuration_migration import captured, backup, environment
from test_mcp_tool_migration import tool
from test_scoped_mcp_app import config


def contract():
    return compile_mcp_tools([tool('docs_echo'), tool('private_echo')], {
        name: {'prefix': name, 'tools': [tool()]} for name in ('docs', 'private')},
        providers=['mcp', 'docs'])


@pytest.mark.parametrize('aliases', [{}, {'mcp': 'shared'}, {'mcp': 'shared', 'docs': 'shared'},
    {'mcp': 'shared', 'docs': 'bad alias'}, {'mcp': 'shared', 'docs': 'docs', 'extra': 'extra'}])
def test_incomplete_or_ambiguous_views_are_not_deployed(aliases):
    with pytest.raises(AssemblyError):
        dependency_inputs(contract(), name='mcp-provider', aliases=aliases)


def test_profile_and_grant_preserve_narrow_view_and_cannot_add_methods(tmp_path):
    captured_contract = contract()
    result = dependency_inputs(captured_contract, name='mcp-provider', aliases={'mcp': 'all', 'docs': 'docs'})
    package = build_migration_package(tmp_path/'mcp', 'linux-amd64', contract=captured_contract)
    manifest = json.loads((package/'app.json').read_text())
    dependency = result['dependencies']['mcp-gateway']
    for alias, methods in [('all', {'docs_echo', 'private_echo'}), ('docs', {'docs_echo'})]:
        policy = result['tools'][alias]
        assert set(_methods(dependency, manifest, policy['methods'])) == methods
        provider = 'mcp' if alias == 'all' else alias
        assert {f['name'] for f in result['profiles']['mcp_servers'][provider]['functions']} == methods
        assert all(rule['bound'] == {} and rule['arguments'] == ['text'] for rule in policy['methods'].values())
    result['profiles']['mcp_servers']['docs']['functions'][0]['description'] = 'edited'
    assert captured_contract['providers']['docs'][0]['description'] != 'edited'


@pytest.mark.asyncio
@pytest.mark.parametrize('captured', [{'MODE': 'read'}, {'MODE': 'read', 'MCP_KEY': '${ORIGINAL_MCP_KEY}'}],
                         indirect=True, ids=['literal', 'vault-secret'])
async def test_capture_deploys_one_shared_mcp_and_binds_distinct_agent_owners(legacy, captured, tmp_path, release, vault):
    source, before, script = captured
    fence, saved = backup(legacy, tmp_path)
    try:
        plan = MCPConfigurationConversion(saved['directory'], digest=saved['sha256'], fence=fence, vault=vault,
            targets={'docs': {'command': [sys.executable, str(script)], 'cwd': str(script.parent)}},
            environments=environment(source['source']) if before['key_matches'] else {
                'docs': {'literals': ['MODE'], 'credentials': {}}})
        target = {'node_id': vault.node_id, 'scope': 'migrated-tools', 'generation': 0}
        base, _ = release
        requirements = json.loads((base/'fleet.json').read_text())['requires']
        platform = requirements['os'][0] + '-' + requirements['arch'][0]
        for changes in ({'target': {**target, 'node_id': 'other-node'}}, {'name': 'agent'},
                        {'aliases': {}}, {'target': {**target, 'generation': True}}):
            arguments = dict(name='mcp-provider', target=target, aliases={'mcp': 'mcp-shared'}) | changes
            with pytest.raises(AssemblyError):
                plan.prepare_deployment(tmp_path/'rejected', platform, **arguments)
            assert not (tmp_path/'rejected').exists()
        candidate = plan.prepare_deployment(tmp_path/'mcp', platform, name='mcp-provider',
            target=target, aliases={'mcp': 'mcp-shared'})
        assert 'original-key' not in json.dumps(candidate)
        plan.provision()
        assert target == {'node_id': vault.node_id, 'scope': 'migrated-tools', 'generation': 0}
        assert candidate['artifact']['revision'] == build_artifact(tmp_path/'mcp')[1]
        agent_package = build_agent(tmp_path/'agent', platform, version='0.7.0', frontend=base/'frontend',
            transport=base/'backend/_vendor/pantheon/models/fleet-app-transport',
            dependencies=candidate['dependencies'])
        packages = {'agent': agent_package, 'allocator': build_allocator(tmp_path/'allocator', platform),
            'model-access': build_models(tmp_path/'models', platform), 'mcp-provider': tmp_path/'mcp'}
        spec = inputs(tmp_path)
        spec['provider_apps'] = candidate['provider_apps']
        spec['tools'] = candidate['tools']
        spec['agent']['dependencies']['profiles'] = candidate['profiles']
        spec['agent']['dependencies']['defaults'] = {'toolsets': [], 'mcp_servers': ['mcp']}
        nodes = Nodes()
        nodes.states[vault.node_id] = {'node_id': vault.node_id, 'owner': vault.owner,
            'dependency_config_protocol': 1, 'installations': {}, 'instances': {}, 'operations': {}}
        for name, package in packages.items():
            _, revision = build_artifact(package)
            if name in spec['targets']: spec['targets'][name]['revision'] = revision
            nodes.manifests[revision] = {'protocol': 1, 'revision': revision,
                'manifest': json.loads((package/'app.json').read_text()),
                'definition': json.loads((package/'fleet.json').read_text())}
        recipe = compose_deployment(**spec)
        result = await finish_deployment(tmp_path/'deployment', nodes, Authority(nodes), recipe['apps'])
        assert result['state'] == 'ready'
        prepared = result['prepared']
        allocator = prepared['allocator']
        policy = nodes.configurations[(allocator['node_id'], allocator['instance_id'], allocator['generation'])][
            'backend']['values']['dependency_binding']['policies']['agent']
        provider_identity = {**prepared['mcp-provider'], 'generation': prepared['mcp-provider']['generation']+1,
                             'component': 'backend', 'port': 'http'}
        assert policy['bindings']['mcp-shared']['provider'] == provider_identity
        assert len(nodes.states[vault.node_id]['instances']) == 1
        # No resource-session is allocated: the captured gateway was shared.
        nodes.resource_session = AsyncMock(side_effect=AssertionError('Shared MCP must not allocate a new process/session'))
        authority = Authority(nodes)
        issue = authority.issue
        authority.issue = AsyncMock(side_effect=issue)
        authority.revoke = AsyncMock()
        sessions = ResourceSessionOwner(nodes, tmp_path/'sessions')
        owner = LiveDependencyOwner(nodes, tmp_path/'live-bindings', sessions, authority)
        capability = ScopedDependencyBindings(owner, **policy)
        grants = [await capability.bind(owner_ref=f'agent-{i}', operation_id=f'agent-{i}-first', aliases=['mcp-shared'])
                  for i in range(2)]
        assert grants[0]['bindings']['mcp-shared']['access_token'] != grants[1]['bindings']['mcp-shared']['access_token']
        assert all(call.args[0]['provider'] == provider_identity for call in authority.issue.await_args_list)
        with pytest.raises(AssemblyError):
            await capability.bind(owner_ref='agent-0', operation_id='escape', aliases=['unapproved'])
        mcp_instance = prepared['mcp-provider']
        mcp_config = nodes.configurations[(mcp_instance['node_id'], mcp_instance['instance_id'],
            mcp_instance['generation'])]['backend']['values']['mcp']
        creds = {alias: RuntimeCredential(row['endpoint'], read_key(vault, row['ref'], row['endpoint']))
                 for alias, row in plan.describe()['credentials'].items()}
        assert all('original-key' not in json.dumps(value) for value in nodes.configurations.values())
        host = ScopedMCP(plan.describe()['contract']['exports'], config(mcp_config['servers'], creds))
        await host.start()
        loop = asyncio.get_running_loop()
        class Link(DependencyClient):
            def invoke(self, name, args=None, *, timeout_seconds=60):
                return {'success': True, 'result': asyncio.run_coroutine_threadsafe(
                    host.call(name, args), loop).result(timeout_seconds)}
        clients = []
        try:
            for i, grant in enumerate(grants):
                raw = grant['bindings']['mcp-shared']
                provider = DependencyToolProvider('mcp', Link(RuntimeCredential(raw['endpoint'], raw['access_token'])),
                    candidate['profiles']['mcp_servers']['mcp']['functions'])
                clients.append(provider)
                agent = await create_agent(name=f'Migrated {i}', icon='test', instructions='Use tools',
                    model='openai/gpt-4o-mini', toolsets=[], mcp_servers=['mcp'],
                    tool_bindings=AgentToolBindings({}, {'mcp': provider}))
                assert await agent.call_tool('mcp__docs_check', {}, {}) == {**before, 'owner_present': False}
            await capability.retire(owner_ref='agent-0')
            authority.revoke.assert_awaited_once_with(grants[0]['bindings']['mcp-shared']['grant_id'])
            assert nodes.states[vault.node_id]['instances'][mcp_instance['instance_id']]['state'] == 'ready'
            again = await capability.bind(owner_ref='agent-1', operation_id='agent-1-first', aliases=['mcp-shared'])
            assert again == grants[1]
            assert await agent.call_tool('mcp__docs_check', {}, {}) == {**before, 'owner_present': False}
            nodes.resource_session.assert_not_awaited()
        finally:
            await asyncio.gather(*(p.shutdown() for p in clients))
            await host.close()
        assert not (tmp_path/'agent-target').exists()
    finally:
        fence.close()
