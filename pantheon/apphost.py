"""apphost — run one App as its own supervised process (§04c `process`).

    python -m pantheon.apphost --app-id shell --workdir /workspace/proj \\
        [--service-name NAME] [--id-hash HASH] [--set key=value ...]

This is the shim that lets an existing ToolSet run unmodified as an App
process: resolve the App in the registry (catalog toolset app, or a packaged
app whose backend names a `module:Class` ToolSet), construct it with the
arguments its manifest placement implies, and hand it to `ToolSet.run()` —
the same NATS worker path every toolset already uses. The supervisor (today
a human or a test; later the fleet runner) owns the process lifecycle;
NATS credentials arrive via environment, injected per-instance by whoever
spawned us.

Deliberately additive: nothing in the existing runtime calls this. It exists
so P3 can swap supervisors without inventing a new process contract.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path

from pantheon.utils.log import logger


def _construct_kwargs(app_id: str, requires: list[str], workdir: str) -> dict:
    """Constructor arguments implied by the App's placement contract.

    Mirrors (and will eventually replace) the endpoint's
    `_prepare_toolset_args` special cases: workspace-bound toolsets take a
    working directory; the file manager calls it `path`.
    """
    kwargs: dict = {}
    if "fs:workspace" in requires or "proc" in requires:
        if app_id == "file-manager":
            kwargs["path"] = workdir
        else:
            kwargs["workdir"] = workdir
    return kwargs


def _resolve_backend(app_id: str):
    from pantheon.apps.catalog import app_entries

    for entry in app_entries():
        if entry.app_id == app_id:
            module = importlib.import_module(entry.module)
            return getattr(module, entry.class_name), list(entry.requires), entry
    raise SystemExit(f"apphost: unknown app id {app_id!r} "
                     f"(known: {', '.join(e.app_id for e in app_entries())})")


async def _run(args) -> None:
    cls, requires, entry = _resolve_backend(args.app_id)
    workdir = str(Path(args.workdir).resolve())
    kwargs = _construct_kwargs(args.app_id, requires, workdir)
    for pair in args.set or []:
        key, _, value = pair.partition("=")
        kwargs[key] = value
    service_name = args.service_name or args.app_id
    if args.id_hash:
        # Stable service-id seed; rides the constructor into _worker_kwargs,
        # same as every existing service (generate_service_id ignores names).
        kwargs["id_hash"] = args.id_hash
    toolset = cls(service_name, **kwargs)
    logger.info(f"[apphost] {args.app_id} ({entry.class_name}) starting "
                f"as service {service_name!r}, workdir={workdir}")
    # --no-remote: construct + run_setup, then exit — the smoke path tests and
    # supervisors use to validate an app boots without needing a bus.
    await toolset.run(remote=not args.no_remote)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pantheon.apphost", description=__doc__)
    parser.add_argument("--app-id", required=True, help="App id from the registry")
    parser.add_argument("--workdir", default=".", help="Workspace directory for fs-bound apps")
    parser.add_argument("--service-name", default=None)
    parser.add_argument("--id-hash", default=None,
                        help="Stable service-id seed (as the hub assigns)")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Extra constructor argument (repeatable)")
    parser.add_argument("--no-remote", action="store_true",
                        help="Setup only, no bus registration (boot smoke test)")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
