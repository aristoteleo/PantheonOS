"""Captured MCP configuration through the ordinary deployment/allocator path.

Node scheduling and grant issuance use deterministic fixtures. The App packages,
deployment coordinator, allocator, Agent routing and stdio MCP are real; this
does not claim a live Fleet installation or admit a legacy Agent data import.
"""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from pantheonos.general_team.agent_deployment import compose_deployment
from pantheonos.general_team.agent_defaults import with_dependency_defaults
from pantheonos.appkit.builtin.mcp.scoped import ScopedMCP
from pantheonos.appkit.dependency_assembly import AssemblyError, _methods
from pantheonos.appkit.dependency_client import DependencyClient
from pantheonos.appkit.lifecycle import build_artifact
from pantheonos.appkit.live_dependencies import LiveDependencyOwner, ScopedDependencyBindings
from pantheonos.appkit.resource_sessions import ResourceSessionOwner
from pantheonos.appkit.runtime_config import RuntimeCredential
from pantheon.chatroom.migration_mcp_configuration import MCPConfigurationConversion
from pantheon.chatroom.migration_mcp_deployment import dependency_inputs
from pantheon.chatroom.migration_mcp_tools import compile_mcp_tools
from pantheon.chatroom.package import build_package as build_agent
from pantheon.dependency_provider import DependencyToolProvider
from pantheon.factory import create_agent
from pantheon.factory.bindings import AgentToolBindings
from pantheonos.appkit.allocator_package import build_package as build_allocator
from pantheonos.models.access_package import build_package as build_models
from pantheonos.appkit.mcp_package import build_migration_package
from test_agent_deployment_recipe import inputs
from test_agent_migration import legacy
from test_agent_migration_credentials import vault, read_key
from test_agent_release import release
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


