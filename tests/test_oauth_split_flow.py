"""Tests for the split OAuth login flow (start / wait / complete / cancel).

The old ``login()`` was all-in-one: backend opened the browser, backend ran
a local callback server, backend blocked until the redirect came back.
That doesn't work when the backend isn't on the user's desktop (WSL1,
remote, headless). The split flow lets the frontend drive
``window.open(auth_url)`` instead, and adds a paste-URL fallback when the
browser can't reach the callback host.

These tests patch the token-exchange network call so everything runs
in-process.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pantheon.utils.oauth import codex as codex_mod
from pantheon.utils.oauth.codex import CodexOAuthError, CodexOAuthManager


def _fake_exchange_code(code: str, redirect_uri: str, verifier: str) -> dict:
    """Stand-in for the POST /oauth/token call. Returns deterministic fake tokens."""
    return {
        "id_token": "fake.id.token",
        "access_token": f"access-for-{code}",
        "refresh_token": "refresh-abc",
    }


def _fake_jwt_claims(_token: str) -> dict:
    return {
        "chatgpt_account_id": "acct-test",
        "organization_id": "org-test",
        "project_id": "proj-test",
    }


@pytest.fixture
def codex_mgr(tmp_path: Path, monkeypatch):
    """Codex manager with isolated auth file and patched token exchange."""
    auth_file = tmp_path / "codex_auth.json"
    monkeypatch.setattr(codex_mod, "_exchange_code", _fake_exchange_code)
    monkeypatch.setattr(codex_mod, "_jwt_org_context", _fake_jwt_claims)
    # Reset in-memory session store between tests
    with codex_mod._SESSIONS_LOCK:
        codex_mod._SESSIONS.clear()
    return CodexOAuthManager(auth_file=auth_file)


# ============================================================================
# start_login
# ============================================================================


def test_start_login_returns_session_id_and_auth_url(codex_mgr):
    result = codex_mgr.start_login()

    assert result["session_id"]
    assert result["auth_url"].startswith("https://auth.openai.com/oauth/authorize?")
    assert "code_challenge=" in result["auth_url"]
    assert "state=" in result["auth_url"]
    assert result["redirect_uri"].startswith("http://localhost:")
    assert result["expires_at"] > 0

    # Session is kept in the in-memory store so wait/complete can find it.
    sess = codex_mod._get_session(result["session_id"])
    assert sess.provider == "codex"
    assert not sess.finalized
    # Teardown to release the callback port
    codex_mgr.cancel_login(result["session_id"])


def test_start_login_every_call_makes_a_fresh_session(codex_mgr):
    a = codex_mgr.start_login()
    b = codex_mgr.start_login()
    assert a["session_id"] != b["session_id"]
    assert a["redirect_uri"] != b["redirect_uri"]  # different random ports
    codex_mgr.cancel_login(a["session_id"])
    codex_mgr.cancel_login(b["session_id"])


# ============================================================================
# complete_login_from_url (paste-URL fallback)
# ============================================================================


def test_complete_login_from_url_happy_path(codex_mgr):
    started = codex_mgr.start_login()
    sess = codex_mod._get_session(started["session_id"])
    callback = f"{started['redirect_uri']}?code=ABC123&state={sess.state}"

    auth = codex_mgr.complete_login_from_url(started["session_id"], callback)

    assert auth["provider"] == "codex"
    assert auth["tokens"]["access_token"] == "access-for-ABC123"
    assert auth["tokens"]["account_id"] == "acct-test"
    # Session has been dropped so a second attempt fails cleanly.
    with pytest.raises(CodexOAuthError):
        codex_mod._get_session(started["session_id"])


def test_complete_login_from_url_rejects_state_mismatch(codex_mgr):
    started = codex_mgr.start_login()
    callback = f"{started['redirect_uri']}?code=ABC123&state=someone-elses-state"

    with pytest.raises(CodexOAuthError, match="state mismatch"):
        codex_mgr.complete_login_from_url(started["session_id"], callback)

    # Session was torn down even on error — can't retry with the same session.
    with pytest.raises(CodexOAuthError):
        codex_mod._get_session(started["session_id"])


def test_complete_login_from_url_rejects_missing_code(codex_mgr):
    started = codex_mgr.start_login()
    sess = codex_mod._get_session(started["session_id"])
    callback = f"{started['redirect_uri']}?state={sess.state}"

    with pytest.raises(CodexOAuthError, match="missing authorization code"):
        codex_mgr.complete_login_from_url(started["session_id"], callback)


def test_complete_login_from_url_rejects_error_param(codex_mgr):
    started = codex_mgr.start_login()
    sess = codex_mod._get_session(started["session_id"])
    callback = (
        f"{started['redirect_uri']}?error=access_denied"
        f"&error_description=User+cancelled&state={sess.state}"
    )

    with pytest.raises(CodexOAuthError, match="User cancelled"):
        codex_mgr.complete_login_from_url(started["session_id"], callback)


def test_complete_login_from_url_rejects_unknown_session(codex_mgr):
    with pytest.raises(CodexOAuthError, match="not found or expired"):
        codex_mgr.complete_login_from_url(
            "abcdef1234567890", "http://localhost:1/auth/callback?code=x&state=y"
        )


def test_complete_login_from_url_rejects_empty_query(codex_mgr):
    started = codex_mgr.start_login()
    callback = started["redirect_uri"]  # no ?query

    with pytest.raises(CodexOAuthError, match="no query parameters"):
        codex_mgr.complete_login_from_url(started["session_id"], callback)


# ============================================================================
# wait_login + cancel_login
# ============================================================================


def test_wait_login_times_out_without_destroying_session(codex_mgr):
    """After a timeout the caller can still fall back to paste-URL —
    so the session must survive timeouts."""
    started = codex_mgr.start_login()

    with pytest.raises(CodexOAuthError, match="Timed out"):
        codex_mgr.wait_login(started["session_id"], timeout_seconds=0)

    # Session is still usable for the paste-URL fallback.
    sess = codex_mod._get_session(started["session_id"])
    callback = f"{started['redirect_uri']}?code=AFTER_TIMEOUT&state={sess.state}"
    auth = codex_mgr.complete_login_from_url(started["session_id"], callback)
    assert auth["tokens"]["access_token"] == "access-for-AFTER_TIMEOUT"


def test_cancel_login_releases_session(codex_mgr):
    started = codex_mgr.start_login()
    codex_mgr.cancel_login(started["session_id"])
    with pytest.raises(CodexOAuthError, match="not found or expired"):
        codex_mgr.complete_login_from_url(
            started["session_id"], f"{started['redirect_uri']}?code=x&state=y"
        )


# ============================================================================
# Back-compat: old login() wrapper still works
# ============================================================================


def test_login_wrapper_calls_start_open_browser_wait(codex_mgr, monkeypatch):
    """``login(open_browser=False)`` should start a session, skip browser,
    then wait. With a 0 timeout and no browser it should raise — but the
    session still gets cleaned up via the wrapper's except path."""

    calls = {"opened": 0}
    monkeypatch.setattr(
        codex_mod.webbrowser,
        "open",
        lambda url: calls.__setitem__("opened", calls["opened"] + 1),
    )

    with pytest.raises(CodexOAuthError, match="Timed out"):
        codex_mgr.login(open_browser=False, timeout_seconds=0)

    assert calls["opened"] == 0

    # Session was torn down by the wrapper's except-path cancel.
    with codex_mod._SESSIONS_LOCK:
        assert not codex_mod._SESSIONS, "wrapper must clean up on failure"
