"""apphost — run one App as its own supervised process (§04c `process`).

    python -m pantheon.apphost --app-id shell --workdir /workspace/proj \\
        [--service-name NAME] [--id-hash HASH] [--set key=value ...]

This is the shim that runs a ToolSet-backed App as a process: resolve the
App's manifest in the registry, import entry.backend (`module:Class`),
construct it with the arguments its placement implies, and hand it to
`ToolSet.run()` — the existing remote worker path. The host handles setup
failure, SIGTERM/SIGINT, admission stop, accepted-call drain and cleanup.
The fleet runner owns the process lifecycle; NATS credentials arrive via environment,
injected per-instance by whoever spawned us.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from pantheon.utils.log import logger


def _construct_kwargs(app_id: str, requires: list[str], workdir: str) -> dict:
    """Constructor arguments implied by the App's placement contract.

    Mirrors (and will eventually replace) the endpoint's
    `_prepare_toolset_args` special cases: workspace-bound toolsets take a
    working directory; file management and transfer call it `path`.
    """
    kwargs: dict = {}
    if "fs:workspace" in requires or "proc" in requires:
        if app_id in {"file-manager", "file-transfer"}:
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
    # Immutable App releases are restarted by their supervisor. The legacy
    # worker re-exec RPC kills unrelated host processes and cannot be exposed
    # from an ordinary App, including through a constructor override.
    kwargs["allow_in_place_restart"] = False
    toolset = cls(service_name, **kwargs)
    logger.info(f"[apphost] {args.app_id} ({cls.__name__}) starting "
                f"as service {service_name!r}, workdir={workdir}")
    from pantheon.apps.host_lifecycle import serve_toolset
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    handlers = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous = signal.getsignal(sig)
            try:
                loop.add_signal_handler(sig, stop.set)
                handlers[sig] = (previous, True)
            except NotImplementedError:
                # Windows event loops do not support add_signal_handler.
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
                handlers[sig] = (previous, False)
        await serve_toolset(toolset, remote=not args.no_remote, stop=stop)
    finally:
        for sig, (previous, registered) in handlers.items():
            if registered:
                loop.remove_signal_handler(sig)
            signal.signal(sig, previous)


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
