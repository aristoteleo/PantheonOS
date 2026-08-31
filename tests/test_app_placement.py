"""P5 acceptance: placement across nodes, locally.

Two real Go runners join one fleet — node A (the "sandbox", no display)
and node B (a "machine" with a display). An App whose manifest requires
["display"] must land on B and answer tool calls there; an App with no
requirements stays on the local node. The resolver is the placer — the
caller just names a toolset.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FLEET_SRC = REPO / "fleet"
NATS_PORT = 42433
RUN_TAG = uuid.uuid4().hex[:8]
FLEET_ID = f"p5{RUN_TAG}"
USER_SEED = f"p5-user-{RUN_TAG}"

pytestmark = [
    pytest.mark.skipif(shutil.which("go") is None, reason="go toolchain required"),
    pytest.mark.skipif(not FLEET_SRC.is_dir(), reason="fleet source worktree required"),
    pytest.mark.asyncio,
]


def _nats_server_bin() -> str:
    cand = Path(sys.executable).parent / "nats-server"
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    found = shutil.which("nats-server")
    if found:
        return found
    raise RuntimeError("nats-server binary not found")


def _runner(tmp: Path, name: str, caps: str, kind: str, env: dict) -> subprocess.Popen:
    log = open(tmp / f"{name}.log", "w")
    return subprocess.Popen(
        ["go", "run", "./cmd/fleet", "up",
         "--nats", f"nats://127.0.0.1:{NATS_PORT}", "--fleet", FLEET_ID,
         "--name", name, "--kind", kind, "--caps", caps,
         "--workdir", str(tmp), "--no-dataplane",
         "--state-dir", str(tmp / f"state-{name}")],
        cwd=str(FLEET_SRC), env=env,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def _test_tree(tmp: Path) -> Path:
    """A builtin tree where shell REQUIRES a display and pty requires nothing."""
    tree = tmp / "apps"
    for app in ("shell", "pty"):
        (tree / app).mkdir(parents=True)
        data = json.loads((REPO / "apps" / app / "app.json").read_text())
        if app == "shell":
            data.setdefault("placement", {})["requires"] = ["display"]
        (tree / app / "app.json").write_text(json.dumps(data))
    return tree


async def test_placement_by_requires(tmp_path, monkeypatch):
    monkeypatch.setenv("NATS_SERVERS", f"nats://127.0.0.1:{NATS_PORT}")
    monkeypatch.setenv("NATS_ENABLE_JETSTREAM", "false")
    env = dict(os.environ, NATS_SERVERS=f"nats://127.0.0.1:{NATS_PORT}",
               PYTHONPATH=str(REPO))
    procs: list[subprocess.Popen] = []
    try:
        procs.append(subprocess.Popen(
            [_nats_server_bin(), "-p", str(NATS_PORT), "-js",
             "-sd", str(tmp_path / "js")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True))
        await asyncio.sleep(1.0)

        procs.append(_runner(tmp_path, "node-a", "proc,fs:workspace", "sandbox", env))
        procs.append(_runner(tmp_path, "node-b", "proc,display", "machine", env))

        # wait for BOTH registrations, and learn who is who
        import nats

        nc = await nats.connect(f"nats://127.0.0.1:{NATS_PORT}")
        resolver = None
        try:
            nodes: dict[str, dict] = {}
            js = nc.jetstream()
            for _ in range(180):
                try:
                    kv = await js.key_value(f"FLEET_{FLEET_ID}_NODES")
                    for key in await kv.keys():
                        rec = json.loads((await kv.get(key)).value.decode())
                        nodes[rec["name"]] = rec
                    if {"node-a", "node-b"} <= set(nodes):
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            else:
                logs = "".join((tmp_path / f"{n}.log").read_text(errors="replace")[-800:]
                               for n in ("node-a", "node-b"))
                pytest.fail(f"runners never both registered:\n{logs}")
            node_a = nodes["node-a"]["node_id"]
            node_b = nodes["node-b"]["node_id"]
            assert "display" not in nodes["node-a"]["capability"]["caps"]
            assert "display" in nodes["node-b"]["capability"]["caps"]

            # the test's App tree: shell needs a display, pty needs nothing
            import pantheon.apps.registry as registry
            monkeypatch.setattr(registry, "BUILTIN_ROOT", _test_tree(tmp_path))

            from pantheon.apps.registry import by_service_type
            from pantheon.apps.resolver import AppInstanceResolver

            resolver = AppInstanceResolver(
                FLEET_ID, node_a, USER_SEED, workdir=str(tmp_path))

            # the placer's own verdicts, before any process starts
            placed_shell = await resolver._place(by_service_type()["shell"])
            placed_pty = await resolver._place(by_service_type()["pty"])
            assert placed_shell == node_b, "display requirement must pick node B"
            assert placed_pty == node_a, "no requirements means the local node"

            # and the full chain: ensure lands the instance on B and it answers
            sid = None
            for _ in range(30):
                try:
                    sid = await resolver.ensure_instance("shell")
                    break
                except Exception:
                    await asyncio.sleep(1.0)
            assert sid, "ensure_instance never succeeded"

            from pantheon.apps.client import AppClient

            client = AppClient(nc, FLEET_ID)
            listing = await client.list(node_b)
            on_b = [i for i in listing if i.get("app_id") == "shell"]
            assert on_b, f"shell instance not on node B: {listing}"

            from pantheon.remote import connect_remote

            result = None
            for _ in range(60):
                try:
                    remote = await asyncio.wait_for(connect_remote(sid), timeout=1.0)
                    result = await asyncio.wait_for(
                        remote.invoke("run_command",
                                      {"command": "echo placed-on-b"}),
                        timeout=30)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert result is not None and "placed-on-b" in json.dumps(result), result
        finally:
            try:
                await resolver.close()
            except Exception:
                pass
            await nc.close()
    finally:
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
