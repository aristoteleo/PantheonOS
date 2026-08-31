"""Packaged-app backend supervisor — part of the desktop App's backend.

A packaged (headed) App may ship a path-form Python backend
(``entry.backend: "backend/__init__.py"``). Each backend runs in ITS OWN
subprocess, spawned lazily and supervised here. The desktop App owns this
because packaged apps are windows on the desktop surface: the desktop
already scans their manifests, and its data server is what ``ctx.serve``
answers with. Nothing a backend does can take down the desktop's own tools
or another app.

Transport is line-delimited JSON-RPC 2.0 over the child's stdio —
deliberately not the NATS bus. The child never holds bus credentials;
everything it wants (the data server, durable state, logging) it asks for
over its one pipe, and the supervisor answers with the desktop's own
facilities. Backends cannot reach each other by geometry rather than by
discipline. (Module-form backends — ``module:Class`` — are full App
instances under the runner and never pass through here.)

The registration mechanism, end to end:

  scan          manifests found under the install scopes become REGISTERED
                entries (id, dir, scope, manifest) — visible before any
                process exists;
  handshake     on first use the child imports the backend, runs
                ``register(ctx)``, and reports the method names it collected;
                those become the entry's callable surface;
  dispatch      ``call()`` refuses methods the handshake never registered —
                ``unknown_method`` comes from the table, not from a timeout;
  lifecycle     registered → spawning → ready → (idle) reaped → registered,
                or → crashed(n) with exponential backoff, capped, → failed.

State the child asks us to persist lives OUTSIDE the package directory
(``.pantheon/app-state/<id>/``), so an uninstall or a dev resync of the
package never deletes user data.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pantheon.utils.log import logger

_MANIFEST_NAMES = ("app.json", "atrium.json")

_HANDSHAKE_TIMEOUT_S = 10.0
_IDLE_REAP_S = 600.0
_BACKOFF_S = [1.0, 2.0, 4.0, 8.0, 16.0]  # then: failed
_STDERR_TAIL = 4000


def _backend_path(manifest: dict) -> str:
    """The path-form backend this supervisor owns, or '' if there is none.

    Module-form backends (``pkg.mod:Class``) are bus services under the
    runner; only a path into the app directory is stdio-supervised here.
    """
    backend = (manifest.get("entry") or {}).get("backend") or ""
    return "" if ":" in backend else backend


@dataclass
class AppEntry:
    """One registered app — the unit the registration mechanism tracks."""

    app_id: str
    dir: Path
    scope: str  # workspace | user | builtin
    manifest: dict
    state: str = "registered"  # registered|spawning|ready|failed
    methods: list[str] = field(default_factory=list)
    methods_info: list[dict] = field(default_factory=list)
    crashes: int = 0
    last_error: str = ""

    def describe(self) -> dict:
        return {
            "id": self.app_id,
            "version": self.manifest.get("version", "0"),
            "scope": self.scope,
            "state": self.state,
            "methods": list(self.methods),
            "methods_info": list(self.methods_info),
            "actions": self.manifest.get("actions", []),
            "opens": self.manifest.get("opens", []),
            "error": self.last_error or None,
        }


class _AppProcess:
    """A live backend child: its pipes, its pending calls, its reader."""

    def __init__(self, entry: AppEntry, proc: asyncio.subprocess.Process):
        self.entry = entry
        self.proc = proc
        self.pending: dict[str, asyncio.Future] = {}
        self.seq = 0
        self.last_used = time.monotonic()
        # Newest source mtime at spawn — call() retires the process when the
        # code on disk moves past it (dev sync, upgrade).
        self.code_stamp = 0.0
        self.stderr_tail = ""
        self.reader_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None

    def next_id(self) -> str:
        self.seq += 1
        return f"c{self.seq}"


class AppSupervisor:
    """Spawns, watches, and speaks for packaged-app backends.

    Owns no app code. ``serve`` is the desktop's own data server —
    ``ctx.serve`` from a child is answered with it directly.
    """

    def __init__(
        self,
        workspace: Path,
        roots: list[tuple[Path, str]],
        serve: Callable[[str], Awaitable[str]],
    ):
        self.workspace = Path(workspace)
        self.roots = roots
        self._serve = serve
        self.entries: dict[str, AppEntry] = {}
        self.procs: dict[str, _AppProcess] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reaper: asyncio.Task | None = None

    # ── registration: scan ──────────────────────────────────────────────

    def scan(self) -> list[dict]:
        """(Re)read the install scopes. First scope wins per id.

        Cheap and synchronous — a handful of manifest reads — and run on every
        registry query rather than cached, because an install is a file write
        and polling filesystems is how every stale-cache bug here starts.
        Running processes of apps that vanished are left to the idle reaper.
        """
        found: dict[str, AppEntry] = {}
        for root, scope in self.roots:
            if not root.is_dir():
                continue
            for app_dir in sorted(root.iterdir()):
                mf = next((app_dir / n for n in _MANIFEST_NAMES
                           if (app_dir / n).is_file()), None)
                if mf is None:
                    continue
                try:
                    manifest = json.loads(mf.read_text())
                except (json.JSONDecodeError, OSError):
                    continue  # half a package must not wedge the registry
                app_id = manifest.get("id")
                if not app_id or app_id in found:
                    continue
                prev = self.entries.get(app_id)
                if prev and prev.dir == app_dir:
                    # Keep live state (process, crash count) across scans — but
                    # the manifest follows the FILE: an upgrade that adds a
                    # backend must be visible on the next scan, not the next
                    # process restart.
                    prev.manifest = manifest
                    found[app_id] = prev
                else:
                    found[app_id] = AppEntry(app_id, app_dir, scope, manifest)
        self.entries = found
        return [e.describe() for e in self.entries.values()]

    # ── lifecycle ───────────────────────────────────────────────────────

    def _interpreter(self, entry: AppEntry) -> str:
        """The interpreter the backend runs under.

        A named env resolves against the conda machinery's env roots; a
        missing or unnamed env falls back to this process's interpreter. The
        child script is stdlib-only precisely so ANY interpreter can run it —
        a conda env has no ``pantheon`` package and must not need one.
        """
        env = ((entry.manifest.get("caps") or {}).get("python") or {}).get("env")
        if env:
            for root in ("/opt/pantheon/envs", str(Path.home() / ".local/share/pantheon/envs")):
                cand = Path(root) / env / "bin" / "python"
                if cand.is_file():
                    return str(cand)
            logger.warning(f"app {entry.app_id}: python env '{env}' not found, using desktop python")
        return sys.executable

    def _code_stamp(self, entry: AppEntry) -> float:
        """Newest .py mtime under the backend's directory.

        The fingerprint of what a spawned process is running. Bounded: a
        backend directory holds a handful of source files, not a dataset.
        """
        backend = _backend_path(entry.manifest)
        root = (entry.dir / backend).parent if backend else entry.dir
        stamp = 0.0
        try:
            for path in root.rglob("*.py"):
                try:
                    stamp = max(stamp, path.stat().st_mtime)
                except OSError:
                    continue
        except OSError:
            pass
        return stamp

    async def _spawn(self, entry: AppEntry) -> _AppProcess:
        runtime = Path(__file__).with_name("app_runtime.py")
        state_dir = self.workspace / ".pantheon" / "app-state" / entry.app_id
        state_dir.mkdir(parents=True, exist_ok=True)
        entry.state = "spawning"
        proc = await asyncio.create_subprocess_exec(
            self._interpreter(entry),
            str(runtime),
            "--app-dir", str(entry.dir),
            "--app-id", entry.app_id,
            "--workspace", str(self.workspace),
            "--state-dir", str(state_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Its own group, so a desktop shutdown can sweep children and a
            # child's own children die with it.
            start_new_session=True,
        )
        ap = _AppProcess(entry, proc)
        ap.code_stamp = self._code_stamp(entry)
        ap.stderr_task = asyncio.create_task(self._drain_stderr(ap))

        # Handshake: one line, {"ready": true, "api": 1, "methods": [...]}.
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), _HANDSHAKE_TIMEOUT_S)
            hello = json.loads(line)
            if not hello.get("ready"):
                raise RuntimeError(hello.get("error") or "backend reported not-ready")
        except Exception as e:
            with contextlib_suppress():
                proc.kill()
            entry.state = "registered"
            raise RuntimeError(
                f"backend for '{entry.app_id}' failed to start: {e}; stderr: {ap.stderr_tail[-800:]}"
            ) from e

        entry.methods = [str(m) for m in hello.get("methods", [])]
        entry.methods_info = [m for m in hello.get("methods_info", []) if isinstance(m, dict)]
        entry.state = "ready"
        entry.last_error = ""
        ap.reader_task = asyncio.create_task(self._read_loop(ap))
        self.procs[entry.app_id] = ap
        self._ensure_reaper()
        logger.info(f"app backend up: {entry.app_id} ({len(entry.methods)} methods)")
        return ap

    async def _drain_stderr(self, ap: _AppProcess) -> None:
        try:
            while True:
                chunk = await ap.proc.stderr.read(1024)
                if not chunk:
                    return
                ap.stderr_tail = (ap.stderr_tail + chunk.decode(errors="replace"))[-_STDERR_TAIL:]
        except Exception:
            return

    async def _read_loop(self, ap: _AppProcess) -> None:
        """Demultiplex the child's stdout: responses to our invokes, and the
        child's own AppContext requests, interleaved on one pipe."""
        try:
            while True:
                line = await ap.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "method" in msg:
                    asyncio.create_task(self._serve_ctx(ap, msg))
                else:
                    fut = ap.pending.pop(str(msg.get("id")), None)
                    if fut and not fut.done():
                        fut.set_result(msg)
        finally:
            await self._on_exit(ap)

    async def _on_exit(self, ap: _AppProcess) -> None:
        entry = ap.entry
        # stdout EOF precedes the reap — wait briefly so the error message
        # carries the real exit code, not None.
        try:
            code = await asyncio.wait_for(ap.proc.wait(), 5)
        except Exception:
            code = ap.proc.returncode
        for fut in ap.pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(
                    f"backend for '{entry.app_id}' exited (code {code}); stderr: {ap.stderr_tail[-800:]}"
                ))
        ap.pending.clear()
        if self.procs.get(entry.app_id) is ap:
            del self.procs[entry.app_id]
        if entry.state == "reaped":
            entry.state = "registered"
            return
        entry.crashes += 1
        entry.last_error = ap.stderr_tail[-800:] or f"exited with code {code}"
        if entry.crashes > len(_BACKOFF_S):
            entry.state = "failed"
            logger.error(f"app backend failed permanently: {entry.app_id}")
        else:
            entry.state = "registered"
            logger.warning(f"app backend exited: {entry.app_id} (crash {entry.crashes}, code {code})")

    async def _serve_ctx(self, ap: _AppProcess, msg: dict) -> None:
        """Answer one AppContext request from the child."""
        method = msg.get("method", "")
        params = msg.get("params") or {}
        out: dict = {"jsonrpc": "2.0", "id": msg.get("id")}
        try:
            if method == "ctx.serve":
                out["result"] = {"url": await self._serve(params["path"])}
            elif method == "ctx.log":
                logger.info(f"[app:{ap.entry.app_id}] {params.get('message', '')}")
                out["result"] = {}
            else:
                out["error"] = {"code": -32601, "message": f"unknown ctx method {method}"}
        except Exception as e:  # noqa: BLE001 — everything must cross as JSON
            out["error"] = {"code": -32000, "message": str(e)}
        await self._send(ap, out)

    async def _send(self, ap: _AppProcess, msg: dict) -> None:
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode()
        ap.proc.stdin.write(data)
        await ap.proc.stdin.drain()

    # ── dispatch ────────────────────────────────────────────────────────

    async def call(self, app_id: str, method: str, args: dict | None, timeout_s: float) -> Any:
        if not self.entries:
            self.scan()
        entry = self.entries.get(app_id)
        # Rescan for an unknown id — and equally for a known entry that
        # declares no backend: an install or upgrade may have grown one since
        # the last scan, and without the rescan that stale entry errors on
        # every call until something else happens to poke app_registry.
        if entry is None or not _backend_path(entry.manifest):
            self.scan()
            entry = self.entries.get(app_id)
        if entry is None:
            raise RuntimeError(f"no app '{app_id}' is installed")
        if not _backend_path(entry.manifest):
            raise RuntimeError(f"app '{app_id}' declares no backend")
        if entry.state == "failed":
            raise RuntimeError(
                f"backend for '{app_id}' has failed repeatedly and is parked; "
                f"last error: {entry.last_error}"
            )

        lock = self._locks.setdefault(app_id, asyncio.Lock())
        async with lock:
            ap = self.procs.get(app_id)
            # Hot reload: the code on disk moved past what this process runs —
            # a dev sync or an upgrade landed. Retire it (not a crash: no
            # backoff, no counter) and let the spawn below run the new code.
            # Mid-flight calls postpone it; the next call gets fresh code.
            if (
                ap is not None
                and ap.proc.returncode is None
                and not ap.pending
                and self._code_stamp(entry) > ap.code_stamp
            ):
                logger.info("app {}: source changed — restarting its backend", app_id)
                ap.proc.terminate()
                try:
                    await asyncio.wait_for(ap.proc.wait(), 5)
                except asyncio.TimeoutError:
                    ap.proc.kill()
                self.procs.pop(app_id, None)
                ap = None
            if ap is None or ap.proc.returncode is not None:
                if entry.crashes:
                    delay = _BACKOFF_S[min(entry.crashes, len(_BACKOFF_S)) - 1]
                    await asyncio.sleep(delay)
                ap = await self._spawn(entry)

        # The handshake's method list is the dispatch table: an unknown method
        # is refused here, from the registration, not discovered by timeout.
        if entry.methods and method not in entry.methods:
            raise RuntimeError(
                f"app '{app_id}' registers no method '{method}' "
                f"(has: {', '.join(entry.methods) or 'none'})"
            )

        ap.last_used = time.monotonic()
        call_id = ap.next_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        ap.pending[call_id] = fut
        await self._send(ap, {
            "jsonrpc": "2.0", "id": call_id, "method": "invoke",
            "params": {"method": method, "args": args or {}},
        })
        try:
            msg = await asyncio.wait_for(fut, timeout_s)
        except asyncio.TimeoutError:
            ap.pending.pop(call_id, None)
            raise RuntimeError(
                f"'{app_id}.{method}' timed out after {timeout_s:.0f}s; "
                f"stderr: {ap.stderr_tail[-400:] or '(quiet)'}"
            ) from None
        if "error" in msg:
            raise RuntimeError(str((msg["error"] or {}).get("message", "backend error")))
        return msg.get("result")

    # ── reaping ─────────────────────────────────────────────────────────

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while self.procs:
            await asyncio.sleep(60)
            now = time.monotonic()
            for app_id, ap in list(self.procs.items()):
                if now - ap.last_used > _IDLE_REAP_S and not ap.pending:
                    ap.entry.state = "reaped"
                    logger.info(f"app backend idle, reaping: {app_id}")
                    with contextlib_suppress():
                        ap.proc.terminate()

    async def shutdown(self) -> None:
        for ap in list(self.procs.values()):
            ap.entry.state = "reaped"
            with contextlib_suppress():
                ap.proc.terminate()


class contextlib_suppress:
    """``contextlib.suppress(Exception)`` without the import noise."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True
