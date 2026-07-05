"""Session-derived fleet credentials for the local backend.

Instead of holding a static ``pbk_`` bearer key, a logged-in local backend can
exchange its platform session (the store-login token) for a short-lived,
fleet-scoped JWT from the hub's ``POST /api/fleet/credential`` and use that as
``FLEET_KEY``. The controller validates the JWT via the hub (``--hub-url``) and
derives the user's fleet from it — so the static key never has to sit in the
backend's environment. The credential is refreshed before it expires.

Opt-in / backward-compatible: a session cred is fetched only when no static
``pbk_`` ``FLEET_KEY`` is set (or ``FLEET_PREFER_SESSION_CRED`` is truthy) AND the
backend is logged in. Otherwise any existing key is left untouched, so current
setups are unchanged.
"""

from __future__ import annotations

import os

import httpx
from loguru import logger


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _store_credentials() -> tuple[str | None, str | None]:
    """(session token, hub url) from the env (hosted runtimes) or the store-login
    file (local ``pantheon store login``)."""
    token = os.environ.get("PANTHEON_STORE_TOKEN")
    hub = os.environ.get("PANTHEON_HUB_URL")
    if token:
        return token, hub
    try:
        from pantheon.store.auth import StoreAuth

        a = StoreAuth()
        if a.is_logged_in:
            return a.token, (hub or a.hub_url)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[fleet-session] store auth unavailable: {e}")
    return None, hub


def use_session_cred() -> bool:
    """Whether the backend should derive its fleet key from the session rather than
    rely on a static one. A static ``pbk_`` key present ⇒ keep it (back-compat)."""
    if _truthy(os.environ.get("FLEET_PREFER_SESSION_CRED")):
        return True
    return not os.environ.get("FLEET_KEY", "").startswith("pbk_")


async def fetch_fleet_session_key() -> tuple[str | None, int]:
    """Exchange the platform session for a short-lived fleet key and publish it to
    the environment (``FLEET_KEY``, and ``FLEET_CONTROLLER_URL`` if the hub returns
    one and it isn't already set). Returns ``(key, ttl_seconds)``, or ``(None, 0)``
    when a session cred isn't applicable or can't be obtained — in which case any
    existing key is left in place (the caller stays on the static key)."""
    if not use_session_cred():
        return None, 0
    token, hub = _store_credentials()
    if not token or not hub:
        logger.debug("[fleet-session] not logged in / no hub — keeping any static FLEET_KEY")
        return None, 0
    url = hub.rstrip("/") + "/api/fleet/credential"
    try:
        async with httpx.AsyncClient(timeout=10.0) as cx:
            r = await cx.post(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            logger.warning(
                f"[fleet-session] {url} -> {r.status_code}; keeping any static FLEET_KEY"
            )
            return None, 0
        data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[fleet-session] fetch failed: {e}; keeping any static FLEET_KEY")
        return None, 0
    key = data.get("fleet_key")
    if not key:
        return None, 0
    os.environ["FLEET_KEY"] = key
    ctrl = data.get("controller_url")
    if ctrl and not os.environ.get("FLEET_CONTROLLER_URL"):
        os.environ["FLEET_CONTROLLER_URL"] = ctrl
    ttl = int(data.get("expires_in") or 0)
    logger.info(
        f"[fleet-session] using a session-derived fleet key "
        f"(ttl {ttl}s, fleet {data.get('fleet_id', '?')})"
    )
    return key, ttl
