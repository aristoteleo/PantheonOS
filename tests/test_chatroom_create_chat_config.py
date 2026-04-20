from pathlib import Path

import pytest

from pantheon.chatroom.room import ChatRoom
from pantheon.factory import get_template_manager
from pantheon.internal.memory import MemoryManager


def _make_chatroom(tmp_path: Path) -> ChatRoom:
    chatroom = ChatRoom.__new__(ChatRoom)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    chatroom.memory_manager = MemoryManager(memory_dir, use_jsonl=True)
    chatroom.template_manager = get_template_manager(tmp_path)
    chatroom.chat_teams = {}
    return chatroom


@pytest.mark.asyncio
async def test_create_chat_can_initialize_template_workspace_and_chat_config(tmp_path: Path):
    chatroom = _make_chatroom(tmp_path)
    workspace_dir = tmp_path / "workspace" / "chat-a"

    template_obj = {
        "id": "scoped-team",
        "name": "Scoped Team",
        "description": "Team created together with the chat.",
        "icon": "🧪",
        "category": "analysis",
        "agents": [
            {
                "id": "analyst",
                "name": "Analyst",
                "model": "gpt-4.1",
                "instructions": "Only work inside the assigned workspace.",
                "toolsets": ["file_manager"],
            }
        ],
        "tags": ["scoped"],
    }
    chat_config = {
        "workspace": {
            "root": str(workspace_dir),
            "mode": "read_selected_dir",
        },
        "features": {
            "isolated": True,
        },
    }

    result = await ChatRoom.create_chat(
        chatroom,
        chat_name="Scoped Chat",
        project_name="proj-a",
        workspace_path=str(workspace_dir),
        template_obj=template_obj,
        chat_config=chat_config,
        project_metadata={"color": "blue"},
    )

    assert result["success"] is True
    assert result["workspace_mode"] == "isolated"
    assert result["workspace_path"] == str(workspace_dir)
    assert result["template"] == {
        "id": "scoped-team",
        "name": "Scoped Team",
        "icon": "🧪",
        "category": "analysis",
        "version": "1.0.0",
        "source_path": None,
        "agent_count": 1,
    }
    assert result["chat_config"] == chat_config
    assert workspace_dir.exists() is True

    memory = chatroom.memory_manager.get_memory(result["chat_id"])
    assert memory.extra_data["project"]["name"] == "proj-a"
    assert memory.extra_data["project"]["color"] == "blue"
    assert memory.extra_data["project"]["workspace_mode"] == "isolated"
    assert memory.extra_data["project"]["workspace_path"] == str(workspace_dir)
    assert memory.extra_data["chat_config"] == chat_config
    assert memory.extra_data["team_template"]["id"] == "scoped-team"
    assert memory.extra_data["team_template"]["agents"][0]["name"] == "Analyst"

    listed = await ChatRoom.list_chats(chatroom, project_name="proj-a")
    assert listed["success"] is True
    assert len(listed["chats"]) == 1
    chat_summary = listed["chats"][0]
    assert chat_summary["workspace_mode"] == "isolated"
    assert chat_summary["workspace_path"] == str(workspace_dir)
    assert chat_summary["chat_config"] == chat_config
    assert chat_summary["template"] == result["template"]
    assert chat_summary["project"]["color"] == "blue"


@pytest.mark.asyncio
async def test_create_chat_rejects_invalid_template_before_creating_memory(tmp_path: Path):
    chatroom = _make_chatroom(tmp_path)

    result = await ChatRoom.create_chat(
        chatroom,
        chat_name="Invalid Chat",
        template_obj={
            "description": "Missing id and name should fail validation.",
            "agents": [],
        },
    )

    assert result["success"] is False
    assert "id and name are required" in result["message"]
    assert chatroom.memory_manager.list_memories() == []
