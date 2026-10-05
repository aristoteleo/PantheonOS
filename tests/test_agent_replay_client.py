"""Frontend replay validation and live observation using the actual event store."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock

import pytest

from pantheon.agent_client import AgentAppClient, AgentClientError, stream_turn
from pantheon.agent_replay import ReplayState, ReplayResetRequired, decode_page
from pantheon.chatroom.event_store import AgentEventStore


@pytest.mark.asyncio
async def test_fragmented_events_restart_readers_and_do_not_mix_chats(tmp_path):
    store = AgentEventStore(tmp_path/'events')
    state = ReplayState('a', store.epoch, 0)
    content = '汉字🙂' * 40000
    try:
        await store.publish('a', 'step', {'step_message': {'id': 'message', 'role': 'assistant', 'content': content}})
        await store.publish('b', 'step', {'step_message': {'content': 'foreign conversation'}})
        observed = []
        while True:
            raw = await store.read('a', {'epoch': state.epoch, 'sequence': state.sequence}, limit=3)
            result = decode_page(state, raw)
            observed.extend(result['events'])
            state = result['state']
            if not result['has_more']: break
            assert state.pending is not None and not observed
        assert observed[0]['data']['step_message']['content'] == content
        assert 'foreign conversation' not in json.dumps(observed)
        assert state.pending is None and not decode_page(state, await store.read('a', raw['cursor']))['events']
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['epoch', 'sequence', 'part', 'identity', 'terminal', 'size', 'conversation'])
async def test_corrupt_page_does_not_consume_cursor_or_pending_fragment(tmp_path, damage):
    store = AgentEventStore(tmp_path/'events')
    try:
        await store.publish('a', 'step', {'content': 'x' * 70000})
        first = await store.read('a', limit=1)
        state = decode_page(ReplayState('a', store.epoch, 0), first)['state']
        before = deepcopy(state)
        page = await store.read('a', first['cursor'], limit=1)
        if damage == 'epoch': page['cursor']['epoch'] = 'other'
        elif damage == 'sequence': page['events'][0]['sequence'] = state.sequence
        elif damage == 'part': page['events'][0]['part'] = 0
        elif damage == 'identity': page['events'][0]['event_id'] = 'other'
        elif damage == 'terminal': page['has_more'] = False
        elif damage == 'conversation': page['chat_id'] = 'other'
        with pytest.raises(AgentClientError): decode_page(state, page, max_event_bytes=1 if damage == 'size' else 2**20)
        assert state == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_retention_reset_does_not_acknowledge_partial_event_and_history_overlap_is_filtered(tmp_path):
    store = AgentEventStore(tmp_path/'events', retain_rows=2)
    try:
        await store.publish('a', 'step', {'content': 'x'*70000})
        first = await store.read('a', limit=1)
        partial = decode_page(ReplayState('a', store.epoch, 0), first)['state']
        await store.publish_chat_finished('a')
        with pytest.raises(ReplayResetRequired): decode_page(partial, await store.read('a', first['cursor']))
        anchor = await store.position()
        state = ReplayState('a', anchor['epoch'], anchor['sequence'], completed_ids=frozenset({'complete'}))
        store.retain_rows = 100
        await store.publish('a', 'chunk', {'chunk': {'message_id': 'complete', 'content': 'already saved'}})
        await store.publish('a', 'tool_delta', {'message_id': 'complete', 'delta': 'already saved'})
        await store.publish('a', 'chunk', {'chunk': {'message_id': 'live', 'content': 'new'}})
        result = decode_page(state, await store.read('a', anchor))
        assert [e['data']['chunk']['content'] for e in result['events']] == ['new']
    finally:
        await store.close()


class Backend:
    def __init__(self, store):
        self.store, self.messages = store, []
        self.calls, self.running = [], False
        self.hold, self.entered, self.stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.block = False
        self.lose_events = False
    async def invoke(self, method, args, timeout):
        self.calls.append(method)
        if method == 'get_agent_app_info':
            return dict(protocol=1, history_protocol=1, event_protocol=1, event_cursor_protocol=1)
        if method == 'list_chats': return {'success': True, 'chats': [{'id': 'a', 'running': self.running}]}
        if method == 'get_agent_event_cursor': return {'protocol': 1, 'chat_id': 'a', 'cursor': await self.store.position()}
        if method == 'read_agent_events':
            return await self.store.read(**args)
        if method == 'open_agent_history':
            return await self.store.save_history('a', self.messages, await self.store.position(), running=self.running)
        if method == 'read_agent_history': return await self.store.read_history(**args)
        if method == 'release_agent_history': return await self.store.release_history(**args)
        if method == 'stop_chat': self.stopping.set(); self.hold.set(); return {'success': True}
        assert method == 'chat'
        self.running = True
        self.entered.set()
        if self.lose_events:
            self.store.retain_rows = 1
            for i in range(4): await self.store.publish('a', 'chunk', {'chunk': {'message_id': 'm', 'content': str(i)}})
        else:
            await self.store.publish('a', 'chunk', {'chunk': {'message_id': 'm', 'content': 'hello'}})
        if self.block: await self.hold.wait()
        self.messages = [{'id': 'm', 'role': 'assistant', 'content': 'hello'}]
        await self.store.publish('a', 'step', {'step_message': self.messages[0]})
        await self.store.publish_chat_finished('a')
        self.running = False
        return {'success': True, 'chat_id': 'a', 'response': 'hello'}


@pytest.mark.asyncio
async def test_stream_observes_before_rpc_completion_and_drains_final_events(tmp_path):
    store = AgentEventStore(tmp_path/'events'); backend = Backend(store); backend.block = True
    events, resets = [], []
    async def event(value):
        assert value['data']['chat_id'] == 'a'
        events.append(value)
        if value['type'] == 'chunk':
            assert backend.running
            backend.hold.set()
    async def reset(value): resets.append(value)
    try:
        result = await stream_turn(AgentAppClient(backend.invoke), 'a', 'go', event, on_reset=reset, interval=.001)
        assert result['response'] == 'hello' and not resets
        assert [e['type'] for e in events] == ['chunk', 'step', 'chat_finished']
        assert backend.calls.count('chat') == 1 and 'open_agent_history' not in backend.calls
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_retention_gap_restores_history_instead_of_resending_prompt(tmp_path):
    store = AgentEventStore(tmp_path/'events'); backend = Backend(store); backend.lose_events = True
    client = AgentAppClient(backend.invoke)
    read = client.read_events
    async def after_send(state):
        await backend.entered.wait()
        while backend.running: await asyncio.sleep(.001)
        return await read(state)
    client.read_events = after_send
    resets, events = [], []
    async def reset(value): resets.append(value)
    async def event(value): events.append(value)
    try:
        assert (await stream_turn(client, 'a', 'go', event, on_reset=reset, interval=.001))['response'] == 'hello'
        assert len(resets) == 1 and resets[0]['messages'] == backend.messages
        assert backend.calls.count('chat') == 1 and backend.calls.count('open_agent_history') == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_stream_requests_stop_and_joins_observer(tmp_path):
    store = AgentEventStore(tmp_path/'events'); backend = Backend(store); backend.block = True
    callback = AsyncMock()
    task = asyncio.create_task(stream_turn(AgentAppClient(backend.invoke), 'a', 'go', callback, on_reset=callback, interval=.001))
    try:
        await backend.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert backend.stopping.is_set() and backend.calls.count('chat') == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_malformed_message_identity_is_a_protocol_error(tmp_path):
    store = AgentEventStore(tmp_path/'events')
    try:
        await store.publish('a', 'chunk', {'chunk': {'message_id': [], 'content': 'bad'}})
        with pytest.raises(AgentClientError, match='message identity'):
            decode_page(ReplayState('a', store.epoch, 0), await store.read('a'))
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stop_failure_preserves_the_original_observer_cancellation(tmp_path):
    store = AgentEventStore(tmp_path/'events'); backend = Backend(store); backend.block = True
    async def invoke(method, args, timeout):
        if method == 'stop_chat':
            backend.stopping.set()
            raise OSError('lost stop response')
        return await backend.invoke(method, args, timeout)
    callback = AsyncMock()
    task = asyncio.create_task(stream_turn(AgentAppClient(invoke), 'a', 'go', callback, on_reset=callback))
    try:
        await backend.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert backend.stopping.is_set() and backend.calls.count('chat') == 1
    finally:
        backend.hold.set()
        await store.close()
