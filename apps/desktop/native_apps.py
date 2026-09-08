"""Owned native GUI processes on the desktop's existing Xpra display.

All async methods run on BrowserEngine's daemon loop. Xlib work runs in
workers, each using the engine's thread-local display connection.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..qupath.bridge import QuPathBridge


@dataclass
class NativeSession:
    id: str
    app_id: str
    path: str
    window_class: str
    process: Any
    log_path: Path
    xid: int | None = None
    starting: bool = True
    bridge: QuPathBridge | None = None


class NativeAppManager:
    START_TIMEOUT = 45.0

    def __init__(self, engine):
        self.engine = engine
        self.sessions: dict[str, NativeSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _settings():
        from pantheon.settings import get_settings

        return get_settings()

    def _resolve_path(self, path: str) -> str:
        if not path:
            return ""
        settings = self._settings()
        roots = [Path(settings.work_dir).resolve(), Path(settings.workspace).resolve()]
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = roots[0] / target
        target = target.resolve()
        if not any(target.is_relative_to(root) for root in roots):
            raise ValueError("QuPath files must be inside the workspace")
        if not target.is_file():
            raise ValueError(f"QuPath file does not exist: {target}")
        if target.suffix.lower() == ".qpdata":
            raise ValueError("Open the QuPath project (.qpproj) containing this .qpdata file")
        return str(target)

    @staticmethod
    def _executable() -> str:
        bundled = Path("/opt/qupath/bin/QuPath")
        if bundled.is_file() and os.access(bundled, os.X_OK):
            return str(bundled)
        installed = shutil.which("qupath")
        if installed:
            return installed
        raise RuntimeError("QuPath is not installed in this workspace image")

    def _environment(self, session_id: str, width: int = 1200,
                     height: int = 800) -> dict[str, str]:
        settings = self._settings()
        # The JDK creates .java/.userPrefs beneath java.util.prefs.userRoot.
        prefs = Path(settings.workspace).resolve() / ".pantheon" / "qupath"
        prefs.mkdir(parents=True, exist_ok=True)
        local_root = Path(tempfile.gettempdir()) / "pantheon-qupath"
        local_root.mkdir(parents=True, exist_ok=True)
        # A restarted window gets fresh credentials and IPC records, even when
        # its stable Atrium session_id is reused.
        local = Path(tempfile.mkdtemp(prefix=f"{session_id}-", dir=local_root))
        env = dict(os.environ)
        env["DISPLAY"] = self.engine._xvfb_display
        env["TMPDIR"] = str(local)
        env["XDG_CACHE_HOME"] = str(local / "cache")
        # jpackage launchers honor JAVA_TOOL_OPTIONS. Keep inherited options;
        # only this child gets persistent preferences and software JavaFX.
        def option(name, value):
            quoted = str(value).replace("\\", "\\\\").replace('"', '\\"')
            return f'-D{name}="{quoted}"'

        inherited = env.get("JAVA_TOOL_OPTIONS", "")
        additions = [option("java.util.prefs.userRoot", prefs),
                     option("java.io.tmpdir", local),
                     # QuPath otherwise uses 80% of Xpra's virtual screen,
                     # producing a huge first frame before host adoption.
                     option("qupath.win.width", width),
                     option("qupath.win.height", height)]
        if "-Dprism.order=" not in inherited:
            additions.append("-Dprism.order=sw")
        if "-Xmx" not in inherited:
            additions.append("-Xmx2g")
        env["JAVA_TOOL_OPTIONS"] = " ".join([inherited, *additions]).strip()
        return env

    @staticmethod
    def _owned_pids(pid: int) -> set[int]:
        """Track launcher descendants without assuming the launcher uses exec."""
        parents: dict[int, int] = {}
        for proc in Path("/proc").glob("[0-9]*/stat"):
            try:
                raw = proc.read_text()
                # comm may contain spaces/parentheses; fields after its last
                # closing parenthesis start with state, ppid, pgrp.
                fields = raw[raw.rfind(")") + 2:].split()
                parents[int(proc.parent.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
        owned = {pid}
        while True:
            children = {child for child, parent in parents.items() if parent in owned}
            updated = owned | children
            if updated == owned:
                return owned
            owned = updated

    def _find_main_window(self, session: NativeSession, width: int, height: int) -> int | None:
        """Stamp only an owned normal window, never another app or a dialog."""
        try:
            d = self.engine._x_display()
            pid_atom = d.intern_atom("_NET_WM_PID")
            type_atom = d.intern_atom("_NET_WM_WINDOW_TYPE")
            normal_atom = d.intern_atom("_NET_WM_WINDOW_TYPE_NORMAL")
            owned = self._owned_pids(session.process.pid)

            def walk(parent, depth=0):
                if depth > 6:
                    return None
                try:
                    children = parent.query_tree().children
                except Exception:
                    return None
                for win in children:
                    try:
                        pid = win.get_full_property(pid_atom, 0)
                        kinds = win.get_full_property(type_atom, 0)
                        # Xpra keeps a mapped client under an unviewable
                        # parent until the first viewer connects. Requiring
                        # IsViewable deadlocks launch before credentials are
                        # returned to that viewer.
                        mapped = win.get_attributes().map_state != 0
                        normal = not kinds or not len(kinds.value) or normal_atom in kinds.value
                        if (pid and len(pid.value) and int(pid.value[0]) in owned
                                and mapped and normal and not win.get_wm_transient_for()):
                            classes = win.get_wm_class() or ()
                            # A launcher's initial splash can omit a type;
                            # require QuPath's application identity as well.
                            if any("qupath" in str(c).lower() for c in classes):
                                win.set_wm_class(session.window_class, "QuPath")
                                win.configure(width=width, height=height)
                                d.sync()
                                return win.id
                    except Exception:
                        pass
                    found = walk(win, depth + 1)
                    if found is not None:
                        return found
                return None

            return walk(d.screen().root)
        except Exception:
            self.engine._reset_x_display()
            return None

    def _window_exists(self, session: NativeSession) -> bool:
        if not session.xid:
            return False
        from Xlib.error import BadWindow

        try:
            d = self.engine._x_display()
            win = d.create_resource_object("window", session.xid)
            pid = win.get_full_property(d.intern_atom("_NET_WM_PID"), 0)
            return (session.window_class in (win.get_wm_class() or ())
                    and pid is not None and bool(len(pid.value))
                    and int(pid.value[0]) in self._owned_pids(session.process.pid))
        except BadWindow:
            return False
        except Exception as error:
            self.engine._reset_x_display()
            raise RuntimeError("Could not inspect the QuPath native window") from error

    def _request_close(self, session: NativeSession) -> bool:
        if not self._window_exists(session):
            return False
        from Xlib import X, protocol

        d = self.engine._x_display()
        win = d.create_resource_object("window", session.xid)
        delete = d.intern_atom("WM_DELETE_WINDOW")
        if delete not in (win.get_wm_protocols() or []):
            raise RuntimeError("QuPath does not support a graceful window close")
        event = protocol.event.ClientMessage(
            window=win, client_type=d.intern_atom("WM_PROTOCOLS"),
            data=(32, [delete, X.CurrentTime, 0, 0, 0]),
        )
        win.send_event(event)
        d.flush()
        return True

    @staticmethod
    def _log_tail(session: NativeSession) -> str:
        try:
            with session.log_path.open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 4000))
                return stream.read().decode("utf-8", "replace").strip()
        except OSError:
            return ""

    @staticmethod
    def _stop_failed_launch(session: NativeSession) -> None:
        """Only startup failures are terminated; an active app owns its saves."""
        if session.process.poll() is not None:
            return
        try:
            os.killpg(session.process.pid, signal.SIGTERM)
            session.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(session.process.pid, signal.SIGKILL)
            session.process.wait(timeout=3)
        except ProcessLookupError:
            pass

    async def launch(self, app_id: str, session_id: str, path: str = "",
                     width: int = 1200, height: int = 800) -> dict:
        if app_id != "qupath":
            raise ValueError(f"Unsupported native app: {app_id}")
        if not session_id or len(session_id) > 200:
            raise ValueError("A stable desktop session_id is required")
        path = self._resolve_path(path)
        width = max(320, min(8192, int(width)))
        height = max(240, min(8192, int(height)))
        key = hashlib.sha256(session_id.encode()).hexdigest()[:20]
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            existing = self.sessions.get(session_id)
            if existing and existing.process.poll() is None:
                if existing.path != path:
                    raise ValueError("This QuPath window is already open with a different file; open a new window")
                if not await asyncio.to_thread(self._window_exists, existing):
                    raise RuntimeError("The previous QuPath process is still shutting down")
                return {**await self.engine.ensure_native_stage(), **self._info(existing, True)}
            executable = self._executable()
            stage = await self.engine.ensure_native_stage()
            argv = [executable, "--quiet"]
            if path:
                flag = "--project" if Path(path).suffix.lower() == ".qpproj" else "--image"
                argv.append(f"{flag}={path}")
            env = self._environment(key, width, height)
            bridge = QuPathBridge(Path(env["TMPDIR"]) / "bridge", key)
            env.update(bridge.launch_environment())
            env["JAVA_TOOL_OPTIONS"] += " " + bridge.startup_option()
            log_path = Path(env["TMPDIR"]) / "qupath.log"
            with log_path.open("ab") as output:
                process = subprocess.Popen(
                    argv, cwd=str(self._settings().workspace), env=env,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            session = NativeSession(session_id, app_id, path,
                                    f"pantheon-native-qupath-{key}", process, log_path, bridge=bridge)
            self.sessions[session_id] = session
            try:
                deadline = asyncio.get_running_loop().time() + self.START_TIMEOUT
                while asyncio.get_running_loop().time() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f"QuPath exited during startup (code {process.returncode})")
                    session.xid = await asyncio.to_thread(
                        self._find_main_window, session, width, height,
                    )
                    if session.xid is not None:
                        session.starting = False
                        return {**stage, **self._info(session, True)}
                    await asyncio.sleep(0.15)
                raise RuntimeError("QuPath did not create a native window before the startup timeout")
            except BaseException as error:
                await asyncio.to_thread(self._stop_failed_launch, session)
                self.sessions.pop(session_id, None)
                if isinstance(error, asyncio.CancelledError):
                    raise
                tail = self._log_tail(session)
                raise RuntimeError(f"{error}{': ' + tail if tail else ''}") from error

    @staticmethod
    def _info(session: NativeSession, running: bool) -> dict:
        bridge_ready = session.bridge.ready() if running and session.bridge else None
        return {"session_id": session.id, "app_id": session.app_id,
                "path": session.path, "window_class": session.window_class,
                "running": running,
                "bridge_ready": bridge_ready is not None,
                "capabilities": bridge_ready.get("capabilities", []) if bridge_ready else [],
                "qupath_version": bridge_ready.get("qupath_version") if bridge_ready else None,
                "state": "starting" if running and session.starting else "running" if running else "stopped"}

    def _live_session(self, session_id: str) -> NativeSession:
        session = self.sessions.get(session_id)
        if session is None or session.process.poll() is not None:
            raise ValueError("This native desktop session is not running")
        return session

    def _live_bridge(self, session: NativeSession) -> QuPathBridge:
        if session.bridge is None or session.bridge.ready() is None:
            raise RuntimeError("The QuPath script bridge is still starting; retry after it becomes ready")
        return session.bridge

    async def read(self, session_id: str, *, annotation_limit: int = 200) -> dict:
        """Current GUI state, with a bounded wait even when JavaFX is busy."""
        session = self._live_session(session_id)
        bridge = self._live_bridge(session)
        result = await bridge.call("state", {"annotation_limit": annotation_limit}, timeout=2)
        return {**result, "native_session_id": session_id}

    def _focus(self, session: NativeSession) -> bool:
        if not self._window_exists(session):
            return False
        from .native_control import NativeWindowController, native_input_lock

        controller = NativeWindowController(self.engine)
        with native_input_lock(self.engine):
            controller._focus(self.engine._x_display(), session.xid, {session.xid})
        return True

    async def call(self, session_id: str, action: str, args: dict | None = None) -> dict:
        """App-specific actions behind the shared desktop_call interface."""
        args = dict(args or {})
        if action == "status":
            return await self.read(session_id, annotation_limit=args.get("annotation_limit", 200))
        if action == "close":
            return await self.close(session_id)
        session = self._live_session(session_id)
        if action == "focus":
            return {"native_session_id": session_id,
                    "focused": await asyncio.to_thread(self._focus, session)}
        bridge = self._live_bridge(session)
        if action == "get_state":
            return await self.read(session_id, annotation_limit=args.get("annotation_limit", 200))
        if action == "script_status":
            return {**bridge.request_status(args.get("request_id")), "native_session_id": session_id}
        if action == "run_script":
            wait_s = args.get("wait_s", 2)
            if isinstance(wait_s, bool) or not isinstance(wait_s, (int, float)) or not 0 <= wait_s <= 10:
                raise ValueError("wait_s must be between 0 and 10 seconds")
            params = {
                "script": args.get("script"), "thread": args.get("thread", "worker"),
                "args": args.get("args", []),
            }
            if "expected_image" in args:
                params["expected_image"] = args["expected_image"]
            if "update_hierarchy" in args:
                params["update_hierarchy"] = args["update_hierarchy"]
            result = await bridge.call("script", params,
                                       request_id=args.get("request_id"), timeout=wait_s)
            return {**result, "native_session_id": session_id}
        raise ValueError(f"Unsupported native desktop action: {action}")

    async def status(self, session_id: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None:
            return {"session_id": session_id, "running": False, "path": "", "window_class": ""}
        running = session.process.poll() is None and (
            session.starting or await asyncio.to_thread(self._window_exists, session))
        return self._info(session, running)

    async def close(self, session_id: str) -> dict:
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            session = self.sessions.get(session_id)
            if session is None or session.process.poll() is not None:
                return {**await self.status(session_id), "close_requested": False}
            requested = await asyncio.to_thread(self._request_close, session)
            # WM_DELETE may open Save/Cancel. The frontend waits for lost-window;
            # a close request is not permission to terminate this process.
            return {**await self.status(session_id), "close_requested": requested}
