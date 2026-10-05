"""Terminal client consumes the same bounded native protocol as the Agent GUI."""
import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from pantheon.agent_client import AgentAppClient, AgentClientError, run_once, select_resume
from pantheon.chatroom.event_store import AgentEventStore


def test_frontend_import_does_not_import_runtime_or_repl():
    command = '''
import sys
from pantheon.agent_client import AgentAppClient
assert not any(k == 'pantheon.agent' or k.startswith(('pantheon.chatroom', 'pantheon.repl', 'pantheon.team')) for k in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', [None, 'digest', 'size', 'parts', 'identity', 'limit'])
async def test_history_reassembles_fragments_and_releases_even_when_invalid(tmp_path, damage):
    store = AgentEventStore(tmp_path/'events')
    messages = [{'role': 'assistant', 'content': '汉字🙂' * 50000}]
    snapshot = await store.save_history('chat', messages, await store.position(), running=False)
    calls = []
    async def invoke(method, args, timeout):
        calls.append((method, args))
        if method == 'open_agent_history':
            value = dict(snapshot)
            if damage == 'digest': value['sha256'] = '0'*64
            if damage == 'size': value['size'] += 1
            return value
        if method == 'read_agent_history':
            value = await store.read_history(**args)
            if damage == 'parts': value['part'] += 1
            if damage == 'identity': value['chat_id'] = 'other'
            return value
        return await store.release_history(**args)
    try:
        client = AgentAppClient(invoke)
        if damage is None:
            assert (await client.history('chat'))['messages'] == messages
            assert len([c for c in calls if c[0] == 'read_agent_history']) > 1
        else:
            with pytest.raises(AgentClientError):
                await client.history('chat', max_bytes=1 if damage == 'limit' else 64*1024*1024)
        assert calls[-1][0] == 'release_agent_history'
        assert store.history_db.execute('SELECT count(*) FROM histories').fetchone()[0] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_mutation_timeout_is_not_replayed_or_exposed():
    invoke = AsyncMock(side_effect=TimeoutError('secret-key'))
    with pytest.raises(AgentClientError, match='outcome is unknown') as error:
        await AgentAppClient(invoke).send('chat', 'hello')
    invoke.assert_awaited_once()
    assert 'secret-key' not in str(error.value)


@pytest.mark.asyncio
async def test_negotiation_rejects_other_protocol_before_mutations():
    invoke = AsyncMock(return_value={'protocol': 1, 'history_protocol': 1, 'event_protocol': False})
    with pytest.raises(AgentClientError, match='protocol'):
        await run_once(AgentAppClient(invoke), 'hello')
    assert [c.args[0] for c in invoke.await_args_list] == ['get_agent_app_info']


def test_resume_uses_app_owned_recency_index_and_identity():
    rows = [{'id': 'older', 'name': 'Old', 'last_activity_date': '2025-01-01T00:00:00Z'},
            {'id': 'newer', 'name': 'New session', 'last_activity_date': '2026-01-01T00:00:00Z'}]
    for choice in (True, '1', 'new', 'New session'):
        assert select_resume(rows, choice) == 'newer'
    assert select_resume(rows, '2') == 'older'
    with pytest.raises(AgentClientError): select_resume([], True)


@pytest.mark.asyncio
@pytest.mark.parametrize('busy', [False, True])
async def test_one_shot_refuses_busy_conversations_and_preserves_model_selection(busy):
    client = AsyncMock()
    client.conversation.return_value = 'chat'
    client.is_running.return_value = busy
    client.call.return_value = {'agents': [{'name': 'Researcher'}]}
    client.send.return_value = {'success': True, 'chat_id': 'chat', 'response': 'reply'}
    if busy:
        with pytest.raises(AgentClientError, match='running'):
            await run_once(client, 'hello', resume=True, model='fleet-model://local/x')
        client.send.assert_not_called()
        client.call.assert_not_called()
    else:
        assert await run_once(client, 'hello', resume=True, model='fleet-model://local/x') == {'chat_id': 'chat', 'response': 'reply'}
        client.call.assert_any_await('set_agent_model', chat_id='chat', agent_name='Researcher', model='fleet-model://local/x')
    client.history.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_client_requests_backend_stop_without_resending():
    client = AsyncMock()
    client.conversation.return_value = 'chat'
    client.is_running.return_value = False
    entered, stopped = asyncio.Event(), asyncio.Event()
    async def send(*args):
        entered.set()
        await asyncio.Event().wait()
    async def stop(chat):
        assert chat == 'chat'
        await asyncio.sleep(.01)
        stopped.set()
        return {'success': True}
    client.send.side_effect, client.stop.side_effect = send, stop
    task = asyncio.create_task(run_once(client, 'hello'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert stopped.is_set()
    client.send.assert_awaited_once()
    client.stop.assert_awaited_once_with('chat')


@pytest.mark.asyncio
@pytest.mark.parametrize('row', [{'id': 'chat', 'running': False}, {'id': 'chat', 'running': True},
                                 {'id': 'chat'}, {'id': 'other', 'running': False}])
async def test_live_status_is_metadata_only_and_missing_status_does_not_authorize_send(row):
    invoke = AsyncMock(return_value={'success': True, 'chats': [row]})
    client = AgentAppClient(invoke)
    if row.get('id') == 'chat' and 'running' in row:
        assert await client.is_running('chat') is row['running']
    else:
        with pytest.raises(AgentClientError): await client.is_running('chat')
    assert [call.args[0] for call in invoke.await_args_list] == ['list_chats']
