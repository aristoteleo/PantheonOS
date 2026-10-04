"""Observe a real legacy gateway, build an App and preserve Agent tool calls."""
import asyncio
from copy import deepcopy
import json

from fastmcp import Client, FastMCP
import pytest

from pantheon.apps.builtin.mcp import MCPGatewayToolSet
from pantheon.apps.builtin.mcp.manager import MCPManager, MCPServerConfig, MCPServerInstance
from pantheon.apps.builtin.mcp.scoped import ScopedMCP, legacy_result
from pantheon.apps.dependency_client import DependencyClient
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.migration_mcp_tools import capture_mcp_tools, compile_mcp_tools
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory import create_agent
from pantheon.factory.bindings import AgentToolBindings
from pantheon.platform.mcp_package import build_migration_package
from pantheon.providers import MCPProvider
from test_scoped_mcp_app import config


async def gateway_fixture(tmp_path):
    servers = {}
    for name in ('docs', 'docs_more'):
        server = FastMCP(name)

        @server.tool
        def echo(text: str) -> dict:
            """Echo text and nested JSON."""
            return {'text': text, 'nested': '[1, 2, 3]'}

        @server.tool
        def fail() -> str:
            """Fail without repeating a mutation."""
            raise RuntimeError('private upstream failure detail')

        servers[name] = server
    manager = MCPManager(log_dir=str(tmp_path/'logs'))
    for name, server in servers.items():
        instance = MCPServerInstance(MCPServerConfig(name=name, type='http', uri='http://127.0.0.1:1/mcp'))
        instance.status = 'running'
        manager.instances[name] = instance
        await manager._gateway.mount_server(name, server)
    return manager, servers


@pytest.mark.asyncio
async def test_observed_gateway_to_versioned_app_to_agent_preserves_names_and_results(tmp_path):
    manager, servers = await gateway_fixture(tmp_path)
    gateway = object.__new__(MCPGatewayToolSet)
    gateway._manager = manager
    captured = await gateway.export_migration_tools(['mcp', 'docs', 'docs_more'])
    assert captured['success']
    contract = captured['contract']
    assert set(contract['exports']) == {'docs_echo', 'docs_fail', 'docs_more_echo', 'docs_more_fail'}
    # This deliberately preserves the old startswith filtering: docs also
    # includes docs_more, rather than silently narrowing the old provider.
    assert len(contract['providers']['docs']) == 4
    assert len(contract['providers']['docs_more']) == 2
    assert '127.0.0.1' not in json.dumps(captured)
    package = build_migration_package(tmp_path/'package', 'darwin-arm64', contract=contract)
    assert json.loads((package/'backend/exports.json').read_text()) == contract['exports']
    assert json.loads((package/'app.json').read_text())['version'] == '0.8.0'
    assert json.loads((package/'migration-tools.json').read_text()) == contract
    host = ScopedMCP(contract['exports'], config({name: {'transport': 'http',
        'url': 'http://127.0.0.1:1/'+name} for name in servers}),
        client_factory=lambda spec: Client(servers[spec['url'].rsplit('/', 1)[-1]]))
    await host.start()
    loop = asyncio.get_running_loop()

    class Link(DependencyClient):
        def invoke(self, name, args=None, *, timeout_seconds=60):
            return {'success': True, 'result': asyncio.run_coroutine_threadsafe(
                host.call(name, args), loop).result(timeout_seconds)}

    created = []
    try:
        for name, functions in contract['providers'].items():
            old = MCPProvider('in-process-fixture', filter_prefix=None if name == 'mcp' else name)
            old._client = Client(manager._gateway._unified_mcp)
            old_tools = await old.list_tools()
            old_agent = await create_agent(name='Original', icon='test', instructions='Use bound tools',
                model='openai/gpt-4o-mini', mcp_servers=[], toolsets=[], tool_bindings=AgentToolBindings())
            await old_agent.mcp(name, old)
            provider = DependencyToolProvider(name, Link(RuntimeCredential('https://localhost/rpc', 'a'*64)), functions)
            created.append(provider)
            agent = await create_agent(name='Migrated', icon='test', instructions='Use bound tools', model='openai/gpt-4o-mini',
                mcp_servers=[name], toolsets=[], tool_bindings=AgentToolBindings({}, {name: provider}))
            menu = await agent.get_tools_for_llm()
            old_menu = await old_agent.get_tools_for_llm()
            assert {t['function']['name'] for t in menu} == {t['function']['name'] for t in old_menu}
            assert all(t['function']['strict'] is False for t in menu if t['function']['name'].startswith(name+'__'))
            for tool in old_tools:
                if tool.name.endswith('_echo'):
                    before = await old_agent.call_tool(name+'__'+tool.name, {'text': 'hello'}, {})
                    after = await agent.call_tool(name+'__'+tool.name, {'text': 'hello'}, {})
                    assert before == after == {'text': 'hello', 'nested': [1, 2, 3]}
            with pytest.raises(Exception):
                await old.call_tool(next(t.name for t in old_tools if t.name.endswith('_fail')), {})
        with pytest.raises(RuntimeError, match='outcome may be unknown') as error:
            await host.call('docs_fail', {})
        assert 'private upstream' not in str(error.value)
    finally:
        for provider in created:
            await provider.shutdown()
        await host.close()


def tool(name='echo', parameters=None):
    return {'name': name, 'description': 'fixture', 'parameters': parameters or {
        'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}}


@pytest.mark.parametrize('problem', ['collision', 'duplicate', 'schema', 'unknown', 'empty', 'reserved', 'overflow'])
def test_invalid_or_changed_catalog_is_not_partially_converted(problem):
    gateway = [tool('docs_echo')]
    servers = {'docs': {'prefix': 'docs', 'tools': [tool()]}}
    if problem == 'collision':
        servers['other'] = deepcopy(servers['docs'])
    elif problem == 'duplicate':
        gateway.append(tool('docs_echo'))
    elif problem == 'schema':
        gateway[0]['parameters']['properties']['text']['type'] = 'integer'
    elif problem == 'unknown':
        gateway[0]['name'] = 'unknown_echo'
    elif problem == 'empty':
        gateway = []
    elif problem == 'reserved':
        for row in (gateway[0], servers['docs']['tools'][0]):
            row['parameters']['properties']['context_variables'] = {'type': 'object'}
    else:
        gateway = [tool('docs_echo'+str(i)) for i in range(65)]
    with pytest.raises(ValueError):
        compile_mcp_tools(gateway, servers, providers=['mcp'])


@pytest.mark.asyncio
async def test_capture_requires_legacy_lifecycle_lock_and_healthy_mount(tmp_path):
    manager, _ = await gateway_fixture(tmp_path)
    with pytest.raises(ValueError, match='locked'):
        await capture_mcp_tools(manager, providers=['mcp'])
    manager.instances['docs'].status = 'stopped'
    async with manager._lock:
        with pytest.raises(ValueError, match='consistent'):
            await capture_mcp_tools(manager, providers=['mcp'])


@pytest.mark.parametrize('value', [{'nested': '{"value": 2}'}, ['[1,2]', '[]', 'plain'], False, 0, '', '[1,2]'])
def test_legacy_structured_result_keeps_original_unwrap_semantics(value):
    from types import SimpleNamespace
    from pantheon.utils.misc import unwrap_single_layer
    result = SimpleNamespace(is_error=False, structured_content=value)
    assert legacy_result(result, {}) == unwrap_single_layer(value)


@pytest.mark.parametrize('text', ['plain text', '{"nested":"[1,2]"}', 'null', 'false', '0', '[]'])
def test_legacy_text_results(text):
    from types import SimpleNamespace
    from pantheon.utils.misc import unwrap_single_layer
    result = SimpleNamespace(is_error=False, structured_content=None, content=[SimpleNamespace(text=text)])
    try:
        expected = unwrap_single_layer(json.loads(text))
    except ValueError:
        expected = text
    assert legacy_result(result, {}) == expected


@pytest.mark.parametrize('problem', ['missing', 'renamed', 'schema', 'format', 'protocol', 'unused'])
def test_release_rejects_changed_provider_views_before_writing(tmp_path, problem):
    contract = compile_mcp_tools([tool('docs_echo')],
        {'docs': {'prefix': 'docs', 'tools': [tool()]}}, providers=['docs'])
    if problem == 'missing':
        contract['providers']['docs'] = []
    elif problem == 'renamed':
        contract['providers']['docs'][0]['name'] = 'echo'
    elif problem == 'schema':
        contract['providers']['docs'][0]['parameters']['properties']['text']['type'] = 'integer'
    elif problem == 'format':
        contract['exports']['docs_echo'].pop('result_format')
    elif problem == 'protocol':
        contract['protocol'] = True
    else:
        contract['exports']['other_echo'] = deepcopy(contract['exports']['docs_echo'])
    with pytest.raises(ValueError):
        build_migration_package(tmp_path/'invalid', 'darwin-arm64', contract=contract)
    assert not (tmp_path/'invalid').exists()


def test_cli_builds_captured_catalog_as_an_ordinary_versioned_artifact(tmp_path, monkeypatch):
    import sys
    from pantheon.platform.mcp_package import main
    from pantheon.apps.lifecycle import build_artifact
    contract = compile_mcp_tools([tool('docs_echo')],
        {'docs': {'prefix': 'docs', 'tools': [tool()]}}, providers=['mcp', 'docs'])
    path = tmp_path/'catalog.json'
    path.write_text(json.dumps(contract))
    output = tmp_path/'release'
    monkeypatch.setattr(sys, 'argv', ['mcp-package', '--legacy-catalog', str(path),
        '--output', str(output), '--platform', 'linux-amd64', '--credential-slot', 'docs-key'])
    main()
    assert json.loads((output/'migration-tools.json').read_text()) == contract
    assert json.loads((output/'backend/exports.json').read_text()) == contract['exports']
    assert build_artifact(output, 'linux-amd64')[0]
