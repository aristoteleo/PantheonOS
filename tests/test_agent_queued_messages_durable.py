"""Messages queued behind a running turn outlive the Agent's process (combined check 7).

A follow-up sent while the Agent answers is queued behind the turn. It used to live in memory only, and a shutdown
dropped it: a pod going away in its last seconds lost the message without a trace. The queue is now kept in the
conversation's metadata, a shutdown keeps it, and the next start runs it (ids the history has are skipped).
"""
import asyncio
from types import SimpleNamespace

import pytest

from pantheon.chatroom.lifecycle import AgentLifetime
from pantheon.chatroom.runtime import AgentRuntime
from pantheon.chatroom.thread import PENDING_MESSAGES, Thread
from pantheon.internal.memory import MemoryManager


def _manager(tmp_path):
    return MemoryManager(tmp_path / "memory", use_jsonl=True)


def _reload(tmp_path, chat_id):
    return _manager(tmp_path).get_memory(chat_id)


def test_the_conversation_queue_is_kept_in_its_metadata_as_it_changes(tmp_path):
    manager = _manager(tmp_path)
    memory = manager.new_memory()
    thread = Thread(team=None, memory=memory, message=[])
    thread.inject_user_messages([{"id": "q1", "role": "user", "content": "one"}])
    thread.inject_user_messages([{"id": "q2", "role": "user", "content": "two"}])
    assert [m["id"] for m in memory.extra_data[PENDING_MESSAGES]] == ["q1", "q2"]
    thread.steer_queue.remove("q1")
    assert [m["id"] for m in memory.extra_data[PENDING_MESSAGES]] == ["q2"]
    thread.steer_queue.drain()
    assert PENDING_MESSAGES not in memory.extra_data


@pytest.mark.asyncio
async def test_a_queued_message_is_on_disk_once_flushed(tmp_path):
    manager = _manager(tmp_path)
    memory = manager.new_memory()
    thread = Thread(team=None, memory=memory, message=[])
    thread.inject_user_messages([{"id": "q1", "role": "user", "content": "follow-up"}])
    await memory.flush(strict=True)
    kept = _reload(tmp_path, memory.id).extra_data.get(PENDING_MESSAGES)
    assert [m["content"] for m in kept] == ["follow-up"]


class _Runtime(SimpleNamespace):
    def _run_recovered(self, *args):
        return AgentRuntime._run_recovered(self, *args)

    def _track_background(self, task):
        self.tasks.append(task)
        return task


@pytest.mark.asyncio
async def test_the_next_start_runs_the_queued_messages_the_history_lacks_and_lists_them_until_then(tmp_path):
    manager = _manager(tmp_path)
    memory = manager.new_memory()
    memory.add_messages([{"id": "a", "role": "user", "content": "already ran"}])
    memory.set_metadata(PENDING_MESSAGES, [{"id": "a", "role": "user", "content": "already ran"},
                                           {"id": "b", "role": "user", "content": "queued, not run"}])
    await memory.flush(strict=True)

    fresh = _manager(tmp_path)
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def chat(chat_id, message):
        calls.append((chat_id, [m["id"] for m in message]))
        # The run takes its input into the history.
        fresh.get_memory(chat_id).add_messages(message)
        started.set()
        await finish.wait()
        return {"success": True}

    runtime = _Runtime(memory_manager=fresh, threads={}, _pending_recovery={}, chat=chat, tasks=[])
    await AgentRuntime._recover_pending_messages(runtime)
    # Listed as queued before anyone is answered: the page waits, it does not offer a resend.
    pending = await AgentRuntime.get_pending_messages(runtime, memory.id)
    assert [m["id"] for m in pending["messages"]] == ["b"]
    assert [m["id"] for m in runtime._pending_recovery[memory.id]] == ["b"]
    await asyncio.wait_for(started.wait(), 5)
    assert calls == [(memory.id, ["b"])]
    finish.set()
    await asyncio.gather(*runtime.tasks)
    assert runtime._pending_recovery == {}
    assert PENDING_MESSAGES not in fresh.get_memory(memory.id).extra_data


@pytest.mark.asyncio
async def test_nothing_runs_twice_when_the_history_has_every_queued_id(tmp_path):
    manager = _manager(tmp_path)
    memory = manager.new_memory()
    memory.add_messages([{"id": "a", "role": "user", "content": "ran"}])
    memory.set_metadata(PENDING_MESSAGES, [{"id": "a", "role": "user", "content": "ran"}])
    await memory.flush(strict=True)
    fresh = _manager(tmp_path)

    async def chat(**_):  # pragma: no cover - must not run
        raise AssertionError("ran twice")

    runtime = _Runtime(memory_manager=fresh, threads={}, _pending_recovery={}, chat=chat, tasks=[])
    await AgentRuntime._recover_pending_messages(runtime)
    assert runtime.tasks == [] and runtime._pending_recovery == {}
    assert PENDING_MESSAGES not in fresh.get_memory(memory.id).extra_data


@pytest.mark.asyncio
async def test_a_continuation_the_shutdown_overtakes_keeps_its_messages_for_the_next_start(tmp_path):
    manager = _manager(tmp_path)
    memory = manager.new_memory()
    thread = Thread(team=None, memory=memory, message=[])
    thread._done.set()
    lifetime = AgentLifetime()
    lifetime._agent_stopping = True
    lifetime.threads = {}
    ran = []

    async def chat(**kw):  # pragma: no cover - must not run while stopping
        ran.append(kw)

    lifetime.chat = chat
    lifetime._continue_accepted_chat(thread, memory.id, [{"id": "c", "role": "user", "content": "left over"}])
    await asyncio.gather(*lifetime._agent_continuations)
    assert ran == []
    assert [m["id"] for m in _reload(tmp_path, memory.id).extra_data[PENDING_MESSAGES]] == ["c"]
