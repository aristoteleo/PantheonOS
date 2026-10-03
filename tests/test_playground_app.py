"""The Playground App owns requests/media without an Agent execution host."""
import asyncio
import ast
import base64
import inspect
import json
import os
from pathlib import Path
import textwrap
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from pantheon.apps.builtin.llm_playground import engine, media
from pantheon.apps.builtin.llm_playground.service import PlaygroundToolSet
from pantheon.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


def test_app_registry_and_legacy_rpc_signatures(tmp_path):
    from pantheon.apphost import _construct_kwargs, _resolve_backend
    from pantheon.apps.registry import reflected_tools, verify_interfaces
    from pantheon.apps.reflect import signature_diff
    cls, requires, app = _resolve_backend('llm-playground')
    assert cls is PlaygroundToolSet
    assert not signature_diff(app.manifest.provides.tools, reflected_tools(app.manifest))
    assert not verify_interfaces(app.manifest)
    service = cls(**_construct_kwargs('llm-playground', requires, str(tmp_path)))
    assert service.workdir == tmp_path
    baseline = json.loads((ROOT / 'docs/agent-app-rpc-inventory.json').read_text())
    for row in baseline['methods']:
        if row['method'].startswith('llm_playground_'):
            method = service.functions[row['method']][0]
            node = ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
            assert ast.unparse(node.args) == row['signature']


def test_legacy_imports_are_aliases():
    from pantheon.chatroom import llm_playground, llm_playground_media
    assert llm_playground is engine
    assert llm_playground_media is media


def test_scoped_routes_do_not_read_another_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    for name in ('OPENAI_API_KEY', 'OPENAI_API_BASE', 'PANTHEON_PLATFORM_PROXY_BASE',
                 'PANTHEON_PLATFORM_PROXY_KEY', 'PLATFORM_MODEL_MODE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('pantheon.utils.oauth.CodexOAuthManager.is_authenticated', lambda _: False)
    monkeypatch.setattr('pantheon.utils.oauth.GeminiCliOAuthManager.is_authenticated', lambda _: False)
    settings = []
    for project in ('one', 'two'):
        path = tmp_path / project
        path.mkdir()
        (path / '.env').write_text(
            f'OPENAI_API_KEY={project}-own\nOPENAI_API_BASE=https://{project}.example/v1\n'
            f'PANTHEON_PLATFORM_PROXY_BASE=https://budget-{project}.example/v1\n'
            f'PANTHEON_PLATFORM_PROXY_KEY={project}-budget\n')
        settings.append(Settings(path, isolated_env=True))
    # Later host changes must not affect either already captured environment.
    monkeypatch.setenv('PANTHEON_PLATFORM_PROXY_KEY', 'different-host-budget')
    for project, snapshot in zip(('one', 'two'), settings):
        routes = engine.routes(snapshot)
        assert routes['openai'].key == f'{project}-own'
        assert routes['openai'].base == f'https://{project}.example/v1'
        assert routes['platform'].key == f'{project}-budget'
        assert routes['platform'].base == f'https://budget-{project}.example/v1'
        assert f'{project}-budget' not in json.dumps(routes['platform'].public())
    assert 'OPENAI_API_KEY' not in os.environ
    assert os.environ['PANTHEON_PLATFORM_PROXY_KEY'] == 'different-host-budget'


@pytest.mark.asyncio
async def test_media_and_cancellation_are_app_instance_local(tmp_path):
    one, two = PlaygroundToolSet(workdir=tmp_path), PlaygroundToolSet(workdir=tmp_path)
    try:
        asset = await one.llm_playground_upload(base64.b64encode(b'audio').decode(), 'audio.wav')
        assert base64.b64decode((await one.llm_playground_media(asset['id']))['data']) == b'audio'
        with pytest.raises(ValueError, match='expired'):
            await two.llm_playground_media(asset['id'])
        await one.llm_playground_cancel('cancel-only-one')
        assert 'cancel-only-one' in one._llm_playground.recent
        assert 'cancel-only-one' not in two._llm_playground.recent
        directory = Path(one._llm_playground.media.directory.name)
        await one.cleanup()
        assert not directory.exists()
        with pytest.raises(RuntimeError, match='stopping'):
            await one.llm_playground_upload('', 'audio.wav')
    finally:
        await one.cleanup()
        await two.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', [False, True])
async def test_slow_settings_can_be_cancelled_or_stopped_without_late_inference(tmp_path, monkeypatch, stop):
    service = PlaygroundToolSet(workdir=tmp_path)
    started, finish = threading.Event(), threading.Event()
    route = engine.Route('openai', 'Own', 'BYOK', 'openai', 'https://api.invalid', 'test-key', True)
    def snapshot():
        started.set()
        assert finish.wait(5)
        return None, {'openai': route}
    monkeypatch.setattr(service, '_playground_snapshot', snapshot)
    complete = AsyncMock()
    monkeypatch.setattr(engine.Playground, '_complete', complete)
    task = asyncio.create_task(service.llm_playground_run('pending-snapshot', 'openai', 'openai/model', 'hi'))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # Readiness/status is responsive while configuration I/O is blocked.
        await asyncio.wait_for(service.llm_playground_status('pending-snapshot'), .2)
        assert service._playground_activity()['active_requests'] == 1
        if stop:
            await asyncio.wait_for(service.cleanup(), .5)
            assert task.cancelled()
        else:
            await service.llm_playground_cancel('pending-snapshot')
        finish.set()
        if not stop:
            with pytest.raises(ValueError, match='cancelled'):
                await task
        complete.assert_not_called()
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await service.cleanup()


@pytest.mark.asyncio
async def test_shutdown_cancels_provider_before_removing_media(tmp_path, monkeypatch):
    service = PlaygroundToolSet(workdir=tmp_path)
    route = engine.Route('openai', 'Own', 'BYOK', 'openai', 'https://api.invalid', 'test-key', True)
    monkeypatch.setattr(service, '_playground_snapshot', lambda: (None, {'openai': route}))
    started, finished = asyncio.Event(), asyncio.Event()
    async def complete(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert Path(service._llm_playground.media.directory.name).exists()
            finished.set()
    monkeypatch.setattr(service._playground(), '_complete', complete)
    task = asyncio.create_task(service.llm_playground_run('stop-provider-01', 'openai', 'openai/model', 'hi'))
    await asyncio.wait_for(started.wait(), 2)
    await service.cleanup()
    assert finished.is_set()
    assert (await task)['cancelled']
    assert not service._playground_calls and not service._llm_playground.tasks
    assert not Path(service._llm_playground.media.directory.name).exists()


@pytest.mark.asyncio
async def test_explicit_source_snapshot_pins_one_call_without_mutation(monkeypatch):
    route = engine.Route('openai', 'Own', 'BYOK', 'openai', 'https://api.invalid', 'initial', True)
    runner = engine.Playground()
    async def complete(selected, *args):
        assert selected is not route
        assert selected.key == 'initial'
        selected.key = 'refreshed'
        return {'success': True}
    monkeypatch.setattr(runner, '_complete', complete)
    monkeypatch.setattr(engine, 'routes', lambda: pytest.fail('used global credentials'))
    try:
        assert (await runner.run('snapshot-request', 'openai', 'openai/model', 'hi',
                                 source_routes={'openai': route}))['success']
        assert route.key == 'initial'
    finally:
        await runner.aclose()
    with pytest.raises(RuntimeError, match='stopping'):
        await runner.run('closed-request', 'openai', 'openai/model', 'hi')


@pytest.mark.asyncio
async def test_fleet_client_is_owned_by_app_not_shared_with_another_app(tmp_path, monkeypatch):
    clients = []
    def new_client(**kwargs):
        client = MagicMock()
        client.complete = AsyncMock(return_value={'content': 'fleet answer', 'route': {'id': 'fleet:mac'}})
        client.aclose = AsyncMock()
        clients.append(client)
        return client
    monkeypatch.setattr('pantheon.models.client.ModelServices', new_client)
    monkeypatch.setattr('pantheon.models.client.get_client', lambda: pytest.fail('borrowed global client'))
    one, two = PlaygroundToolSet(workdir=tmp_path), PlaygroundToolSet(workdir=tmp_path)
    try:
        for service in (one, two):
            monkeypatch.setattr(service, '_playground_snapshot', lambda: pytest.fail('read unrelated BYOK settings'))
            result = await service.llm_playground_run('fleet-owned-request', 'fleet:mac',
                                                      'fleet-model://mac/example', 'hello')
            assert result['success'] and result['output'] == 'fleet answer'
        assert len(clients) == 2
        await one.cleanup()
        clients[0].aclose.assert_awaited_once()
        clients[1].aclose.assert_not_called()
        assert not two._llm_playground.closed
    finally:
        await one.cleanup()
        await two.cleanup()
    for client in clients:
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_cleanup_waiter_does_not_abandon_release(tmp_path, monkeypatch):
    service = PlaygroundToolSet(workdir=tmp_path)
    runner = service._playground()
    entered, finish = asyncio.Event(), asyncio.Event()
    close = runner.aclose
    async def slow_close():
        entered.set()
        await finish.wait()
        await close()
    monkeypatch.setattr(runner, 'aclose', slow_close)
    waiter = asyncio.create_task(service.cleanup())
    await entered.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(RuntimeError, match='stopping'):
        await service.llm_playground_status('new-request')
    finish.set()
    await asyncio.wait_for(service.cleanup(), 1)
    assert not Path(runner.media.directory.name).exists()
