import json
import shutil
import tempfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from pantheon.chatroom.room import ChatRoom
from pantheon.internal.memory import JSONLBackend, MemoryManager


def _empty_chatroom():
    room = ChatRoom.__new__(ChatRoom)
    room.chat_teams = {}
    room.threads = {}
    room.project_manager = SimpleNamespace(list_projects=lambda: [])
    return room


@pytest.mark.asyncio
async def test_activity_dates_keep_their_instant_across_reads_and_timezones(tmp_path):
    from datetime import datetime
    manager = MemoryManager(tmp_path, use_jsonl=True)
    dates = ["2026-08-18T01:00:00+00:00", "2026-08-17T19:00:00-07:00", "2026-08-16T10:00:00", "invalid"]
    for value in dates:
        chat = manager.new_memory(value)
        chat.set_metadata("last_activity_date", value)
        manager.save_one(chat.id)
    room = _empty_chatroom()
    room.memory_manager = manager
    first = await room.list_chats()
    second = await room.list_chats()
    assert first["success"] is True
    assert first == second
    rows = first["chats"]
    assert [row["name"] for row in rows[:2]] == [dates[1], dates[0]]
    assert all(datetime.fromisoformat(row["last_activity_date"]).tzinfo for row in rows[:-1])
    assert rows[-1]["last_activity_date"] is None


@pytest.mark.asyncio
async def test_list_chats_skips_corrupted_metadata():
    temp_dir = tempfile.mkdtemp()
    try:
        memory_dir = Path(temp_dir)
        manager = MemoryManager(memory_dir, use_jsonl=True)

        valid = manager.new_memory("Healthy Chat")
        valid.extra_data["last_activity_date"] = "2026-04-08T09:40:00"
        manager.save_one(valid.id)

        broken = manager.new_memory("Broken Chat")
        broken.add_messages([{"role": "user", "content": "still recoverable"}])
        manager.save_one(broken.id)

        broken_meta = memory_dir / f"{broken.id}.meta.json"
        broken_meta.write_text('{"id":"broken","name":"Broken Chat","extra_data":{"a', encoding="utf-8")

        chatroom = _empty_chatroom()
        chatroom.memory_manager = MemoryManager(memory_dir, use_jsonl=True)

        result = await ChatRoom.list_chats(chatroom)

        assert result["success"] is True
        assert [chat["id"] for chat in result["chats"]] == [valid.id]
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_list_chats_skips_corrupted_legacy_json_metadata():
    temp_dir = tempfile.mkdtemp()
    try:
        memory_dir = Path(temp_dir)
        manager = MemoryManager(memory_dir, use_jsonl=False)

        valid = manager.new_memory("Healthy Legacy Chat")
        valid.extra_data["last_activity_date"] = "2026-04-08T09:40:00"
        manager.save_one(valid.id)

        broken = manager.new_memory("Broken Legacy Chat")
        manager.save_one(broken.id)
        (memory_dir / f"{broken.id}.json").write_text("{broken json", encoding="utf-8")

        chatroom = _empty_chatroom()
        chatroom.memory_manager = MemoryManager(memory_dir, use_jsonl=True)

        result = await ChatRoom.list_chats(chatroom)

        assert result["success"] is True
        assert [chat["id"] for chat in result["chats"]] == [valid.id]
        assert result["skipped_chats"] == [{"id": broken.id, "message": "metadata unreadable"}]
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_list_chats_uses_metadata_without_loading_jsonl_messages(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    try:
        memory_dir = Path(temp_dir)
        manager = MemoryManager(memory_dir, use_jsonl=True)

        chat = manager.new_memory("Metadata Chat")
        chat.extra_data["last_activity_date"] = "2026-06-12T10:00:00"
        chat.extra_data["project"] = {
            "name": "proj-a",
            "workspace_mode": "isolated",
            "workspace_path": "/workspace/proj-a",
        }
        chat.add_messages([{"role": "user", "content": "history should not be read"}])
        manager.save_one(chat.id)

        chatroom = _empty_chatroom()
        chatroom.memory_manager = MemoryManager(memory_dir, use_jsonl=True)

        def fail_load_messages(self, memory_id):
            raise AssertionError(f"load_messages should not be called for {memory_id}")

        monkeypatch.setattr(JSONLBackend, "load_messages", fail_load_messages)

        result = await ChatRoom.list_chats(chatroom, project_name="proj-a")

        assert result["success"] is True
        assert len(result["chats"]) == 1
        summary = result["chats"][0]
        assert summary["id"] == chat.id
        assert summary["name"] == "Metadata Chat"
        assert summary["workspace_mode"] == "isolated"
        assert summary["workspace_path"] == "/workspace/proj-a"
        assert chat.id not in chatroom.memory_manager.memory_store
    finally:
        shutil.rmtree(temp_dir)


def test_repl_renders_both_utc_and_legacy_activity_dates():
    from datetime import datetime, timezone
    from pantheon.repl.utils import format_relative_time
    assert format_relative_time(datetime.now(timezone.utc).isoformat()) == "just now"
    assert format_relative_time(datetime.now().isoformat()) == "just now"
    assert format_relative_time("invalid") == "-"
