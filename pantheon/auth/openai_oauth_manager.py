"""
OpenAI OAuth 2.0 Manager for PantheonOS.

Based on omicverse's architecture but implemented independently.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Callable

import requests

from pantheon.utils.log import logger


OPENAI_AUTH_ISSUER = "https://auth.openai.com"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_ORIGINATOR = "pi"
OPENAI_CALLBACK_PORT = 1455
OPENAI_SCOPE = "openid profile email offline_access"


@dataclass
class OAuthTokens:
    id_token: str
    access_token: str
    refresh_token: str
    account_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> OAuthTokens:
        return cls(
            id_token=data["id_token"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            account_id=data.get("account_id"),
            organization_id=data.get("organization_id"),
            project_id=data.get("project_id"),
        )

    def to_dict(self) -> dict:
        return {
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "account_id": self.account_id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
        }


@dataclass
class AuthRecord:
    provider: str
    tokens: OAuthTokens
    last_refresh: str
    email: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> AuthRecord:
        return cls(
            provider=data.get("provider", "openai-codex"),
            tokens=OAuthTokens.from_dict(data["tokens"]),
            last_refresh=data.get("last_refresh", _utc_now()),
            email=data.get("email"),
        )

    def to_dict(self) -> dict:
        result = {
            "provider": self.provider,
            "tokens": self.tokens.to_dict(),
            "last_refresh": self.last_refresh,
        }
        if self.email:
            result["email"] = self.email
        return result


@dataclass
class OAuthStatus:
    authenticated: bool
    email: str = ""
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    token_expires_at: Optional[float] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _decode_jwt_payload(token: str) -> dict:
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


def _extract_org_context(token: str) -> dict:
    payload = _decode_jwt_payload(token)
    nested = payload.get("https://api.openai.com/auth", {})
    if not isinstance(nested, dict):
        nested = {}

    context = {}
    for key in ("organization_id", "project_id", "chatgpt_account_id"):
        value = str(nested.get(key) or "").strip()
        if value:
            context[key] = value
    return context


def _token_expired(token: str, skew_seconds: int = 300) -> bool:
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return time.time() >= (float(exp) - skew_seconds)


def _extract_email(token: str) -> str:
    payload = _decode_jwt_payload(token)
    return payload.get("email", "")


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = "PantheonOAuth/1.0"

    def do_GET(self) -> None:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_error(404)
            return

        params = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        self.server.result = params
        self.server.event.set()

        body = (
            "<html><body><h3>OpenAI OAuth complete</h3>"
            "<p>You can close this window and return to Pantheon.</p></body></html>"
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class OpenAIOAuthManager:
    """
    Manage OpenAI OAuth state for Pantheon.
    """

    AUTHORIZATION_ENDPOINT = f"{OPENAI_AUTH_ISSUER}/oauth/authorize"
    TOKEN_ENDPOINT = f"{OPENAI_AUTH_ISSUER}/oauth/token"
    CLIENT_ID = OPENAI_CLIENT_ID
    SCOPE = OPENAI_SCOPE

    _instance: Optional[OpenAIOAuthManager] = None
    _lock = threading.Lock()

    def __init__(self, auth_path: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        if auth_path is None:
            auth_path = Path.home() / ".pantheon" / "oauth.json"
        self.auth_path = auth_path

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _create_callback_server(self, event: threading.Event) -> tuple:
        for port in (OPENAI_CALLBACK_PORT, 0):
            try:
                server = ThreadingHTTPServer(("localhost", port), _OAuthCallbackHandler)
                server.event = event
                server.result = {}
                return server, server.server_address[1]
            except OSError:
                continue
        raise RuntimeError("Could not start OAuth callback server")

    def _build_auth_url(self, code_challenge: str, redirect_uri: str, state: str, workspace_id: Optional[str] = None) -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": self.CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": self.SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": OPENAI_ORIGINATOR,
            "state": state,
        }

        if workspace_id:
            params["allowed_workspace_id"] = workspace_id

        return f"{self.AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    def _exchange_code_for_tokens(self, code: str, redirect_uri: str, code_verifier: str) -> dict:
        response = requests.post(
            self.TOKEN_ENDPOINT,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.CLIENT_ID,
                "code_verifier": code_verifier,
            },
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(f"OAuth token exchange failed: HTTP {response.status_code} {response.text[:300]}")

        data = response.json()
        required_keys = ("id_token", "access_token", "refresh_token")
        if not all(data.get(key) for key in required_keys):
            raise RuntimeError("OAuth token exchange returned incomplete credentials")

        return data

    def _refresh_token(self, refresh_token: str) -> dict:
        response = requests.post(
            self.TOKEN_ENDPOINT,
            data={
                "client_id": self.CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(f"Token refresh failed: HTTP {response.status_code} {response.text[:300]}")

        data = response.json()
        access_token = str(data.get("access_token") or "").strip()
        id_token = str(data.get("id_token") or "").strip()
        next_refresh = str(data.get("refresh_token") or refresh_token).strip()

        if not access_token or not id_token:
            raise RuntimeError("Token refresh returned incomplete credentials")

        return {
            "id_token": id_token,
            "access_token": access_token,
            "refresh_token": next_refresh,
        }

    def _build_auth_record(self, tokens_data: dict) -> AuthRecord:
        claims = _extract_org_context(tokens_data["id_token"])
        return AuthRecord(
            provider="openai-codex",
            tokens=OAuthTokens(
                id_token=tokens_data["id_token"],
                access_token=tokens_data["access_token"],
                refresh_token=tokens_data["refresh_token"],
                account_id=claims.get("chatgpt_account_id"),
                organization_id=claims.get("organization_id"),
                project_id=claims.get("project_id"),
            ),
            last_refresh=_utc_now(),
        )

    def _load_auth_record(self) -> Optional[AuthRecord]:
        if not self.auth_path.exists():
            return None
        try:
            with open(self.auth_path, "r") as f:
                data = json.load(f)
            return AuthRecord.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load auth record: {e}")
            return None

    def _save_auth_record(self, record: AuthRecord) -> None:
        try:
            self.auth_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.auth_path, "w") as f:
                json.dump(record.to_dict(), f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save auth record: {e}")

    def _parse_manual_callback(self, value: str) -> dict:
        from urllib.parse import parse_qs, urlparse

        text = (value or "").strip()
        if not text:
            raise ValueError("Missing OAuth callback URL or code/state pair")

        if "://" in text:
            parsed = urlparse(text)
            params = parse_qs(parsed.query)
            return {key: values[-1] for key, values in params.items() if values}

        if "#" in text:
            code, state = text.split("#", 1)
            return {"code": code.strip(), "state": state.strip()}

        raise ValueError("Could not parse OAuth callback input")

    def login(
        self,
        *,
        workspace_id: Optional[str] = None,
        open_browser: bool = True,
        timeout_seconds: int = 300,
        prompt_for_redirect: Optional[Callable[[str], str]] = None,
    ) -> bool:
        """
        Initiate OpenAI OAuth login flow.
        """
        with self._lock:
            verifier, challenge = _pkce_pair()
            state = _b64url(secrets.token_bytes(24))

            event = threading.Event()
            server, port = self._create_callback_server(event)
            redirect_uri = f"http://localhost:{port}/auth/callback"

            auth_url = self._build_auth_url(challenge, redirect_uri, state, workspace_id)

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            logger.info(f"OAuth server started on port {port}")

            try:
                if open_browser:
                    webbrowser.open(auth_url)

                logger.info("Waiting for OAuth callback...")

                if not event.wait(timeout_seconds):
                    if prompt_for_redirect is None:
                        logger.warning("OAuth callback timeout")
                        raise TimeoutError("Timed out waiting for OpenAI OAuth callback")
                    else:
                        manual = prompt_for_redirect(auth_url)
                        params = self._parse_manual_callback(manual)
                else:
                    params = dict(getattr(server, "result", {}) or {})
            finally:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
                try:
                    thread.join(timeout=2)
                except Exception:
                    pass

            if params.get("state") != state:
                raise ValueError("OAuth callback state mismatch")

            if params.get("error"):
                detail = str(params.get("error_description") or params["error"])
                raise RuntimeError(f"OpenAI OAuth failed: {detail}")

            code = str(params.get("code") or "").strip()
            if not code:
                raise ValueError("OAuth callback did not include a code")

            tokens_data = self._exchange_code_for_tokens(code, redirect_uri, verifier)
            record = self._build_auth_record(tokens_data)
            self._save_auth_record(record)

            logger.info("OpenAI OAuth login successful")
            return True

    def refresh(self) -> bool:
        """Refresh the access token."""
        auth = self._load_auth_record()
        if not auth or not auth.tokens.refresh_token:
            raise ValueError("No refresh token available")

        refreshed = self._refresh_token(auth.tokens.refresh_token)
        record = self._build_auth_record(refreshed)
        self._save_auth_record(record)
        return True

    def ensure_access_token(self, refresh_if_needed: bool = True) -> Optional[str]:
        """Get a valid access token."""
        auth = self._load_auth_record()
        if not auth:
            return None

        access_token = auth.tokens.access_token
        refresh_token = auth.tokens.refresh_token

        if refresh_if_needed and refresh_token and (not access_token or _token_expired(access_token)):
            self.refresh()
            auth = self._load_auth_record()
            access_token = auth.tokens.access_token if auth else None

        return access_token

    def get_status(self) -> OAuthStatus:
        """Get current OAuth status."""
        auth = self._load_auth_record()

        if not auth or not auth.tokens.access_token:
            return OAuthStatus(authenticated=False)

        access_token = auth.tokens.access_token
        id_token = auth.tokens.id_token

        token_expires_at = None
        if access_token and _token_expired(access_token):
            refresh_token = auth.tokens.refresh_token
            if refresh_token:
                try:
                    self.refresh()
                    auth = self._load_auth_record()
                    if auth:
                        access_token = auth.tokens.access_token
                        id_token = auth.tokens.id_token
                except Exception as e:
                    logger.warning(f"Token refresh failed: {e}")

        return OAuthStatus(
            authenticated=bool(access_token),
            email=_extract_email(id_token) if id_token else "",
            organization_id=auth.tokens.organization_id,
            project_id=auth.tokens.project_id,
            token_expires_at=token_expires_at,
        )

    def logout(self) -> None:
        """Clear OAuth credentials."""
        if self.auth_path.exists():
            self.auth_path.unlink()
        self.reset_instance()


_oauth_manager: Optional[OpenAIOAuthManager] = None


def get_oauth_manager() -> OpenAIOAuthManager:
    """Get the OAuth manager singleton."""
    global _oauth_manager
    if _oauth_manager is None:
        _oauth_manager = OpenAIOAuthManager()
    return _oauth_manager
