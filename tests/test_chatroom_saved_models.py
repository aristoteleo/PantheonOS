from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _MockResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


class _MockAsyncClient:
    def __init__(self, response: _MockResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_discover_provider_models_openai_success():
    from pantheon.chatroom.room import ChatRoom

    room = ChatRoom.__new__(ChatRoom)
    response = _MockResponse(
        200,
        {
            "data": [
                {"id": "gpt-4.1"},
                {"id": "gpt-4.1-mini"},
            ]
        },
    )

    with patch("httpx.AsyncClient", return_value=_MockAsyncClient(response)):
        result = await ChatRoom.discover_provider_models(
            room,
            provider="openai",
            api_key="sk-test",
            api_base="https://example.com/v1",
        )

    assert result["success"] is True
    assert result["provider"] == "openai"
    assert result["models"] == ["gpt-4.1", "gpt-4.1-mini"]


@pytest.mark.asyncio
async def test_discover_provider_models_reports_unsupported_discovery():
    from pantheon.chatroom.room import ChatRoom

    room = ChatRoom.__new__(ChatRoom)
    response = _MockResponse(404, {})

    with patch("httpx.AsyncClient", return_value=_MockAsyncClient(response)):
        result = await ChatRoom.discover_provider_models(
            room,
            provider="openai",
            api_key="sk-test",
            api_base="https://example.com/v1",
        )

    assert result["success"] is False
    assert result["error_code"] == "discovery_unsupported"


@pytest.mark.asyncio
async def test_discover_provider_models_rejects_non_ascii_api_key():
    from pantheon.chatroom.room import ChatRoom

    room = ChatRoom.__new__(ChatRoom)
    result = await ChatRoom.discover_provider_models(
        room,
        provider="openai",
        api_key="sk-测试密钥",
        api_base="https://example.com/v1",
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_saved_models_round_trip():
    from pantheon.chatroom.room import ChatRoom

    room = ChatRoom.__new__(ChatRoom)
    settings = MagicMock()
    settings.get.side_effect = lambda key, default=None: (
        {"openai": ["gpt-4.1"], "anthropic": [], "gemini": []}
        if key == "models.saved_models"
        else default
    )

    with patch("pantheon.chatroom.room.get_settings", return_value=settings):
        result = await ChatRoom.saved_models(room)

    assert result == {
        "success": True,
        "saved_models": {
            "openai": ["gpt-4.1"],
            "anthropic": [],
            "gemini": [],
        },
    }

    settings = MagicMock()
    with patch("pantheon.chatroom.room.get_settings", return_value=settings):
        result = await ChatRoom.saved_models(
            room,
            saved_models={
                "openai": ["openai/gpt-4.1", "gpt-4.1-mini"],
                "anthropic": ["claude-sonnet-4-20250514"],
                "gemini": [],
            },
        )

    settings.persist_project_value.assert_called_once_with(
        "models.saved_models",
        {
            "openai": ["gpt-4.1", "gpt-4.1-mini"],
            "anthropic": ["claude-sonnet-4-20250514"],
            "gemini": [],
        },
    )
    settings.reload.assert_called_once()
    assert result["success"] is True
