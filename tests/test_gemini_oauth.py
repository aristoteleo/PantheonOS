import json
from pathlib import Path
from unittest import mock

import pytest

from pantheon.utils.oauth.gemini import GeminiCliOAuthManager


@pytest.fixture
def mock_settings():
    class DummySettings:
        def get(self, key, default=None):
            return default

    return DummySettings()


def test_gemini_cli_oauth_import_from_cli(tmp_path: Path):
    auth_file = tmp_path / "pantheon-gemini.json"
    cli_auth = tmp_path / "oauth_creds.json"
    cli_auth.write_text(
        json.dumps(
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_at": 9_999_999_999,
                "email": "user@example.com",
                "project_id": "demo-project",
            }
        ),
        encoding="utf-8",
    )

    mgr = GeminiCliOAuthManager(auth_file=auth_file)
    result = mgr.import_from_gemini_cli(path=cli_auth)

    assert result is not None
    assert mgr.is_authenticated() is True
    assert mgr.get_access_token(refresh_if_needed=False) == "access-token"
    assert mgr.get_email() == "user@example.com"
    assert mgr.get_project_id() == "demo-project"


def test_model_selector_detects_gemini_oauth(mock_settings, monkeypatch):
    from pantheon.utils.model_selector import ModelSelector

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    class FakeCodex:
        def is_authenticated(self):
            return False

    class FakeGemini:
        def is_authenticated(self):
            return True

    monkeypatch.setattr("pantheon.utils.oauth.CodexOAuthManager", FakeCodex)
    monkeypatch.setattr("pantheon.utils.oauth.GeminiCliOAuthManager", FakeGemini)

    selector = ModelSelector(mock_settings)
    providers = selector._get_available_providers()

    assert "gemini-cli" in providers


def test_gemini_cli_import_backfills_project_via_code_assist(monkeypatch, tmp_path: Path):
    auth_file = tmp_path / "pantheon-gemini.json"
    cli_auth = tmp_path / "oauth_creds.json"
    cli_auth.write_text(
        json.dumps(
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_at": 9_999_999_999,
                "email": "user@example.com",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pantheon.utils.oauth.gemini.resolve_google_oauth_identity",
        lambda token: {"email": "user@example.com", "project_id": "resolved-project"},
    )

    mgr = GeminiCliOAuthManager(auth_file=auth_file)
    result = mgr.import_from_gemini_cli(path=cli_auth)

    assert result is not None
    assert mgr.get_project_id() == "resolved-project"


def test_gemini_cli_get_client_kwargs_resolves_missing_project(monkeypatch, tmp_path: Path):
    auth_file = tmp_path / "pantheon-gemini.json"
    mgr = GeminiCliOAuthManager(auth_file=auth_file)
    mgr.save(
        {
            "provider": "gemini_cli",
            "tokens": {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_at": 9_999_999_999,
                "email": "user@example.com",
                "project_id": "",
            },
        }
    )

    monkeypatch.setattr(
        GeminiCliOAuthManager,
        "build_google_credentials",
        lambda self, refresh_if_needed=True: object(),
    )
    monkeypatch.setattr(
        "pantheon.utils.oauth.gemini.resolve_google_oauth_identity",
        lambda token: {"email": "user@example.com", "project_id": "resolved-project"},
    )

    kwargs = mgr.get_client_kwargs(refresh_if_needed=False)

    assert kwargs["project"] == "resolved-project"
    assert mgr.get_project_id() == "resolved-project"


@pytest.mark.asyncio
async def test_llm_acompletion_passes_gemini_cli_oauth_payload(monkeypatch):
    from pantheon.utils import llm as llm_module

    captured = {}

    class FakeAdapter:
        async def acompletion(self, **kwargs):
            captured.update(kwargs)
            return []

    class FakeManager:
        def build_api_key_payload(self, refresh_if_needed=True, import_if_missing=True):
            assert refresh_if_needed is True
            assert import_if_missing is True
            return '{"token":"oauth-access-token","projectId":"demo-project"}'

    class DummyResponse:
        usage = None

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("pantheon.utils.adapters.get_adapter", lambda sdk: FakeAdapter())
    monkeypatch.setattr(llm_module, "stream_chunk_builder", lambda chunks: DummyResponse())
    monkeypatch.setattr("pantheon.utils.oauth.GeminiCliOAuthManager", FakeManager)

    await llm_module.acompletion(
        model="gemini-cli/gemini-2.5-flash",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert captured["model"] == "gemini-2.5-flash"
    assert captured["api_key"] == '{"token":"oauth-access-token","projectId":"demo-project"}'
    assert "oauth_client_kwargs" not in captured


@pytest.mark.asyncio
async def test_llm_acompletion_preserves_project_error(monkeypatch):
    from pantheon.utils import llm as llm_module
    from pantheon.utils.oauth import GeminiCliOAuthError

    class FakeManager:
        def build_api_key_payload(self, refresh_if_needed=True, import_if_missing=True):
            raise GeminiCliOAuthError(
                "Gemini CLI OAuth could not resolve a Code Assist project."
            )

    monkeypatch.setattr("pantheon.utils.oauth.GeminiCliOAuthManager", FakeManager)

    with pytest.raises(RuntimeError, match=r"OAUTH_PROJECT_REQUIRED"):
        await llm_module.acompletion(
            model="gemini-cli/gemini-2.5-flash",
            messages=[{"role": "user", "content": "hello"}],
        )


@pytest.mark.asyncio
async def test_gemini_cli_adapter_uses_omicclaw_request_shape():
    from pantheon.utils.adapters.gemini_cli_adapter import GeminiCliAdapter

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "response": {
                        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                        "usageMetadata": {
                            "promptTokenCount": 3,
                            "candidatesTokenCount": 5,
                            "totalTokenCount": 8,
                        },
                    }
                }
            ).encode("utf-8")

    def fake_urlopen(req, context=None, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    adapter = GeminiCliAdapter()

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        chunks = await adapter.acompletion(
            model="gemini-2.5-flash-lite",
            messages=[
                {"role": "system", "content": "You are precise."},
                {"role": "user", "content": "hello"},
            ],
            api_key='{"token":"oauth-token","projectId":"demo-project"}',
            max_output_tokens=128,
        )

    assert captured["url"] == "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
    assert captured["body"]["model"] == "gemini-2.5-flash-lite"
    assert captured["body"]["project"] == "demo-project"
    assert captured["body"]["request"]["systemInstruction"] == {
        "role": "system",
        "parts": [{"text": "You are precise."}],
    }
    assert captured["body"]["request"]["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert captured["body"]["request"]["generationConfig"]["maxOutputTokens"] == 128
    assert chunks[-1]["usage"]["total_tokens"] == 8


@pytest.mark.asyncio
async def test_gemini_cli_adapter_uppercases_schema_and_strips_unsupported_keys():
    from pantheon.utils.adapters.gemini_cli_adapter import GeminiCliAdapter

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}}'

    def fake_urlopen(req, context=None, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    adapter = GeminiCliAdapter()

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        await adapter.acompletion(
            model="gemini-2.5-flash-lite",
            messages=[{"role": "user", "content": "run tool"}],
            api_key='{"token":"oauth-token","projectId":"demo-project"}',
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "python_exec",
                        "description": "Run Python",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "code": {
                                    "type": "string",
                                    "format": "python",
                                    "default": "print('hi')",
                                }
                            },
                            "required": ["code"],
                        },
                    },
                }
            ],
        )

    schema = captured["body"]["request"]["tools"][0]["functionDeclarations"][0]["parameters"]
    assert schema["type"] == "OBJECT"
    assert schema["properties"]["code"]["type"] == "STRING"
    assert "format" not in schema["properties"]["code"]
    assert "default" not in schema["properties"]["code"]


def test_gemini_cli_schema_cleaner_preserves_property_named_pattern():
    from pantheon.utils.adapters.gemini_cli_adapter import _clean_schema_for_gemini_cli

    cleaned = _clean_schema_for_gemini_cli(
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text to search for"},
                "path": {"type": "string"},
            },
            "required": ["pattern", "path"],
        }
    )

    assert cleaned["properties"]["pattern"]["type"] == "STRING"
    assert cleaned["required"] == ["pattern", "path"]
