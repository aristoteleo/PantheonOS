"""The unified App registry (P1): one table for everything that is an App.

Two sources today, deliberately read-only in this phase — the runtime keeps
running exactly as before, this registry only DESCRIBES it:

  * toolset apps  — the catalog's triage + a manifest reflected live from
    each class's @tool markers (same-source with what workers register);
  * packaged apps — app.json / atrium.json directories under the install
    scopes (the same scopes the desktop discovers).

The frontend's built-in headed apps stay declared in pantheon-ui's
registry.ts; folding them in happens when the shell registers as a frontend
node (P2), not by duplicating their table here.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path

from pantheon.apps.catalog import CatalogEntry, app_entries
from pantheon.apps.reflect import reflect_toolset_class
from pantheon.apps.schema import (
    MANIFEST_NAMES,
    AppManifest,
    Entry,
    Placement,
    Provides,
    Runtime,
    Surface,
    parse_manifest,
)
from pantheon.utils.log import logger


@dataclass
class RegisteredApp:
    manifest: AppManifest
    source: str            # "toolset" | "packaged"
    dir: str | None = None  # packaged apps: the install directory
    scope: str | None = None
    catalog: CatalogEntry | None = None


def _toolset_class(entry: CatalogEntry) -> type:
    module = importlib.import_module(entry.module)
    return getattr(module, entry.class_name)


def toolset_app(entry: CatalogEntry) -> RegisteredApp:
    """Reflect one catalog entry into a headless App manifest."""
    cls = _toolset_class(entry)
    from pantheon import __version__ as pantheon_version

    manifest = AppManifest(
        id=entry.app_id,
        name=entry.class_name.removesuffix("ToolSet"),
        version=pantheon_version,
        description=entry.description or None,
        surface=Surface.headless,
        runtime=Runtime(entry.runtime),
        entry=Entry(backend=f"{entry.module}:{entry.class_name}"),
        provides=Provides(tools=reflect_toolset_class(cls)),
        placement=Placement(requires=list(entry.requires), prefer=list(entry.prefer)),
    )
    return RegisteredApp(manifest=manifest, source="toolset", catalog=entry)


def toolset_apps() -> list[RegisteredApp]:
    out = []
    for entry in app_entries():
        try:
            out.append(toolset_app(entry))
        except Exception as e:  # a broken import must not hide the rest
            logger.error(f"[apps] reflect failed for {entry.class_name}: {e}")
    return out


def default_scope_roots(workspace: Path | None = None) -> list[tuple[Path, str]]:
    """The packaged-app install scopes, mirroring the desktop's resolution
    order (workspace > user). The builtin scope joins when factory apps ship."""
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
    seen: set[str] = set()
    out: list[RegisteredApp] = []
    for root, scope in scope_roots:
        if not root.is_dir():
            continue
        for app_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            data = None
            for name in MANIFEST_NAMES:
                mf = app_dir / name
                if mf.is_file():
                    try:
                        data = json.loads(mf.read_text(encoding="utf-8"))
                    except Exception as e:
                        logger.warning(f"[apps] unreadable manifest {mf}: {e}")
                    break
            if data is None:
                continue
            try:
                manifest = parse_manifest(data)
            except Exception as e:
                logger.warning(f"[apps] invalid manifest in {app_dir}: {e}")
                continue
            if manifest.id in seen:  # scope precedence: earlier root wins
                continue
            seen.add(manifest.id)
            out.append(
                RegisteredApp(manifest=manifest, source="packaged", dir=str(app_dir), scope=scope)
            )
    return out


def all_apps(workspace: Path | None = None) -> list[RegisteredApp]:
    apps = toolset_apps()
    ids = {a.manifest.id for a in apps}
    for app in packaged_apps(default_scope_roots(workspace)):
        if app.manifest.id in ids:
            logger.warning(
                f"[apps] packaged app '{app.manifest.id}' shadows a builtin toolset app; skipped"
            )
            continue
        apps.append(app)
    return apps


def emit_manifests(out_dir: Path) -> list[Path]:
    """Write each toolset app's reflected app.json for inspection/diffing.

    CI can commit these and diff on PRs — a changed tool signature then shows
    up in review as a manifest change, which is the whole point of §06.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for app in toolset_apps():
        path = out_dir / f"{app.manifest.id}.app.json"
        path.write_text(
            app.manifest.model_dump_json(indent=2, exclude_defaults=True) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written
