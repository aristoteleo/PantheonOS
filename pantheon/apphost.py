"""apphost — run one App as its own supervised process (§04c `process`).

    python -m pantheon.apphost --app-id shell --workdir /workspace/proj \\
        [--service-name NAME] [--id-hash HASH] [--set key=value ...]

This is the shim that runs a ToolSet-backed App as a process: resolve the
App's manifest in the registry, import entry.backend (`module:Class`),
construct it with the arguments its placement implies, and hand it to
`ToolSet.run()` — the NATS worker path every service uses. The fleet runner
owns the process lifecycle; NATS credentials arrive via environment,
injected per-instance by whoever spawned us.
"""

from __future__ import annotations

import argparse
import asyncio
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
    from pantheon.apps.registry import backend_class, builtin_apps

    apps = {a.manifest.id: a for a in builtin_apps()}
    app = apps.get(app_id)
    if app is None:
        raise SystemExit(f"apphost: unknown app id {app_id!r} "
                         f"(known: {', '.join(sorted(apps))})")
    if app.manifest.runtime.value == "builtin":
        raise SystemExit(f"apphost: {app_id!r} is a runner builtin — the fleet "
                         f"runner serves it in-process, there is no python backend")
    return backend_class(app.manifest), list(app.manifest.placement.requires), app


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
    logger.info(f"[apphost] {args.app_id} ({cls.__name__}) starting "
                f"as service {service_name!r}, workdir={workdir}")
    # --no-remote: construct + run_setup + cleanup, then exit — the smoke path
    # tests and supervisors use to validate an app boots without needing a bus.
    # (The bus path runs cleanup itself on worker shutdown; the embed path
    # doesn't, and an app holding real resources — an HTTP gateway, kernels —
    # would otherwise never let the process exit.)
    await toolset.run(remote=not args.no_remote)
    if args.no_remote:
        await toolset.cleanup()


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
