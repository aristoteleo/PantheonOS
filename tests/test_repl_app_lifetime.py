"""CLI lifecycle must admit work after readiness and drain before returning.

The real App case uses local HTTP/SSE inference and HTTPS dependency fixtures;
it is not acceptance of a shipped CLI/local Fleet composition.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.repl.core import Repl
from test_agent_application import application, TEMPLATE
from test_agent_dependency_bindings import endpoint, forbid_ambient_tools
from test_agent_model_scope import endpoint as model_endpoint
from test_provisioned_agent_instances import Provisioner


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.setattr('pantheon.repl.core.CLI_HISTORY_FILE', str(tmp_path / 'history'))
    monkeypatch.setattr(Repl, '_setup_signal_handlers', lambda self: None)
    monkeypatch.setattr(Repl, 'print_greeting', AsyncMock())
    monkeypatch.setattr(Repl, '_print_session_summary', AsyncMock())
    monkeypatch.setattr(Repl, '_init_renderers', lambda self: None)
    def make(runtime, chat_id=None):
        return Repl(chatroom=runtime, chat_id=chat_id)
    return make


def runtime():
    events = []
    async def setup(): events.append('ready')
    async def create(*args):
        assert events == ['ready']
        events.append('chat')
        return {'chat_id': 'chat'}
    async def team(*args, **kwargs):
        assert events[-1] == 'chat'
        events.append('team')
        return SimpleNamespace(agents={})
    async def cleanup(): events.append('closed')
    return SimpleNamespace(events=events, run_setup=setup, create_chat=create,
                           get_team_for_chat=team, cleanup=cleanup)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'setup', 'team', 'execute', 'cleanup'])
async def test_oneshot_waits_for_readiness_and_never_hides_failed_drain(cli, failure, monkeypatch):
    app = runtime()
    repl = cli(app)
    async def execute(_):
        assert app.events[-1] == 'team'
        app.events.append('execute')
        if failure == 'execute': raise ValueError('execute failed')
    repl._handle_message_or_command = execute
    if failure == 'setup': app.run_setup = AsyncMock(side_effect=ValueError('setup failed'))
    if failure == 'team': app.get_team_for_chat = AsyncMock(side_effect=ValueError('team failed'))
    if failure == 'cleanup': app.cleanup = AsyncMock(side_effect=ValueError('cleanup failed'))
    monkeypatch.setenv('PANTHEON_HEADLESS', 'previous')
    call = repl.run(message='hi', once=True, log_to_file=False, disable_logging=False)
    if failure:
        with pytest.raises(ValueError, match=failure + ' failed'): await call
    else:
        await call
        assert app.events == ['ready', 'chat', 'team', 'execute', 'closed']
    assert os.environ['PANTHEON_HEADLESS'] == 'previous'
    if failure != 'cleanup': assert app.events[-1] == 'closed'
    for name in ('_setup_task', '_team_task', '_warmup_task'):
        task = getattr(repl, name, None)
        assert task is None or task.done()


@pytest.mark.asyncio
async def test_repeated_interrupt_does_not_abandon_setup_or_cleanup(cli):
    app = runtime()
    entered, release, draining, drained = (asyncio.Event() for _ in range(4))
    async def setup():
        entered.set()
        await release.wait()
        app.events.append('ready')
    async def cleanup():
        assert app.events == ['ready']
        draining.set()
        await drained.wait()
        app.events.append('closed')
    app.run_setup, app.cleanup = setup, cleanup
    task = asyncio.create_task(cli(app).run(message='hi', once=True, log_to_file=False))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert not task.done() and not draining.is_set()
    release.set()
    await asyncio.wait_for(draining.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    drained.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert app.events == ['ready', 'closed']


@pytest.mark.asyncio
async def test_interactive_client_exit_also_drains_exactly_once(cli):
    app = runtime()
    repl = cli(app)
    repl._use_prompt_toolkit = False
    def eof(): raise EOFError
    repl.ask_user_input = eof
    with pytest.raises(EOFError):
        await repl.run(log_to_file=False, disable_logging=False)
    assert app.events == ['ready', 'chat', 'team', 'closed']


@pytest.mark.asyncio
async def test_real_agent_app_oneshot_reopens_conversation_and_releases_data_mount(
        cli, tmp_path, endpoint, model_endpoint, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('ambient settings used')
    monkeypatch.setattr('pantheon.settings.get_settings', forbidden)
    root, workspace = tmp_path / 'app', tmp_path / 'workspace'
    app = application(root, workspace, Provisioner(endpoint), model_url=model_endpoint.url)
    await app.run_setup()
    created = await app.create_chat('CLI', template_obj=TEMPLATE)
    chat_id = created['chat_id']
    for message in ('first CLI message', 'resume CLI message'):
        repl = cli(app, chat_id)
        # Use actual Agent/Team/model request processing and history persistence.
        await repl.run(message=message, once=True, log_to_file=False, disable_logging=False)
        # Reopening also proves the previous run drained and released its store.
        app = application(root, workspace, Provisioner(endpoint), model_url=model_endpoint.url)
    try:
        messages = app.memory_manager.get_memory(chat_id).get_messages(for_llm=False)
        text = repr(messages)
        assert 'first CLI message' in text and 'resume CLI message' in text
        assert 'scoped reply' in text
        assert len(model_endpoint.requests) == 2
        assert all(headers['Authorization'] == 'Bearer app-fixture'
                   for _, headers, _ in model_endpoint.requests)
    finally:
        await app.cleanup()


def test_importing_cli_does_not_import_platform_or_combined_service(tmp_path):
    code = '''
import importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'pantheon.chatroom.room' or fullname.startswith('pantheon.platform'):
            raise AssertionError('CLI imported platform: ' + fullname)
sys.meta_path.insert(0, Boundary())
from pantheon.repl import Repl
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
