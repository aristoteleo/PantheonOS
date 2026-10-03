"""Owned App composition with actual templates, Agents, stores and wire calls.

The dependency/model servers supply deterministic granted endpoints. This is
not a packaged Desktop test or a live Fleet rollout.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.chatroom.app_data import AgentAppData, AppProjects
from pantheon.chatroom.application import AgentApplication
from pantheon.chatroom.environment import AgentEnvironment
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.settings import Settings
from pantheon.utils.model_scope import ModelCallScope
from test_agent_dependency_bindings import endpoint, forbid_ambient_tools
from test_agent_model_scope import endpoint as model_endpoint
from test_agent_instance_factory import RECIPE
from test_provisioned_agent_instances import Provisioner


PLUGIN_KEYS = ('task_system', 'think_system', 'fleet_system', 'model_services_system',
               'memory_system', 'learning_system', 'compression')
TEMPLATE = {'id': 'test', 'name': 'Test', 'description': 'App composition test',
            'agents': [{'id': 'member', **RECIPE}]}


def project_view(workspace, *, name='Shared', identity='project-1'):
    return AppProjects([{'id': identity, 'name': name, 'path': str(workspace)}],
                       active_id=identity, default_id=identity)


def scoped_settings(root, environment=None, *, enable=()):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    configuration = root / 'configuration' / '.pantheon'
    configuration.mkdir(parents=True, exist_ok=True)
    (configuration / 'settings.json').write_text(json.dumps({
        **{key: {'enabled': key in enable} for key in PLUGIN_KEYS},
        'default_template_auto_update': False,
    }))
    return Settings(root / 'configuration', user_home=root / 'user',
                    isolated_env=True, environment=environment or {})


def application(root, workspace, provisioner, *, model_url=None, name='Shared',
                close_dependencies=None, enable=()):
    settings = scoped_settings(root, {'OPENAI_API_KEY': 'app-fixture',
        'OPENAI_API_BASE': model_url + '/byok/v1'} if model_url else {}, enable=enable)
    return AgentApplication('agent', data_dir=root, namespace='agent-data',
        projects=project_view(workspace, name=name), settings=settings,
        model_scope=ModelCallScope(settings), provisioner=provisioner,
        ensure_services=AsyncMock(), validate_model=lambda _: (True, ''),
        close_dependencies=close_dependencies)


@pytest.mark.asyncio
async def test_versions_share_workspace_but_not_conversations_and_reopen_by_project_id(
        tmp_path, endpoint, model_endpoint, forbid_ambient_tools, monkeypatch):
    workspace = tmp_path / 'workspace'
    legacy = workspace / '.pantheon' / 'memory'
    legacy.mkdir(parents=True)
    marker = legacy / 'existing-user-data'
    marker.write_bytes(b'leave existing CLI/Desktop data alone')
    # A scoped application must not import the old composition's credentials.
    def forbidden(*a, **k):
        raise AssertionError('Agent App used ambient settings')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    monkeypatch.setenv('OPENAI_API_KEY', 'ambient-secret')
    monkeypatch.setenv('LLM_FORCE_PROXY', 'true')
    p, q = Provisioner(endpoint), Provisioner(endpoint)
    roots = [tmp_path / 'stable', tmp_path / 'candidate']
    apps = [application(root, workspace, provisioner, model_url=model_endpoint.url)
            for root, provisioner in zip(roots, (p, q))]
    identities, chats = [], []
    try:
        for app in apps:
            await app.run_setup()
            created = await app.create_chat('App chat', project_name='Shared', template_obj=TEMPLATE)
            assert created['success'], created
            chat = created['chat_id']
            chats.append(chat)
            assert await app._project_dir_for_chat(chat) == str(workspace)
            info = await app.get_agents(chat)
            assert info['success'], info
            identities.append(info['agents'][0]['instance']['instance_id'])
            agent = app.chat_teams[chat].team_agents[0]
            assert (await agent.call_tool('shell__execute', {'command': 'pwd'}))['session'] == 'session-a'
            response = await agent.run('Reply once')
            assert response.content == 'scoped reply'
            for project_name in (None, 'Shared'):
                rows = (await app.list_chats(project_name=project_name))['chats']
                assert [row['id'] for row in rows] == [chat]
        assert identities[0] != identities[1]
        assert len(model_endpoint.requests) == 2
        assert all(headers['Authorization'] == 'Bearer app-fixture'
                   for _, headers, _ in model_endpoint.requests)
        assert list(legacy.iterdir()) == [marker]
        # Same mount must reject another writer before a chat or Agent exists.
        with pytest.raises(TimeoutError):
            application(roots[0], workspace, p)
    finally:
        await asyncio.gather(*(app.cleanup() for app in apps))
    # A project rename/move keeps conversations because its ID is stable.
    moved = tmp_path / 'renamed-workspace'
    moved.mkdir()
    restored = application(roots[0], moved, p, name='Renamed')
    try:
        await restored.run_setup()
        assert [c['id'] for c in (await restored.list_chats('Renamed'))['chats']] == [chats[0]]
        assert await restored._project_dir_for_chat(chats[0]) == str(moved)
        info = await restored.get_agents(chats[0])
        assert info['success'], info
        assert info['agents'][0]['instance']['instance_id'] == identities[0]
        assert p.calls[0] == p.calls[1]  # Reuse the durable allocation operation.
        assert (await restored.list_chats('Shared'))['chats']  # Legacy name metadata remains queryable.
    finally:
        await restored.cleanup()


@pytest.mark.asyncio
async def test_failed_enabled_plugin_setup_releases_writer_after_cleanup(tmp_path, endpoint):
    workspace = tmp_path / 'workspace'
    closed = AsyncMock()
    app = application(tmp_path / 'app', workspace, Provisioner(endpoint),
                      close_dependencies=closed, enable=('memory_system',))
    with pytest.raises(RuntimeError, match='memory'):
        await app.run_setup()
    # Cleanup propagates the failed setup but still closes owned resources.
    with pytest.raises(RuntimeError):
        await app.cleanup()
    closed.assert_awaited_once()
    next_app = application(tmp_path / 'app', workspace, Provisioner(endpoint))
    await next_app.run_setup()
    await next_app.cleanup()


@pytest.mark.asyncio
async def test_shutdown_keeps_mount_locked_until_accepted_tool_finishes(tmp_path, endpoint):
    root, workspace = tmp_path / 'app', tmp_path / 'workspace'
    closed = AsyncMock()
    app = application(root, workspace, Provisioner(endpoint), close_dependencies=closed)
    try:
        await app.run_setup()
        created = await app.create_chat('Run', template_obj=TEMPLATE)
        assert created['success'], created
        chat = created['chat_id']
        assert (await app.get_agents(chat))['success']
        agent = app.chat_teams[chat].team_agents[0]
        endpoint.hold = True
        call = asyncio.create_task(agent.call_tool('shell__execute', {'command': 'write'}))
        assert await asyncio.to_thread(endpoint.entered.wait, 2)
        stopping = asyncio.create_task(app.cleanup())
        await asyncio.sleep(.02)
        assert not stopping.done()
        closed.assert_not_awaited()
        with pytest.raises(TimeoutError):
            AgentAppData(root, namespace='agent-data', projects=project_view(workspace))
        endpoint.release.set()
        await call
        await stopping
        closed.assert_awaited_once()
        AgentAppData(root, namespace='agent-data', projects=project_view(workspace)).close()
    finally:
        endpoint.release.set()
        await app.cleanup()


def test_project_snapshot_validation_and_untrusted_names_do_not_choose_paths(tmp_path):
    workspace = tmp_path / 'workspace'
    projects = project_view(workspace, identity='../../outside')
    data = AgentAppData(tmp_path / 'data', namespace='one', projects=projects)
    try:
        target = Path(data.project_memory_dir(str(workspace)))
        assert target.is_relative_to(data.root / 'conversations' / 'projects')
        snapshot = projects.list_projects()
        snapshot[0]['id'] = 'mutated'
        assert data.project_memory_dir(str(workspace)) == str(target)
        with pytest.raises(ValueError, match='outside'):
            data.project_memory_dir('/unregistered')
    finally:
        data.close()
    with pytest.raises(ValueError, match='namespace'):
        AgentAppData(tmp_path / 'data', namespace='different', projects=projects)
    with pytest.raises(ValueError, match='ambiguous'):
        AppProjects(projects.list_projects() * 2)


def test_explicit_routing_failure_never_falls_back_to_project_or_home(tmp_path):
    def fail(_):
        raise ValueError('unavailable binding')
    env = AgentEnvironment(projects=project_view(tmp_path / 'workspace'), templates=object(),
        settings=lambda: None, ensure_services=AsyncMock(), create_agents=AsyncMock(),
        validate_model=lambda _: (True, ''), project_memory_dir=fail)
    with pytest.raises(ValueError, match='unavailable'):
        AgentRuntime(memory_dir=str(tmp_path / 'home'), environment=env)
    assert not (tmp_path / 'home').exists()
    assert not (tmp_path / 'workspace').exists()


@pytest.mark.asyncio
async def test_legacy_composition_keeps_project_local_memory(tmp_path):
    workspace = tmp_path / 'legacy'
    env = AgentEnvironment(projects=project_view(workspace), templates=object(),
        settings=lambda: SimpleNamespace(workspace=workspace), ensure_services=AsyncMock(),
        create_agents=AsyncMock(), validate_model=lambda _: (True, ''),
        create_plugins=AsyncMock(return_value=[]))
    app = AgentRuntime(memory_dir=str(tmp_path / 'legacy-home'), environment=env)
    try:
        created = await app.create_chat('Legacy', project_name='Shared')
        assert created['success']
        assert list((workspace / '.pantheon' / 'memory').glob(created['chat_id'] + '.*'))
        assert (await app.list_chats('Shared'))['chats'][0]['id'] == created['chat_id']
    finally:
        await app.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('jsonl', [True, False])
async def test_flush_preserves_unloaded_chats_and_cancels_old_metadata_timers(tmp_path, jsonl):
    from pantheon.internal.memory import MemoryManager
    original = MemoryManager(tmp_path, use_jsonl=jsonl)
    first = original.new_memory('first')
    untouched = original.new_memory('unloaded')
    untouched.set_metadata('project', {'name': 'Keep'})
    await original.flush()
    before = {path: Path(path).read_bytes() for path in untouched.storage_files if Path(path).exists()}
    reloaded = MemoryManager(tmp_path, use_jsonl=jsonl)
    changed = reloaded.get_memory(first.id)
    changed.set_metadata('team_template', TEMPLATE)
    timer = changed._persist_task
    await reloaded.flush()
    assert timer.done()
    assert all(Path(path).read_bytes() == content for path, content in before.items())
    reopened = MemoryManager(tmp_path, use_jsonl=jsonl)
    assert reopened.get_memory(first.id).extra_data['team_template'] == TEMPLATE
    assert reopened.get_memory(untouched.id).extra_data['project'] == {'name': 'Keep'}


@pytest.mark.asyncio
async def test_failed_metadata_flush_is_not_reported_as_clean_shutdown(tmp_path, endpoint, monkeypatch):
    closed = AsyncMock()
    app = application(tmp_path / 'app', tmp_path / 'workspace', Provisioner(endpoint),
                      close_dependencies=closed)
    await app.run_setup()
    created = await app.create_chat('Pending metadata', template_obj=TEMPLATE)
    memory = app.memory_manager.get_memory(created['chat_id'])
    timer = memory._persist_task
    def broken(_):
        raise OSError('simulated full disk')
    monkeypatch.setattr(memory._backend, 'persist', broken)
    from pantheon.apps.host_lifecycle import AppShutdownError
    with pytest.raises(AppShutdownError):
        await app.cleanup()
    assert timer.done()
    closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_scoped_resume_does_not_change_process_cwd(tmp_path):
    import os
    from pantheon.agent import Agent
    from pantheon.internal.memory import Memory
    from pantheon.internal.memory.session_storage import getSessionStorageState
    workspace = tmp_path / 'isolated-workspace'
    workspace.mkdir()
    memory = Memory('App conversation')
    memory.extra_data['project'] = {'workspace_mode': 'isolated',
                                   'workspace_path': str(workspace), 'original_cwd': os.getcwd()}
    settings = scoped_settings(tmp_path / 'app')
    agent = Agent(name='Scoped', instructions='Use the supplied workspace',
                  model='openai/gpt-4o-mini', model_scope=ModelCallScope(settings))
    before = os.getcwd()
    context = await agent._prepare_execution_context('continue', memory=memory, use_memory=True,
        context_variables={'workdir': str(workspace), 'project_root': str(workspace)})
    assert os.getcwd() == before
    assert context.context_variables['workdir'] == str(workspace)
    assert getSessionStorageState(memory)['metadata']['worktreeSession']['worktreePath'] == str(workspace)
