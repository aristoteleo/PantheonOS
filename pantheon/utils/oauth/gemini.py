"""
Gemini CLI OAuth support.

Imports or manages Gemini CLI OAuth credentials and exposes helpers for
the same Code Assist auth/project discovery flow omicclaw uses.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..log import logger

GOOGLE_OAUTH_CLIENT_ID_KEYS = (
    "OPENCLAW_GEMINI_OAUTH_CLIENT_ID",
    "GEMINI_CLI_OAUTH_CLIENT_ID",
)
GOOGLE_OAUTH_CLIENT_SECRET_KEYS = (
    "OPENCLAW_GEMINI_OAUTH_CLIENT_SECRET",
    "GEMINI_CLI_OAUTH_CLIENT_SECRET",
)
GOOGLE_REDIRECT_URI = "http://localhost:8085/oauth2callback"
GOOGLE_CALLBACK_PORT = 8085
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
GOOGLE_CODE_ASSIST_ENDPOINT_PROD = "https://cloudcode-pa.googleapis.com"
GOOGLE_CODE_ASSIST_ENDPOINT_DAILY = "https://daily-cloudcode-pa.sandbox.googleapis.com"
GOOGLE_CODE_ASSIST_ENDPOINT_AUTOPUSH = "https://autopush-cloudcode-pa.sandbox.googleapis.com"
GOOGLE_CODE_ASSIST_ENDPOINTS = (
    GOOGLE_CODE_ASSIST_ENDPOINT_PROD,
    GOOGLE_CODE_ASSIST_ENDPOINT_DAILY,
    GOOGLE_CODE_ASSIST_ENDPOINT_AUTOPUSH,
)
CODE_ASSIST_ENDPOINT = f"{GOOGLE_CODE_ASSIST_ENDPOINT_PROD}/v1internal:loadCodeAssist"
GOOGLE_TIER_FREE = "free-tier"
GOOGLE_TIER_LEGACY = "legacy-tier"
GOOGLE_TIER_STANDARD = "standard-tier"
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
)
DEFAULT_VERTEX_LOCATION = "us-central1"

AUTH_DIR = Path.home() / ".pantheon" / "oauth"
AUTH_FILE = AUTH_DIR / "gemini_cli.json"
GEMINI_CLI_AUTH = Path.home() / ".gemini" / "oauth_creds.json"

_GOOGLE_CLIENT_ID_RE = re.compile(r"(\d+-[a-z0-9]+\.apps\.googleusercontent\.com)")
_GOOGLE_CLIENT_SECRET_RE = re.compile(r"(GOCSPX-[A-Za-z0-9_-]+)")


class GeminiCliOAuthError(RuntimeError):
    """Raised when Gemini CLI OAuth login, import, or refresh fails."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_bytes(32).hex()
    challenge = _b64url(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _normalize_expires_at(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        epoch = float(value)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        return int(epoch)
    text = str(value).strip()
    if not text:
        return None
    try:
        epoch = float(text)
        if epoch > 1_000_000_000_000:
            epoch /= 1000.0
        return int(epoch)
    except Exception:
        pass
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return None


def token_expired(expires_at: object, skew_seconds: int = 300) -> bool:
    epoch = _normalize_expires_at(expires_at)
    if not epoch:
        return True
    return time.time() >= (float(epoch) - skew_seconds)


def _resolve_env(keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def _find_binary_on_path(name: str) -> Path | None:
    path_env = str(os.environ.get("PATH") or "")
    if not path_env:
        return None
    exts = (".cmd", ".bat", ".exe", "") if os.name == "nt" else ("",)
    for directory in path_env.split(os.pathsep):
        if not directory:
            continue
        for ext in exts:
            candidate = Path(directory) / f"{name}{ext}"
            if candidate.exists():
                try:
                    return candidate.resolve()
                except Exception:
                    return candidate
    return None


def _candidate_gemini_cli_dirs(binary_path: Path) -> list[Path]:
    resolved = binary_path.resolve()
    binary_dir = resolved.parent
    candidates = [
        resolved.parent.parent,
        resolved.parent / "node_modules" / "@google" / "gemini-cli",
        binary_dir / "node_modules" / "@google" / "gemini-cli",
        binary_dir.parent / "node_modules" / "@google" / "gemini-cli",
        binary_dir.parent / "lib" / "node_modules" / "@google" / "gemini-cli",
    ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).replace("\\", "/").lower() if os.name == "nt" else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _find_file(root: Path, filename: str, depth: int = 10) -> Path | None:
    if depth <= 0 or not root.exists() or not root.is_dir():
        return None
    try:
        for entry in root.iterdir():
            if entry.is_file() and entry.name == filename:
                return entry
            if entry.is_dir() and not entry.name.startswith("."):
                found = _find_file(entry, filename, depth - 1)
                if found is not None:
                    return found
    except Exception:
        return None
    return None


def extract_gemini_cli_credentials() -> tuple[str, str | None] | None:
    gemini_path = _find_binary_on_path("gemini")
    if gemini_path is None:
        return None

    content = ""
    for root in _candidate_gemini_cli_dirs(gemini_path):
        search_paths = [
            root / "node_modules" / "@google" / "gemini-cli-core" / "dist" / "src" / "code_assist" / "oauth2.js",
            root / "node_modules" / "@google" / "gemini-cli-core" / "dist" / "code_assist" / "oauth2.js",
        ]
        for search_path in search_paths:
            if search_path.exists():
                try:
                    content = search_path.read_text(encoding="utf-8")
                except Exception:
                    content = ""
                if content:
                    break
        if content:
            break
        found = _find_file(root, "oauth2.js", depth=10)
        if found is not None:
            try:
                content = found.read_text(encoding="utf-8")
            except Exception:
                content = ""
            if content:
                break

    if not content:
        return None

    client_id_match = _GOOGLE_CLIENT_ID_RE.search(content)
    client_secret_match = _GOOGLE_CLIENT_SECRET_RE.search(content)
    if not client_id_match:
        return None
    secret = client_secret_match.group(1) if client_secret_match else None
    return client_id_match.group(1), secret


def resolve_oauth_client_config() -> tuple[str, str | None]:
    env_client_id = _resolve_env(GOOGLE_OAUTH_CLIENT_ID_KEYS)
    env_client_secret = _resolve_env(GOOGLE_OAUTH_CLIENT_SECRET_KEYS)
    if env_client_id:
        return env_client_id, env_client_secret

    extracted = extract_gemini_cli_credentials()
    if extracted is not None:
        return extracted[0], extracted[1]

    raise GeminiCliOAuthError(
        "Gemini CLI not found. Install it first, or set GEMINI_CLI_OAUTH_CLIENT_ID."
    )


def _fetch_user_email(access_token: str) -> str:
    try:
        resp = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.is_success:
            payload = resp.json()
            if isinstance(payload, dict):
                return str(payload.get("email") or "").strip()
    except Exception:
        pass
    return ""


def _resolve_env_project() -> str:
    return str(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        or ""
    ).strip()


def _resolve_platform() -> str:
    if os.name == "nt":
        return "WINDOWS"
    try:
        if os.uname().sysname.lower() == "darwin":  # type: ignore[attr-defined]
            return "MACOS"
    except Exception:
        pass
    return "PLATFORM_UNSPECIFIED"


def _is_vpc_sc_affected(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    details = error.get("details")
    if not isinstance(details, list):
        return False
    for item in details:
        if isinstance(item, dict) and item.get("reason") == "SECURITY_POLICY_VIOLATED":
            return True
    return False


def _default_tier(allowed_tiers: Any) -> str:
    if not isinstance(allowed_tiers, list) or not allowed_tiers:
        return GOOGLE_TIER_LEGACY
    for tier in allowed_tiers:
        if isinstance(tier, dict) and tier.get("isDefault"):
            return str(tier.get("id") or GOOGLE_TIER_LEGACY)
    return GOOGLE_TIER_LEGACY


def _poll_operation(endpoint: str, operation_name: str, headers: dict[str, str]) -> dict[str, Any]:
    for _ in range(24):
        time.sleep(5)
        resp = httpx.get(
            f"{endpoint}/v1internal/{operation_name}",
            headers=headers,
            timeout=10,
        )
        if not resp.is_success:
            continue
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("done"):
            return payload
    raise GeminiCliOAuthError("Operation polling timeout")


def _proxy_style_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "x-goog-api-client": "gl-python/pantheon",
        "Accept": "application/json",
    }


def _load_code_assist_proxy_style(access_token: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{GOOGLE_CODE_ASSIST_ENDPOINT_PROD}/v1internal:loadCodeAssist",
        headers=_proxy_style_headers(access_token),
        json={
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        },
        timeout=10,
    )
    if not resp.is_success:
        raise GeminiCliOAuthError(f"loadCodeAssist failed: {resp.status_code} {resp.reason_phrase}")
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def discover_google_project(access_token: str) -> str:
    env_project = _resolve_env_project()
    try:
        load_assist = _load_code_assist_proxy_style(access_token)
        gcp_managed = bool(load_assist.get("gcpManaged"))
        project_value = load_assist.get("cloudaicompanionProject")
        if not gcp_managed:
            if isinstance(project_value, str) and project_value.strip():
                return project_value.strip()
            if isinstance(project_value, dict):
                project_id = str(project_value.get("id") or "").strip()
                if project_id:
                    return project_id
        elif isinstance(project_value, str) and project_value.strip():
            return project_value.strip()
        elif isinstance(project_value, dict):
            project_id = str(project_value.get("id") or "").strip()
            if project_id:
                return project_id
    except Exception as exc:
        logger.warning("gemini_cli_oauth_proxy_load_code_assist_failed error=%s", exc)

    platform_name = _resolve_platform()
    metadata = {
        "ideType": "ANTIGRAVITY",
        "platform": platform_name,
        "pluginType": "GEMINI",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "gl-python/pantheon",
        "Client-Metadata": json.dumps(metadata),
    }
    load_body: dict[str, Any] = {"metadata": dict(metadata)}
    if env_project:
        load_body["cloudaicompanionProject"] = env_project
        load_body["metadata"]["duetProject"] = env_project

    data: dict[str, Any] = {}
    active_endpoint = GOOGLE_CODE_ASSIST_ENDPOINT_PROD
    load_error: Exception | None = None
    for endpoint in GOOGLE_CODE_ASSIST_ENDPOINTS:
        try:
            resp = httpx.post(
                f"{endpoint}/v1internal:loadCodeAssist",
                headers=headers,
                json=load_body,
                timeout=10,
            )
            if not resp.is_success:
                payload = None
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if _is_vpc_sc_affected(payload):
                    data = {"currentTier": {"id": GOOGLE_TIER_STANDARD}}
                    active_endpoint = endpoint
                    load_error = None
                    break
                load_error = GeminiCliOAuthError(
                    f"loadCodeAssist failed: {resp.status_code} {resp.reason_phrase}"
                )
                continue
            payload = resp.json()
            if isinstance(payload, dict):
                data = payload
            active_endpoint = endpoint
            load_error = None
            break
        except Exception as exc:
            load_error = exc if isinstance(exc, Exception) else GeminiCliOAuthError("loadCodeAssist failed")

    has_load_code_assist_data = bool(
        data.get("currentTier")
        or data.get("cloudaicompanionProject")
        or data.get("allowedTiers")
    )
    if not has_load_code_assist_data and load_error is not None:
        if env_project:
            return env_project
        raise GeminiCliOAuthError(str(load_error))

    current_tier = data.get("currentTier")
    current_project = data.get("cloudaicompanionProject")
    if current_tier:
        if isinstance(current_project, str) and current_project:
            return current_project
        if isinstance(current_project, dict):
            project_id = str(current_project.get("id") or "").strip()
            if project_id:
                return project_id
        if env_project:
            return env_project
        raise GeminiCliOAuthError(
            "This account requires GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID to be set."
        )

    tier_id = _default_tier(data.get("allowedTiers")) or GOOGLE_TIER_FREE
    if tier_id != GOOGLE_TIER_FREE and not env_project:
        raise GeminiCliOAuthError(
            "This account requires GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID to be set."
        )

    onboard_body: dict[str, Any] = {
        "tierId": tier_id,
        "metadata": dict(metadata),
    }
    if tier_id != GOOGLE_TIER_FREE and env_project:
        onboard_body["cloudaicompanionProject"] = env_project
        onboard_body["metadata"]["duetProject"] = env_project

    onboard_resp = httpx.post(
        f"{active_endpoint}/v1internal:onboardUser",
        headers=headers,
        json=onboard_body,
        timeout=10,
    )
    if not onboard_resp.is_success:
        raise GeminiCliOAuthError(
            f"onboardUser failed: {onboard_resp.status_code} {onboard_resp.reason_phrase}"
        )

    operation = onboard_resp.json()
    if not isinstance(operation, dict):
        operation = {}
    if not operation.get("done") and operation.get("name"):
        operation = _poll_operation(active_endpoint, str(operation["name"]), headers)

    project_id = str((((operation.get("response") or {}).get("cloudaicompanionProject") or {}).get("id")) or "").strip()
    if project_id:
        return project_id
    if env_project:
        return env_project
    raise GeminiCliOAuthError(
        "Could not discover or provision a Google Cloud project. "
        "Set GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID."
    )


def resolve_google_oauth_identity(access_token: str) -> dict[str, str]:
    result: dict[str, str] = {}
    email = _fetch_user_email(access_token)
    if email:
        result["email"] = email
    try:
        project_id = discover_google_project(access_token)
    except Exception as exc:
        project_id = _resolve_env_project()
        logger.warning(
            "gemini_cli_oauth_project_discovery_failed error=%s fallback_project=%s",
            exc,
            bool(project_id),
        )
    if project_id:
        result["project_id"] = project_id
    return result


def exchange_code_for_tokens(*, code: str, code_verifier: str) -> dict[str, Any]:
    client_id, client_secret = resolve_oauth_client_config()
    body: dict[str, Any] = {
        "client_id": client_id,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    if client_secret:
        body["client_secret"] = client_secret

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Accept": "*/*",
            "User-Agent": "google-api-nodejs-client/9.15.1",
        },
        timeout=30,
    )
    if not resp.is_success:
        raise GeminiCliOAuthError(f"Token exchange failed: {resp.text[:300]}")
    data = resp.json()
    access_token = str(data.get("access_token") or "").strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    expires_in = data.get("expires_in")
    if not refresh_token:
        raise GeminiCliOAuthError("No refresh token received. Please try again.")
    if not access_token:
        raise GeminiCliOAuthError("OAuth token exchange returned no access token")
    try:
        expires_seconds = int(expires_in)
    except Exception as exc:
        raise GeminiCliOAuthError("OAuth token exchange returned invalid expiry") from exc
    identity = resolve_google_oauth_identity(access_token)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + expires_seconds - 300,
        "email": str(identity.get("email") or "").strip(),
        "project_id": str(identity.get("project_id") or "").strip(),
    }


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    client_id, client_secret = resolve_oauth_client_config()
    body: dict[str, Any] = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client_secret:
        body["client_secret"] = client_secret

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Accept": "*/*",
            "User-Agent": "google-api-nodejs-client/9.15.1",
        },
        timeout=30,
    )
    if not resp.is_success:
        raise GeminiCliOAuthError(f"Google token refresh failed: {resp.text[:300]}")
    data = resp.json()
    access_token = str(data.get("access_token") or "").strip()
    next_refresh = str(data.get("refresh_token") or refresh_token).strip()
    if not access_token:
        raise GeminiCliOAuthError("Google token refresh returned no access token")
    try:
        expires_seconds = int(data.get("expires_in"))
    except Exception as exc:
        raise GeminiCliOAuthError("Google token refresh returned invalid expiry") from exc
    return {
        "access_token": access_token,
        "refresh_token": next_refresh,
        "expires_at": int(time.time()) + expires_seconds - 300,
    }


def _normalize_auth_record(record: dict[str, Any]) -> dict[str, Any]:
    auth = dict(record)
    tokens = dict(auth.get("tokens") or {})
    expires_at = _normalize_expires_at(
        tokens.get("expires_at")
        or tokens.get("expires")
        or tokens.get("expiry")
    )
    if expires_at:
        tokens["expires_at"] = expires_at
    access_token = str(tokens.get("access_token") or tokens.get("access") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or tokens.get("refresh") or "").strip()
    email = str(tokens.get("email") or "").strip()
    project_id = str(tokens.get("project_id") or tokens.get("projectId") or "").strip()
    auth["tokens"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "email": email,
        "project_id": project_id,
    }
    auth["provider"] = "gemini_cli"
    if "last_refresh" not in auth or not auth.get("last_refresh"):
        auth["last_refresh"] = _utc_now()
    return auth


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = "PantheonGeminiOAuth/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/oauth2callback":
            self.send_error(404)
            return
        params = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        self.server.result = params  # type: ignore[attr-defined]
        self.server.event.set()  # type: ignore[attr-defined]
        body = (
            "<html><body><h3>Gemini CLI OAuth complete</h3>"
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


class GeminiCliOAuthManager:
    """Manage Gemini CLI OAuth state."""

    def __init__(self, auth_file: Path | None = None) -> None:
        self.auth_file = auth_file or AUTH_FILE

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

    def load(self) -> dict[str, Any]:
        return _normalize_auth_record(self._load())

    def save(self, auth: dict[str, Any]) -> dict[str, Any]:
        return self._save(_normalize_auth_record(auth))

    def get_tokens(self) -> dict[str, Any]:
        return dict(self.load().get("tokens") or {})

    def get_access_token(self, refresh_if_needed: bool = True) -> str | None:
        tokens = self.get_tokens()
        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        expires_at = tokens.get("expires_at")
        if refresh_if_needed and refresh_token and (not access_token or token_expired(expires_at)):
            try:
                refreshed = self.refresh()
                access_token = str((refreshed.get("tokens") or {}).get("access_token") or "").strip()
            except Exception as e:
                logger.warning(f"[Gemini OAuth] Refresh failed: {e}")
                return None
        return access_token or None

    def get_project_id(self) -> str | None:
        return str(self.get_tokens().get("project_id") or "").strip() or None

    def get_email(self) -> str | None:
        return str(self.get_tokens().get("email") or "").strip() or None

    def is_authenticated(self) -> bool:
        tokens = self.get_tokens()
        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        expires_at = tokens.get("expires_at")
        if access_token and not token_expired(expires_at):
            return True
        return bool(refresh_token)

    def ensure_project_id(self, refresh_if_needed: bool = True) -> str | None:
        project_id = self.get_project_id() or _resolve_env_project()
        if project_id:
            return project_id

        access_token = self.get_access_token(refresh_if_needed=refresh_if_needed)
        if not access_token:
            return None

        identity = resolve_google_oauth_identity(access_token)
        project_id = str(identity.get("project_id") or "").strip()
        email = str(identity.get("email") or "").strip()
        if not project_id and not email:
            return None

        auth = self.load()
        tokens = dict(auth.get("tokens") or {})
        updated_tokens = dict(tokens)
        if project_id:
            updated_tokens["project_id"] = project_id
        if email and not str(tokens.get("email") or "").strip():
            updated_tokens["email"] = email
        auth["tokens"] = updated_tokens
        auth["last_refresh"] = _utc_now()
        self.save(auth)
        return project_id or None

    def ensure_access_token(self, refresh_if_needed: bool = True) -> str | None:
        return self.get_access_token(refresh_if_needed=refresh_if_needed)

    def ensure_access_token_with_import_fallback(
        self,
        *,
        refresh_if_needed: bool = True,
        import_if_missing: bool = True,
    ) -> str | None:
        access_token = self.ensure_access_token(refresh_if_needed=refresh_if_needed)
        if access_token or not import_if_missing:
            return access_token
        imported = self.import_from_gemini_cli()
        if not imported:
            return None
        return self.ensure_access_token(refresh_if_needed=refresh_if_needed)

    def build_api_key_payload(
        self,
        *,
        refresh_if_needed: bool = True,
        import_if_missing: bool = True,
    ) -> str | None:
        access_token = self.ensure_access_token_with_import_fallback(
            refresh_if_needed=refresh_if_needed,
            import_if_missing=import_if_missing,
        )
        if not access_token:
            return None
        auth = self.load()
        tokens = dict((auth.get("tokens") or {}))
        project_id = str(tokens.get("project_id") or "").strip()
        if not project_id:
            try:
                identity = resolve_google_oauth_identity(access_token)
            except Exception as exc:
                logger.warning("gemini_cli_oauth_project_resolution_failed error=%s", exc)
                identity = {}
            resolved_project_id = str(identity.get("project_id") or "").strip()
            resolved_email = str(identity.get("email") or tokens.get("email") or "").strip()
            if resolved_project_id or resolved_email:
                auth["tokens"] = {
                    **tokens,
                    "access_token": access_token,
                    "project_id": resolved_project_id or project_id,
                    "email": resolved_email,
                }
                auth["last_refresh"] = _utc_now()
                auth = self.save(auth)
                tokens = dict((auth.get("tokens") or {}))
                project_id = str(tokens.get("project_id") or "").strip()
        payload = {"token": access_token}
        if project_id:
            payload["projectId"] = project_id
        return json.dumps(payload, separators=(",", ":"))

    def import_from_gemini_cli(self, path: Path | None = None) -> dict[str, Any] | None:
        auth_path = path or GEMINI_CLI_AUTH
        if not auth_path.exists():
            return None
        try:
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        record = {
            "provider": "gemini_cli",
            "tokens": {
                "access_token": payload.get("access_token") or payload.get("access") or payload.get("accessToken"),
                "refresh_token": payload.get("refresh_token") or payload.get("refresh") or payload.get("refreshToken"),
                "expires_at": payload.get("expires_at") or payload.get("expires") or payload.get("expiresAt"),
                "email": payload.get("email"),
                "project_id": payload.get("project_id") or payload.get("projectId"),
            },
            "last_refresh": _utc_now(),
            "source": str(auth_path),
        }
        normalized = _normalize_auth_record(record)
        refresh_token = str((normalized.get("tokens") or {}).get("refresh_token") or "").strip()
        access_token = str((normalized.get("tokens") or {}).get("access_token") or "").strip()
        if access_token and not str((normalized.get("tokens") or {}).get("project_id") or "").strip():
            identity = resolve_google_oauth_identity(access_token)
            if identity.get("project_id") or identity.get("email"):
                normalized["tokens"] = {
                    **dict(normalized.get("tokens") or {}),
                    "project_id": identity.get("project_id") or (normalized.get("tokens") or {}).get("project_id"),
                    "email": identity.get("email") or (normalized.get("tokens") or {}).get("email"),
                }
        if not refresh_token and not access_token:
            return None
        if refresh_token and token_expired((normalized.get("tokens") or {}).get("expires_at")):
            return self.refresh(record=normalized)
        return self.save(normalized)

    def login(
        self,
        *,
        open_browser: bool = True,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        verifier, challenge = _pkce_pair()
        state = verifier
        client_id, _ = resolve_oauth_client_config()
        auth_url = (
            f"{GOOGLE_AUTH_URL}?"
            + urlencode(
                {
                    "client_id": client_id,
                    "response_type": "code",
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "scope": " ".join(GOOGLE_SCOPES),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                    "access_type": "offline",
                    "prompt": "consent",
                }
            )
        )

        server = self._create_callback_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if open_browser:
                try:
                    webbrowser.open(auth_url)
                except Exception:
                    pass
            if not server.event.wait(timeout_seconds):  # type: ignore[attr-defined]
                raise GeminiCliOAuthError("Timed out waiting for Gemini CLI OAuth callback")
            params = dict(getattr(server, "result", {}) or {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        if str(params.get("state") or "").strip() != state:
            raise GeminiCliOAuthError("Gemini CLI OAuth callback state mismatch")
        if params.get("error"):
            raise GeminiCliOAuthError(
                f"Gemini CLI OAuth failed: {params.get('error_description') or params['error']}"
            )
        code = str(params.get("code") or "").strip()
        if not code:
            raise GeminiCliOAuthError("Gemini CLI OAuth callback did not include a code")

        tokens = exchange_code_for_tokens(code=code, code_verifier=verifier)
        record = {
            "provider": "gemini_cli",
            "tokens": tokens,
            "last_refresh": _utc_now(),
        }
        return self.save(record)

    def refresh(self, record: dict[str, Any] | None = None) -> dict[str, Any]:
        auth = _normalize_auth_record(record or self.load())
        tokens = dict(auth.get("tokens") or {})
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not refresh_token:
            raise GeminiCliOAuthError("No refresh token is available for Gemini CLI OAuth")
        refreshed = refresh_access_token(refresh_token)
        project_id = str(tokens.get("project_id") or "").strip()
        email = str(tokens.get("email") or "").strip()
        if not project_id or not email:
            identity = resolve_google_oauth_identity(refreshed["access_token"])
            project_id = project_id or str(identity.get("project_id") or "").strip()
            email = email or str(identity.get("email") or "").strip()
        auth["provider"] = "gemini_cli"
        auth["tokens"] = {
            **refreshed,
            "project_id": project_id,
            "email": email,
        }
        auth["last_refresh"] = _utc_now()
        return self.save(auth)

    def build_google_credentials(self, refresh_if_needed: bool = True):
        from google.oauth2.credentials import Credentials

        tokens = self.get_tokens()
        access_token = self.get_access_token(refresh_if_needed=refresh_if_needed)
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        client_id, client_secret = resolve_oauth_client_config()
        kwargs: dict[str, Any] = {
            "token": access_token,
            "refresh_token": refresh_token or None,
            "token_uri": GOOGLE_TOKEN_URL,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": list(GOOGLE_SCOPES),
        }
        expires_at = _normalize_expires_at(tokens.get("expires_at"))
        if expires_at:
            # google-auth compares against a naive UTC datetime internally.
            # Passing a timezone-aware datetime here triggers
            # "can't compare offset-naive and offset-aware datetimes".
            kwargs["expiry"] = datetime.utcfromtimestamp(expires_at)
        return Credentials(**kwargs)

    def get_client_kwargs(self, refresh_if_needed: bool = True) -> dict[str, Any]:
        credentials = self.build_google_credentials(refresh_if_needed=refresh_if_needed)
        project_id = self.ensure_project_id(refresh_if_needed=refresh_if_needed)
        if not project_id:
            raise GeminiCliOAuthError(
                "Gemini CLI OAuth could not resolve a Code Assist project. "
                "Set GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_PROJECT_ID if your account requires a manual project."
            )
        location = str(os.environ.get("GOOGLE_CLOUD_LOCATION") or DEFAULT_VERTEX_LOCATION).strip()
        return {
            "vertexai": True,
            "credentials": credentials,
            "project": project_id,
            "location": location,
        }

    @staticmethod
    def _create_callback_server() -> ThreadingHTTPServer:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", GOOGLE_CALLBACK_PORT), _OAuthCallbackHandler)
        except OSError as exc:
            raise GeminiCliOAuthError(
                f"Could not start Gemini CLI OAuth callback server on port {GOOGLE_CALLBACK_PORT}"
            ) from exc
        server.event = threading.Event()  # type: ignore[attr-defined]
        server.result = {}  # type: ignore[attr-defined]
        return server
