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

Shutdown stops admission, invokes begin_shutdown, joins accepted calls and runs
cleanup before exiting. The parent must keep answering callbacks while draining
and check the exit code; sending shutdown alone is not proof of a clean stop.

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
import signal
import sys
import threading
import traceback
from pathlib import Path


_OUTPUT_LOCK = threading.Lock()
_PROTOCOL_OUTPUT = None


def _out(msg: dict) -> None:
    with _OUTPUT_LOCK:
        stream = _PROTOCOL_OUTPUT if _PROTOCOL_OUTPUT is not None else sys.stdout
        stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
        stream.flush()


class _State:
    """A small KV that survives the process — one JSON file, written whole."""

    def __init__(self, state_dir: Path):
        self._lock = threading.RLock()
        self._path = state_dir / "state.json"
        try:
            self._data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1))
            tmp.replace(self._path)


class AppContext:
    """The backend's one door to the world (spec §6.3)."""

    def __init__(self, app_id: str, workspace: Path, state_dir: Path, rpc: "_Rpc"):
        self.app_id = app_id
        self.workspace = workspace
        self.state_dir = state_dir
        self.state = _State(state_dir)
        self._rpc = rpc
        self._methods: dict[str, object] = {}
        self.concurrent_methods: set[str] = set()
        # Privileged backends opt in during register(). The native HTTP host
        # then requires the Runner's per-generation token for every POST.
        self.require_rpc_token = False
        self._cleanup = None
        self.begin_shutdown = None

    def method(self, fn):
        """Register ``fn`` as a callable backend method. Decorator."""
        self._methods[fn.__name__] = fn
        return fn

    def on_cleanup(self, fn):
        """Release child processes after in-flight calls finish on shutdown."""
        self._cleanup = fn
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
        self._disconnected = False

    def disconnect(self):
        self._disconnected = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError('App supervisor disconnected'))

    def notify(self, method: str, params: dict) -> None:
        self._seq += 1
        _out({"jsonrpc": "2.0", "id": f"n{self._seq}", "method": method, "params": params})

    async def request(self, method: str, params: dict, timeout_s: float = 60.0):
        if self._disconnected:
            raise ConnectionError('App supervisor disconnected')
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
    manifest_path = next(app_dir / name for name in ('app.json', 'atrium.json') if (app_dir / name).is_file())
    manifest = json.loads(manifest_path.read_text())
    relative = (manifest.get('entry') or {}).get('backend') or 'backend/__init__.py'
    backend_file = (app_dir / relative).resolve()
    if backend_file.is_dir():
        backend_file = backend_file / '__init__.py'
    if not backend_file.is_relative_to(app_dir.resolve()):
        raise ValueError('Backend entry escapes the App directory')
    backend_dir = backend_file.parent
    vendor = backend_dir / "_vendor"
    if vendor.is_dir():
        sys.path.insert(0, str(vendor))
    spec = importlib.util.spec_from_file_location(
        f"atrium_app_{app_dir.name}_backend", backend_file,
        submodule_search_locations=[str(backend_dir)] if backend_file.name == "__init__.py" else None,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _close_context(ctx):
    if ctx._cleanup:
        result = ctx._cleanup()
        if inspect.isawaitable(result):
            await result


async def _serve_stdio(ctx, rpc, reader) -> int:
    """Own admitted calls through drain; keep callback replies readable meanwhile.

    Shutdown is an admission boundary, not cancellation of accepted mutations.
    Providers may interrupt their resources in begin_shutdown. Cleanup runs only
    after admitted handlers have joined. EOF revokes callbacks but still drains.
    An enclosing sandbox owner must confirm container termination independently.
    """
    active = set()
    serial_sync = asyncio.Lock()
    stopping = asyncio.Event()
    failures = []

    async def handle(msg):
        mid = msg.get('id')
        try:
            params = msg.get('params') or {}
            name = params.get('method', '')
            fn = ctx._methods.get(name)
            if fn is None:
                raise ValueError(f"no registered method '{name}'")
            args = params.get('args') or {}
            if not isinstance(args, dict):
                raise ValueError('args must be an object')
            # A synchronous tool must not prevent the reader from accepting
            # interrupt/status or delivering a pending AppContext response.
            if inspect.iscoroutinefunction(fn):
                result = await fn(**args)
            elif name in ctx.concurrent_methods:
                result = await asyncio.to_thread(fn, **args)
            else:
                async with serial_sync:
                    result = await asyncio.to_thread(fn, **args)
            if inspect.isawaitable(result):
                result = await result
            _out({'jsonrpc': '2.0', 'id': mid, 'result': result if result is not None else {}})
        except Exception as exc:
            traceback.print_exc()
            _out({'jsonrpc': '2.0', 'id': mid,
                  'error': {'code': -32000, 'message': f'{type(exc).__name__}: {exc}'}})

    def finished(task):
        active.discard(task)
        if task.cancelled():
            failures.append(RuntimeError('An admitted App call was cancelled'))
        elif task.exception() is not None:
            failures.append(task.exception())

    async def read():
        try:
            while True:
                line = await reader.readline()
                if not line:
                    rpc.disconnect()
                    return
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if rpc.settle(msg):
                    continue
                method, mid = msg.get('method'), msg.get('id')
                if method == 'shutdown':
                    stopping.set()
                elif method == 'ping':
                    _out({'jsonrpc': '2.0', 'id': mid, 'result': {'stopping': stopping.is_set()}})
                elif stopping.is_set() or method != 'invoke':
                    _out({'jsonrpc': '2.0', 'id': mid, 'error': {
                        'code': -32000 if stopping.is_set() else -32601,
                        'message': 'App is stopping' if stopping.is_set() else f'unknown method {method}'}})
                else:
                    task = asyncio.create_task(handle(msg))
                    active.add(task)
                    task.add_done_callback(finished)
        finally:
            rpc.disconnect()
            stopping.set()

    loop = asyncio.get_running_loop()
    signals = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
            signals.append(sig)
        except (NotImplementedError, RuntimeError):
            pass  # Platforms without loop signal support still use the pipe.
    read_task = asyncio.create_task(read())
    try:
        await stopping.wait()
        # Let handlers already admitted by the reader enter provider ownership
        # before telling the provider to interrupt its active resources.
        await asyncio.sleep(0)
        if ctx.begin_shutdown:
            try:
                result = ctx.begin_shutdown()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                failures.append(exc)
        while active:
            await asyncio.gather(*tuple(active), return_exceptions=True)
        try:
            await _close_context(ctx)
        except Exception as exc:
            failures.append(exc)
    finally:
        read_task.cancel()
        outcome = await asyncio.gather(read_task, return_exceptions=True)
        if isinstance(outcome[0], Exception):
            failures.append(outcome[0])
        rpc.disconnect()
        for sig in signals:
            loop.remove_signal_handler(sig)
    for exc in failures:
        print(f'App shutdown failed: {type(exc).__name__}: {exc}', file=sys.stderr)
    return 1 if failures else 0


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
        try:
            await _close_context(ctx)
        except Exception:
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

    # stdin as an async stream
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    try:
        return await _serve_stdio(ctx, rpc, reader)
    finally:
        transport.close()


if __name__ == "__main__":
    # Backend imports and Python tools may print or configure logging on stdout.
    # Reserve the inherited stream for RPC before loading any backend code.
    _PROTOCOL_OUTPUT = sys.stdout
    sys.stdout = sys.stderr
    sys.exit(asyncio.run(main()))
