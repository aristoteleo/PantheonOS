from unittest.mock import patch

from pantheon.auth import openai_provider


def test_check_origin_accepts_allowed_origin():
    handler = openai_provider._OAuthCallbackHandler.__new__(openai_provider._OAuthCallbackHandler)
    handler.headers = {"Origin": "https://auth.openai.com/some/path"}
    assert handler._check_origin() is True


def test_check_origin_rejects_untrusted_origin():
    handler = openai_provider._OAuthCallbackHandler.__new__(openai_provider._OAuthCallbackHandler)
    handler.headers = {"Origin": "https://evil.example.com"}
    assert handler._check_origin() is False


def test_decode_jwt_payload_does_not_fallback_for_sensitive_claims():
    with patch.object(openai_provider, "_decode_jwt_payload_verified", return_value={}):
        with patch.object(openai_provider, "_decode_jwt_payload_unverified", return_value={"email": "forged@example.com"}):
            payload = openai_provider._decode_jwt_payload("fake-token")
            assert payload == {}
            assert openai_provider._extract_email("fake-token") == ""


def test_decode_jwt_payload_allows_unverified_fallback_for_exp_only():
    with patch.object(openai_provider, "_decode_jwt_payload_verified", return_value={}):
        with patch.object(openai_provider, "_decode_jwt_payload_unverified", return_value={"exp": 2000000000}):
            payload = openai_provider._decode_jwt_payload("fake-token", allow_unverified_fallback=True)
            assert payload == {"exp": 2000000000}
            assert openai_provider._extract_token_exp("fake-token") == 2000000000.0
