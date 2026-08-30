"""The full P3 loop, locally: brain-side AppClient → fleet runner (real Go,
dev mode) → apphost process → tool call over the bus.

Needs go + a local NATS with JetStream; skipped where either is missing.
This is the integration seam the endpoint removal rides on — if this test
holds, the ChatRoom can obtain a working toolset service without any
endpoint involved.
"""

import asyncio
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FLEET_SRC = Path("/Users/weizexu/Projects/PantheonOS-fleet-app/fleet")
NATS_PORT = 42431
FLEET_ID = "e2etest"
NODE_ID = "node-e2e"

pytestmark = [
    pytest.mark.skipif(shutil.which("go") is None, reason="go toolchain required"),
    pytest.mark.skipif(not FLEET_SRC.is_dir(), reason="fleet source worktree required"),
    pytest.mark.asyncio,
]


def _nats_server_bin() -> str:
    # nats-server-bin installs the binary into the venv's bin/, next to python
    cand = Path(sys.executable).parent / "nats-server"
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    found = shutil.which("nats-server")
    if found:
        return found
    raise RuntimeError("nats-server binary not found")


async def test_app_start_to_tool_call(tmp_path, monkeypatch):
    # the test process itself dials the instance via pantheon.remote, which
    # reads NATS_SERVERS from the environment
    monkeypatch.setenv("NATS_SERVERS", f"nats://127.0.0.1:{NATS_PORT}")
    env = dict(os.environ, NATS_SERVERS=f"nats://127.0.0.1:{NATS_PORT}", PYTHONPATH=str(REPO))
    procs: list[subprocess.Popen] = []
    try:
        procs.append(subprocess.Popen(
            [_nats_server_bin(), "-p", str(NATS_PORT), "-js",
             "-sd", str(tmp_path / "js")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        await asyncio.sleep(1.0)

        # the Go runner, dev mode (no controller), data plane off for speed
        procs.append(subprocess.Popen(
            ["go", "run", "./cmd/fleet", "up",
             "--nats", f"nats://127.0.0.1:{NATS_PORT}", "--fleet", FLEET_ID,
             "--name", NODE_ID,
             "--workdir", str(tmp_path), "--no-dataplane",
             "--state-dir", str(tmp_path / "state"), "--kind", "sandbox"],
            cwd=str(FLEET_SRC), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))

        import nats

        nc = await nats.connect(f"nats://127.0.0.1:{NATS_PORT}")
        try:
            from pantheon.apps.client import AppClient
            from pantheon.apps.spec import apphost_spec
            from pantheon.remote import connect_remote

            # the runner registers itself in the JetStream KV registry; its
            # node id is generated from the state-dir identity, so discover it
            node_id = None
            node_rec = None
            js = nc.jetstream()
            for _ in range(120):
                try:
                    kv = await js.key_value(f"FLEET_{FLEET_ID}_NODES")
                    keys = await kv.keys()
                    if keys:
                        node_id = keys[0]
                        entry = await kv.get(node_id)
                        node_rec = json.loads(entry.value.decode())
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            if node_id is None:
                out = procs[1].stdout.read() if procs[1].stdout else ""
                pytest.fail(f"runner never registered; runner output:\n{out[-2000:]}")

            # the new registration fields are on the wire
            assert node_rec["kind"] == "sandbox", node_rec.get("kind")
            assert "proc" in node_rec["capability"].get("caps", []), node_rec["capability"]

            client = AppClient(nc, FLEET_ID)
            for _ in range(30):
                if await client.ping(node_id, timeout=1.0):
                    break
                await asyncio.sleep(0.5)
            else:
                pytest.fail("runner registered but never answered ping")

            spec = apphost_spec("shell", user_seed="e2e-user", workdir=str(tmp_path),
                                env={"NATS_SERVERS": f"nats://127.0.0.1:{NATS_PORT}",
                                     "PYTHONPATH": str(REPO)})
            resp = await client.start(node_id, spec)
            assert resp.get("ok"), resp

            # the instance's service becomes dialable
            remote = None
            for _ in range(60):
                try:
                    remote = await asyncio.wait_for(
                        connect_remote(spec["service_id"]), timeout=1.0)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert remote is not None, "apphost service never registered on the bus"

            result = await asyncio.wait_for(
                remote.invoke("run_command", {"command": "echo p3-loop-works"}),
                timeout=30)
            assert "p3-loop-works" in json.dumps(result, default=str), result

            instances = await client.list(node_id)
            mine = [i for i in instances if i["app_id"] == "shell"]
            assert mine and mine[0]["health"] in ("starting", "healthy"), instances

            resp = await client.stop(node_id, "shell")
            assert resp.get("ok"), resp
        finally:
            await nc.close()
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
