import io
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_openai_embedding_uses_openai_provider_base(monkeypatch):
    from pantheon.utils import llm as llm_module

    calls = {}

    class FakeAdapter:
        async def aembedding(self, **kwargs):
            calls.update(kwargs)
            return [[0.1, 0.2]]

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://openai-proxy.example/v1")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr("pantheon.utils.adapters.get_adapter", lambda _sdk: FakeAdapter())

    result = await llm_module.openai_embedding(["hello"], model="text-embedding-3-large")

    assert result == [[0.1, 0.2]]
    assert calls["api_key"] == "openai-key"
    assert calls["base_url"] == "https://openai-proxy.example/v1"


@pytest.mark.asyncio
async def test_speech_to_text_uses_openai_provider_base(monkeypatch):
    from pantheon.chatroom.room import ChatRoom

    calls = {}

    class FakeAdapter:
        async def atranscription(self, **kwargs):
            calls.update(kwargs)
            return SimpleNamespace(text="hello world")

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://openai-proxy.example/v1")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr("pantheon.utils.adapters.get_adapter", lambda _sdk: FakeAdapter())

    room = ChatRoom(endpoint="fake-service-id")
    result = await room.speech_to_text(b"fake-audio")

    assert result["success"] is True
    assert result["text"] == "hello world"
    assert calls["base_url"] == "https://openai-proxy.example/v1"
    assert calls["api_key"] == "openai-key"
    assert isinstance(calls["file"], io.BytesIO)
