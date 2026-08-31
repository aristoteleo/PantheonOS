"""The packaged-app backend supervisor, end to end through real subprocesses.

A fake packaged app is installed into a temp workspace scope; app_call must
spawn its backend under app_runtime.py, dispatch registered methods, refuse
unregistered ones from the handshake table, answer ctx.serve with the
injected data server, and persist ctx.state across a backend restart.
"""

import asyncio
import json
from pathlib import Path

import pytest

from apps.desktop.app_supervisor import AppSupervisor

BACKEND = '''
def register(ctx):
    @ctx.method
    def echo(text: str = ""):
        """Echo the text back."""
        ctx.log(f"echoing {text!r}")
        return {"echoed": text, "app": ctx.app_id}

    @ctx.method
    def remember(key: str, value=None):
        if value is not None:
            ctx.state.set(key, value)
        return {"value": ctx.state.get(key)}

    @ctx.method
    async def serve_me(path: str):
        url = await ctx.serve(path)
        return {"url": url}

    @ctx.method
    def crash():
        import sys
        sys.exit(3)
'''


def _install(root: Path, app_id: str = "fakeapp") -> Path:
    app_dir = root / app_id
    (app_dir / "backend").mkdir(parents=True)
    (app_dir / "app.json").write_text(json.dumps({
        "id": app_id, "name": "Fake", "version": "1.0", "apiVersion": 2,
        "kind": "service", "surface": "dom",
        "entry": {"frontend": "frontend/main.js", "backend": "backend/__init__.py"},
    }))
    (app_dir / "backend" / "__init__.py").write_text(BACKEND)
    return app_dir


@pytest.fixture()
def sup(tmp_path):
    workspace = tmp_path / "ws"
    scope = workspace / ".pantheon" / "apps"
    scope.mkdir(parents=True)
    _install(scope)
    served: list[str] = []

    async def serve(path: str) -> str:
        served.append(path)
        return f"http://data/{Path(path).name}"

    s = AppSupervisor(workspace=workspace, roots=[(scope, "workspace")], serve=serve)
    s.served = served
    return s


@pytest.mark.asyncio
async def test_call_and_registry(sup):
    apps = sup.scan()
    assert [a["id"] for a in apps] == ["fakeapp"]
    assert apps[0]["state"] == "registered"

    out = await sup.call("fakeapp", "echo", {"text": "hi"}, 15)
    assert out == {"echoed": "hi", "app": "fakeapp"}

    entry = sup.entries["fakeapp"]
    assert entry.state == "ready"
    assert set(entry.methods) == {"echo", "remember", "serve_me", "crash"}
    # methods_info carries signatures for the Interfaces view
    info = {m["name"]: m for m in entry.methods_info}
    assert info["echo"]["doc"] == "Echo the text back."
    assert info["echo"]["params"][0]["name"] == "text"
    await sup.shutdown()


@pytest.mark.asyncio
async def test_unknown_method_refused_from_table(sup):
    await sup.call("fakeapp", "echo", {}, 15)
    with pytest.raises(RuntimeError, match="registers no method 'nope'"):
        await sup.call("fakeapp", "nope", {}, 15)
    with pytest.raises(RuntimeError, match="no app 'ghost' is installed"):
        await sup.call("ghost", "echo", {}, 15)
    await sup.shutdown()


@pytest.mark.asyncio
async def test_ctx_serve_answered_by_desktop(sup):
    out = await sup.call("fakeapp", "serve_me", {"path": "/data/plot.png"}, 15)
    assert out == {"url": "http://data/plot.png"}
    assert sup.served == ["/data/plot.png"]
    await sup.shutdown()


@pytest.mark.asyncio
async def test_state_survives_restart_and_crash_reports_reason(sup):
    await sup.call("fakeapp", "remember", {"key": "k", "value": 42}, 15)
    with pytest.raises(RuntimeError, match="exited \\(code 3\\)"):
        await sup.call("fakeapp", "crash", {}, 15)
    # next call respawns (after backoff) and the JSON state file is intact
    out = await sup.call("fakeapp", "remember", {"key": "k"}, 15)
    assert out == {"value": 42}
    await sup.shutdown()
