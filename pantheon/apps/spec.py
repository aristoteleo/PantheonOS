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

import os
import sys
from pathlib import Path

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
    # Go builtin opt-in (§04c): apps named in PANTHEON_APPS_GO_BUILTIN run
    # inside the fleet runner itself — no command line, no python on the
    # node. Stays an env opt-in until check-compat parity certifies each app
    # (then the catalog's runtime flips and this env becomes the override).
    go_builtin = {
        a.strip() for a in os.environ.get("PANTHEON_APPS_GO_BUILTIN", "").split(",")
        if a.strip()
    }
    if app_id in go_builtin:
        return {
            "app_id": app_id,
            "scope": scope,
            "version": version,
            "service_id": generate_service_id(seed),
            "runtime": "builtin",
            "command": [],
            "dir": workdir,
            "env": {},
        }
    # The apphost process must find pantheon regardless of its own cwd (the
    # workdir) or whatever relative PYTHONPATH the caller inherited — inject
    # this pantheon's own location, absolute, ahead of anything given.
    import pantheon as _pantheon

    pantheon_root = str(Path(_pantheon.__file__).resolve().parent.parent)
    merged_env = dict(env or {})
    existing_pp = merged_env.get("PYTHONPATH", "")
    parts = [pantheon_root] + [p for p in existing_pp.split(os.pathsep) if p and p != pantheon_root]
    merged_env["PYTHONPATH"] = os.pathsep.join(parts)
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
        "env": merged_env,
    }
