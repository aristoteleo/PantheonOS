"""
OpenAI OAuth 2.0 Provider for PantheonOS.

Security Notes:
- JWT tokens are base64-decoded for payload extraction but signature is NOT verified.
  This is a common simplification for client-side token inspection. The OAuth flow
  itself provides security via PKCE and HTTPS. Only use tokens from trusted sources.
- Token files are saved with 0o600 permissions (user-only read/write).
- Logout attempts to revoke tokens on OpenAI's server.

Known Risks:
- This implementation reuses OpenAI Codex CLI's OAuth client ID and originator.
  OpenAI does not currently offer public OAuth app registration for third-party tools.
  OpenAI can revoke or restrict this client ID at any time, breaking auth for all users.
  This is an undocumented, unsupported integration path that could change without notice.
- OAuth tokens managed here are account credentials. PantheonOS should not inject them
  into generic OpenAI API SDK calls as a substitute for ``OPENAI_API_KEY``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Callable

import requests

from pantheon.auth.oauth_manager import (
    OAuthProvider,
    OAuthTokens,
    AuthRecord,
    OAuthStatus,
)
from pantheon.utils.log import logger


OPENAI_AUTH_ISSUER = "https://auth.openai.com"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_ORIGINATOR = "pi"
OPENAI_CALLBACK_PORT = 1455
OPENAI_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
OPENAI_OIDC_CONFIG_URL = f"{OPENAI_AUTH_ISSUER}/.well-known/openid-configuration"
_OIDC_CONFIG_CACHE: dict[str, object] = {"value": None, "expires_at": 0.0}
_JWKS_CLIENT_CACHE: dict[str, object] = {"value": None, "expires_at": 0.0}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _decode_jwt_payload_unverified(token: str) -> dict:
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


def _get_oidc_config() -> dict:
    now = time.time()
    cached = _OIDC_CONFIG_CACHE.get("value")
    if isinstance(cached, dict) and now < float(_OIDC_CONFIG_CACHE.get("expires_at", 0.0)):
        return cached

    response = requests.get(OPENAI_OIDC_CONFIG_URL, timeout=10)
    response.raise_for_status()
    config = response.json()
    if not isinstance(config, dict):
        raise RuntimeError("OIDC discovery returned invalid payload")

    _OIDC_CONFIG_CACHE["value"] = config
    _OIDC_CONFIG_CACHE["expires_at"] = now + 3600
    return config


def _get_jwks_client():
    now = time.time()
    cached = _JWKS_CLIENT_CACHE.get("value")
    if cached is not None and now < float(_JWKS_CLIENT_CACHE.get("expires_at", 0.0)):
        return cached

    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError("PyJWT is required for JWT signature verification") from exc

    config = _get_oidc_config()
    jwks_uri = str(config.get("jwks_uri") or "").strip()
    if not jwks_uri:
        raise RuntimeError("OIDC discovery did not include jwks_uri")

    client = jwt.PyJWKClient(jwks_uri)
    _JWKS_CLIENT_CACHE["value"] = client
    _JWKS_CLIENT_CACHE["expires_at"] = now + 3600
    return client


def _decode_jwt_payload_verified(token: str) -> dict:
    if not token:
        return {}

    try:
        import jwt

        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=OPENAI_AUTH_ISSUER,
            options={"verify_aud": False},
        )
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.warning(f"JWT signature verification failed: {exc}")
        return {}


def _decode_jwt_payload(token: str, *, allow_unverified_fallback: bool = False) -> dict:
    payload = _decode_jwt_payload_verified(token)
    if payload:
        return payload
    if allow_unverified_fallback:
        return _decode_jwt_payload_unverified(token)
    return {}


def jwt_auth_claims(token: str) -> dict:
    payload = _decode_jwt_payload(token)
    nested = payload.get("https://api.openai.com/auth")
    return nested if isinstance(nested, dict) else {}


def jwt_org_context(token: str) -> dict:
    claims = jwt_auth_claims(token)
    context = {}
    for key in ("organization_id", "project_id", "chatgpt_account_id"):
        value = str(claims.get(key) or "").strip()
        if value:
            context[key] = value
    return context


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
    payload = _decode_jwt_payload(token, allow_unverified_fallback=True)
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return time.time() >= (float(exp) - skew_seconds)


def _extract_email(token: str) -> str:
    payload = _decode_jwt_payload(token)
    return payload.get("email", "")


def _extract_token_exp(token: str) -> float | None:
    payload = _decode_jwt_payload(token, allow_unverified_fallback=True)
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = "PantheonOAuth/1.0"
    ALLOWED_ORIGINS = {"https://auth.openai.com", "https://openai.com"}

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        
        if origin:
            for allowed in self.ALLOWED_ORIGINS:
                if origin.startswith(allowed):
                    return True
        if referer:
            for allowed in self.ALLOWED_ORIGINS:
                if referer.startswith(allowed):
                    return True
        if not origin and not referer:
            return True
        return False

    def do_GET(self) -> None:
        from urllib.parse import parse_qs, urlparse

        if not self._check_origin():
            self.send_error(403)
            return

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


class OpenAIOAuthProvider:
    """
    OpenAI OAuth provider for Pantheon.
    """

    AUTHORIZATION_ENDPOINT = f"{OPENAI_AUTH_ISSUER}/oauth/authorize"
    TOKEN_ENDPOINT = f"{OPENAI_AUTH_ISSUER}/oauth/token"
    CLIENT_ID = OPENAI_CLIENT_ID
    SCOPE = OPENAI_SCOPE

    _lock = threading.Lock()

    @property
    def name(self) -> str:
        return "openai"

    @property
    def display_name(self) -> str:
        return "OpenAI"

    def __init__(self, auth_path: Optional[Path] = None):
        if auth_path is None:
            auth_path = Path.home() / ".pantheon" / "oauth_openai.json"
        self.auth_path = auth_path

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
            provider="openai",
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
            os.chmod(self.auth_path, stat.S_IRUSR | stat.S_IWUSR)
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
            time.sleep(0.5)

            logger.info(f"OAuth server started on port {port}")
            logger.info(f"Callback URL: {redirect_uri}")

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

    def ensure_access_token_with_codex_fallback(
        self,
        *,
        refresh_if_needed: bool = True,
        import_codex_if_missing: bool = True,
    ) -> Optional[str]:
        """Return a usable access token, importing Codex CLI auth when available."""
        access_token = self.ensure_access_token(refresh_if_needed=refresh_if_needed)
        if access_token or not import_codex_if_missing:
            return access_token

        imported = import_from_codex_cli()
        if not imported:
            return None

        return self.ensure_access_token(refresh_if_needed=refresh_if_needed)

    def build_codex_auth_context(
        self,
        *,
        refresh_if_needed: bool = True,
        import_codex_if_missing: bool = True,
    ) -> Optional[dict]:
        """Build auth context for Codex-specific OAuth calls.

        This is intentionally separate from generic OpenAI API auth. The returned
        context is only meant for the Codex/ChatGPT backend path.
        """
        access_token = self.ensure_access_token_with_codex_fallback(
            refresh_if_needed=refresh_if_needed,
            import_codex_if_missing=import_codex_if_missing,
        )
        if not access_token:
            return None

        auth = self._load_auth_record()
        tokens = auth.tokens if auth else OAuthTokens("", access_token, "")

        return {
            "base_url": f"{OPENAI_CODEX_BASE_URL}/codex",
            "access_token": access_token,
            "account_id": tokens.account_id,
            "organization_id": tokens.organization_id,
            "project_id": tokens.project_id,
        }

    def get_status(self) -> OAuthStatus:
        """Get current OAuth status."""
        auth = self._load_auth_record()

        if not auth or not auth.tokens.access_token:
            return OAuthStatus(authenticated=False, provider="openai")

        access_token = auth.tokens.access_token
        id_token = auth.tokens.id_token

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

        token_expires_at = _extract_token_exp(id_token) if id_token else None
        if token_expires_at is None:
            token_expires_at = _extract_token_exp(access_token) if access_token else None

        return OAuthStatus(
            authenticated=bool(access_token),
            email=_extract_email(id_token) if id_token else "",
            organization_id=auth.tokens.organization_id,
            project_id=auth.tokens.project_id,
            token_expires_at=token_expires_at,
            provider="openai",
        )

    def logout(self) -> None:
        """Clear OAuth credentials and revoke tokens on OpenAI server."""
        auth = self._load_auth_record()

        if auth and auth.tokens.access_token:
            try:
                requests.post(
                    f"{OPENAI_AUTH_ISSUER}/oauth/revoke",
                    data={"token": auth.tokens.access_token},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"Failed to revoke access token: {e}")

            try:
                requests.post(
                    f"{OPENAI_AUTH_ISSUER}/oauth/revoke",
                    data={"token": auth.tokens.refresh_token},
                    timeout=10,
                )
            except Exception as e:
                logger.warning(f"Failed to revoke refresh token: {e}")

        if self.auth_path.exists():
            self.auth_path.unlink()


CODEX_CLI_AUTH_PATH = Path.home() / ".codex" / "auth.json"


def import_from_codex_cli() -> bool:
    """Import authentication from Codex CLI.
    
    Reads the existing Codex CLI authentication and converts it to our format.
    This allows PantheonOS to use Codex CLI's existing login session.
    
    Returns:
        True if import successful, False otherwise
    """
    import json
    from datetime import datetime, timezone
    
    if not CODEX_CLI_AUTH_PATH.exists():
        logger.warning(f"Codex CLI auth file not found: {CODEX_CLI_AUTH_PATH}")
        return False
    
    try:
        with open(CODEX_CLI_AUTH_PATH, "r") as f:
            codex_data = json.load(f)
        
        tokens_data = codex_data.get("tokens", {})
        if not tokens_data:
            logger.warning("Codex CLI auth file has no tokens")
            return False
        
        access_token = tokens_data.get("access_token")
        id_token = tokens_data.get("id_token")
        refresh_token = tokens_data.get("refresh_token")
        
        if not access_token:
            logger.warning("Codex CLI has no access token")
            return False
        
        account_id = tokens_data.get("account_id")
        
        auth_record = AuthRecord(
            provider="openai",
            tokens=OAuthTokens(
                id_token=id_token or "",
                access_token=access_token,
                refresh_token=refresh_token or "",
                account_id=account_id,
            ),
            last_refresh=datetime.now(timezone.utc).isoformat(),
            email=_extract_email(id_token) if id_token else "",
        )
        
        provider = OpenAIOAuthProvider()
        provider._save_auth_record(auth_record)
        
        logger.info(f"Successfully imported Codex CLI authentication")
        return True
        
    except Exception as e:
        logger.error(f"Failed to import Codex CLI auth: {e}")
        return False


def get_openai_oauth_provider() -> OpenAIOAuthProvider:
    """Get the OpenAI OAuth provider."""
    return OpenAIOAuthProvider()
