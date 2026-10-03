import asyncio
import json
import threading

import pytest

from pantheon.chatroom.event_store import AgentEventStore
from pantheon.chatroom.stream import NATSStreamAdapter


def messages(pages):
    assembled, result = {}, []
    for page in pages:
        for fragment in page['events']:
            parts = assembled.setdefault(fragment['event_id'], [])
            assert fragment['part'] == len(parts)
            parts.append(fragment['json_fragment'])
            if len(parts) == fragment['total']:
                result.append(json.loads(''.join(parts)))
    return result


@pytest.mark.asyncio
async def test_restart_fragment_replay_chat_isolation_and_gateway_size(tmp_path):
    root = tmp_path/'events'
    store = AgentEventStore(root)
    huge = '🧬 中文 " \\ ' * 80000
    await store.publish('a', 'step', {'type': 'step_message', 'content': huge})
    await store.publish('b', 'chunk', {'content': 'private other chat'})
    pages, cursor = [], None
    while True:
        page = await store.read('a', cursor, limit=3)
        assert not page['reset_required']
        assert len(json.dumps({'success': True, 'result': page}).encode()) < 512*1024
        pages.append(page)
        cursor = page['cursor']
        await store.close()
        store = AgentEventStore(root)  # Also resume in the middle of a large event.
        if not page['has_more']:
            break
    assert messages(pages)[0]['data']['content'] == huge
    assert all('private other chat' not in json.dumps(p) for p in pages)
    assert (await store.read('a', cursor))['events'] == []
    await store.publish_chat_finished('a')
    assert messages([await store.read('a', cursor)])[0]['type'] == 'chat_finished'
    await store.close()


@pytest.mark.asyncio
async def test_retention_never_returns_partial_old_event_and_reports_gap(tmp_path):
    store = AgentEventStore(tmp_path/'events', retain_rows=3, retain_bytes=20000)
    await store.publish('a', 'step', {'content': 'x'*45000})
    first = await store.read('a', limit=1)
    assert first['has_more']
    await store.publish_chat_finished('a')
    reset = await store.read('a', first['cursor'])
    assert reset['reset_required'] and not reset['events']
    assert not (await store.read('a', reset['cursor']))['reset_required']
    wrong_epoch = {**reset['cursor'], 'epoch': 'different-store'}
    assert (await store.read('a', wrong_epoch))['reset_required']
    ahead = {**reset['cursor'], 'sequence': reset['cursor']['sequence']+1}
    assert (await store.read('a', ahead))['reset_required']
    assert store.rows == 1
    await store.close()


@pytest.mark.asyncio
async def test_close_waits_for_cancelled_accepted_disk_work(tmp_path):
    store = AgentEventStore(tmp_path/'events')
    started, release = threading.Event(), threading.Event()
    def work():
        started.set()
        assert release.wait(5)
        (tmp_path/'completed').write_text('done')
    operation = asyncio.create_task(store._call(work))
    await asyncio.to_thread(started.wait, 5)
    operation.cancel()
    closing = asyncio.create_task(store.close())
    await asyncio.sleep(.02)
    assert not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    await closing
    assert (tmp_path/'completed').read_text() == 'done'
    with pytest.raises(RuntimeError, match='closed'):
        await store.publish_chat_finished('a')


@pytest.mark.asyncio
async def test_same_chunk_and_tool_event_shapes_as_legacy_stream(tmp_path, monkeypatch):
    store = AgentEventStore(tmp_path/'events')
    legacy = NATSStreamAdapter()
    seen = []
    async def capture(chat, kind, data):
        seen.append((kind, data))
    monkeypatch.setattr(legacy, 'publish', capture)
    for adapter in (legacy, store):
        chunk, step = adapter.create_hooks('a')
        await chunk({'begin': True})
        await chunk({'tool_calls': [{'function': {'name': 'shell_run', 'arguments': '{'}}]})
        await chunk({'content': 'reply'})
        await step({'role': 'assistant', 'content': 'reply'})
        await adapter.publish_chat_finished('a')
    result = messages([await store.read('a')])
    assert [(item['type'], {k: v for k, v in item['data'].items() if k != 'chat_id'})
            for item in result] == seen
    await store.close()


@pytest.mark.parametrize('cursor', [{'epoch':'a','sequence':True}, {'sequence':1}, {'epoch':'a','sequence':-1}])
@pytest.mark.asyncio
async def test_bad_cursors_are_rejected(tmp_path, cursor):
    store = AgentEventStore(tmp_path/'events')
    try:
        with pytest.raises(ValueError, match='cursor'):
            await store.read('a', cursor)
    finally:
        await store.close()


def test_event_journal_rejects_symlink(tmp_path):
    root = tmp_path/'events'
    root.mkdir(mode=0o700)
    (root/'events.sqlite3').symlink_to(tmp_path/'foreign')
    with pytest.raises(ValueError, match='private'):
        AgentEventStore(root)
