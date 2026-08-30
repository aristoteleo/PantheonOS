"""Building an app_start spec — the control plane's half of P3.

The fleet runner supervises whatever concrete process spec it is sent
(fleet/internal/apps); resolving an App's manifest into that spec happens
HERE, next to the registry. For a python App the command line is the
pantheon.apphost invocation; a Go builtin will swap the command without the
caller noticing.

The wire shape mirrors fleet's proto.AppCommand exactly — the cross-language
boundary is this JSON.
"""

from __future__ import annotations

import sys

from pantheon.apps.catalog import app_entries
from pantheon.utils.misc import generate_service_id


def instance_service_seed(user_seed: str, app_id: str, scope: str = "app") -> str:
    """The stable id-hash seed for one App instance.

    Same family as every existing service: generate_service_id(seed) ignores
    names, so distinct seeds are what keep instances on distinct subjects
    (the chatroom/-endpoint suffix precedent).
    """
    if scope == "app":
        return f"{user_seed}-app-{app_id}"
    return f"{user_seed}-app-{app_id}-{scope}"


def apphost_spec(
    app_id: str,
    *,
    user_seed: str,
    workdir: str,
    scope: str = "app",
    python: str | None = None,
    env: dict[str, str] | None = None,
    version: str = "",
) -> dict:
    """An app_start payload (proto.AppCommand) for a catalog App.

    Args:
        app_id: A catalog App id (the registry refuses unknown ids).
        user_seed: The user's stable id-hash (as the hub assigns).
        workdir: Workspace directory for fs-bound apps.
        python: Interpreter for the apphost process (default: this one).
        env: Extra per-instance environment (e.g. scoped NATS creds).
    """
    known = {e.app_id for e in app_entries()}
    if app_id not in known:
        raise ValueError(f"unknown app id {app_id!r} (known: {sorted(known)})")
    seed = instance_service_seed(user_seed, app_id, scope)
    return {
        "app_id": app_id,
        "scope": scope,
        "version": version,
        "service_id": generate_service_id(seed),
        "command": [
            python or sys.executable,
            "-m", "pantheon.apphost",
            "--app-id", app_id,
            "--workdir", workdir,
            "--id-hash", seed,
        ],
        "dir": workdir,
        "env": dict(env or {}),
    }
