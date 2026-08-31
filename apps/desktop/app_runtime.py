"""App backend bootstrap — the child half of the desktop supervisor's protocol.

Run BY PATH under whatever interpreter the app's manifest names:

    <interpreter> app_runtime.py --app-dir … --app-id … --workspace … --state-dir …

STDLIB-ONLY, load-bearing: the interpreter is typically a conda analysis env
(``pantheon-base``) in which the ``pantheon`` package does not exist and must
not need to. Everything this file is arrives in this file.

Protocol (line-delimited JSON-RPC 2.0 on stdio):

  child → parent, once:   {"ready": true, "api": 1, "methods": [...]}
  parent → child:         {"id", "method": "invoke", "params": {method, args}}
                          {"method": "shutdown"} / {"method": "ping", "id"}
  child → parent:         responses; and its own AppContext requests
                          ("ctx.serve", "ctx.log"), interleaved on the pipe.

The app's backend package is imported from ``<app-dir>/backend``; its
``register(ctx)`` collects methods via the ``@ctx.method`` decorator. Durable
state is a JSON file under ``--state-dir`` — outside the package directory,
so reinstalls and resyncs cannot eat it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import sys
import traceback
from pathlib import Path


def _out(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class _State:
    """A small KV that survives the process — one JSON file, written whole."""

    def __init__(self, state_dir: Path):
        self._path = state_dir / "state.json"
        try:
            self._data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1))
        tmp.replace(self._path)


class AppContext:
    """The backend's one door to the world (spec §6.3)."""

    def __init__(self, app_id: str, workspace: Path, state_dir: Path, rpc: "_Rpc"):
        self.app_id = app_id
        self.workspace = workspace
        self.state = _State(state_dir)
        self._rpc = rpc
        self._methods: dict[str, object] = {}

    def method(self, fn):
        """Register ``fn`` as a callable backend method. Decorator."""
        self._methods[fn.__name__] = fn
        return fn

    async def serve(self, path) -> str:
        """A data-server URL for a file — answered by the endpoint."""
        res = await self._rpc.request("ctx.serve", {"path": str(path)})
        return res["url"]

    def log(self, message: str) -> None:
        self._rpc.notify("ctx.log", {"message": str(message)})


class _Rpc:
    """The child's side of the pipe: requests up, responses matched back."""

    def __init__(self):
        self._seq = 0
        self._pending: dict[str, asyncio.Future] = {}

    def notify(self, method: str, params: dict) -> None:
        self._seq += 1
        _out({"jsonrpc": "2.0", "id": f"n{self._seq}", "method": method, "params": params})

    async def request(self, method: str, params: dict, timeout_s: float = 60.0):
        self._seq += 1
        rid = f"q{self._seq}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        _out({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            msg = await asyncio.wait_for(fut, timeout_s)
        finally:
            self._pending.pop(rid, None)
        if "error" in msg:
            raise RuntimeError(str((msg["error"] or {}).get("message", method + " failed")))
        return msg.get("result")

    def settle(self, msg: dict) -> bool:
        fut = self._pending.get(str(msg.get("id")))
        if fut and not fut.done():
            fut.set_result(msg)
            return True
        return False


def _load_backend(app_dir: Path):
    """Import ``<app-dir>/backend`` as an isolated module.

    ``_vendor`` (pinned pure-Python deps, spec §3.3) goes FIRST on sys.path so
    the app's pins win inside the app's own process — there is nothing else in
    this process for them to conflict with.
    """
    backend_dir = app_dir / "backend"
    vendor = backend_dir / "_vendor"
    if vendor.is_dir():
        sys.path.insert(0, str(vendor))
    spec = importlib.util.spec_from_file_location(
        f"atrium_app_{app_dir.name}_backend", backend_dir / "__init__.py",
        submodule_search_locations=[str(backend_dir)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", required=True)
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--state-dir", required=True)
    ns = ap.parse_args()

    rpc = _Rpc()
    ctx = AppContext(ns.app_id, Path(ns.workspace), Path(ns.state_dir), rpc)

    try:
        mod = _load_backend(Path(ns.app_dir))
        register = getattr(mod, "register", None)
        if register is None:
            raise RuntimeError("backend/__init__.py defines no register(ctx)")
        out = register(ctx)
        if inspect.isawaitable(out):
            await out
    except Exception as e:  # noqa: BLE001 — the parent needs the reason
        traceback.print_exc()
        _out({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 1

    def _method_info(name, fn):
        """Signature + first doc line, best-effort — the Interfaces UI's food."""
        info = {"name": name, "params": [], "doc": ""}
        try:
            import inspect
            for pname, param in inspect.signature(fn).parameters.items():
                if pname in ("self", "ctx"):
                    continue
                entry = {"name": pname}
                if param.annotation is not inspect.Parameter.empty:
                    ann = param.annotation
                    entry["type"] = getattr(ann, "__name__", None) or str(ann)
                if param.default is not inspect.Parameter.empty:
                    entry["default"] = repr(param.default)
                info["params"].append(entry)
            doc = inspect.getdoc(fn) or ""
            info["doc"] = doc.strip().split("\n")[0][:200]
        except Exception:
            pass
        return info

    _out({
        "ready": True, "api": 1,
        "methods": sorted(ctx._methods),
        # Names alone say nothing; the desktop's Interfaces view shows the
        # agent-callable surface with signatures and one-line docs.
        "methods_info": [_method_info(n, f) for n, f in sorted(ctx._methods.items())],
    })

    async def handle(msg: dict) -> None:
        mid = msg.get("id")
        method = msg.get("method")
        if method == "ping":
            _out({"jsonrpc": "2.0", "id": mid, "result": {}})
            return
        if method != "invoke":
            _out({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"unknown method {method}"}})
            return
        params = msg.get("params") or {}
        name = params.get("method", "")
        fn = ctx._methods.get(name)
        if fn is None:
            _out({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"no registered method '{name}'"}})
            return
        try:
            result = fn(**(params.get("args") or {}))
            if inspect.isawaitable(result):
                result = await result
            _out({"jsonrpc": "2.0", "id": mid, "result": result if result is not None else {}})
        except Exception as e:  # noqa: BLE001 — errors must cross as JSON
            traceback.print_exc()
            _out({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"}})

    # stdin as an async stream
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            return 0  # parent closed the pipe: we are being reaped
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("method") == "shutdown":
            return 0
        # A response to one of OUR requests, or work for us — one pipe, both.
        if not rpc.settle(msg):
            asyncio.create_task(handle(msg))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
