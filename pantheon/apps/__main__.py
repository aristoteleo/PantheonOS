"""CLI for the App registry.

    python -m pantheon.apps list             # the unified table
    python -m pantheon.apps emit            # refresh provides.tools in each app.json
    python -m pantheon.apps schema          # print the manifest JSON Schema
    python -m pantheon.apps check           # each app.json's tools face vs reflection
    python -m pantheon.apps prestart S1,S2  # warm App instances (sandbox boot)
    python -m pantheon.apps check-compat OLD NEW   # gate: OLD/NEW = app dir or app.json
    python -m pantheon.apps publish DIR     # check-compat vs last tag, then tag v<version>
    python -m pantheon.apps fork SRC DSTROOT [NEWID]   # copy + git-init an App
    python -m pantheon.apps install SRC DSTROOT        # clone/copy + dependency closure
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _list() -> None:
    from pantheon.apps.registry import all_apps

    rows = []
    for app in all_apps(workspace=Path.cwd()):
        m = app.manifest
        n_tools = len(m.provides.tools)
        rows.append((m.id, m.kind.value, m.runtime.value, m.surface.value, n_tools,
                     ",".join(m.placement.requires) or "-", app.source))
    w = [max(len(str(r[i])) for r in rows + [("id", "kind", "runtime", "surface", 0, "requires", "source")])
         for i in range(7)]
    header = ("id", "kind", "runtime", "surface", "tools", "requires", "source")
    print("  ".join(str(h).ljust(w[i]) for i, h in enumerate(header)))
    for r in sorted(rows):
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


def _emit() -> None:
    """Refresh provides.tools in every builtin app.json from reflection."""
    import json as _json

    from pantheon.apps.registry import (
        BUILTIN_ROOT,
        parse_manifest,
        refresh_manifest,
        verifiable,
    )

    for app_dir in sorted(BUILTIN_ROOT.iterdir()):
        mf = app_dir / "app.json"
        if not mf.is_file():
            continue
        if not verifiable(parse_manifest(_json.loads(mf.read_text()))):
            print(f"skipped  {mf} (no python backend)")
            continue
        changed = refresh_manifest(app_dir)
        print(f"{'updated' if changed else 'fresh  '}  {mf}")


def _schema() -> None:
    from pantheon.apps.schema import json_schema

    print(json.dumps(json_schema(), indent=2))


def _check() -> None:
    """check-compat, manifest-at-the-root form: each builtin app.json's
    provides.tools must equal what its entry.backend class reflects, and
    every promised interface member must exist.

    Exit 1 with a per-app signature diff when they disagree — the reviewable
    unit for 'did I break a promised interface'.
    """
    from pantheon.apps.reflect import signature_diff
    from pantheon.apps.registry import (
        BUILTIN_ROOT,
        builtin_apps,
        reflected_tools,
        verifiable,
        verify_interfaces,
    )

    failed = False
    count = 0
    for app in builtin_apps():
        if not verifiable(app.manifest):
            continue
        count += 1
        try:
            fresh = reflected_tools(app.manifest)
        except Exception as e:
            print(f"{app.manifest.id}: backend unimportable: {e}")
            failed = True
            continue
        problems = signature_diff(app.manifest.provides.tools, fresh)
        problems += [f"broken interface promise: {m}"
                     for m in verify_interfaces(app.manifest)]
        if problems:
            print(f"{app.manifest.id}:")
            for p in problems:
                print(f"  {p}")
            failed = True

    if failed:
        print("\nRun `python -m pantheon.apps emit` and review the diff.")
        sys.exit(1)
    print(f"OK: {count} python-backed app manifests match their code.")


def _prestart(services: list[str], wait: float) -> None:
    """Warm App instances so the first bind doesn't pay the cold start.

    Run by the sandbox entrypoint (background). The local runner joins
    asynchronously, so this waits for its
    runtime.json up to --wait seconds, then ensures each instance. Failures
    are per-service and non-fatal: this is an optimization, the resolver
    still lazy-starts at bind time.
    """
    import asyncio
    import time

    from pantheon.apps.resolver import get_shared_resolver

    resolver = get_shared_resolver()
    if resolver is None:
        print("prestart: resolver not wired (no user seed) — nothing to do")
        return

    async def run() -> int:
        deadline = time.monotonic() + wait
        while True:
            try:
                await resolver._ensure_coords()
                break
            except RuntimeError as e:
                if time.monotonic() >= deadline:
                    print(f"prestart: runner never joined ({e}); giving up")
                    return 1
                await asyncio.sleep(1.0)
        failures = 0
        # The very instances the runtime will ask for: workers bind
        # project-scoped instances rooted in the active layout (room.py),
        # so warming the app-scoped ones just started a SECOND set.
        cwd = os.getcwd()
        scope = resolver.project_scope(cwd)
        for service in services:
            if not resolver.resolves(service):
                print(f"prestart: {service}: not in the App catalog, skipped")
                continue
            try:
                sid = await resolver.ensure_instance(
                    service, scope=scope, workdir=cwd)
                print(f"prestart: {service} -> {sid[:12]}…")
            except Exception as e:
                failures += 1
                print(f"prestart: {service}: {e}")
        await resolver.close()
        return 1 if failures else 0

    sys.exit(asyncio.run(run()))


def _load_manifest_arg(arg: str):
    """An app dir or a manifest file, either way the parsed manifest."""
    from pantheon.apps.schema import parse_manifest

    p = Path(arg)
    if p.is_dir():
        for name in ("app.json", "atrium.json"):
            if (p / name).is_file():
                p = p / name
                break
        else:
            print(f"no app manifest under {arg}")
            sys.exit(2)
    return parse_manifest(json.loads(p.read_text()))


def _check_compat(old_arg: str, new_arg: str) -> None:
    from pantheon.apps.compat import check_compat

    report = check_compat(_load_manifest_arg(old_arg), _load_manifest_arg(new_arg))
    print(report.render())
    sys.exit(0 if report.ok else 1)


def _publish(app_dir: str) -> None:
    from pantheon.apps.versioning import VersioningError, publish

    try:
        tag = publish(Path(app_dir))
        print(f"published {tag}")
    except VersioningError as e:
        print(str(e))
        sys.exit(1)


def _fork(src: str, dst_root: str, new_id: str | None) -> None:
    from pantheon.apps.versioning import VersioningError, fork

    try:
        dst = fork(Path(src), Path(dst_root), new_id)
        print(f"forked to {dst}")
    except VersioningError as e:
        print(str(e))
        sys.exit(1)


def _install(src: str, dst_root: str) -> None:
    from pantheon.apps.versioning import VersioningError, install

    try:
        for p in install(src, Path(dst_root)):
            print(f"installed {p}")
    except VersioningError as e:
        print(str(e))
        sys.exit(1)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        _list()
    elif cmd == "emit":
        _emit()
    elif cmd == "schema":
        _schema()
    elif cmd == "check":
        _check()
    elif cmd == "prestart":
        services = (sys.argv[2] if len(sys.argv) > 2
                    else "shell,file_manager,desktop").split(",")
        wait = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
        _prestart([s.strip() for s in services if s.strip()], wait)
    elif cmd == "check-compat" and len(sys.argv) >= 4:
        _check_compat(sys.argv[2], sys.argv[3])
    elif cmd == "publish" and len(sys.argv) >= 3:
        _publish(sys.argv[2])
    elif cmd == "fork" and len(sys.argv) >= 4:
        _fork(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == "install" and len(sys.argv) >= 4:
        _install(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
