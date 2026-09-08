"""UI/project/agent callers must share the browser and native registry."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pantheon.apps.resolver import AppInstanceResolver


def resolver(monkeypatch, root='/workspace'):
    r = AppInstanceResolver('fleet', 'node', 'same-user', root)
    client = SimpleNamespace(ping=AsyncMock(return_value=True), start=AsyncMock(return_value={'ok': True}))
    r._ensure_client = AsyncMock(return_value=client)
    r._ensure_coords = AsyncMock()
    r._place = AsyncMock(return_value='node')
    return r, client


@pytest.mark.asyncio
async def test_desktop_calls_from_agent_and_two_ui_projects_share_one_process(monkeypatch):
    monkeypatch.setenv('PANTHEON_BROWSER_PROFILE_RECOVERY', 'exclusive-modal-sandbox-v1')
    r, client = resolver(monkeypatch)
    agent = await r.ensure_instance('desktop')
    ui = await r.ensure_instance('desktop', scope=r.project_scope('/workspace/project-a'), workdir='/workspace/project-a')
    other_ui = await r.ensure_instance('desktop', scope=r.project_scope('/workspace/project-b'), workdir='/workspace/project-b')
    assert agent == ui == other_ui
    client.start.assert_awaited_once()
    spec = client.start.call_args.args[1]
    assert spec['scope'] == 'app' and spec['dir'] == '/workspace'
    assert spec['env']['PANTHEON_BROWSER_PROFILE_RECOVERY'] == 'exclusive-modal-sandbox-v1'


@pytest.mark.asyncio
async def test_separate_resolvers_choose_the_same_desktop_service(monkeypatch):
    a, _ = resolver(monkeypatch, '/workspace')
    b, _ = resolver(monkeypatch, '/workspace/project')
    assert await a.ensure_instance('desktop') == await b.ensure_instance('desktop', scope='project')


@pytest.mark.asyncio
async def test_non_desktop_services_still_have_project_isolation(monkeypatch):
    r, client = resolver(monkeypatch)
    first = await r.ensure_instance('file_manager', scope='one', workdir='/workspace/one')
    second = await r.ensure_instance('file_manager', scope='two', workdir='/workspace/two')
    assert first != second and client.start.await_count == 2
    assert client.start.call_args_list[0].args[1]['dir'] == '/workspace/one'
    assert client.start.call_args_list[1].args[1]['dir'] == '/workspace/two'
