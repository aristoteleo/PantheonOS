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
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FLEET_SRC = Path("/Users/weizexu/Projects/PantheonOS-fleet-app/fleet")
NATS_PORT = 42431
# Unique per run: `go run`'s grandchild survives terminate(), so a leaked
# runner/apphost from an earlier run would re-register into a same-named
# fleet/service on the next run's NATS and answer with a stale workdir.
RUN_TAG = uuid.uuid4().hex[:8]
FLEET_ID = f"e2e{RUN_TAG}"
USER_SEED = f"e2e-user-{RUN_TAG}"
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
    # Production workers run with JetStream disabled — negotiation and
    # discovery must work without the KV store, so the test does too.
    monkeypatch.setenv("NATS_ENABLE_JETSTREAM", "false")
    env = dict(os.environ, NATS_SERVERS=f"nats://127.0.0.1:{NATS_PORT}", PYTHONPATH=str(REPO))
    procs: list[subprocess.Popen] = []
    try:
        # start_new_session: each subprocess leads its own process group, so
        # teardown can killpg and take the go-run grandchild and any apphost
        # children with it (terminate() alone leaks them).
        procs.append(subprocess.Popen(
            [_nats_server_bin(), "-p", str(NATS_PORT), "-js",
             "-sd", str(tmp_path / "js")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True))
        await asyncio.sleep(1.0)

        # the Go runner, dev mode (no controller), data plane off for speed.
        # Output to a file: reading a PIPE after terminate blocks while the
        # go-run grandchild still holds it.
        runner_log = open(tmp_path / "runner.log", "w")
        procs.append(subprocess.Popen(
            ["go", "run", "./cmd/fleet", "up",
             "--nats", f"nats://127.0.0.1:{NATS_PORT}", "--fleet", FLEET_ID,
             "--name", NODE_ID,
             "--workdir", str(tmp_path), "--no-dataplane",
             "--state-dir", str(tmp_path / "state"), "--kind", "sandbox"],
            cwd=str(FLEET_SRC), env=env,
            stdout=runner_log, stderr=subprocess.STDOUT,
            start_new_session=True))

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
                out = (tmp_path / "runner.log").read_text(errors="replace")
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

            spec = apphost_spec("shell", user_seed=USER_SEED, workdir=str(tmp_path),
                                env={"NATS_SERVERS": f"nats://127.0.0.1:{NATS_PORT}",
                                     "PYTHONPATH": str(REPO)})
            resp = await client.start(node_id, spec)
            assert resp.get("ok"), resp

            # the instance's service becomes dialable. With JetStream off,
            # connect_remote has no KV to validate against and returns
            # immediately — readiness is proven by the invoke answering.
            result = None
            for _ in range(60):
                try:
                    remote = await asyncio.wait_for(
                        connect_remote(spec["service_id"]), timeout=1.0)
                    result = await asyncio.wait_for(
                        remote.invoke("run_command",
                                      {"command": "echo p3-loop-works"}),
                        timeout=30)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert result is not None, "apphost service never answered on the bus"
            assert "p3-loop-works" in json.dumps(result, default=str), result

            instances = await client.list(node_id)
            mine = [i for i in instances if i["app_id"] == "shell"]
            assert mine and mine[0]["health"] in ("starting", "healthy"), instances

            resp = await client.stop(node_id, "shell")
            assert resp.get("ok"), resp

            # --- P3 slice 1: the flag-ON binding path -----------------------
            # The exact route create_agents_from_template takes when
            # PANTHEON_APPS_VIA_FLEET is wired: resolver ensures the instance,
            # ToolsetProxy.from_toolset dials it directly — no endpoint alive
            # anywhere in this process tree.
            monkeypatch.setenv("PANTHEON_APPS_VIA_FLEET", "1")
            monkeypatch.setenv("PANTHEON_FLEET_ID", FLEET_ID)
            monkeypatch.setenv("PANTHEON_FLEET_NODE_ID", node_id)
            monkeypatch.setenv("PANTHEON_USER_SEED", USER_SEED)
            from pantheon.apps.resolver import AppInstanceResolver
            from pantheon.endpoint import ToolsetProxy

            resolver = AppInstanceResolver.from_env(workdir=str(tmp_path))
            assert resolver is not None and resolver.resolves("file_manager")
            sid = await resolver.ensure_instance("file_manager")

            proxy = ToolsetProxy.from_toolset(sid)
            (tmp_path / "hello.txt").write_text("via-fleet")
            content = None
            last_err = None
            for _ in range(40):
                try:
                    res = await asyncio.wait_for(
                        proxy.invoke("read_file", {"file_path": "hello.txt"}), timeout=3)
                    content = json.dumps(res, default=str)
                    break
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    await asyncio.sleep(0.5)
            diag = await client.list(node_id)
            if not (content and "via-fleet" in content):
                runner_out = (tmp_path / "runner.log").read_text(errors="replace")
                pytest.fail(
                    f"content={content!r} last_err={last_err} instances={diag}\n"
                    f"--- runner output tail ---\n{runner_out[-3000:]}")
            # --- P3 slice 3: a project-scoped instance is its own process ----
            proj = tmp_path / "projA"
            proj.mkdir()
            (proj / "hello.txt").write_text("in-project")
            scope = resolver.project_scope(str(proj))
            sid_proj = await resolver.ensure_instance(
                "file_manager", scope=scope, workdir=str(proj))
            assert sid_proj != sid  # distinct instance from the app-scope one

            proxy_proj = ToolsetProxy.from_toolset(sid_proj)
            content = None
            for _ in range(40):
                try:
                    res = await asyncio.wait_for(
                        proxy_proj.invoke("read_file", {"file_path": "hello.txt"}),
                        timeout=3)
                    content = json.dumps(res, default=str)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert content and "in-project" in content, content

            # --- Go builtin shell (§04c): no python process, in-runner ------
            # The spec opts in via PANTHEON_APPS_GO_BUILTIN; the runner serves
            # the shell@1 surface itself over appsvc (JSON wire, negotiated
            # from the KV registration's serialization field).
            monkeypatch.setenv("PANTHEON_APPS_GO_BUILTIN", "shell")
            go_spec = apphost_spec("shell", user_seed=USER_SEED + "-go",
                                   workdir=str(tmp_path))
            assert go_spec["runtime"] == "builtin" and go_spec["command"] == []
            resp = await client.start(node_id, go_spec)
            assert resp.get("ok"), resp

            go_proxy = ToolsetProxy.from_toolset(go_spec["service_id"])
            go_res = None
            for _ in range(40):
                try:
                    go_res = await asyncio.wait_for(
                        go_proxy.invoke("run_command",
                                        {"command": "echo go-builtin-works"}),
                        timeout=3)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert go_res and go_res.get("success"), go_res
            assert "go-builtin-works" in go_res["output"], go_res
            assert go_res["status"] == "completed" and go_res["truncated"] is False

            # session persistence across calls (same auto session)
            await go_proxy.invoke("run_command", {"command": "export GOMARK=yes"})
            go_res = await go_proxy.invoke("run_command", {"command": "echo mark=$GOMARK"})
            assert "mark=yes" in go_res["output"], go_res

            # hidden tools answer on the wire (the frontend's contract)
            created = await go_proxy.invoke("new_shell", {})
            assert created["success"] and created["shell_id"]
            in_shell = await go_proxy.invoke("run_command_in_shell", {
                "shell_id": created["shell_id"], "command": "echo manual-go"})
            assert in_shell["success"] and "manual-go" in in_shell["output"]
            closed = await go_proxy.invoke("close_shell",
                                           {"shell_id": created["shell_id"]})
            assert closed["success"], closed

            # parity: the Go list_tools surface matches the Python
            # ShellToolSet's reflected shell face (names + param names/types)
            from pantheon.apps.reflect import reflect_toolset_class
            from pantheon.toolsets.shell import ShellToolSet

            go_tools = await go_proxy.invoke("list_tools", {})
            assert go_tools["success"]
            go_vis = {t["name"]: t for t in go_tools["tools"]}
            py_manifest = reflect_toolset_class(ShellToolSet)
            py_vis = {t.name: t for t in py_manifest if not t.hidden
                      and t.name != "list_tools"}
            assert set(go_vis) == set(py_vis), (set(go_vis), set(py_vis))
            for name, py_tool in py_vis.items():
                go_params = [(i["name"], i["type"]) for i in go_vis[name]["inputs"]]
                py_params = [(p.name, p.type) for p in py_tool.params]
                assert go_params == py_params, (name, go_params, py_params)

            resp = await client.stop(node_id, "shell")
            assert resp.get("ok"), resp

            # --- Go builtin pty: sessions + the frontend's stream protocol --
            import base64 as b64

            monkeypatch.setenv("PANTHEON_APPS_GO_BUILTIN", "pty")
            pty_spec = apphost_spec("pty", user_seed=USER_SEED + "-go",
                                    workdir=str(tmp_path))
            assert pty_spec["runtime"] == "builtin"
            resp = await client.start(node_id, pty_spec)
            assert resp.get("ok"), resp

            pty_proxy = ToolsetProxy.from_toolset(pty_spec["service_id"])
            opened = None
            for _ in range(40):
                try:
                    opened = await asyncio.wait_for(
                        pty_proxy.invoke("pty_open", {"cols": 90, "rows": 28}),
                        timeout=3)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert opened and opened.get("success"), opened
            assert opened["cols"] == 90 and opened["stream_id"].startswith("pty_")
            initial = b64.b64decode(opened["initial_output"])
            assert b"agent operating system" in initial  # the banner

            # frontend contract: pty.data frames arrive on the stream subject
            frames: list[dict] = []
            got_data = asyncio.Event()

            async def on_stream(msg):
                frames.append(json.loads(msg.data.decode()))
                if any(f["data"].get("type") == "pty.data" for f in frames):
                    got_data.set()

            stream_sub = await nc.subscribe(
                f"pantheon.stream.{opened['stream_id']}", cb=on_stream)
            keys = b64.b64encode(b"echo pty-stream-works\r").decode()
            wrote = await pty_proxy.invoke(
                "pty_write", {"session_id": opened["session_id"], "data": keys})
            assert wrote["success"], wrote
            await asyncio.wait_for(got_data.wait(), timeout=10)
            datas = b"".join(
                b64.b64decode(f["data"]["data"]) for f in frames
                if f["data"].get("type") == "pty.data")
            assert b"pty-stream-works" in datas, datas[:200]
            assert frames[0]["type"] == "custom"
            assert frames[0]["session_id"] == opened["stream_id"]
            await stream_sub.unsubscribe()

            # attach replays scrollback; close reaps
            att = await pty_proxy.invoke(
                "pty_attach", {"session_id": opened["session_id"]})
            assert att["success"] and b"pty-stream-works" in b64.b64decode(att["scrollback"])
            closed = await pty_proxy.invoke(
                "pty_close", {"session_id": opened["session_id"]})
            assert closed["success"], closed

            resp = await client.stop(node_id, "pty")
            assert resp.get("ok"), resp

            # --- Go builtin file-manager: the fs core, tree-sitter excluded -
            # The python file-manager instances from the resolver legs hold
            # the (app_id, scope) supervisor slots — stop them first, or the
            # idempotent Start silently keeps the python ones.
            await client.stop(node_id, "file-manager", scope=scope)
            await client.stop(node_id, "file-manager")
            monkeypatch.setenv("PANTHEON_APPS_GO_BUILTIN", "file-manager")
            fm_spec = apphost_spec("file-manager", user_seed=USER_SEED + "-go",
                                   workdir=str(tmp_path))
            assert fm_spec["runtime"] == "builtin"
            resp = await client.start(node_id, fm_spec)
            assert resp.get("ok"), resp

            fm_proxy = ToolsetProxy.from_toolset(fm_spec["service_id"])
            written = None
            for _ in range(40):
                try:
                    written = await asyncio.wait_for(
                        fm_proxy.invoke("write_file", {
                            "file_path": "go/fm.txt", "content": "alpha\nbeta\n"}),
                        timeout=3)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            assert written and written.get("success"), written

            got = await fm_proxy.invoke("read_file", {"file_path": "go/fm.txt"})
            assert got["success"] and got["content"] == "alpha\nbeta\n"
            assert got["total_lines"] == 2 and got["truncated"] is False

            upd = await fm_proxy.invoke("update_file", {
                "file_path": "go/fm.txt", "old_string": "beta", "new_string": "gamma"})
            assert upd["success"] and upd["replacements"] == 1

            patched = await fm_proxy.invoke("apply_patch", {"patch": (
                "--- a/go/fm.txt\n+++ b/go/fm.txt\n@@ -1,2 +1,2 @@\n"
                " alpha\n-gamma\n+delta\n")})
            assert patched["success"], patched
            assert patched["summary"]["modified"] == 1

            found = await fm_proxy.invoke("glob", {"pattern": "**/*.txt"})
            assert found["success"] and any(
                f["path"] == "go/fm.txt" for f in found["files"]), found
            hits = await fm_proxy.invoke("grep", {"pattern": "delta"})
            assert hits["success"] and hits["total_matches"] >= 1, hits

            # tree-sitter faces are excluded: symbol mode answers the python
            # no-tree-sitter error; view_file_outline is not served at all
            sym = await fm_proxy.invoke("read_file", {
                "file_path": "go/fm.txt", "symbol": "X"})
            assert sym == {"success": False,
                           "error": "Code navigation requires tree-sitter"}
            with pytest.raises(Exception, match="not found"):
                await fm_proxy.invoke("view_file_outline", {"file_path": "go/fm.txt"})

            # parity on the served visible subset: signatures match the
            # Python FileManagerToolSet reflection exactly
            from pantheon.toolsets.file import FileManagerToolSet

            fm_tools = await fm_proxy.invoke("list_tools", {})
            go_fm = {t["name"]: t for t in fm_tools["tools"]}
            assert set(go_fm) == {"read_file", "write_file", "update_file",
                                  "apply_patch", "glob", "grep"}, set(go_fm)
            py_fm = {t.name: t for t in reflect_toolset_class(FileManagerToolSet)}
            for name, go_tool in go_fm.items():
                go_params = [(i["name"], i["type"]) for i in go_tool["inputs"]]
                py_params = [(p.name, p.type) for p in py_fm[name].params]
                assert go_params == py_params, (name, go_params, py_params)

            resp = await client.stop(node_id, "file-manager")
            assert resp.get("ok"), resp
            monkeypatch.delenv("PANTHEON_APPS_GO_BUILTIN")
            await resolver.close()
        finally:
            await nc.close()
    finally:
        for p in reversed(procs):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except Exception:
                    p.kill()


async def test_shared_resolver_env_gating(monkeypatch):
    """The shared resolver is None while unwired, builds once when wired."""
    from pantheon.apps import resolver as R

    R.reset_shared_resolver()
    monkeypatch.delenv("PANTHEON_APPS_VIA_FLEET", raising=False)
    assert R.get_shared_resolver() is None

    R.reset_shared_resolver()
    monkeypatch.setenv("PANTHEON_APPS_VIA_FLEET", "1")
    monkeypatch.setenv("PANTHEON_FLEET_ID", "f")
    monkeypatch.setenv("PANTHEON_FLEET_NODE_ID", "n")
    monkeypatch.setenv("PANTHEON_USER_SEED", "s")
    r = R.get_shared_resolver()
    assert r is not None and r.resolves("shell") and not r.resolves("nope")
    assert R.get_shared_resolver() is r  # cached
    R.reset_shared_resolver()


async def test_resolver_lazy_coords_from_runtime_json(tmp_path, monkeypatch):
    """Sandbox wiring: no explicit ids, coordinates read from runtime.json."""
    import json as J

    from pantheon.apps.resolver import AppInstanceResolver

    monkeypatch.setenv("PANTHEON_APPS_VIA_FLEET", "1")
    monkeypatch.delenv("PANTHEON_FLEET_ID", raising=False)
    monkeypatch.delenv("PANTHEON_FLEET_NODE_ID", raising=False)
    monkeypatch.setenv("PANTHEON_USER_SEED", "seed")
    monkeypatch.setenv("PANTHEON_FLEET_STATE_DIR", str(tmp_path))

    r = AppInstanceResolver.from_env(workdir=str(tmp_path))
    assert r is not None  # deferred, not refused

    # the runner hasn't joined yet -> a clear error, caller falls back
    with pytest.raises(RuntimeError, match="not joined"):
        r._ensure_coords()

    (tmp_path / "runtime.json").write_text(
        J.dumps({"node_id": "n_abc", "fleet_id": "f1", "nats_url": "nats://x"}))
    r._ensure_coords()
    assert r._fleet == "f1" and r._node == "n_abc"

    # without a seed the resolver stays off entirely
    monkeypatch.delenv("PANTHEON_USER_SEED", raising=False)
    monkeypatch.delenv("ID_HASH", raising=False)
    assert AppInstanceResolver.from_env() is None


async def test_resolver_circuit_breaker(tmp_path, monkeypatch):
    """A wired-but-unreachable fleet path disables itself after MAX_FAILURES."""
    from pantheon.apps.resolver import AppInstanceResolver

    monkeypatch.setenv("PANTHEON_APPS_VIA_FLEET", "1")
    monkeypatch.setenv("PANTHEON_USER_SEED", "seed")
    monkeypatch.setenv("PANTHEON_FLEET_STATE_DIR", str(tmp_path))
    r = AppInstanceResolver.from_env(workdir=str(tmp_path))
    assert r is not None and r.resolves("shell")
    for _ in range(AppInstanceResolver.MAX_FAILURES):
        with pytest.raises(RuntimeError):
            await r.ensure_instance("shell")  # runtime.json missing -> fails
    assert r._disabled and not r.resolves("shell")


def test_prestart_cli_gives_up_cleanly_without_runner(tmp_path):
    """prestart waits for runtime.json, then exits 1 without raising."""
    env = dict(os.environ, PYTHONPATH=str(REPO),
               PANTHEON_APPS_VIA_FLEET="1", PANTHEON_USER_SEED="seed",
               PANTHEON_FLEET_STATE_DIR=str(tmp_path))
    env.pop("PANTHEON_FLEET_ID", None)
    env.pop("PANTHEON_FLEET_NODE_ID", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pantheon.apps", "prestart", "shell", "2"],
        env=env, cwd=str(REPO), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "runner never joined" in proc.stdout
