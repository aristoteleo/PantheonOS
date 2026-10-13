"""Revert, edit-and-resend, branch switching and fork on the conversation tree."""
from types import SimpleNamespace

import pytest

from pantheon.chatroom.room import ChatRoom
from pantheon.internal.memory import MemoryManager


def _room(tmp_path):
    room = ChatRoom.__new__(ChatRoom)
    room.chat_teams = {}
    room.threads = {}
    room.project_manager = SimpleNamespace(list_projects=lambda: [])
    room.memory_manager = MemoryManager(tmp_path, use_jsonl=True)
    room._current_chat_id = None
    return room


def _chat(room):
    memory = room.memory_manager.new_memory("Chat")
    memory.add_messages([
        {"id": "u1", "role": "user", "content": "first question"},
        {"id": "a1", "role": "assistant", "content": "first answer"},
        {"id": "u2", "role": "user", "content": "second question"},
        {"id": "a2", "role": "assistant", "content": "second answer"},
    ])
    room.memory_manager.save_one(memory.id)
    return memory


def _contents(reply):
    return [m["content"] for m in reply["messages"]]


@pytest.mark.asyncio
async def test_revert_and_resend_keeps_both_versions_and_switches_between_them(tmp_path):
    room = _room(tmp_path)
    memory = _chat(room)

    reverted = await room.revert_to_message(memory.id, "u2")
    assert reverted["success"] and reverted["reverted_message"]["content"] == "second question"
    assert _contents(await room.get_chat_messages(memory.id)) == ["first question", "first answer"]

    # The edited question and its answer form a second branch.
    memory.add_messages([{"id": "u2b", "role": "user", "content": "second question, edited"},
                         {"id": "a2b", "role": "assistant", "content": "another answer"}])
    room.memory_manager.save_one(memory.id)
    reply = await room.get_chat_messages(memory.id)
    assert _contents(reply)[-2:] == ["second question, edited", "another answer"]
    edited = next(m for m in reply["messages"] if m["id"] == "u2b")
    assert edited["_branch"] == {"index": 1, "count": 2, "siblings": ["u2", "u2b"]}

    assert (await room.switch_branch(memory.id, "u2"))["success"]
    reply = await room.get_chat_messages(memory.id)
    assert _contents(reply) == ["first question", "first answer", "second question", "second answer"]
    assert next(m for m in reply["messages"] if m["id"] == "u2")["_branch"]["index"] == 0

    # Survives a restart: the branch choice is in the stored log.
    reloaded = _room(tmp_path)
    assert _contents(await reloaded.get_chat_messages(memory.id))[-1] == "second answer"
    assert not (await reloaded.switch_branch(memory.id, "nope"))["success"]


@pytest.mark.asyncio
async def test_fork_copies_the_conversation_up_to_a_message_and_records_its_source(tmp_path):
    room = _room(tmp_path)
    memory = _chat(room)
    forked = await room.fork_chat(memory.id, "a1")
    assert forked["success"] and not forked["is_user_node"]
    copy = room.memory_manager.get_memory(forked["chat_id"])
    assert [m["content"] for m in copy._messages] == ["first question", "first answer"]
    assert copy.extra_data["forked_from"] == {"chat_id": memory.id, "message_id": "a1"}
    # The source is unchanged.
    assert len(room.memory_manager.get_memory(memory.id)._messages) == 4
