"""Sign in to a Pantheon platform, and use its LLM budget from the CLI.

The desktop app and the web app already work this way: you sign in once and
your usage goes against the budget your account has. The CLI did not, so using
it meant bringing your own API key — paying a second time for models the
platform was already paying for.

This is deliberately not a new kind of credential. Signing in exchanges your
password for the same token the web app uses, and the token retrieves the same
LiteLLM key your sandbox runs on. Spend from the CLI lands on the same budget
and in the same usage reporting; there is nothing separate to reconcile.

The token is stored, the password never is.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

CREDENTIALS = Path.home() / ".pantheon" / "credentials.json"

DEFAULT_HUB = "https://app.pantheonos.stanford.edu"


def _hub_url(explicit: str | None = None) -> str:
    return (explicit or os.getenv("PANTHEON_HUB_URL") or DEFAULT_HUB).rstrip("/")


def _post(url: str, payload: dict, token: str | None = None, timeout: int = 30) -> Any:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(url: str, token: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def save_credentials(data: dict) -> None:
    """Write credentials readable only by this user.

    A token that grants spending against someone's budget should not be
    world-readable on a shared machine, and the file is created before it is
    written so there is no window where it is.
    """
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.touch(mode=0o600, exist_ok=True)
    CREDENTIALS.chmod(stat.S_IRUSR | stat.S_IWUSR)
    CREDENTIALS.write_text(json.dumps(data, indent=2))


def load_credentials() -> dict | None:
    try:
        return json.loads(CREDENTIALS.read_text())
    except (OSError, ValueError):
        return None


def login(username: str, password: str, hub: str | None = None) -> dict:
    """Exchange a password for a token, and the token for an LLM key.

    Returns the stored credential record. Raises on failure with the reason
    the platform gave, rather than a generic one — "wrong password" and "that
    host is not a Pantheon Hub" call for different responses from whoever
    typed the command.
    """
    base = _hub_url(hub)
    try:
        auth = _post(f"{base}/api/auth/login", {"username": username, "password": password})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Wrong username or password.") from e
        raise RuntimeError(f"Sign-in failed ({e.code}) at {base}.") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach {base}: {e.reason}") from e

    token = auth.get("access_token")
    if not token:
        raise RuntimeError("The platform did not return a token.")

    try:
        key = _get(f"{base}/api/llm/my-key", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                "This platform does not offer CLI budget access yet "
                "(no /api/llm/my-key). Its Hub needs updating."
            ) from e
        raise RuntimeError(f"Signed in, but could not get an LLM key ({e.code}).") from e

    record = {
        "hub": base,
        "username": username,
        "token": token,
        "api_key": key["api_key"],
        "base_url": key["base_url"],
        "user_id": key.get("user_id"),
    }
    save_credentials(record)
    return record


def logout() -> bool:
    """Forget the stored credentials. True if there were any."""
    if CREDENTIALS.exists():
        CREDENTIALS.unlink()
        return True
    return False


def platform_llm_env() -> dict[str, str]:
    """LLM settings for whatever context this is running in.

    Inside a sandbox there is nothing to sign in to: the Hub already injected
    the user's platform key and proxy address when it created the sandbox, and
    the chatroom is running on them. Asking someone to log in again, on a
    machine that is already authenticated as them, to obtain a key that is
    already sitting in the environment, would be a worse experience than not
    having the feature.

    So the environment wins, and `pantheon login` is for the other case — a
    laptop, where nothing has been injected.

    Empty when neither applies: using your own API key is an ordinary choice
    and should cost nothing.
    """
    injected = os.getenv("LLM_API_KEY")
    injected_base = os.getenv("LLM_API_BASE")
    if injected and injected_base:
        return {
            "OPENAI_API_KEY": injected,
            "OPENAI_BASE_URL": injected_base,
            "LITELLM_BASE_URL": injected_base,
        }

    creds = load_credentials()
    if not creds or not creds.get("api_key"):
        return {}
    return {
        "OPENAI_API_KEY": creds["api_key"],
        "OPENAI_BASE_URL": creds["base_url"],
        "LITELLM_BASE_URL": creds["base_url"],
    }


def where_credentials_came_from() -> str:
    """Which of the two contexts supplied the credentials, for `whoami`."""
    if os.getenv("LLM_API_KEY") and os.getenv("LLM_API_BASE"):
        return "sandbox"
    return "login" if load_credentials() else "none"


def status() -> str:
    """One line describing whether this machine is signed in."""
    source = where_credentials_came_from()
    if source == "sandbox":
        return (
            "Using this workspace's platform budget "
            f"({os.getenv('LLM_API_BASE')}). No sign-in needed here."
        )
    creds = load_credentials()
    if not creds:
        return "Not signed in. Run `pantheon login` to use a platform budget."
    return f"Signed in to {creds['hub']} as {creds.get('username', '?')}."
