import pytest


@pytest.mark.asyncio
async def test_gemini_embedding_uses_custom_base_url(monkeypatch):
    from pantheon.utils.adapters.gemini_adapter import GeminiAdapter

    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 200

        @property
        def text(self) -> str:
            return ""

        def json(self):
            return {"embedding": {"values": [0.1, 0.2]}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            captured["url"] = url
            return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GEMINI_API_BASE", "https://gemini-proxy.example")
    monkeypatch.setattr("pantheon.utils.adapters.gemini_adapter.httpx.AsyncClient", FakeAsyncClient)

    adapter = GeminiAdapter()
    result = await adapter.aembedding(model="gemini/text-embedding-004", input=["hello"])

    assert result == [[0.1, 0.2]]
    assert captured["url"].startswith(
        "https://gemini-proxy.example/v1beta/models/text-embedding-004:embedContent?key=gemini-key"
    )
