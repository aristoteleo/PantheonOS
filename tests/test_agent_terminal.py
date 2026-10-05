import io
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from pantheon.agent_client import AgentAppClient
from pantheon.agent_terminal import run_interactive, TerminalRenderer
from pantheon.chatroom.event_store import AgentEventStore
from test_agent_replay_client import Backend


def test_terminal_import_has_no_runtime_or_legacy_repl():
    result = subprocess.run([sys.executable, '-c', '''
import sys
from pantheon.agent_terminal import run_interactive
assert not any(k == 'pantheon.agent' or k.startswith(('pantheon.chatroom', 'pantheon.repl', 'pantheon.team')) for k in sys.modules)
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_commands_and_turns_share_the_bound_app_without_local_runtime(tmp_path):
    store = AgentEventStore(tmp_path/'events'); backend = Backend(store)
    mutations = []
    async def invoke(method, args, timeout):
        if method == 'list_available_models': return {'models': ['fleet-model://local/x']}
        if method == 'get_agents': return {'agents': [{'name': 'Researcher'}]}
        if method in ('set_agent_model', 'set_active_agent'):
            mutations.append((method, args)); return {'success': True}
        return await backend.invoke(method, args, timeout)
    lines = iter(['/help', '/chats', '/models', '/model fleet-model://local/x', '/agents', '/agent Researcher',
                  'first', 'second', '/history', '/unknown must not be submitted', '/quit'])
    async def read(): return next(lines)
    output = io.StringIO()
    try:
        await run_interactive(AgentAppClient(invoke), chat_id='a', read_line=read, console=Console(file=output, width=120))
        assert backend.calls.count('chat') == 2
        assert mutations == [('set_agent_model', {'chat_id': 'a', 'agent_name': 'Researcher', 'model': 'fleet-model://local/x'}),
                             ('set_active_agent', {'chat_id': 'a', 'agent_name': 'Researcher'})]
        assert 'Unknown or incomplete command' in output.getvalue()
        assert output.getvalue().count('hello') == 3  # Two streamed turns and /history.
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_idle_eof_does_not_submit_or_stop_someone_elses_run():
    client = AsyncMock()
    client.negotiate.return_value = {'event_cursor_protocol': 1}
    client.conversation.return_value = 'a'
    async def read(): raise EOFError
    await run_interactive(client, chat_id='a', read_line=read, console=Console(file=io.StringIO()))
    client.send.assert_not_called()
    client.stop.assert_not_called()


@pytest.mark.asyncio
async def test_reset_is_explicit_and_renderer_does_not_read_tool_result_paths(tmp_path):
    path = tmp_path/'must-not-open'; path.write_text('PRIVATE FILE')
    output = io.StringIO(); renderer = TerminalRenderer(Console(file=output, width=120))
    message = {'id': 'm', 'role': 'tool', 'content': json.dumps({'image_path': str(path)})}
    await renderer.reset({'messages': [message], 'inflight': []})
    assert 'Stream gap' in output.getvalue() and 'PRIVATE FILE' not in output.getvalue()


@pytest.mark.asyncio
async def test_redirected_file_input_is_bounded_and_does_not_close_stdin(tmp_path, monkeypatch):
    from pantheon.agent_client import AgentClientError
    from pantheon.agent_terminal import TerminalInput
    path = tmp_path/'input'
    path.write_bytes('你好\n/quit\n'.encode())
    with path.open('rb') as source:
        monkeypatch.setattr(sys, 'stdin', source)
        async with TerminalInput() as terminal:
            assert await terminal.read() == '你好\n'
            assert await terminal.read() == '/quit\n'
            with pytest.raises(EOFError): await terminal.read()
        assert not source.closed
    path.write_bytes(b'x' * (128 * 1024 + 1))
    with path.open('rb') as source:
        monkeypatch.setattr(sys, 'stdin', source)
        async with TerminalInput() as terminal:
            with pytest.raises(AgentClientError, match='line limit'): await terminal.read()
