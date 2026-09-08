"""Local real NATS/fleet/AppInstanceResolver transport for Desktop acceptance.

Run with the Python environment used for app backends. Nothing is mocked: the
HTTP adapter only exposes ToolsetProxy calls and relays real NATS streams to a
separate browser test viewport. See README.md for dependencies and commands.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
from urllib.parse import urlsplit
import uuid


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Dedicated scratch directory; never a user workspace")
    parser.add_argument("--runtime", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--port", type=int, default=48180)
    parser.add_argument("--ui-url", default="http://localhost:5173")
    parser.add_argument("--fleet-bin", default=os.environ.get("FLEET_BIN") or shutil.which("fleet"), required=False)
    parser.add_argument("--nats-bin", default=os.environ.get("NATS_BIN") or shutil.which("nats-server"), required=False)
    args = parser.parse_args()
    args.root, args.runtime = args.root.resolve(), args.runtime.resolve()
    if args.root == args.runtime or args.runtime.is_relative_to(args.root):
        parser.error("--root must be a dedicated scratch directory, not a repository or its parent")
    for name in ("fleet_bin", "nats_bin"):
        binary = getattr(args, name)
        if not binary or not Path(binary).is_file():
            parser.error(f"Provide --{name.replace('_', '-')} (executable not found)")
        setattr(args, name, str(Path(binary).resolve()))
    ui = urlsplit(args.ui_url)
    if ui.scheme not in ("http", "https") or not ui.netloc:
        parser.error("--ui-url must be the Vite server URL")
    args.ui_origin = f"{ui.scheme}://{ui.netloc}"
    return args


async def run(args: argparse.Namespace) -> None:
    from aiohttp import web

    root, work, repo = args.root, args.root / "workspace", args.runtime
    root.mkdir(parents=True, exist_ok=True)
    work.mkdir(exist_ok=True)
    previous = root / "runtime.json"
    if previous.exists():
        old_pid = json.loads(previous.read_text()).get("pid")
        if old_pid:
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(f"An audit server already owns {root} (pid {old_pid})")
    (root / ".desktop-audit").write_text("Isolated Desktop acceptance workspace\n")
    nport, tag = free_port(), "appaudit" + uuid.uuid4().hex[:8]
    # A fresh run-specific state directory cannot adopt a prior fleet's state.
    state_dir = root / "fleet-state" / tag
    env = dict(os.environ, NATS_SERVERS=f"nats://127.0.0.1:{nport}",
               NATS_ENABLE_JETSTREAM="false", NATS_SUBJECT_PREFIX=tag,
               PYTHONPATH=os.pathsep.join(filter(None, [str(repo), os.environ.get("PYTHONPATH")])),
               PANTHEON_USER_SEED=tag, PANTHEON_FLEET_STATE_DIR=str(state_dir),
               FLEET_NODE_CAPS="proc,fs:workspace,dom,display")
    os.environ.update({k: v for k, v in env.items() if k.startswith(("NATS_", "PANTHEON_"))})
    sys.path.insert(0, str(repo))
    processes, logs, history = [], [], []
    runner = nc = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        commands = [
            ("nats", [args.nats_bin, "-a", "127.0.0.1", "-p", str(nport), "-js", "-sd", str(root / "jetstream" / tag)]),
            ("fleet", [args.fleet_bin, "up", "--nats", env["NATS_SERVERS"], "--fleet", tag,
                       "--name", "audit", "--workdir", str(work), "--no-dataplane",
                       "--state-dir", str(state_dir), "--kind", "sandbox"]),
        ]
        for name, command in commands:
            log = (root / f"{name}.log").open("w")
            logs.append(log)
            processes.append(subprocess.Popen(command, cwd=repo, env=env, stdout=log,
                                              stderr=subprocess.STDOUT, start_new_session=True))
            await asyncio.sleep(1)
            if stop.is_set():
                return
        for _ in range(90):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError(f"NATS/fleet exited during startup; inspect {root}/*.log")
            if (state_dir / "runtime.json").exists():
                break
            if stop.is_set():
                return
            await asyncio.sleep(.5)
        else:
            raise TimeoutError(f"Fleet did not become ready; inspect {root / 'fleet.log'}")

        import nats
        from pantheon.apps.proxy import ToolsetProxy
        from pantheon.apps.resolver import AppInstanceResolver

        nc = await nats.connect(env["NATS_SERVERS"])
        resolver = AppInstanceResolver.from_env(workdir=str(work))
        proxies, locks, queues = {}, {}, {}

        async def invoke(method, params, toolset):
            if toolset not in proxies:
                async with locks.setdefault(toolset, asyncio.Lock()):
                    if toolset not in proxies:
                        service = await resolver.ensure_instance(toolset)
                        proxies[toolset] = ToolsetProxy.from_toolset(service)
            result = await asyncio.wait_for(proxies[toolset].invoke(method, params), 180)
            history.append({"method": method, "toolset": toolset,
                            "success": result.get("success") if isinstance(result, dict) else None})
            return result

        async def on_stream(message):
            stream = message.subject.split(".pantheon.stream.", 1)[-1]
            data = json.loads(message.data)
            for queue in list(queues.values()):
                queue.put_nowait({"stream": stream, "message": data})

        await nc.subscribe(f"{tag}.pantheon.stream.>", cb=on_stream)

        @web.middleware
        async def cors(request, handler):
            try:
                response = web.Response() if request.method == "OPTIONS" else await handler(request)
            except web.HTTPException:
                raise
            except Exception as error:
                response = web.json_response({"error": f"{type(error).__name__}: {error}"}, status=400)
            response.headers.update({"Access-Control-Allow-Origin": args.ui_origin,
                                     "Access-Control-Allow-Headers": "*",
                                     "Access-Control-Allow-Methods": "GET,POST,OPTIONS"})
            return response

        async def rpc(request):
            value = await request.json()
            method = value.get("method_name", value.get("method"))
            toolset = value.get("toolset_name", value.get("toolset", "desktop")) or "desktop"
            return web.json_response(await invoke(method, value.get("args", {}), toolset))

        async def events(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                                   "Access-Control-Allow-Origin": args.ui_origin})
            await response.prepare(request)
            ident, queue = uuid.uuid4().hex, asyncio.Queue()
            queues[ident] = queue
            try:
                await response.write(b": connected\n\n")
                while not stop.is_set():
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                        await response.write(("data:" + json.dumps(event) + "\n\n").encode())
                    except asyncio.TimeoutError:
                        await response.write(b": keepalive\n\n")
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                queues.pop(ident, None)
            return response

        async def meta(_request):
            return web.json_response({"workspace": str(work), "root": str(root), "tag": tag, "audit": True})

        app = web.Application(middlewares=[cors], client_max_size=32 * 1024 * 1024)
        app.router.add_post("/rpc", rpc)
        app.router.add_get("/events", events)
        app.router.add_get("/meta", meta)
        runner = web.AppRunner(app, shutdown_timeout=2)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", args.port).start()
        metadata = {"pid": os.getpid(), "api": f"http://127.0.0.1:{args.port}", "tag": tag,
                    "workspace": str(work), "processes": [p.pid for p in processes]}
        previous.write_text(json.dumps(metadata, indent=2))
        print("AUDIT_RPC_READY " + json.dumps(metadata), flush=True)
        await stop.wait()
    finally:
        if runner is not None:
            await runner.cleanup()
        if nc is not None:
            await nc.close()
        (root / "rpc-summary.json").write_text(json.dumps(history, indent=2))
        for process in reversed(processes):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in reversed(processes):
            try:
                await asyncio.to_thread(process.wait, timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                await asyncio.to_thread(process.wait)
        for log in logs:
            log.close()
        (root / "stopped.json").write_text(json.dumps({"stopped": True, "child_pids": [p.pid for p in processes]}))


if __name__ == "__main__":
    asyncio.run(run(arguments()))
