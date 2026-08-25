"""The desktop toolset — the user's machine, and everything on its screen.

The pod owns the desktop, so this is where the agent reaches it:

  * **windows** — ``desktop_apps`` / ``desktop_windows`` / ``desktop_open``,
    then ``desktop_read`` / ``desktop_update`` / ``desktop_set`` /
    ``desktop_call`` / ``desktop_screenshot`` to drive any one of them,
    including windows the USER opened. Which windows exist is the session
    document (``desktop_session.py``), shared by every viewport;
  * **the browser** — one headless Chromium in the pod (``browser.py``);
  * **data** — ``serve_local_data`` and the endpoint machinery that hands
    workspace files to apps over HTTP (``data_server.py``).

What used to live here as well was the LIVE VIEW plane: per-view sessions
the agent opened in the old chat sidebar, driven by ``live_view_*`` tools
and reported back by ``report_view_state``. That mechanism is retired —
Atrium windows are the one way a viewer reaches the screen, and they reach
every viewport rather than one browser tab. A desktop-native live view is
planned; it will be built on the session document, not on this.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger

# How long desktop_call waits for a window to return an action result.
ACTION_TIMEOUT_SECONDS = 30

# How long desktop_screenshot waits for the UI to render and return a frame.
SNAPSHOT_TIMEOUT_SECONDS = 25

# Cap on how many diagnostics (console errors / warnings) a session keeps.
MAX_DIAGNOSTICS = 50


def _deep_merge(target: Any, patch: Any) -> Any:
    """Deep-merge ``patch`` into ``target``, returning a new value."""
    if not isinstance(patch, dict):
        return patch
    base = target if isinstance(target, dict) else {}
    out = dict(base)
    for key, value in patch.items():
        out[key] = _deep_merge(base.get(key), value)
    return out


class DesktopToolSet(ToolSet):
    """The desktop plane: the agent's hands and eyes on Atrium windows.

    Agent-facing surface: desktop_windows / desktop_open / desktop_read /
    desktop_update / desktop_call (+ serve_local_data and the generic data
    endpoints). The live_view_* tools that used to sit beside them are
    gone: they drove the old chat sidebar, and Atrium windows are the one
    mechanism now.
    """

    def __init__(self, name: str = "desktop", **kwargs):
        super().__init__(name, **kwargs)
        # request_id -> Future, resolved by report_snapshot.
        self._pending_snapshots: dict[str, asyncio.Future] = {}
        # request_id -> Future, resolved by report_desktop_result.
        self._pending_desktop: dict[str, asyncio.Future] = {}
        self._nats = None  # lazy NATSStreamAdapter
        self._data_server = None  # lazy LiveViewDataServer
        self._browser_endpoints = False  # browser-frame/-input registered

    # ── internals ─────────────────────────────────────────────────────────

    def _chat_id(self) -> str | None:
        """Resolve the chat id from the execution context.

        Agent tool calls carry it as `chat_id` (injected by room.chat); the
        UI's proxy_toolset path injects it as `session_id`. NOT `client_id`
        (that is the UI connection id, stable across chats).
        """
        ctx = self.get_context() or {}
        return ctx.get("session_id") or ctx.get("chat_id")

    # ── the desktop session ───────────────────────────────────────────────

    def _desktop(self):
        """The window document, as the record on disk currently has it.

        Not per toolset instance (built per connection) and not per process
        either (a ProcessJob per toolset): both gave some browser its own
        desktop, which is the bug the document exists to fix wearing a
        different hat. The store reads the record through on every call.
        """
        from .desktop_session import get_store

        return get_store()

    async def _publish_desktop(self, event: dict[str, Any]) -> None:
        """Announce a change to every viewport of this pod.

        Pod-scoped, not per chat: a desktop belongs to the machine, and every
        view of it has to hear about a window opening whatever conversation —
        or none — that view has on screen.
        """
        from .desktop_session import DESKTOP_STREAM

        if self._nats is None:
            from pantheon.chatroom.stream import NATSStreamAdapter

            self._nats = NATSStreamAdapter()
        try:
            await self._nats.publish_stream(DESKTOP_STREAM, event)
        except Exception as e:  # noqa: BLE001  streaming is best-effort
            logger.error("desktop: publish failed: {}", e)

    @tool(exclude=True)
    async def desktop_session_get(self) -> dict:
        """UI-only: the whole window document, for a viewport attaching or
        recovering from a missed delta."""
        store = self._desktop()
        return {"success": True, "session": store.current(), "host": store.where()}

    @tool(exclude=True)
    async def desktop_intent(self, kind: str, args: dict | None = None) -> dict:
        """UI-only: ask for a change to the desktop.

        Clients send intents rather than state, so this is the only writer and
        the order of two viewports' edits is decided in one place. The reply
        carries whatever the caller needs back — a minted window id — and the
        ops themselves, so the caller can show the result now instead of
        waiting to hear its own broadcast come back. Applying by `seq` makes
        that idempotent: the broadcast arrives, is not newer, and is dropped.
        """
        store = self._desktop()
        try:
            ops, result = store.apply(kind, args or {})
        except (KeyError, ValueError) as e:
            return {"success": False, "error": str(e)}
        if ops:
            await self._publish_desktop({
                "type": "desktop.delta",
                "seq": store.session.seq,
                "ops": ops,
            })
        return {"success": True, "seq": store.session.seq, "ops": ops,
                "host": store.where(), **result}

    # ── who is looking (presence.py) ──────────────────────────────────────

    def _presence(self):
        from .presence import get_store as presence_store

        return presence_store()

    @tool(exclude=True)
    async def desktop_presence(
        self,
        viewport_id: str = "",
        clients: list | None = None,
        visible: bool = True,
        active: bool = False,
    ) -> dict:
        """UI-only: renew this page's leases, and read back who else is here.

        One call per page, on a heartbeat — a page has at most one viewport and
        any number of chat clients, so sending them together keeps this to one
        message rather than one per entity. The reply carries the whole live
        registry, so a viewport that wants to draw other people's cursors does
        not need a second round trip.

        `active` means real user input since the last beat, not that the beat
        happened: a background tab must not out-rank the window someone is
        typing in when the anchor is resolved.
        """
        registry, changed = self._presence().announce(
            viewport_id=viewport_id, clients=clients,
            visible=visible, active=active)
        # Only membership is worth telling anyone about. Broadcasting renewals
        # would wake every viewport on this pod every few seconds per open tab.
        if changed:
            await self._publish_desktop({"type": "desktop.presence", **registry})
        return {"success": True, **registry}

    @tool(exclude=True)
    async def desktop_presence_leave(
        self, viewport_id: str = "", client_ids: list | None = None,
    ) -> dict:
        """UI-only: give up leases on the way out (pagehide).

        Best-effort by nature — it does not fire for a crash, a dropped
        connection or a sleeping laptop, which is exactly why the lease exists.
        This only saves the TTL in the common case.
        """
        registry, changed = self._presence().leave(
            viewport_id=viewport_id, client_ids=client_ids)
        if changed:
            await self._publish_desktop({"type": "desktop.presence", **registry})
        return {"success": True, **registry}

    @tool(exclude=True)
    async def desktop_broadcast(self, topic: str = "", payload: dict | None = None) -> dict:
        """UI-only: pass an EPHEMERAL value to the pod's other viewports.

        The counterpart to `desktop_intent`, for state whose only interesting
        version is the newest one — a camera mid-drag, a cursor, a selection
        being scrubbed. It is relayed and forgotten: nothing is sequenced,
        nothing is written to the record, and a viewport that joins later
        learns none of it.

        That is the point rather than a limitation. Putting a camera in the
        session document would sequence and persist a value that changes at
        pointer rates and that nobody should reload into — someone else's zoom
        restored at boot is not a feature. What must survive a reload belongs
        in an intent instead.
        """
        if not topic:
            return {"success": False, "error": "desktop_broadcast needs a topic"}
        await self._publish_desktop({
            "type": "desktop.broadcast",
            "topic": topic,
            "payload": payload or {},
        })
        return {"success": True}

    @tool(exclude=True)
    async def desktop_anchor(self, chat_id: str = "") -> dict:
        """UI-only: which viewport this chat's directed requests should reach.

        Exposed rather than kept internal because "why that screen?" is
        otherwise unanswerable after the fact, and not being able to name the
        copy that answered was the whole difficulty of debugging the session.
        """
        chat_id = chat_id or self._chat_id() or ""
        return {"success": True, "chat_id": chat_id,
                **self._presence().anchor_for(chat_id)}

    def _data_roots(self) -> list:
        """Directories the data server should expose: the workspace (agent
        data + agent-written components) and the skills dirs (viewer plugins)."""
        from pantheon.settings import get_settings

        s = get_settings()
        roots = [s.work_dir]
        try:
            roots.append(s.workspace)
        except Exception:  # noqa: BLE001
            pass
        roots.append(s.skills_dir)
        roots.append(s.global_skills_dir)
        roots.append(s.factory_skills_dir)
        return roots

    async def _ensure_data_server(self):
        """Lazily start the CORS data server over all relevant roots."""
        if self._data_server is None:
            from .data_server import LiveViewDataServer

            self._data_server = LiveViewDataServer()
        await self._data_server.ensure_started(self._data_roots())
        return self._data_server

    def _package_screenshot(self, data_url: str, stem: str) -> dict:
        """Save a captured data URL and hand it back, inline when the model
        can see images in tool results — shared by desktop_screenshot and
        desktop_screenshot."""
        try:
            import base64
            from pathlib import Path
            from pantheon.settings import get_settings

            header, _, b64 = str(data_url).partition(",")
            ext = "jpg" if "jpeg" in header else "png"
            snap_dir = get_settings().pantheon_dir / "live_view_snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            path = snap_dir / f"{stem}-{int(time.time())}.{ext}"
            Path(path).write_bytes(base64.b64decode(b64))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"failed to save snapshot: {e}"}

        result: dict = {
            "success": True,
            "path": str(path),
            "note": (
                "Screenshot of the window. WebGL/canvas surfaces are captured "
                "when the app provides its own snapshot; if THIS image is blank, "
                "fall back to reading state + asking the user."
            ),
        }
        try:
            from pantheon.agent import get_current_run_model
            from pantheon.utils.vision_capability import supports_tool_result_image

            if supports_tool_result_image(get_current_run_model()):
                result["content_blocks"] = [
                    {"type": "image_url", "image_url": {"url": str(data_url)}}
                ]
                result["note"] += " The screenshot is shown inline above."
            else:
                result["note"] += " View the saved file with observe_images."
        except Exception:  # noqa: BLE001
            result["note"] += " View the saved file with observe_images."
        return result

    @tool
    async def desktop_screenshot(self, window_id: str) -> dict:
        """See what a desktop window currently shows, as an image.

        Works on any packaged-app window — including ones the user opened.
        Returns the screenshot inline (vision-capable models) and saves it to
        `path`. Use it to VERIFY after desktop_open / desktop_update: state
        alone does not prove the view looks right.
        """
        anchor = self._presence().anchor_for(self._chat_id() or "")
        viewport_id = anchor.get("viewport_id")
        if not viewport_id:
            return {"success": False, "error": f"no desktop to ask — {anchor.get('reason')}"}
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_snapshots[request_id] = future
        await self._publish_desktop({
            "type": "desktop.snapshot",
            "window_id": window_id,
            "request_id": request_id,
            "viewport_id": viewport_id,
        })
        try:
            data_url = await asyncio.wait_for(future, timeout=SNAPSHOT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return {"success": False, "error": "screenshot timed out — is the window still open?"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._pending_snapshots.pop(request_id, None)
        return self._package_screenshot(data_url, window_id)

    @tool
    async def serve_local_data(self, path: str) -> dict:
        """Expose a local workspace file or directory over HTTP (CORS).

        LiveView components run in the browser and fetch their data — and, for
        agent-generated components, their own code — over HTTP. Local
        workspace paths are not browser-fetchable; this lazily starts a
        localhost CORS static server and returns a URL for `path`.

        Use this to make data servable before referencing it from a view
        config. To show a file, prefer desktop_open — it runs the app's whole
        open pipeline; serving is for data an app will fetch by URL.

        Args:
            path: Absolute path, or path relative to the workspace, to a file
                or directory to serve.

        Returns:
            dict with success, base_url, and url (the URL for `path`).
        """
        from pathlib import Path

        p = Path(path)
        if not p.is_absolute():
            from pantheon.settings import get_settings
            p = get_settings().work_dir / p
        p = p.resolve()
        if not p.exists():
            return {"success": False, "error": f"Path does not exist: {p}"}

        server = await self._ensure_data_server()
        url = server.url_for(p)
        if url is None:
            roots = ", ".join(str(r) for r in server.roots)
            return {
                "success": False,
                "error": (
                    f"Path {p} is outside the LiveView data server roots "
                    f"({roots}). Put files to serve under the workspace."
                ),
            }
        return {"success": True, "base_url": server.base_url, "url": url}

    @tool
    async def serve_endpoint(
        self, name: str, path: str, config: dict | None = None,
    ) -> dict:
        """Expose a lightweight Python HTTP endpoint over the LiveView data server.

        Any LiveView can fetch the returned URL: built-in viewer plugins
        (Gosling, Cytoscape, IGV adapters, etc.) when their config accepts a
        data URL, or a custom LiveView app that calls fetch(url). Use this
        when the browser needs computed data rather than a file already on
        disk (that case is serve_local_data).

        The endpoint module must export either:
            async def handle(request): ...
        or:
            def build(): return handle
            def build(config): return handle

        The handler receives an aiohttp.web.Request, so the frontend and server
        coordinate through normal HTTP parameters: path segments (`tail`), query
        params, headers, or POST JSON. `config` is only for registration-time
        JSON constants passed to build(config), such as fixed paths or sample
        names. Use request parameters for runtime controls and files for large
        arrays or binary data.
        Keep request handlers light: precompute heavy results before serving,
        or run complex apps as separate processes and proxy them in a later
        endpoint mode.

        Args:
            name: URL segment for the endpoint. Letters, numbers, "_" and "-"
                only. Registering the same name replaces the handler.
            path: Absolute path, or workspace-relative path, to a Python module.
            config: Optional JSON-serializable constants for build(config).

        Returns:
            dict with success, base_url, and url (the endpoint base URL).
        """
        from pathlib import Path

        from .data_server import LiveViewDataServer

        try:
            LiveViewDataServer.validate_endpoint_name(name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        try:
            json.dumps(config if config is not None else {})
        except (TypeError, ValueError) as e:
            return {
                "success": False,
                "error": f"Endpoint config must be JSON-serializable: {e}",
            }

        p = Path(path)
        if not p.is_absolute():
            from pantheon.settings import get_settings
            p = get_settings().work_dir / p
        p = p.resolve()
        if not p.exists():
            return {"success": False, "error": f"Path does not exist: {p}"}
        if not p.is_file():
            return {"success": False, "error": f"Path is not a file: {p}"}
        roots = [root.resolve() for root in self._data_roots() if root.exists()]
        if not any(self._path_is_relative_to(p, root) for root in roots):
            return {
                "success": False,
                "error": (
                    f"Path {p} is outside the LiveView data server roots "
                    f"({', '.join(str(r) for r in roots)}). Put endpoint "
                    "modules under the workspace."
                ),
            }

        try:
            handler = self._load_endpoint_handler(p, config)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

        try:
            server = await self._ensure_data_server()
            url = await server.register_endpoint(name, handler)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}

        return {"success": True, "base_url": server.base_url, "url": url}

    def _load_endpoint_handler(self, path, config: dict | None = None) -> Any:
        """Load a handler callable from an endpoint module."""
        module_name = f"_pantheon_desktop_endpoint_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load endpoint module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        finally:
            sys.modules.pop(module_name, None)

        if hasattr(module, "build"):
            builder = getattr(module, "build")
            if not callable(builder):
                raise RuntimeError("Endpoint `build` export is not callable")
            handler = self._call_endpoint_builder(builder, config)
        else:
            handler = getattr(module, "handle", None)

        if not callable(handler):
            raise RuntimeError(
                "Endpoint module must export `handle(request)` or `build()`",
            )
        return handler

    @staticmethod
    def _call_endpoint_builder(builder, config: dict | None) -> Any:
        signature = inspect.signature(builder)
        params = list(signature.parameters.values())
        positional = [
            p for p in params
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        has_varargs = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL for p in params
        )
        required = [
            p for p in positional if p.default is inspect.Parameter.empty
        ]
        if len(required) > 1:
            raise RuntimeError("Endpoint build() must accept zero or one argument")
        accepts_config = has_varargs or len(positional) >= 1
        if config is not None:
            if not accepts_config:
                raise RuntimeError("Endpoint build() does not accept config")
            return builder(config)
        if required:
            return builder({})
        return builder()

    @staticmethod
    def _path_is_relative_to(path, root) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @tool
    async def manage_endpoints(
        self, action: str, name: str | None = None,
    ) -> dict:
        """Manage dynamic LiveView endpoints (list, info, unregister).

        This tool provides endpoint lifecycle management operations to
        complement serve_endpoint. Use it to inspect registered endpoints
        or clean up ones that are no longer needed.

        Args:
            action: Operation to perform:
                - "list": List all registered endpoints with their URLs
                - "info": Get details about a specific endpoint (requires name)
                - "unregister": Remove an endpoint registration (requires name)
            name: Endpoint name (required for "info" and "unregister" actions)

        Returns:
            dict with success status and operation-specific data:
            - list: {"success": True, "endpoints": [{"name": ..., "url": ...}, ...]}
            - info: {"success": True, "name": ..., "url": ..., "exists": True}
            - unregister: {"success": True, "removed": True/False}

        Examples:
            manage_endpoints("list")
            manage_endpoints("info", "ab_track")
            manage_endpoints("unregister", "old_endpoint")
        """
        if action not in ("list", "info", "unregister"):
            return {
                "success": False,
                "error": f"Invalid action '{action}'. Must be 'list', 'info', or 'unregister'",
            }

        if action in ("info", "unregister") and not name:
            return {
                "success": False,
                "error": f"Action '{action}' requires a 'name' parameter",
            }

        try:
            server = await self._ensure_data_server()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"Data server not available: {e}"}

        if action == "list":
            endpoints = server.list_endpoints()
            return {"success": True, "endpoints": endpoints}

        if action == "info":
            from .data_server import LiveViewDataServer
            try:
                LiveViewDataServer.validate_endpoint_name(name)  # type: ignore[arg-type]
            except ValueError as e:
                return {"success": False, "error": str(e)}

            exists = server.endpoint_exists(name)  # type: ignore[arg-type]
            if not exists:
                return {
                    "success": True,
                    "name": name,
                    "exists": False,
                    "url": None,
                }
            url = server.url_for_endpoint(name)  # type: ignore[arg-type]
            return {
                "success": True,
                "name": name,
                "exists": True,
                "url": url,
            }

        if action == "unregister":
            from .data_server import LiveViewDataServer
            try:
                LiveViewDataServer.validate_endpoint_name(name)  # type: ignore[arg-type]
            except ValueError as e:
                return {"success": False, "error": str(e)}

            removed = server.unregister_endpoint(name)  # type: ignore[arg-type]
            return {"success": True, "removed": removed}

        return {"success": False, "error": "Unreachable"}

    # ── UI-facing methods (not exposed to the LLM) ────────────────────────

    @tool(exclude=True)
    async def set_data_endpoint(self, tunnel_base: str) -> dict:
        """Hub → backend: deliver the public HTTPS base for this sandbox's
        LiveView data port (a Modal encrypted-tunnel URL).

        In server mode the data server binds 0.0.0.0:<fixed port> and the hub
        exposes it via a tunnel whose URL is only known *after* the sandbox is
        created. The hub calls this once (after readiness) so url_for can mint
        browser-reachable URLs instead of 127.0.0.1. The path token travels
        separately as an env var injected at sandbox creation. See
        docs/2026-06-10-live-view-server-mode.md (pantheon-hub)."""
        if not tunnel_base:
            return {"success": False, "error": "tunnel_base required"}
        server = await self._ensure_data_server()
        server.set_tunnel_base(tunnel_base)
        logger.info("desktop: data endpoint set to {}", tunnel_base)
        return {"success": True}

    # ── the desktop plane (app-spec: the agent interface, normalized) ──────
    #
    # These drive THE DESKTOP: any window, whoever opened it — the user's
    # double-click included — by window_id, plus app-or-file opening with the
    # same routing
    # the desktop itself uses. Addressed to ONE viewport (presence.py decides
    # which) over the pod-scoped stream and answered by report_desktop_result,
    # so the screen that answers need not be showing this conversation — or
    # any conversation at all.

    async def _desktop_request(self, event_type: str, payload: dict, timeout: float = 30.0) -> dict:
        """Ask ONE screen to do something that only a live browser can do.

        Addressed, not broadcast. The registry says which viewport hosts this
        chat (presence.py), the event carries that id, and every other viewport
        ignores it — which retires the Web Locks election that used to decide,
        anonymously and per browser profile, who would answer.

        Two limits fall away with it. There is no ping round trip before the
        real request: the registry already knows whether a screen is there, and
        a lease says so more reliably than a probe that races. And a chat
        context is no longer required — a request with no chat still resolves
        to the pod's most recently active viewport, so the standalone desktop
        stops being a place where `desktop_open` answers "no chat context".
        """
        chat_id = self._chat_id() or ""
        anchor = self._presence().anchor_for(chat_id)
        viewport_id = anchor.get("viewport_id")
        if not viewport_id:
            # Say which thing is missing. "The desktop did not answer" was the
            # old text for this, and it sent people looking for a bug in a
            # desktop that was simply not open.
            return {"success": False, "error": f"no desktop to ask — {anchor.get('reason')}"}
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_desktop[request_id] = future
        # Pod-scoped, not the chat stream: the addressed viewport may be
        # showing a different conversation, or none.
        await self._publish_desktop({
            "type": event_type,
            "request_id": request_id,
            "viewport_id": viewport_id,
            **payload,
        })
        try:
            value = await asyncio.wait_for(future, timeout=timeout)
            return {"success": True, "result": value}
        except asyncio.TimeoutError:
            return {"success": False,
                    "error": f"the desktop did not answer in {timeout:g}s "
                             f"(asked the viewport that {anchor.get('reason')})"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._pending_desktop.pop(request_id, None)

    @tool
    async def desktop_apps(self) -> dict:
        """List the apps installed on the user's desktop — what you can open.

        Each entry: `app_id` (what desktop_open's `app` takes), `name`,
        `description`, `opens` (file extensions it claims), `actions`
        (names for desktop_call), `backend` (whether app_call reaches a
        backend process), and `skill` (the path of the doc describing its
        state contract — read_file it before driving anything non-trivial).

        Call this when you need to name an app explicitly, or to see what
        can open a given file type.
        """
        return await self._desktop_request("desktop.apps", {})

    @tool
    async def desktop_windows(self) -> dict:
        """List every window on the user's desktop — whoever opened it.

        The desktop has virtual spaces (mac-style, numbered from 1): the
        top-level `active_space` / `space_count` describe them, and each
        window carries its `space`. Focusing or opening a window on another
        space carries the user there.

        Each entry: `window_id`, `app_id` (manifest id for packaged apps),
        `name`, `title`, `path` (the file it shows, when opened on one),
        `controllable` (whether desktop_read/update/call can drive it — true
        for packaged-app windows), `actions` it exposes, and `pty_session`
        for a Terminal window.

        This is how you reach windows the USER opened: find its window_id
        here, then desktop_read / desktop_update / desktop_call it exactly
        like a view you opened yourself.

        A TERMINAL WINDOW IS ALSO DRIVABLE, by a different route. It is a
        view onto a pty session on this pod, so `pty_write(pty_session,
        base64("echo hi\n"))` types into the terminal the user is looking
        at — they see the command and its output in their own window. Prefer
        that to running the command out of sight and pasting the result: it
        is the difference between working in their terminal and describing
        what you did somewhere else. (desktop_read/update/call will refuse
        it — those are the packaged-app bridge, which a built-in app has no
        part in. That refusal is not "the Terminal cannot be driven".)
        """
        # Answered from the pod's own document, so this works with no desktop
        # on screen at all — the window list is a property of the machine, and
        # asking a browser for it was what produced "the desktop did not
        # answer" for a question the pod could always answer itself.
        store = self._desktop()
        store.current()          # read the record through before answering
        s = store.session
        windows = [
            {
                "window_id": wid,
                "app_id": w.get("app_id"),
                "name": w.get("app_id"),
                "title": w.get("title"),
                "path": w.get("path") or None,
                "space": w.get("space", 1),
                "minimized": bool(w.get("minimized")),
                "status": w.get("status", "ready"),
                "opened_by": w.get("opened_by") or None,
                # A Terminal window is a VIEW onto a pty session running on
                # this pod — so it is drivable, just not through the packaged-
                # app bridge. pty_write(session_id, <base64>) and the command
                # appears in the window the user is watching, with its output,
                # as if they had typed it.
                "pty_session": (w.get("args") or {}).get("ptySession") or None,
            }
            for wid, w in sorted(s.windows.items(), key=lambda kv: kv[1].get("z", 0))
        ]
        return {"success": True, "result": {
            "windows": windows,
            "space_count": s.spaces,
            # Which space a person is LOOKING at belongs to that person, not to
            # the machine — two viewports may be on different ones. Reported
            # for compatibility as the space the topmost window sits on.
            "active_space": windows[-1]["space"] if windows else 1,
        }}

    @tool
    async def desktop_open(
        self, app: str = "", path: str = "", state: dict = {}, window_id: str = "",
        module: str = "", title: str = "",
    ) -> dict:
        """Open an app window on the desktop, the way a double-click would.

        WINDOWS ARE LONG-LIVED. If something is wrong with what a window
        shows, CORRECT IT IN PLACE — desktop_update (patch), desktop_set
        (replace), desktop_call (an action), or this tool with `window_id`
        to show a different file in that same window. Opening again is for a
        genuinely new thing; the desktop is the user's, and a pile of
        near-identical windows is a mess they have to clean up.

        Args:
            app: app id (e.g. "molstar", "vitessce"); desktop_apps() lists
                them. Omit with `path` to route by file type, as a
                double-click does.
            path: file to open. The app's own open pipeline runs (backend
                prepare, format conversion) — you do NOT need serve_local_data.
            state: initial state instead of / merged over a file, for apps
                driven by state (the contract each app's skill documents).
            window_id: show it in THIS existing window instead of a new one.
            module: a frontend ES-module SOURCE you wrote —
                `export function setup(app, root) { … }` — to open as a BESPOKE
                window with no install and no manifest. The app gets the full
                bridge (app.onState / setState / defineAction / onSnapshot /
                fs) and is drivable with desktop_read / update / set / call
                exactly like a packaged app. This is the fast path for a
                one-off UI. For something reusable, write a package under
                `.pantheon/apps/<id>/` and open it by `app` id instead.
            title: window title, used with `module`.

        Returns `window_id`, and `reused: true` when it landed in a window
        that was already showing that file.
        """
        if module:
            url = await self._serve_bespoke_module(module)
            if not url:
                return {
                    "success": False,
                    "error": (
                        "the data server has no browser-reachable URL for the "
                        "module yet (tunnel not delivered) — try again shortly"
                    ),
                }
            return await self._desktop_request("desktop.open", {
                "app": "", "path": "", "state": state or {},
                "window_id": window_id, "module_url": url,
                "title": title or "Agent app",
            }, timeout=90.0)
        return await self._desktop_request(
            "desktop.open",
            {"app": app, "path": path, "state": state or {}, "window_id": window_id},
            timeout=120.0)

    async def _serve_bespoke_module(self, source: str) -> str | None:
        """Write an agent-authored frontend module to the workspace and serve
        it, returning a browser-reachable URL (or None if unservable).

        Written as `.jsx` so the app host transpiles it (Sucrase) — valid for
        plain JS too, so the agent can use JSX without a build step.
        """
        import hashlib
        from pantheon.settings import get_settings

        slug = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
        bespoke_dir = get_settings().work_dir / ".pantheon" / "bespoke"
        bespoke_dir.mkdir(parents=True, exist_ok=True)
        mod_path = (bespoke_dir / f"{slug}.jsx").resolve()
        mod_path.write_text(source, encoding="utf-8")
        server = await self._ensure_data_server()
        return server.url_for(mod_path)

    @tool
    async def desktop_set(self, window_id: str, state: dict) -> dict:
        """Replace a window's state wholesale — the fix-in-place for apps
        whose state IS a config (Vitessce, Gosling, IGV).

        desktop_update deep-merges, so it can add and change but never
        remove; when the config is wrong rather than incomplete, set the
        whole corrected one here instead of opening another window.
        """
        return await self._desktop_request(
            "desktop.set", {"window_id": window_id, "state": state or {}})

    @tool
    async def desktop_read(self, window_id: str) -> dict:
        """Read a window's current state (the same shape its skill documents).

        Works on any packaged-app window, including ones the user opened.
        """
        return await self._desktop_request("desktop.read", {"window_id": window_id})

    @tool
    async def desktop_update(self, window_id: str, patch: dict) -> dict:
        """Deep-merge a patch into a window's state — for ANY packaged-app
        window by window_id, however it was opened.

        The first thing to reach for when a view is wrong: fix the window
        you have rather than opening another one.

        To show DIFFERENT DATA in a file window, patch ``path`` (the
        workspace file). A path is durable: it lands in the session record,
        every viewport re-opens the app from it, and it survives page
        reloads and pod replacement (served URLs are re-derived fresh).
        State patched WITHOUT a path — a raw ``url``, say — only changes the
        live views and is gone after a reload.
        """
        patch = patch or {}
        path = patch.get("path")
        if isinstance(path, str) and path:
            # Write the durable pointer through to the session record. The
            # broadcast makes every viewport reload the app from the new
            # file; the rest of the patch still rides the live update below.
            store = self._desktop()
            try:
                ops, _ = store.apply("set", {
                    "window_id": window_id,
                    "patch": {"path": path, "args": {"path": path}},
                })
            except (KeyError, ValueError) as e:
                return {"success": False, "error": str(e)}
            if ops:
                await self._publish_desktop({
                    "type": "desktop.delta",
                    "seq": store.session.seq,
                    "ops": ops,
                })
        return await self._desktop_request(
            "desktop.update", {"window_id": window_id, "patch": patch})

    @tool
    async def desktop_call(
        self, window_id: str, action: str = "", args: dict = {}, **kwargs,
    ) -> dict:
        """Invoke a named action on a window — the same handlers its menus
        trigger (defineAction). List a window's actions via desktop_windows.
        Also accepts window ops: action "$close" closes the window."""
        # Underscore-prefixed kwargs happen: the framework passes _background,
        # and a model that has seen it sometimes writes _action / _args too.
        # Three consecutive calls once died on "missing 1 required positional
        # argument: \'action\'" for exactly that. Accept both spellings.
        if not action:
            action = str(kwargs.get("_action") or "")
        if not args:
            raw = kwargs.get("_args")
            if isinstance(raw, str):
                try:
                    import json as _json
                    raw = _json.loads(raw)
                except Exception:
                    raw = None
            if isinstance(raw, dict):
                args = raw
        if not action:
            return {"success": False,
                    "error": "desktop_call needs an action name — desktop_windows() lists each "
                             "window's actions"}
        return await self._desktop_request(
            "desktop.call", {"window_id": window_id, "action": action, "args": args or {}},
            timeout=60.0)

    @tool(exclude=True)
    async def report_desktop_result(
        self, request_id: str, ok: bool = True, value: Any = None, error: str = "",
    ) -> dict:
        """UI-only: the desktop answers a desktop.* request."""
        future = self._pending_desktop.get(request_id)
        if future is None:
            return {"success": False, "error": "unknown or expired request"}
        if not future.done():
            if ok:
                future.set_result(value)
            else:
                future.set_exception(RuntimeError(error or "desktop request failed"))
        return {"success": True}

    @tool(exclude=True)
    async def report_snapshot(
        self,
        request_id: str,
        ok: bool,
        data_url: str | None = None,
        error: str | None = None,
    ) -> dict:
        """UI → backend: deliver a captured snapshot, resolving the pending
        desktop_screenshot."""
        future = self._pending_snapshots.get(request_id)
        if future is not None and not future.done():
            if ok and data_url:
                future.set_result(data_url)
            else:
                future.set_exception(RuntimeError(error or "snapshot failed"))
        return {"success": True}

    def _browser_engine(self):
        from .browser import BrowserEngine

        return BrowserEngine.instance()

    async def _browser_urls(self) -> tuple[str, str]:
        """Register the stream endpoints (once) and return their URLs."""
        server = await self._ensure_data_server()
        from .browser import make_frame_handler, make_input_handler, make_stream_handler
        from .vp8cast import make_cast_handler

        engine = self._browser_engine()
        if not self._browser_endpoints:
            await server.register_endpoint("browser-frame", make_frame_handler(engine))
            await server.register_endpoint("browser-input", make_input_handler(engine))
            # Push variant: frames stream, input rides back — one socket, no
            # per-frame round trip. The WebRTC gateway consumes this.
            await server.register_endpoint("browser-stream", make_stream_handler(engine))
            # VP8 variant: same contract, ~5x fewer bytes through the
            # ~20 Mbps tunnel. The gateway prefers it and repackages the
            # packets into RTP without re-encoding.
            await server.register_endpoint("browser-cast", make_cast_handler(engine))
            self._browser_endpoints = True
        frame = server.url_for_endpoint("browser-frame")
        inp = server.url_for_endpoint("browser-input")
        if not frame or not inp:
            raise RuntimeError(
                "the data server has no browser-reachable URL yet (tunnel not set)")
        return frame, inp

    def _resolve_page(self, engine, page_id: str = ""):
        """The addressed page — accepting a desktop Browser WINDOW id too.

        Agents naturally pass the window id they referenced (#app:win-N); a
        window is not a page, but its shared tab state names one, so the
        natural guess resolves instead of dead-ending. Failures explain the
        namespaces and the way forward.
        """
        try:
            return engine.latest(page_id)
        except KeyError:
            if page_id:
                w = (self._desktop().session.windows or {}).get(page_id)
                if w and w.get("app_id") == "browser":
                    shared = ((w.get("args") or {}).get("shared") or {}).get("v") or {}
                    pages = list(shared.get("pages") or [])
                    idx = shared.get("active", 0)
                    pid = None
                    if isinstance(idx, int) and 0 <= idx < len(pages):
                        pid = pages[idx]
                    pid = pid or next((x for x in pages if x), None)
                    if pid:
                        return engine.get(pid)
                    raise KeyError(
                        f"'{page_id}' is a desktop Browser WINDOW whose active tab is an "
                        f"empty New Tab — there is no page to drive yet. Call "
                        f"browser_open(url, window_id='{page_id}') to open the page as a "
                        "tab IN that window, then drive it by the returned page_id.")
            known = sorted(getattr(engine, "pages", {}).keys())
            raise KeyError(
                f"no such page: {page_id!r} — page ids come from browser_open "
                f"(currently open: {known if known else 'none'}). A desktop window id "
                "only resolves for Browser windows.")

    def _adopt_page_into_window(self, window_id: str, page_id: str) -> dict:
        """Show an engine page as a tab of an EXISTING Browser window.

        The window's tab set lives in its shared app state (pages + active);
        writing it through the session makes every viewport converge on the
        new tab. An empty New Tab slot is taken over rather than left behind.
        """
        store = self._desktop()
        w = (store.session.windows or {}).get(window_id)
        if not w:
            raise KeyError(f"no such window: {window_id}")
        if w.get("app_id") != "browser":
            raise ValueError(f"window {window_id} is {w.get('app_id')!r}, not a Browser window")
        shared = ((w.get("args") or {}).get("shared") or {}).get("v") or {}
        pages = list(shared.get("pages") or [])
        idx = shared.get("active", 0)
        if pages and isinstance(idx, int) and 0 <= idx < len(pages) and pages[idx] is None:
            pages[idx] = page_id
            active = idx
        else:
            pages.append(page_id)
            active = len(pages) - 1
        ops, _ = store.apply("set", {
            "window_id": window_id,
            "patch": {"args": {"shared": {"by": "agent", "v": {"pages": pages, "active": active}}}},
        })
        return {"ops": ops, "seq": store.session.seq}

    async def _browser_page_info(self, session) -> dict:
        engine = self._browser_engine()
        return {
            "page_id": session.id,
            "url": session.url,
            "title": await engine.call(session.title()),
        }

    @tool
    async def browser_open(self, url: str = "", show: bool = True,
                           window_id: str = "") -> dict:
        """Open a real browser page (Chromium in this sandbox) and, by default,
        show it to the user as a Browser window on their desktop.

        THE PAGE IS SHARED. The user sees it live and can click, type and log
        in; you drive the SAME page with browser_goto / browser_click /
        browser_type / browser_read. When a site needs a login, open it, ask
        the user to sign in, then continue — the profile (cookies, sessions)
        persists in the sandbox.

        Args:
            url: address to load (https:// is assumed when the scheme is
                missing). Empty opens a blank page.
            show: also open the desktop Browser window (needs an Atrium
                desktop on this chat). Pass False to browse headlessly.
            window_id: an EXISTING Browser window to open the page in — the
                page appears there as a tab (taking over an empty New Tab)
                instead of a new window. This is how "search in THIS browser
                window" is done.

        Returns `page_id` for the other browser_* tools, plus url/title, and
        `window_id` when a desktop window was opened or reused.
        """
        try:
            from .browser import normalize_url

            engine = self._browser_engine()
            session = await engine.call(engine.open_page(normalize_url(url)))
            info = await self._browser_page_info(session)
            result: dict = {"success": True, **info}
            if window_id:
                adopted = self._adopt_page_into_window(window_id, session.id)
                if adopted.get("ops"):
                    await self._publish_desktop({
                        "type": "desktop.delta",
                        "seq": adopted["seq"],
                        "ops": adopted["ops"],
                    })
                result["window_id"] = window_id
            elif show:
                shown = await self._desktop_request("desktop.open", {
                    "app": "browser", "path": "",
                    "state": {"page_id": session.id}, "window_id": "",
                }, timeout=60.0)
                if shown.get("success"):
                    result["window_id"] = (shown.get("result") or {}).get("window_id")
                else:
                    result["shown"] = False
                    result["show_error"] = shown.get("error")
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_goto(self, url: str, page_id: str = "") -> dict:
        """Navigate a browser page (the newest one unless `page_id` says
        otherwise). The user watching the window sees the navigation live."""
        try:
            engine = self._browser_engine()
            session = self._resolve_page(engine, page_id)
            await engine.call(engine.navigate(session.id, "goto", url))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_read(self, page_id: str = "") -> dict:
        """Read the visible text of a browser page (title, url, body text
        truncated to ~8k chars). Your eyes on the shared page — use it after
        navigating, or after asking the user to do something there."""
        try:
            from .browser import READ_LIMIT

            engine = self._browser_engine()
            session = self._resolve_page(engine, page_id)
            text = await engine.call(session.page.evaluate(
                "() => document.body ? document.body.innerText : ''"))
            if len(text) > READ_LIMIT:
                text = text[:READ_LIMIT] + "\n… (truncated)"
            return {"success": True, **await self._browser_page_info(session),
                    "text": text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_click(self, selector: str, page_id: str = "") -> dict:
        """Click an element on a browser page by CSS selector (or text=...,
        role=... — any Playwright selector). 5s timeout when nothing matches."""
        try:
            engine = self._browser_engine()
            session = self._resolve_page(engine, page_id)
            await engine.call(session.page.click(selector, timeout=5000))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_type(
        self, selector: str, text: str, submit: bool = False, page_id: str = "",
    ) -> dict:
        """Fill a field on a browser page (replaces its value). `submit`
        presses Enter afterwards."""
        try:
            engine = self._browser_engine()
            session = self._resolve_page(engine, page_id)
            await engine.call(session.page.fill(selector, text, timeout=5000))
            if submit:
                await engine.call(session.page.press(selector, "Enter"))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_screenshot(self, page_id: str = "", path: str = "") -> dict:
        """Screenshot a browser page to a workspace file (JPEG) and return its
        path — observe_image it to see the page as pixels."""
        try:
            import time as _time
            from pathlib import Path as _Path

            engine = self._browser_engine()
            session = self._resolve_page(engine, page_id)
            rel = path or f"browser-shot-{int(_time.time())}.jpg"
            out = _Path(rel)
            if not out.is_absolute():
                out = _Path.cwd() / out
            out.parent.mkdir(parents=True, exist_ok=True)
            # scale="css": the page may render at Retina density for the
            # human viewer; agent vision stays at CSS pixels.
            data = await engine.call(
                session.page.screenshot(type="jpeg", quality=80, scale="css"))
            out.write_bytes(data)
            return {"success": True, "path": str(out),
                    **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_pages(self) -> dict:
        """List open browser pages (newest last) with their ids and urls."""
        try:
            engine = self._browser_engine()
            pages = []
            for s in sorted(engine.pages.values(), key=lambda x: x.created_at):
                pages.append(await self._browser_page_info(s))
            return {"success": True, "pages": pages}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_close(self, page_id: str) -> dict:
        """Close a browser page. The user's window for it (if any) goes blank
        — prefer leaving pages open for the user unless they are truly done."""
        try:
            engine = self._browser_engine()
            await engine.call(engine.close_page(page_id))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── UI plumbing (excluded from the agent) ─────────────────────────────

    @tool(exclude=True)
    async def browser_ui_page(self, url: str = "", page_id: str = "") -> dict:
        """UI → backend: create (or attach to) a page and get stream URLs.

        The Atrium Browser window calls this on mount: with `page_id` when the
        agent already opened the page (desktop.open state), without to start a
        fresh page. Returns frame/input endpoint URLs plus the page's state.
        """
        try:
            engine = self._browser_engine()
            if page_id:
                session = engine.get(page_id)
            else:
                from .browser import normalize_url

                session = await engine.call(engine.open_page(normalize_url(url)))
            frame_url, input_url = await self._browser_urls()
            return {
                "success": True,
                **await self._browser_page_info(session),
                "frame_url": frame_url,
                "input_url": input_url,
                "width": session.width,
                "height": session.height,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_nav(self, page_id: str, op: str, url: str = "") -> dict:
        """UI → backend: toolbar navigation (goto/back/forward/reload/stop)."""
        try:
            engine = self._browser_engine()
            await engine.call(engine.navigate(page_id, op, url))
            return {"success": True,
                    **await self._browser_page_info(engine.get(page_id))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_close(self, page_id: str) -> dict:
        """UI → backend: the Browser window closed; drop its page."""
        try:
            engine = self._browser_engine()
            await engine.call(engine.close_page(page_id))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_clear_data(self) -> dict:
        """UI → backend: sign out of every site (clear cookies + storage)."""
        try:
            engine = self._browser_engine()
            await engine.call(engine.clear_data())
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
