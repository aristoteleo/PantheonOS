"""
OpenAI Codex OAuth — browser-based login to ChatGPT backend-api.

Implements OAuth 2.0 Authorization Code flow with PKCE.
Tokens are stored in ~/.pantheon/oauth/codex.json.
Supports importing tokens from Codex CLI (~/.codex/auth.json).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..log import logger

# ============ Constants ============

AUTH_ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ORIGINATOR = "pi"
CALLBACK_PORT = 1455
SCOPE = "openid profile email offline_access"
CODEX_BASE_URL = "https://chatgpt.com/backend-api"

# Auth storage
AUTH_DIR = Path.home() / ".pantheon" / "oauth"
AUTH_FILE = AUTH_DIR / "codex.json"
CODEX_CLI_AUTH = Path.home() / ".codex" / "auth.json"


class CodexOAuthError(RuntimeError):
    """Raised when Codex OAuth login or refresh fails."""


# ============ Utility Functions ============


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    """Generate PKCE verifier and challenge pair."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without verification (for reading claims)."""
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _jwt_org_context(token: str) -> dict[str, str]:
    """Extract org/account/project from JWT claims."""
    payload = _decode_jwt_payload(token)
    nested = payload.get("https://api.openai.com/auth")
    claims = nested if isinstance(nested, dict) else {}
    context = {}
    for key in ("organization_id", "project_id", "chatgpt_account_id"):
        value = str(claims.get(key) or "").strip()
        if value:
            context[key] = value
    return context


def _token_expired(token: str, skew_seconds: int = 300) -> bool:
    """Check if JWT access_token is expired (with skew)."""
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return time.time() >= (float(exp) - skew_seconds)


# ============ Token Exchange ============


def _exchange_code(code: str, redirect_uri: str, code_verifier: str) -> dict[str, str]:
    """Exchange authorization code for tokens."""
    resp = httpx.post(
        f"{AUTH_ISSUER}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    if not resp.is_success:
        raise CodexOAuthError(f"Token exchange failed: HTTP {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    if not all(data.get(k) for k in ("id_token", "access_token", "refresh_token")):
        raise CodexOAuthError("Token exchange returned incomplete credentials")
    return {
        "id_token": str(data["id_token"]),
        "access_token": str(data["access_token"]),
        "refresh_token": str(data["refresh_token"]),
    }


def _refresh_tokens(refresh_token: str) -> dict[str, str]:
    """Refresh access token using refresh token."""
    resp = httpx.post(
        f"{AUTH_ISSUER}/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if not resp.is_success:
        raise CodexOAuthError(f"Token refresh failed: HTTP {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    access_token = str(data.get("access_token") or "").strip()
    id_token = str(data.get("id_token") or "").strip()
    next_refresh = str(data.get("refresh_token") or refresh_token).strip()
    if not access_token or not id_token:
        raise CodexOAuthError("Token refresh returned incomplete credentials")
    return {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": next_refresh,
    }


# ============ Callback Server ============


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "PantheonOAuth/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_error(404)
            return
        params = {k: v[-1] for k, v in parse_qs(parsed.query).items() if v}
        self.server.result = params
        self.server.event.set()
        body = (
            "<html><body><h3>OAuth complete</h3>"
            "<p>You can close this window and return to Pantheon.</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return  # Suppress HTTP server logs


# ============ OAuth Manager ============


class CodexOAuthManager:
    """Manage Codex OAuth tokens — login, refresh, import, and storage."""

    def __init__(self, auth_file: Path | None = None):
        self.auth_file = auth_file or AUTH_FILE

    # ---- Storage ----

    def _load(self) -> dict[str, Any]:
        if self.auth_file.exists():
            try:
                return json.loads(self.auth_file.read_text())
            except Exception:
                pass
        return {}

    def _save(self, auth: dict[str, Any]) -> dict[str, Any]:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(json.dumps(auth, indent=2))
        os.chmod(self.auth_file, 0o600)
        return auth

    # ---- Token Access ----

    def get_tokens(self) -> dict[str, str]:
        """Get stored tokens dict."""
        return self._load().get("tokens", {})

    def get_access_token(self, auto_refresh: bool = True) -> str | None:
        """Get a valid access token, refreshing if needed."""
        tokens = self.get_tokens()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")

        if not access_token:
            return None

        if auto_refresh and _token_expired(access_token) and refresh_token:
            logger.info("[Codex OAuth] Access token expired, refreshing...")
            try:
                self.refresh()
                tokens = self.get_tokens()
                access_token = tokens.get("access_token", "")
            except Exception as e:
                logger.warning(f"[Codex OAuth] Refresh failed: {e}")
                return None

        return access_token if access_token and not _token_expired(access_token) else None

    def get_account_id(self) -> str | None:
        """Get ChatGPT account_id for API calls."""
        return self.get_tokens().get("account_id") or None

    def is_authenticated(self) -> bool:
        """Check if we have a valid (or refreshable) token."""
        tokens = self.get_tokens()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        if access_token and not _token_expired(access_token):
            return True
        return bool(refresh_token)

    # ---- Login Flow ----

    def login(
        self,
        *,
        open_browser: bool = True,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Start browser-based OAuth login flow.

        Opens browser to OpenAI auth page. User logs in, callback
        redirects to local server. Returns auth record with tokens.
        """
        verifier, challenge = _pkce_pair()
        state = _b64url(secrets.token_bytes(24))

        event = threading.Event()
        server = self._create_server(event)
        _, port = server.server_address
        redirect_uri = f"http://localhost:{port}/auth/callback"

        auth_url = (
            f"{AUTH_ISSUER}/oauth/authorize?"
            + urlencode({
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "scope": SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "state": state,
                "originator": ORIGINATOR,
            })
        )

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            logger.info(f"[Codex OAuth] Opening browser for login...")
            logger.info(f"[Codex OAuth] Auth URL: {auth_url}")
            if open_browser:
                webbrowser.open(auth_url)

            if not event.wait(timeout_seconds):
                raise CodexOAuthError("Timed out waiting for OAuth callback")

            params = getattr(server, "result", {}) or {}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        # Validate callback
        if params.get("state") != state:
            raise CodexOAuthError("OAuth callback state mismatch")
        if params.get("error"):
            raise CodexOAuthError(f"OAuth failed: {params.get('error_description', params['error'])}")

        code = str(params.get("code", "")).strip()
        if not code:
            raise CodexOAuthError("OAuth callback missing authorization code")

        # Exchange code for tokens
        tokens = _exchange_code(code, redirect_uri, verifier)
        claims = _jwt_org_context(tokens["id_token"])

        auth = {
            "provider": "codex",
            "tokens": {
                **tokens,
                "account_id": claims.get("chatgpt_account_id"),
                "organization_id": claims.get("organization_id"),
                "project_id": claims.get("project_id"),
            },
            "last_refresh": _utc_now(),
        }

        logger.info("[Codex OAuth] Login successful")
        return self._save(auth)

    # ---- Refresh ----

    def refresh(self) -> dict[str, Any]:
        """Refresh the access token using the stored refresh token."""
        auth = self._load()
        tokens = auth.get("tokens", {})
        refresh_token = tokens.get("refresh_token", "")
        if not refresh_token:
            raise CodexOAuthError("No refresh token available")

        refreshed = _refresh_tokens(refresh_token)
        claims = _jwt_org_context(refreshed["id_token"])

        auth["tokens"] = {
            **refreshed,
            "account_id": claims.get("chatgpt_account_id"),
            "organization_id": claims.get("organization_id"),
            "project_id": claims.get("project_id"),
        }
        auth["last_refresh"] = _utc_now()

        logger.info("[Codex OAuth] Token refreshed successfully")
        return self._save(auth)

    # ---- Import from Codex CLI ----

    def import_from_codex_cli(self) -> dict[str, Any] | None:
        """Import tokens from Codex CLI auth file (~/.codex/auth.json)."""
        if not CODEX_CLI_AUTH.exists():
            logger.info(f"[Codex OAuth] Codex CLI auth not found at {CODEX_CLI_AUTH}")
            return None

        try:
            codex_data = json.loads(CODEX_CLI_AUTH.read_text())
        except Exception as e:
            logger.warning(f"[Codex OAuth] Failed to read Codex CLI auth: {e}")
            return None

        tokens = codex_data.get("tokens", {})
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")

        if not access_token and not refresh_token:
            logger.info("[Codex OAuth] Codex CLI auth has no tokens")
            return None

        # Don't refresh here — OpenAI refresh_tokens are single-use.
        # If Codex CLI already used it, refreshing would fail with "refresh_token_reused".
        # Just import as-is; get_access_token() will refresh lazily when needed.
        if not access_token and refresh_token:
            # No access_token at all — must refresh to get one
            try:
                logger.info("[Codex OAuth] No access_token, attempting refresh...")
                refreshed = _refresh_tokens(refresh_token)
                tokens = refreshed
            except CodexOAuthError as e:
                logger.warning(f"[Codex OAuth] Refresh failed (token may be reused): {e}")
                # Still import what we have — the token may work or login will be needed

        claims = _jwt_org_context(tokens.get("id_token", "") or tokens.get("access_token", ""))

        auth = {
            "provider": "codex",
            "tokens": {
                **tokens,
                "account_id": claims.get("chatgpt_account_id"),
                "organization_id": claims.get("organization_id"),
                "project_id": claims.get("project_id"),
            },
            "last_refresh": _utc_now(),
            "source": str(CODEX_CLI_AUTH),
        }

        logger.info("[Codex OAuth] Imported tokens from Codex CLI")
        return self._save(auth)

    # ---- Internal ----

    @staticmethod
    def _create_server(event: threading.Event) -> ThreadingHTTPServer:
        for port in (CALLBACK_PORT, 0):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), _CallbackHandler)
                server.event = event
                server.result = {}
                return server
            except OSError:
                continue
        raise CodexOAuthError("Could not start local OAuth callback server")
