import inspect
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
    chatroom.project_manager = type(
        "PM",
        (),
        {
            "list_projects": lambda self: [],
            "active_project": None,
        },
    )()
    return chatroom


def test_model_parameters_are_appended_to_existing_tool_signatures():
    create_params = list(inspect.signature(ChatRoom.create_chat).parameters)
    setup_params = list(inspect.signature(ChatRoom.setup_team_for_chat).parameters)

    assert create_params[:9] == [
        "self",
        "chat_name",
        "project_name",
        "workspace_path",
        "workspace_mode",
        "template_id",
        "template_obj",
        "chat_config",
        "project_metadata",
    ]
    assert create_params[-2:] == ["model", "validate_model"]

    assert setup_params[:4] == [
        "self",
        "chat_id",
        "template_obj",
        "save_to_memory",
    ]
    assert setup_params[-3:] == ["model", "validate_model", "template_id"]


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
async def test_create_chat_applies_model_after_template_id_resolution(tmp_path: Path):
    chatroom = _make_chatroom(tmp_path)

    result = await ChatRoom.create_chat(
        chatroom,
        chat_name="Model Override Chat",
        template_id="default",
        model="openai/gpt-5.4",
        validate_model=False,
    )

    assert result["success"] is True
    assert result["template"]["id"] == "default"
    assert result["template"]["agent_count"] > 0

    memory = chatroom.memory_manager.get_memory(result["chat_id"])
    team_template = memory.extra_data["team_template"]
    assert team_template["id"] == "default"
    assert {agent["model"] for agent in team_template["agents"]} == {"openai/gpt-5.4"}


@pytest.mark.asyncio
async def test_setup_team_for_chat_can_update_only_saved_template_model(tmp_path: Path):
    chatroom = _make_chatroom(tmp_path)
    template_obj = {
        "id": "scoped-team",
        "name": "Scoped Team",
        "description": "Team created together with the chat.",
        "agents": [
            {
                "id": "analyst",
                "name": "Analyst",
                "model": "gpt-4.1",
                "instructions": "Only work inside the assigned workspace.",
            },
            {
                "id": "reviewer",
                "name": "Reviewer",
                "model": "gpt-4.1-mini",
                "instructions": "Review the analyst's work.",
            },
        ],
    }

    created = await ChatRoom.create_chat(
        chatroom,
        chat_name="Model Update Chat",
        template_obj=template_obj,
    )
    chat_id = created["chat_id"]
    chatroom.chat_teams[chat_id] = object()

    result = await ChatRoom.setup_team_for_chat(
        chatroom,
        chat_id=chat_id,
        model="openai/gpt-5.4",
        validate_model=False,
    )

    assert result["success"] is True
    assert result["chat_id"] == chat_id
    assert chat_id not in chatroom.chat_teams

    memory = chatroom.memory_manager.get_memory(chat_id)
    team_template = memory.extra_data["team_template"]
    assert team_template["id"] == "scoped-team"
    assert {agent["model"] for agent in team_template["agents"]} == {"openai/gpt-5.4"}


@pytest.mark.asyncio
async def test_setup_team_for_chat_can_apply_template_id_directly(tmp_path: Path):
    chatroom = _make_chatroom(tmp_path)
    created = await ChatRoom.create_chat(chatroom, chat_name="Template ID Chat")
    chat_id = created["chat_id"]
    chatroom.chat_teams[chat_id] = object()

    result = await ChatRoom.setup_team_for_chat(
        chatroom,
        chat_id=chat_id,
        template_id="default",
    )

    assert result["success"] is True
    assert result["chat_id"] == chat_id
    assert result["template"]["id"] == "default"
    assert chat_id not in chatroom.chat_teams

    memory = chatroom.memory_manager.get_memory(chat_id)
    assert memory.extra_data["team_template"]["id"] == "default"


@pytest.mark.asyncio
async def test_create_chat_validates_model_by_default_before_creating_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    chatroom = _make_chatroom(tmp_path)

    monkeypatch.setattr(
        ChatRoom,
        "_validate_model_provider",
        lambda _self, _model: (False, "Provider unavailable"),
    )

    result = await ChatRoom.create_chat(
        chatroom,
        chat_name="Rejected Model Chat",
        template_id="default",
        model="missing-provider/model",
    )

    assert result["success"] is False
    assert result["message"] == "Provider unavailable"
    assert chatroom.memory_manager.list_memories() == []


@pytest.mark.asyncio
async def test_setup_team_for_chat_validates_model_by_default_without_mutating_saved_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    chatroom = _make_chatroom(tmp_path)
    template_obj = {
        "id": "scoped-team",
        "name": "Scoped Team",
        "description": "Team created together with the chat.",
        "agents": [
            {
                "id": "analyst",
                "name": "Analyst",
                "model": "gpt-4.1",
                "instructions": "Only work inside the assigned workspace.",
            }
        ],
    }
    created = await ChatRoom.create_chat(
        chatroom,
        chat_name="Rejected Setup Model Chat",
        template_obj=template_obj,
    )
    chat_id = created["chat_id"]

    monkeypatch.setattr(
        ChatRoom,
        "_validate_model_provider",
        lambda _self, _model: (False, "Provider unavailable"),
    )

    result = await ChatRoom.setup_team_for_chat(
        chatroom,
        chat_id=chat_id,
        model="missing-provider/model",
    )

    assert result["success"] is False
    assert "Provider unavailable" in result["message"]

    memory = chatroom.memory_manager.get_memory(chat_id)
    team_template = memory.extra_data["team_template"]
    assert team_template["agents"][0]["model"] == "gpt-4.1"


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
