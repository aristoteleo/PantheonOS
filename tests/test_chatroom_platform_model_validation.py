"""Provider validation for platform models routed through OpenRouter.

In ``PLATFORM_MODEL_MODE=openrouter`` the model picker offers platform models as
``openrouter/<vendor>/<model>`` ids that the platform's own OpenRouter key pays for.
The first path segment is a routing prefix, not a native provider, so validating it
against the native-provider credential set rejected every such pick and blocked both
chat/agent creation and agent model updates.
"""

from pathlib import Path

import pytest

from pantheon.chatroom.room import ChatRoom
from pantheon.factory import get_template_manager
from pantheon.internal.memory import MemoryManager
from pantheon.utils.model_selector import ModelSelector

PLATFORM_MODEL = "openrouter/x-ai/grok-4.5"


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


def _enable_platform_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployment fronted by the platform proxy with OpenRouter as the model source."""
    monkeypatch.setenv("PLATFORM_MODEL_MODE", "openrouter")
    monkeypatch.setenv("PANTHEON_PLATFORM_PROXY_BASE", "https://proxy.example/v1")
    monkeypatch.setenv("PANTHEON_PLATFORM_PROXY_KEY", "sk-virtual-key")
    monkeypatch.delenv("LLM_FORCE_PROXY", raising=False)


def _disable_platform_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLATFORM_MODEL_MODE", raising=False)
    monkeypatch.delenv("PANTHEON_PLATFORM_PROXY_BASE", raising=False)
    monkeypatch.delenv("PANTHEON_PLATFORM_PROXY_KEY", raising=False)
    monkeypatch.delenv("LLM_FORCE_PROXY", raising=False)
    monkeypatch.delenv("LLM_API_BASE", raising=False)


class _StubAgent:
    def __init__(self, name: str):
        self.name = name
        self.models = ["openai/gpt-5.4"]
        self.model_params: dict = {}


class _StubTeam:
    def __init__(self, agents: list, source_path: str):
        self.team_agents = agents
        self._source_path = source_path


def test_validate_model_provider_accepts_platform_openrouter_model(
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_platform_openrouter(monkeypatch)
    room = ChatRoom.__new__(ChatRoom)

    assert ChatRoom._validate_model_provider(room, PLATFORM_MODEL) == (True, "")


def test_validate_model_provider_accepts_platform_vendor_prefixed_model(
    monkeypatch: pytest.MonkeyPatch,
):
    """A saved/pinned id can arrive without the openrouter/ routing prefix."""
    _enable_platform_openrouter(monkeypatch)
    room = ChatRoom.__new__(ChatRoom)

    assert ChatRoom._validate_model_provider(room, "x-ai/grok-4.5") == (True, "")


def test_validate_model_provider_rejects_openrouter_model_in_direct_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    """Platform budget without OpenRouter as the source: nothing routes there."""
    _enable_platform_openrouter(monkeypatch)
    monkeypatch.setenv("PLATFORM_MODEL_MODE", "direct")
    room = ChatRoom.__new__(ChatRoom)

    is_valid, message = ChatRoom._validate_model_provider(room, PLATFORM_MODEL)
    assert is_valid is False
    assert "openrouter" in message


def test_validate_model_provider_rejects_openrouter_model_without_byok_key(
    monkeypatch: pytest.MonkeyPatch,
):
    _disable_platform_proxy(monkeypatch)
    monkeypatch.setattr(
        ModelSelector, "_get_available_providers", lambda _self: {"openai"}
    )
    room = ChatRoom.__new__(ChatRoom)

    is_valid, message = ChatRoom._validate_model_provider(room, PLATFORM_MODEL)
    assert is_valid is False
    assert "openrouter" in message


@pytest.mark.asyncio
async def test_create_chat_accepts_platform_openrouter_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_platform_openrouter(monkeypatch)
    chatroom = _make_chatroom(tmp_path)

    result = await ChatRoom.create_chat(
        chatroom,
        chat_name="Platform Model Chat",
        template_id="default",
        model=PLATFORM_MODEL,
    )

    assert result["success"] is True
    memory = chatroom.memory_manager.get_memory(result["chat_id"])
    team_template = memory.extra_data["team_template"]
    assert {agent["model"] for agent in team_template["agents"]} == {PLATFORM_MODEL}


@pytest.mark.asyncio
async def test_set_agent_model_accepts_platform_openrouter_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _enable_platform_openrouter(monkeypatch)
    chatroom = _make_chatroom(tmp_path)

    created = await ChatRoom.create_chat(
        chatroom,
        chat_name="Platform Model Update Chat",
        template_id="default",
    )
    chat_id = created["chat_id"]

    # Persist into a copy of the template so the test never writes to the factory file.
    factory_default = Path(chatroom.template_manager.get_template("default").source_path)
    team_file = tmp_path / "default.md"
    team_file.write_text(factory_default.read_text(encoding="utf-8"), encoding="utf-8")

    team = _StubTeam([_StubAgent("Leader")], str(team_file))

    async def _get_team_for_chat(*_args, **_kwargs):
        return team

    chatroom.get_team_for_chat = _get_team_for_chat

    result = await ChatRoom.set_agent_model(
        chatroom,
        chat_id=chat_id,
        agent_name="Leader",
        model=PLATFORM_MODEL,
    )

    assert result["success"] is True, result.get("message")
    assert result["resolved_models"] == [PLATFORM_MODEL]
    assert team.team_agents[0].models == [PLATFORM_MODEL]

    memory = chatroom.memory_manager.get_memory(chat_id)
    leader = next(
        agent
        for agent in memory.extra_data["team_template"]["agents"]
        if (agent.get("name") or "").lower() == "leader"
    )
    assert leader["model"] == PLATFORM_MODEL


@pytest.mark.asyncio
async def test_set_agent_model_still_rejects_unreachable_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _disable_platform_proxy(monkeypatch)
    monkeypatch.setattr(
        ModelSelector, "_get_available_providers", lambda _self: {"openai"}
    )
    chatroom = _make_chatroom(tmp_path)

    created = await ChatRoom.create_chat(
        chatroom,
        chat_name="Rejected Update Chat",
        template_id="default",
    )
    team = _StubTeam([_StubAgent("Leader")], str(tmp_path / "missing.md"))

    async def _get_team_for_chat(*_args, **_kwargs):
        return team

    chatroom.get_team_for_chat = _get_team_for_chat

    result = await ChatRoom.set_agent_model(
        chatroom,
        chat_id=created["chat_id"],
        agent_name="Leader",
        model=PLATFORM_MODEL,
    )

    assert result["success"] is False
    assert "openrouter" in result["message"]
    assert team.team_agents[0].models == ["openai/gpt-5.4"]
