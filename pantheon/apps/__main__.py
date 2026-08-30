"""CLI for the App registry.

    python -m pantheon.apps list             # the unified table
    python -m pantheon.apps emit [DIR]      # write reflected app.json files
    python -m pantheon.apps schema          # print the manifest JSON Schema
    python -m pantheon.apps check           # reflection vs committed manifests
    python -m pantheon.apps prestart S1,S2  # warm App instances (sandbox boot)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _list() -> None:
    from pantheon.apps.registry import all_apps

    rows = []
    for app in all_apps(workspace=Path.cwd()):
        m = app.manifest
        n_tools = len(m.provides.tools)
        kind = app.catalog.kind if app.catalog else "packaged"
        rows.append((m.id, kind, m.runtime.value, m.surface.value, n_tools,
                     ",".join(m.placement.requires) or "-", app.source))
    w = [max(len(str(r[i])) for r in rows + [("id", "kind", "runtime", "surface", 0, "requires", "source")])
         for i in range(7)]
    header = ("id", "kind", "runtime", "surface", "tools", "requires", "source")
    print("  ".join(str(h).ljust(w[i]) for i, h in enumerate(header)))
    for r in sorted(rows):
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


def _emit(out: str) -> None:
    from pantheon.apps.registry import emit_manifests

    for path in emit_manifests(Path(out)):
        print(path)


def _schema() -> None:
    from pantheon.apps.schema import json_schema

    print(json.dumps(json_schema(), indent=2))


def _check() -> None:
    """check-compat, phase-1 form: current reflection vs committed manifests.

    Exit 1 with a per-app signature diff when they disagree — the reviewable
    unit for 'did I break a promised interface'.
    """
    import tempfile

    from pantheon.apps.registry import emit_manifests

    committed_dir = Path("docs/app-manifests")
    with tempfile.TemporaryDirectory() as td:
        fresh = {p.name: p.read_text() for p in emit_manifests(Path(td))}
    committed = {p.name: p.read_text() for p in committed_dir.glob("*.app.json")}
    from pantheon.apps.reflect import signature_diff
    from pantheon.apps.schema import parse_manifest

    failed = False
    for name in sorted(set(fresh) | set(committed)):
        if name not in committed:
            print(f"NEW app manifest (not committed): {name}")
            failed = True
            continue
        if name not in fresh:
            print(f"REMOVED app (manifest still committed): {name}")
            failed = True
            continue
        if fresh[name] == committed[name]:
            continue
        a = parse_manifest(json.loads(committed[name])).provides.tools
        b = parse_manifest(json.loads(fresh[name])).provides.tools
        problems = signature_diff(a, b) or ["(non-signature manifest change)"]
        print(f"{name}:")
        for p in problems:
            print(f"  {p}")
        failed = True
    if failed:
        print("\nRun `python -m pantheon.apps emit docs/app-manifests` and review the diff.")
        sys.exit(1)
    print(f"OK: {len(fresh)} app manifests match the committed contract.")


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
                resolver._ensure_coords()
                break
            except RuntimeError as e:
                if time.monotonic() >= deadline:
                    print(f"prestart: runner never joined ({e}); giving up")
                    return 1
                await asyncio.sleep(1.0)
        failures = 0
        for service in services:
            if not resolver.resolves(service):
                print(f"prestart: {service}: not in the App catalog, skipped")
                continue
            try:
                sid = await resolver.ensure_instance(service)
                print(f"prestart: {service} -> {sid[:12]}…")
            except Exception as e:
                failures += 1
                print(f"prestart: {service}: {e}")
        await resolver.close()
        return 1 if failures else 0

    sys.exit(asyncio.run(run()))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        _list()
    elif cmd == "emit":
        _emit(sys.argv[2] if len(sys.argv) > 2 else "build/app-manifests")
    elif cmd == "schema":
        _schema()
    elif cmd == "check":
        _check()
    elif cmd == "prestart":
        services = (sys.argv[2] if len(sys.argv) > 2
                    else "shell,file_manager,desktop").split(",")
        wait = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
        _prestart([s.strip() for s in services if s.strip()], wait)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
