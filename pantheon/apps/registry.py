"""The unified App registry: discovery by manifest, everywhere.

Every App is a directory whose root carries its definition — app.json.
There are only two places such directories live, and they are read the
same way:

  * builtin apps  — pantheon/apps/builtin/<dir>/app.json, shipped with the
    image (first-party; the store's git-forge repos will eventually replace
    this vendoring);
  * packaged apps — app.json / atrium.json directories under the install
    scopes (workspace > user), exactly as the desktop discovers them.

The manifest is the source of truth for identity, kind, runtime, placement
and interfaces. The tools face inside it is kept honest by reflection:
`python -m pantheon.apps check` diffs each manifest's provides.tools
against the live @tool markers of its entry.backend class, and `emit`
refreshes them in place. A builtin app whose manifest lies fails CI, not
discovery.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path

from pantheon.apps.reflect import reflect_toolset_class
from pantheon.apps.schema import (
    MANIFEST_NAMES,
    AppManifest,
    parse_manifest,
)
from pantheon.utils.log import logger

#: Where the first-party App directories live.
BUILTIN_ROOT = Path(__file__).resolve().parent / "builtin"


@dataclass
class RegisteredApp:
    manifest: AppManifest
    source: str            # "builtin" | "packaged"
    dir: str | None = None
    scope: str | None = None

    @property
    def service_type(self) -> str:
        return service_type_of(self.manifest)


def service_type_of(manifest: AppManifest) -> str:
    """The snake_case service name templates use ('file_manager'): the app
    id with dashes underscored. One derivation for every runtime — a Go
    builtin has no Python class to derive from."""
    return manifest.id.replace("-", "_")


def backend_class(manifest: AppManifest) -> type:
    """Import the class named by entry.backend ('module:Class')."""
    backend = manifest.entry.backend
    if not backend or ":" not in backend:
        raise ValueError(f"{manifest.id}: entry.backend is not module:Class ({backend!r})")
    module, cls = backend.rsplit(":", 1)
    return getattr(importlib.import_module(module), cls)


def builtin_apps() -> list[RegisteredApp]:
    """Scan the builtin App directories. A broken manifest is logged and
    skipped — one bad app must not take discovery down."""
    out: list[RegisteredApp] = []
    for app_dir in sorted(BUILTIN_ROOT.iterdir()):
        manifest_path = app_dir / "app.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = parse_manifest(json.loads(manifest_path.read_text()))
        except Exception as e:
            logger.error(f"[apps] invalid builtin manifest {manifest_path}: {e}")
            continue
        out.append(RegisteredApp(manifest=manifest, source="builtin", dir=str(app_dir)))
    return out


def by_service_type() -> dict[str, RegisteredApp]:
    """Template service names ('shell', 'file_manager') -> builtin App.

    Headless apps only — a headed app (frontend-only manifest) is not a
    service anyone binds by name."""
    return {app.service_type: app
            for app in builtin_apps()
            if app.manifest.entry.backend or app.manifest.runtime.value == "builtin"}


def by_app_id() -> dict[str, RegisteredApp]:
    return {app.manifest.id: app for app in builtin_apps()}


def verify_interfaces(manifest: AppManifest) -> list[str]:
    """Interface members that do not exist in the tools face (empty = OK).

    An interface naming a tool the App no longer has is a broken promise —
    the exact situation §06's contract checks exist to catch.
    """
    have = {t.name for t in manifest.provides.tools}
    return [
        f"{i.name}@{i.version}:{member}"
        for i in manifest.provides.interfaces
        for member in i.tools
        if member not in have
    ]


def reflected_tools(manifest: AppManifest):
    """The live tools face of a manifest's backend class (embedded/process
    apps). Builtin-runtime apps have no Python backend — their Go
    registration is held to the manifest by the e2e parity tests — and
    headed apps have no tools face at all; neither is reflectable."""
    return reflect_toolset_class(backend_class(manifest))


def verifiable(manifest: AppManifest) -> bool:
    """Whether check/emit can hold this manifest to a Python backend."""
    return bool(manifest.entry.backend) and manifest.runtime.value != "builtin"


def refresh_manifest(app_dir: Path) -> bool:
    """Rewrite provides.tools in one app.json from reflection (emit).

    Everything else in the file — identity, kind, placement, interfaces —
    is human-owned definition and left untouched. Returns True if the file
    changed."""
    path = app_dir / "app.json"
    data = json.loads(path.read_text())
    manifest = parse_manifest(data)
    tools = reflected_tools(manifest)
    fresh = [json.loads(t.model_dump_json(exclude_defaults=True)) for t in tools]
    changed = data.get("provides", {}).get("tools") != fresh
    data.setdefault("provides", {})["tools"] = fresh
    manifest2 = parse_manifest(data)
    missing = verify_interfaces(manifest2)
    if missing:
        raise ValueError(f"{manifest.id}: interface members not in tools face: {missing}")
    if changed:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return changed


def default_scope_roots(workspace: Path | None = None) -> list[tuple[Path, str]]:
    """The packaged-app install scopes, mirroring the desktop's resolution
    order (workspace > user)."""
    roots: list[tuple[Path, str]] = []
    if workspace is not None:
        roots.append((Path(workspace) / ".pantheon" / "apps", "workspace"))
    roots.append((Path.home() / ".pantheon" / "apps", "user"))
    return roots


def packaged_apps(scope_roots: list[tuple[Path, str]]) -> list[RegisteredApp]:
    """Scan install scopes for app dirs; first manifest name found wins.

    Invalid manifests are skipped with a log line, not raised — an app the
    user half-copied must not take discovery down (same policy as the shell).
    """
    seen: dict[str, RegisteredApp] = {}
    for root, scope in scope_roots:
        if not root.is_dir():
            continue
        for app_dir in sorted(root.iterdir()):
            if not app_dir.is_dir():
                continue
            manifest_path = next(
                (app_dir / n for n in MANIFEST_NAMES if (app_dir / n).is_file()), None
            )
            if manifest_path is None:
                continue
            try:
                manifest = parse_manifest(json.loads(manifest_path.read_text()))
            except Exception as e:
                logger.warning(f"[apps] skipping invalid manifest {manifest_path}: {e}")
                continue
            if manifest.id in seen:
                continue  # earlier scope wins (workspace > user)
            seen[manifest.id] = RegisteredApp(
                manifest=manifest, source="packaged", dir=str(app_dir), scope=scope
            )
    return list(seen.values())


def all_apps(workspace: Path | None = None) -> list[RegisteredApp]:
    """Builtin + packaged, with builtin ids unshadowable: a volume app that
    claims a first-party id is dropped (and logged) rather than trusted."""
    builtin = builtin_apps()
    builtin_ids = {a.manifest.id for a in builtin}
    out = list(builtin)
    for app in packaged_apps(default_scope_roots(workspace)):
        if app.manifest.id in builtin_ids:
            logger.warning(
                f"[apps] packaged app at {app.dir} shadows builtin id "
                f"'{app.manifest.id}' — ignored"
            )
            continue
        out.append(app)
    return out
