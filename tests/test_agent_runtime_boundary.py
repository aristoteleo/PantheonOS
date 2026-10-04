"""Domain API ownership and explicit composition, alongside real host tests."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.chatroom.environment import AgentEnvironment
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.factory.provisioned_instances import ProvisionedAgentInstanceFactory


def test_core_preserves_agent_rpc_surface_without_platform_management():
    inventory = json.loads((Path(__file__).resolve().parents[1] /
                            'docs/agent-app-rpc-inventory.json').read_text())
    for row in inventory['methods']:
        method = getattr(AgentRuntime, row['method'], None)
        assert bool(getattr(method, '_is_tool', False)) == (row['owner'] == 'agent'), row['method']


def test_core_cannot_silently_construct_a_global_environment():
    with pytest.raises(TypeError, match='environment'):
        AgentRuntime()


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic', [False, True])
async def test_preflight_uses_effective_per_member_dependencies_and_excludes_local_tools(automatic):
    configs = {
        'first': {'name': 'First', 'icon': 'test', 'model': 'openai/gpt-4o-mini', 'instructions': 'Use tools',
                  'toolsets': ['shell', 'think', 'task', 'skills', 'mcp:docs'], 'mcp_servers': ['mcp']},
        'second': {'name': 'Second', 'icon': 'test', 'model': 'openai/gpt-4o-mini', 'instructions': 'Use docs',
                   'toolsets': ['mcp:docs'], 'mcp_servers': []},
    }
    original = deepcopy(configs)
    factory = ProvisionedAgentInstanceFactory.__new__(ProvisionedAgentInstanceFactory)
    factory._defaults = {'toolsets': [], 'mcp_servers': ['mcp'] if automatic else [],
                         'mcp_unified_precedence': True}
    app = AgentRuntime.__new__(AgentRuntime)
    app.template_manager = SimpleNamespace(prepare_team=lambda _: (configs, {'shell', 'mcp:docs'}, {'mcp'}))
    app._environment = SimpleNamespace(prepare_agent_configs=factory.prepare_configs)
    app._ensure_services = AsyncMock()
    # Stop at the allocation boundary: no database, model or remote service is
    # necessary to verify the exact requests preflight sends before allocation.
    app._create_agents = AsyncMock(side_effect=RuntimeError('allocation boundary'))
    with pytest.raises(RuntimeError, match='allocation boundary'):
        await app._create_team_from_template(SimpleNamespace(name='Test'), chat_id='conversation')
    calls = app._ensure_services.await_args_list
    assert [(call.args[0], set(call.args[1])) for call in calls] == [
        ('mcp', {'mcp'} if automatic else {'mcp', 'docs'}), ('toolset', {'shell'})]
    effective = app._create_agents.await_args.args[0]
    assert effective == factory.prepare_configs(configs)
    assert effective['first']['toolsets'] == ['shell', 'think', 'task', 'skills']
    assert configs == original


@pytest.mark.asyncio
async def test_two_environments_keep_workspaces_and_dependencies_separate(tmp_path):
    apps, calls = [], []

    def compose(label):
        root = tmp_path / label
        root.mkdir()
        settings = SimpleNamespace(workspace=root, pantheon_dir=root / '.pantheon')
        project = SimpleNamespace(name=label, path=str(root))
        projects = SimpleNamespace(active_project=project, default_project=project,
            list_projects=lambda: [{'name': label, 'path': str(root)}],
            get_project=lambda path: project if path == str(root) else None)

        async def ensure(kind, names):
            calls.append((label, kind, names))
            raise PermissionError('binding is unavailable')

        async def agents(configs, *, conversation_id=None):
            calls.append((label, 'agents', configs, conversation_id))
            return []

        environment = AgentEnvironment(projects=projects, templates=object(),
            settings=lambda: settings, ensure_services=ensure, create_agents=agents,
            validate_model=lambda model: (False, label + ': unavailable ' + model))
        app = AgentRuntime(name=label, memory_dir=str(root / '.pantheon' / 'memory'),
                           environment=environment)
        apps.append(app)
        return app

    first, second = compose('one'), compose('two')
    try:
        chats = await asyncio.gather(*(app.create_chat(label, workspace_mode='isolated')
                                       for label, app in zip(('one', 'two'), apps)))
        for label, app, chat in zip(('one', 'two'), apps, chats):
            assert chat['success']
            workspace = Path(chat['workspace_path'])
            assert workspace.is_relative_to(tmp_path / label)
            assert workspace.is_dir()
            listing = await app.list_chats()
            assert [row['id'] for row in listing['chats']] == [chat['chat_id']]
            # Errors remain visible; no fallback owner resolver or retry.
            with pytest.raises(PermissionError, match='binding is unavailable'):
                await app._ensure_services('toolset', ['shell'])
            assert await app._create_agents({'fixture': {}}, conversation_id=chat['chat_id']) == []
            assert app._validate_model_provider('a-model') == (False, label + ': unavailable a-model')
        assert calls == [('one', 'toolset', ['shell']), ('one', 'agents', {'fixture': {}}, chats[0]['chat_id']),
                         ('two', 'toolset', ['shell']), ('two', 'agents', {'fixture': {}}, chats[1]['chat_id'])]
        await first.cleanup()
        # Closing one core does not invalidate the other one's conversation.
        assert (await second.list_chats())['chats'][0]['id'] == chats[1]['chat_id']
    finally:
        await asyncio.gather(*(app.cleanup() for app in apps))


def test_activity_is_agent_owned_and_counts_shared_default_team_once():
    app = AgentRuntime.__new__(AgentRuntime)
    manager = SimpleNamespace(list_tasks=lambda: [SimpleNamespace(status='running'),
                                                  SimpleNamespace(status='completed')])
    agent = SimpleNamespace(_bg_manager=manager)
    team = SimpleNamespace(agents={'first': agent, 'alias': agent})
    app._default_team = team
    app.chat_teams = {'a': team, 'b': team}
    app.threads = {'a': object()}
    assert app._get_activity_status() == {'active_threads': 1, 'bg_tasks': 1,
                                         'has_active_tasks': True, 'activity_scope': 'agent'}
