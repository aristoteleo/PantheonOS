import pytest


@pytest.mark.asyncio
async def test_check_api_keys_reports_base_urls_and_fallback(monkeypatch):
    from pantheon.chatroom.room import ChatRoom

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://openai-proxy.example/v1")
    monkeypatch.setenv("ANTHROPIC_API_BASE", "https://anthropic-proxy.example")
    monkeypatch.setenv("GEMINI_API_BASE", "https://gemini-proxy.example")
    monkeypatch.setenv("LLM_API_BASE", "https://fallback.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "fallback-key")

    room = ChatRoom.__new__(ChatRoom)
    result = await ChatRoom.check_api_keys(room)

    assert result["keys"]["OPENAI_API_KEY"]["configured"] is True
    assert result["base_urls"]["OPENAI_API_BASE"]["configured"] is True
    assert result["base_urls"]["ANTHROPIC_API_BASE"]["configured"] is True
    assert result["base_urls"]["GEMINI_API_BASE"]["configured"] is True
    assert result["fallback"]["LLM_API_BASE"]["configured"] is True
    assert result["fallback"]["LLM_API_KEY"]["configured"] is True
