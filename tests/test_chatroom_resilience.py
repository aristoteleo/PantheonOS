import json
import shutil
import tempfile
from pathlib import Path

import pytest

from pantheon.chatroom.room import ChatRoom
from pantheon.internal.memory import MemoryManager


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

        chatroom = ChatRoom.__new__(ChatRoom)
        chatroom.memory_manager = MemoryManager(memory_dir, use_jsonl=True)

        result = await ChatRoom.list_chats(chatroom)

        assert result["success"] is True
        assert [chat["id"] for chat in result["chats"]] == [valid.id]
    finally:
        shutil.rmtree(temp_dir)
