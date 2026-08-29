"""CLI for the App registry.

    python -m pantheon.apps list             # the unified table
    python -m pantheon.apps emit [DIR]      # write reflected app.json files
    python -m pantheon.apps schema          # print the manifest JSON Schema
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


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        _list()
    elif cmd == "emit":
        _emit(sys.argv[2] if len(sys.argv) > 2 else "build/app-manifests")
    elif cmd == "schema":
        _schema()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
