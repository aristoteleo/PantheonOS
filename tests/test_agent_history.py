import hashlib
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from pantheon.chatroom.event_store import AgentEventStore, MAX_SNAPSHOTS
from pantheon.chatroom.native import NativeAgentApplication
from pantheon.chatroom.runtime import AgentRuntime


@pytest.mark.asyncio
async def test_slow_history_serialization_does_not_block_live_events(tmp_path, monkeypatch):
    store = AgentEventStore(tmp_path / 'events')
    started, release = threading.Event(), threading.Event()
    encode = json.JSONEncoder.iterencode
    def slow(self, obj, *args, **kwargs):
        if isinstance(obj, dict) and 'messages' in obj:
            started.set()
            assert release.wait(5)
        yield from encode(self, obj, *args, **kwargs)
    monkeypatch.setattr(json.JSONEncoder, 'iterencode', slow)
    save = asyncio.create_task(store.save_history('chat', [], await store.position()))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        await asyncio.wait_for(store.publish('chat', 'chunk', {'chunk': {'message_id': 'live', 'content': 'still streaming'}}), 1)
        assert 'still streaming' in (await store.read('chat'))['events'][0]['json_fragment']
    finally:
        release.set()
        await save
        await store.close()


@pytest.mark.asyncio
async def test_complete_history_survives_restart_and_does_not_move_with_live_events(tmp_path):
    root = tmp_path / 'events'
    store = AgentEventStore(root)
    content = '🧬 中文 " \\ ' * 90000
    source = [{'id': 'm1', 'role': 'assistant', 'content': content,
               'raw_content': {'stdout': content, 'base64_uri': content}}]
    await store.publish('chat', 'step', {'content': 'before snapshot'})
    descriptor = await store.save_history('chat', source, await store.position())
    source[0]['content'] = 'edited after snapshot'
    await store.publish('chat', 'step', {'content': 'during download'})
    chunks = []
    for part in range(descriptor['parts']):
        await store.close()
        store = AgentEventStore(root)
        page = await store.read_history('chat', descriptor['snapshot_id'], part)
        assert page == await store.read_history('chat', descriptor['snapshot_id'], part)
        assert len(json.dumps({'success': True, 'result': page}).encode()) < 512 * 1024
        chunks.append(page['json_fragment'])
    raw = ''.join(chunks).encode('ascii')
    assert len(raw) == descriptor['size']
    assert hashlib.sha256(raw).hexdigest() == descriptor['sha256']
    history = json.loads(raw)
    assert history['messages'][0]['content'] == content
    assert history['messages'][0]['raw_content']['stdout'] == content
    events = await store.read('chat', descriptor['cursor'])
    assert not events['reset_required']
    assert 'during download' in events['events'][0]['json_fragment']
    with pytest.raises(ValueError, match='unavailable'):
        await store.read_history('other-chat', descriptor['snapshot_id'], 0)
    await store.release_history('other-chat', descriptor['snapshot_id'])
    await store.read_history('chat', descriptor['snapshot_id'], 0)
    await store.release_history('chat', descriptor['snapshot_id'])
    await store.release_history('chat', descriptor['snapshot_id'])
    with pytest.raises(ValueError, match='unavailable'):
        await store.read_history('chat', descriptor['snapshot_id'], 0)
    await store.close()


@pytest.mark.asyncio
async def test_snapshot_keeps_active_stream_prefix_after_replay_eviction_and_restart(tmp_path):
    root = tmp_path / 'events'
    store = AgentEventStore(root, retain_rows=1)
    for content in ('first ', 'second ', 'third'):
        await store.publish('chat', 'chunk', {'type': 'chunk', 'chunk': {'message_id': 'active', 'content': content}})
    cursor, active = await store.history_checkpoint('chat')
    assert ''.join(event['data']['chunk']['content'] for event in active) == 'first second third'
    assert (await store.read('chat'))['reset_required']
    snapshot = await store.save_history('chat', [], cursor, active)
    loaded = json.loads((await store.read_history('chat', snapshot['snapshot_id'], 0))['json_fragment'])
    assert loaded['inflight'] == active
    await store.close()
    store = AgentEventStore(root)
    assert (await store.history_checkpoint('chat'))[1] == active
    await store.recover_interrupted_streams()
    assert (await store.history_checkpoint('chat'))[1] == []
    finished = json.loads((await store.read('chat', cursor))['events'][0]['json_fragment'])
    assert finished['data']['status'] == 'interrupted'
    await store.publish('chat', 'chunk', {'chunk': {'message_id': 'next', 'content': 'fresh'}})
    await store.publish('chat', 'step', {'step_message': {'id': 'next'}})
    assert (await store.history_checkpoint('chat'))[1] == []
    await store.close()


@pytest.mark.asyncio
async def test_expiry_capacity_and_failed_serialization_leave_no_partial_history(tmp_path, monkeypatch):
    store = AgentEventStore(tmp_path / 'events')
    cursor = await store.position()
    with pytest.raises(TypeError):
        await store.save_history('chat', [{'content': 'x'*300000, 'invalid': object()}], cursor)
    assert store.history_db.execute('SELECT COUNT(*) FROM history_parts').fetchone()[0] == 0
    snapshots = [await store.save_history('chat', [], cursor) for _ in range(MAX_SNAPSHOTS)]
    with pytest.raises(RuntimeError, match='Too many'):
        await store.save_history('chat', [], cursor)
    first = snapshots[0]
    for part in (-1, True, first['parts']):
        with pytest.raises(ValueError, match='part'):
            await store.read_history('chat', first['snapshot_id'], part)
    monkeypatch.setattr('pantheon.chatroom.event_store.time.time', lambda: snapshots[-1]['expires_at']+1)
    with pytest.raises(ValueError, match='expired'):
        await store.read_history('chat', first['snapshot_id'], 0)
    assert store.history_db.execute('SELECT COUNT(*) FROM history_parts').fetchone()[0] == 0
    await store.save_history('chat', [], cursor)
    await store.close()


@pytest.mark.asyncio
async def test_native_copy_and_legacy_presentation_do_not_truncate_authoritative_memory(tmp_path):
    raw = {'stdout': 's'*20000, 'base64_uri': 'keep image'}
    message = {'content': 'c'*220000, 'raw_content': raw}
    memory = SimpleNamespace(get_messages=lambda *args: [message])
    store = AgentEventStore(tmp_path / 'events')
    owner = SimpleNamespace(memory_manager=SimpleNamespace(get_memory=lambda _: memory),
                            _nats_adapter=store)
    shown = await AgentRuntime._get_sanitized_messages(owner, 'chat', True)
    assert len(shown[0]['content']) < len(message['content'])
    assert 'base64_uri' not in shown[0]['raw_content']
    assert len(raw['stdout']) == 20000 and raw['base64_uri'] == 'keep image'
    # A published event during the memory read must be after the descriptor's
    # cursor, even though it may overlap with the copied message identities.
    async def load(_):
        await store.publish('chat', 'step', {'id': 'late', 'content': 'overlap'})
        return memory
    owner.memory_manager.get_memory = load
    snapshot = await NativeAgentApplication.open_agent_history(owner, 'chat')
    page = await store.read('chat', snapshot['cursor'])
    assert 'overlap' in page['events'][0]['json_fragment']
    parts = [await store.read_history('chat', snapshot['snapshot_id'], i) for i in range(snapshot['parts'])]
    assert json.loads(''.join(p['json_fragment'] for p in parts))['messages'] == [message]
    await store.close()
