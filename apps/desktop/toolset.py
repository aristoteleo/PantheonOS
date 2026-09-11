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

# A Browser action may first recreate a missing native page (150s), attach
# its stream (30s, plus a compatibility retry), claim it (40s), and navigate
# (40s). Keep the outer request alive while those bounded steps run.
BROWSER_WINDOW_TIMEOUT_SECONDS = 300.0

# The first browser capture needs a user gesture and a surface picker.
# Subsequent requests reuse the authorized stream; the UI bounds frame reads.
SNAPSHOT_TIMEOUT_SECONDS = 180

# Cap on how many diagnostics (console errors / warnings) a session keeps.
MAX_DIAGNOSTICS = 50


def normalize_window_reference(reference: str) -> str:
    """Accept the exact #app mention spelling without changing page/app ids."""
    for prefix in ("#app:", "app:"):
        if reference.startswith(prefix) and reference[len(prefix):].startswith("win-"):
            return reference[len(prefix):]
    return reference


class DesktopRequestError(RuntimeError):
    """A failed frontend operation can still identify its existing window."""

    def __init__(self, message: str, value: Any = None):
        super().__init__(message)
        self.value = value


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
        self._apps_supervisor = None  # lazy AppSupervisor (packaged backends)
        self._browser_creation_locks: dict[str, asyncio.Lock] = {}

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

    async def _publish_desktop(self, event: dict[str, Any]) -> bool:
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
            published = await self._nats.publish_stream(DESKTOP_STREAM, event)
            return published is not False
        except Exception as e:  # noqa: BLE001  streaming is best-effort
            logger.error("desktop: publish failed: {}", e)
            return False

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
            args = dict(args or {})
            if kind == "open" and not args.get("window_id") and str(args.get("app_id", "")).startswith("pkg:"):
                from .store_manager import AppStoreManager
                manager = AppStoreManager(self._app_scope_roots())
                app_id = args["app_id"].removeprefix("pkg:")
                current = manager.find(app_id)
                if not manager.versions.restriction(current['manifest']):
                    window_args = dict(args.get("args") or {})
                    revision = window_args.get('appRevision') or {}
                    resolved = await asyncio.to_thread(manager.versions.resolve, app_id,
                                                       revision.get('scope', ''), revision.get('commit', ''), revision.get('repository_id', ''))
                    window_args['appRevision'] = resolved['revision']
                    args['args'] = window_args
            ops, result = store.apply(kind, args)
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
        # The page reports when its desktop finished coming up (frontends
        # ship this already); accepted so a newer page never turns every
        # heartbeat into a TypeError against an older pod.
        ready: bool = False,
        presence_id: str = "",
        sequence: int | None = None,
        **_future: object,
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
            visible=visible, active=active,
            presence_id=presence_id, sequence=sequence)
        if ready and viewport_id and registry.get("applied", True):
            # An expired connection must not trigger work after a newer leave
            # or lease. Only an accepted, ready desktop starts its prewarm.
            self._prewarm_browser()
        # Only membership is worth telling anyone about. Broadcasting renewals
        # would wake every viewport on this pod every few seconds per open tab.
        if changed:
            await self._publish_desktop({"type": "desktop.presence", **registry})
        return {"success": True, **registry}

    @tool(exclude=True)
    async def desktop_presence_leave(
        self, viewport_id: str = "", client_ids: list | None = None,
        presence_id: str = "", sequence: int | None = None,
    ) -> dict:
        """UI-only: give up leases on the way out (pagehide).

        Best-effort by nature — it does not fire for a crash, a dropped
        connection or a sleeping laptop, which is exactly why the lease exists.
        This only saves the TTL in the common case.
        """
        registry, changed = self._presence().leave(
            viewport_id=viewport_id, client_ids=client_ids,
            presence_id=presence_id, sequence=sequence)
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
        # First-party App tree (<repo>/apps): bundled headed apps' frontends
        # (frontend/main.js + assets) are fetched by the shell over this
        # server, exactly like a volume-installed packaged app's.
        from pantheon.apps.registry import BUILTIN_ROOT

        roots.append(BUILTIN_ROOT)
        # Store installs are user-owned, shared across this user's workspaces.
        for root, scope in self._app_scope_roots():
            if scope == "user":
                store = root.parent / "app-store"
                # Editable Git working trees are distinct from immutable
                # launch snapshots and private forks. Store's Source button
                # opens these repositories through the normal file viewers.
                roots.extend([
                    root, store / "snapshots", store / "forks", store / "repositories",
                ])
        return roots

    async def _ensure_data_server(self):
        """Lazily start the CORS data server over all relevant roots."""
        if self._data_server is None:
            from .data_server import LiveViewDataServer

            self._data_server = LiveViewDataServer()
        await self._data_server.ensure_started(self._data_roots())
        return self._data_server

    def _package_screenshot(self, data_url: str, stem: str, *, native: bool = False) -> dict:
        """Save a captured data URL and hand it back, inline when the model
        can see images in tool results."""
        try:
            import base64
            from pathlib import Path
            from pantheon.settings import get_settings

            header, _, b64 = str(data_url).partition(",")
            ext = "jpg" if "jpeg" in header else "png"
            snap_dir = get_settings().pantheon_dir / "live_view_snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            path = snap_dir / f"{stem}-{int(time.time())}-{uuid.uuid4().hex[:12]}.{ext}"
            Path(path).write_bytes(base64.b64decode(b64))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"failed to save snapshot: {e}"}

        result: dict = {
            "success": True,
            "path": str(path),
            "note": (
                "Native application export for native-input coordinates. This excludes Atrium chrome and overlays; "
                "it is NOT a screenshot of the user's visible browser. "
            ) if native else (
                "Browser-composited screenshot of the visible window region, including "
                "web controls, iframes and canvas content. Occluding windows remain "
                "visible, as they are to the user; hidden content is not reconstructed."
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
    async def desktop_screenshot(self, window_id: str, source: str = "screen") -> dict:
        """See what a desktop window currently shows, as an image.

        Captures the user's current Atrium tab, cropped to this window,
        including its chrome and web content. The first request waits for the
        user to share this tab; later requests reuse that sharing session.
        If sharing is declined or unsupported, no image is captured: do not
        retry without the user's intent to share. No app-export fallback.
        Returns the screenshot inline (vision-capable models) and saves it to
        `path`. Use it to verify visible results; state alone is not proof.
        Browser-frame pixels are not native input coordinates. Only when
        planning desktop_act pixel input, explicitly pass source="native"
        to export the owned native application's image in its input coordinate
        system. That export omits Atrium chrome/occlusion and is never used as
        a fallback when the user declines browser sharing.
        """
        window_id = normalize_window_reference(window_id)
        if source not in {"screen", "native"}:
            return {"success": False, "error": "source must be 'screen' or 'native'"}
        if source == "native":
            try:
                from .native_control import NativeWindowController
                native = await self._native_target(window_id)
                if native is None:
                    raise ValueError("This window has no native input surface; use source='screen'.")
                engine, target, targets = native
                shot = await engine.call(NativeWindowController(engine).screenshot(target["xid"]))
                data_url = shot.pop("data_url")
                return {**self._package_screenshot(data_url, "native-window", native=True), **shot,
                        "source": "native-application-export", "coordinate_space": "native-window-pixels",
                        "window_id": window_id, "native_windows": self._public_native_targets(targets)}
            except Exception as e:
                return {"success": False, "error": str(e)}
        anchor = self._presence().anchor_for(self._chat_id() or "")
        viewport_id = anchor.get("viewport_id")
        if not viewport_id:
            return {"success": False, "error": f"no desktop to ask — {anchor.get('reason')}"}
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_snapshots[request_id] = future
        event = {
            "type": "desktop.snapshot",
            "capture_mode": "browser-region-capture",
            "window_id": window_id,
            "request_id": request_id,
            "viewport_id": viewport_id,
            "timeout_ms": max(1, int((SNAPSHOT_TIMEOUT_SECONDS - 10) * 1000)),
        }
        completed = False
        try:
            if not await self._publish_desktop(event):
                return {"success": False, "error": "screenshot request could not be delivered"}
            data_url = await asyncio.wait_for(future, timeout=SNAPSHOT_TIMEOUT_SECONDS)
            completed = True
            return {**self._package_screenshot(data_url, window_id),
                    "source": "browser-region-capture", "viewport_id": viewport_id,
                    "coordinate_space": "browser-capture-pixels-not-native-input",
                    "window_id": window_id}
        except asyncio.TimeoutError:
            return {"success": False, "error": "screenshot authorization or capture timed out; no image was received"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._pending_snapshots.pop(request_id, None)
            if not future.done():
                future.cancel()
            if not completed:
                # Dismiss pending consent/capture when Stop cancels the tool,
                # or when its deadline expires, rather than leaving a stale CTA.
                await self._publish_desktop({**event, "type": "desktop.snapshot.cancel"})

    @tool
    async def serve_local_data(self, path: str, node_id: str | None = None) -> dict:
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

        if node_id:
            from pantheon.apps.builtin.fleet.local_node import local_node_id
            if local_node_id() != node_id:
                return {'success': False, 'error_code': 'different_file_node',
                        'error': 'This Desktop does not own the requested file node'}

        p = Path(path)
        if not p.is_absolute():
            from pantheon.settings import get_settings
            p = get_settings().work_dir / p
        p = p.resolve()
        if not p.exists():
            return {"success": False, "error": f"Path does not exist: {p}"}

        server = await self._ensure_data_server()
        from .data_server import TunnelNotReady

        try:
            url = server.url_for(p)
        except TunnelNotReady:
            return {
                "success": False,
                "error": (
                    f"{p} is servable, but no tunnel base has arrived yet — "
                    "a browser-reachable URL exists only after a desktop "
                    "connects (set_data_endpoint). Retry once a shell is up."
                ),
            }
        if url is None:
            roots = ", ".join(str(r) for r in server.roots)
            return {
                "success": False,
                "error": (
                    f"Path {p} is outside the LiveView data server roots "
                    f"({roots}). Put files to serve under the workspace."
                ),
            }
        return {"success": True, "base_url": server.base_url, "url": url, "node_id": node_id}

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
        # Start Chromium NOW, in the background. It takes a couple of
        # seconds warm and much longer on a cold profile, and it used to be
        # launched by the first page open itself — the prewarm that was
        # supposed to prevent that ran in the same call, after it. So the
        # first page a user opened always paid the full launch: measured at
        # 3.6 s against 1.4 s for the next one. This hook runs once, when
        # the hub hands over the tunnel, which is minutes before anybody
        # clicks anything.
        self._prewarm_browser()
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
        if isinstance(payload.get("window_id"), str):
            payload = {**payload, "window_id": normalize_window_reference(payload["window_id"])}
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
        try:
            published = await self._publish_desktop({
                "type": event_type,
                "request_id": request_id,
                "viewport_id": viewport_id,
                **payload,
            })
            if published is False:
                return {"success": False, "error": "The Desktop request could not be delivered. Reconnect the desktop before retrying."}
            value = await asyncio.wait_for(future, timeout=timeout)
            return {"success": True, "result": value}
        except asyncio.TimeoutError:
            return {"success": False, "request_id": request_id,
                    "completion": "unknown",
                    "window_id": payload.get("window_id", ""),
                    "action": payload.get("action", ""),
                    "error": f"the desktop did not answer in {timeout:g}s "
                             f"(asked the viewport that {anchor.get('reason')}). "
                             "The operation may still finish; read the existing window "
                             "before retrying. Do not open another window or repeat the action blindly."}
        except DesktopRequestError as e:
            return {"success": False, "error": str(e),
                    **({"result": e.value} if e.value is not None else {})}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._pending_desktop.pop(request_id, None)

    @tool(exclude=True)
    async def desktop_sync_apps(
        self, files: dict = {}, manifest: list | None = None,
    ) -> dict:
        """Write a batch of dev-app package files, in one call.

        The desktop writes every package under its `apps/` tree to the pod on
        connect, so the packaged path is exercised by the same session that is
        editing it. Fifty-eight files at one RPC each — plus a directory
        apiece and a sweep of what the tree no longer ships — is most of the
        wait a user spends looking at "Preparing the desktop", and none of it
        is work: it is round trips.

        Paths are relative to the workspace's `.pantheon/apps`, and stay
        there: this writes packages, not arbitrary files.

        Passing `manifest` (the complete list of relative paths this sync
        owns) also prunes what an earlier sync wrote and the tree no longer
        ships. Unconditional write-through is deliberate — a Volume outlives
        the code that wrote it, and "did it change" bookkeeping is how stale
        state survives — so this makes the honest thing cheap rather than
        replacing it with a guess.
        """
        from pathlib import Path

        from pantheon.settings import get_settings

        root = Path(get_settings().workspace) / ".pantheon" / "apps"

        def _safe(rel: str) -> Path | None:
            target = (root / rel).resolve()
            return target if target.is_relative_to(root.resolve()) else None

        def _write() -> dict:
            written, refused = 0, []
            for rel, content in (files or {}).items():
                target = _safe(str(rel))
                if target is None:
                    refused.append(str(rel))
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")
                written += 1
            pruned = 0
            if manifest is not None:
                keep = {str(r) for r in manifest}
                record = root / ".sync-manifest.json"
                try:
                    before = json.loads(record.read_text())
                except Exception:
                    before = []
                for rel in before:
                    if rel in keep:
                        continue
                    stale = _safe(str(rel))
                    if stale is not None and stale.exists():
                        stale.unlink()
                        pruned += 1
                record.parent.mkdir(parents=True, exist_ok=True)
                record.write_text(json.dumps(sorted(keep)))
                # User-space Apps are owned by the user. A development sync
                # must never delete those repositories or their local changes.
            return {"written": written, "pruned": pruned, "refused": refused}

        try:
            result = await asyncio.to_thread(_write)
            if result["refused"]:
                logger.warning("desktop: refused {} path(s) outside the app tree",
                               len(result["refused"]))
            return {"success": True, "root": str(root), **result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _app_scope_roots(self) -> list:
        """Where packaged apps live, in precedence order (first id wins).

        The user scopes come from the registry (one chain for every
        loader), then the builtin App tree. Headed surfaces let workspace
        shadow builtin — both ends are sandboxed (opaque iframe, no-creds
        stdio child), and the shadow is how an app is forked for
        development. The bus-service face keeps the opposite rule
        (registry.all_apps drops shadowers) because there it would take
        over credentials.
        """
        from pathlib import Path

        from pantheon.settings import get_settings

        from pantheon.apps.registry import BUILTIN_ROOT, default_scope_roots

        settings = get_settings()
        return [
            *default_scope_roots(Path(settings.workspace)),
            (BUILTIN_ROOT, "builtin"),
            # Pre-unification install path, still on older volumes.
            (Path("/app/pantheon/factory/templates/apps"), "builtin"),
        ]

    def _apps(self):
        """The packaged-app backend supervisor, built on first use."""
        if self._apps_supervisor is None:
            from pathlib import Path

            from pantheon.settings import get_settings

            from .app_supervisor import AppSupervisor

            async def _serve(path: str) -> str:
                res = await self.serve_local_data(path)
                if not res.get("success") or not res.get("url"):
                    raise RuntimeError(res.get("error") or "serve_local_data refused")
                return res["url"]

            self._apps_supervisor = AppSupervisor(
                workspace=Path(get_settings().workspace),
                roots=self._app_scope_roots(),
                serve=_serve,
            )
        return self._apps_supervisor

    @tool
    async def desktop_store_apps(self) -> dict:
        """Store inventory, including headless, built-in and shadowed App copies."""
        from .store_manager import AppStoreManager
        try:
            manager = AppStoreManager(self._app_scope_roots())
            migration = await asyncio.to_thread(manager.versions.ensure)
            result = await asyncio.to_thread(manager.inventory)
            result["warnings"].extend(migration["warnings"])
            supervisor = self._apps_supervisor
            async def add_icon(app):
                from pathlib import Path
                icon = app["manifest"].get("icon")
                relative = icon.get("path") if isinstance(icon, dict) else None
                if not isinstance(relative, str) or not relative:
                    return
                root = Path(app["dir"]).resolve()
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    return
                try:
                    served = await self.serve_local_data(str(path))
                    if served.get("success"):
                        app["icon_url"] = served.get("url")
                except Exception:
                    pass  # An unavailable icon must not hide an installed App.
            for app in result["apps"]:
                entry = supervisor.entries.get(app["id"]) if supervisor else None
                app["backend_state"] = entry.state if entry and str(entry.dir) == app["dir"] else None
            await asyncio.gather(*(add_icon(app) for app in result["apps"]))
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @tool
    async def desktop_store_manage(self, action: str, app_id: str = "", scope: str = "user", download: dict | None = None, version: str = "", archive_id: str = "", repository_id: str = "", name: str = "") -> dict:
        """Manage App Git repositories on this Desktop node.

        First use desktop_store_apps for app_id, scope and repository_id.
        Actions: fork (private clone, optional name), versions, tag (commit a
        new semantic version), default (version='latest' follows committed HEAD;
        tag/full SHA pins future launches), resolve (latest, tag or full SHA),
        instances (running backends), start/stop (one version backend), prepare (review a public release), remove
        (recoverable Trash), trash, restore, purge (permanently delete a Trash
        entry and its version snapshots after user confirmation). install accepts a Store download;
        fork_download imports a public release as a new private repository.
        A tag is LOCAL: only desktop_app_store(action='publish') makes it public.
        With no explicit default, new launches follow the newest personal fork's
        committed HEAD. Explicit Official or pinned choices remain in effect.
        Use desktop_app_develop for editing/testing on the correct node.
        """
        from .store_manager import AppStoreManager
        try:
            manager = AppStoreManager(self._app_scope_roots())
            if action == 'purge':
                info = await asyncio.to_thread(manager.branches.trash_entry, archive_id)
                store = self._desktop()
                store.current()
                for window in store.session.windows.values():
                    revision = (window.get('args') or {}).get('appRevision') or {}
                    if revision.get('repository_id') == info['repository_id'] or (not revision.get('repository_id') and
                            (window.get('app_id') or '').removeprefix('pkg:') == info['app_id']):
                        raise ValueError('Close this App’s windows before permanently deleting its files')
                supervisor = self._apps_supervisor
                if supervisor:
                    for process in supervisor.procs.values():
                        if process.proc.returncode is None and (process.entry.repository_id == info['repository_id'] or
                                (not process.entry.repository_id and process.entry.app_id == info['app_id'])):
                            raise ValueError('Stop this App’s running backends before permanently deleting its files')
                return await asyncio.to_thread(manager.branches.purge, archive_id)
            if action == "install":
                payload = download or {}
                declared = (payload.get("app_release") or {}).get("app_id")
                if not declared:
                    for path, content in (payload.get("files") or {}).items():
                        if path in ("app.json", "atrium.json", f"{payload.get('name')}/app.json", f"{payload.get('name')}/atrium.json"):
                            declared = json.loads(content).get("id")
                            break
                if not declared:
                    raise ValueError("App download has no manifest identity; refresh the Store release")
                app_id = declared
            operations = {
                "published": lambda: manager.bind_publication(app_id, scope, repository_id, download or {}),
                "install": lambda: manager.install(download or {}, app_id),
                "copy": lambda: manager.branches.fork_app(app_id, scope, version, repository_id, name),
                "fork": lambda: manager.branches.fork_app(app_id, scope, version, repository_id, name),
                "fork_download": lambda: manager.branches.fork_download(download or {}, name),
                "trash": manager.branches.list_trash,
                "restore": lambda: manager.branches.restore(archive_id),
                "remove": lambda: manager.remove(app_id, scope, repository_id),
                "prepare": lambda: manager.prepare(app_id, scope, repository_id),
                "tag": lambda: manager.tag(app_id, version, scope, repository_id),
                "initialize": manager.versions.ensure,
                "versions": lambda: manager.versions.versions(app_id, scope, repository_id),
                "default": lambda: manager.versions.set_default(app_id, scope, version, repository_id),
                "resolve": lambda: manager.versions.resolve(app_id, scope, version, repository_id),
            }
            if action == 'instances':
                return {'success': True, 'instances': self._apps().instances()}
            if action == 'stop':
                resolved = await asyncio.to_thread(manager.versions.resolve, app_id, scope, version, repository_id)
                key = f"{app_id}@{resolved['repository_id']}:{resolved['revision']['commit']}"
                return await self._apps().stop(key)
            if action == "start":
                resolved = await asyncio.to_thread(manager.versions.resolve, app_id, scope, version, repository_id)
                result = await self._apps().call(app_id, None, {}, 60, pinned=resolved)
                return {"success": True, "instance": result}
            if action not in operations:
                raise ValueError(f"Unknown App Store action: {action}")
            if action in ("published", "fork_download", "prepare", "initialize", "versions", "default", "resolve", "fork", "copy", "trash", "restore") or (action == "remove" and scope == "fork"):
                return await asyncio.to_thread(operations[action])
            supervisor = self._apps()
            # Share the spawn lock: an App cannot start halfway through its
            # installation changing. Never interrupt an existing backend.
            lock = supervisor._locks.setdefault(app_id, asyncio.Lock())
            async with lock:
                process = supervisor.procs.get(app_id)
                if process and process.proc.returncode is None:
                    raise ValueError("Stop the running App backend before changing its installation")
                result = await asyncio.to_thread(operations[action])
                supervisor.scan()
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @tool
    async def desktop_store_git(self, app_id: str, scope: str = "user", limit: int = 100, repository_id: str = "") -> dict:
        """Read an App's Git DAG, including branches, tags and merge parents."""
        from .store_manager import AppStoreManager
        try:
            return await asyncio.to_thread(AppStoreManager(self._app_scope_roots()).history, app_id, scope, limit, repository_id)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @tool
    async def desktop_app_develop(self, action: str, app_id: str, repository_id: str = "", scope: str = "",
                                  path: str = "", files: dict | None = None, branch: str = "", message: str = "",
                                  expected_commit: str = "", command: list[str] | None = None) -> dict:
        """Develop App code on the Desktop node that actually owns its Git repo.

        Actions: create (new private App), status, files, read (relative path),
        write (files mapping), diff, branch (create and switch), switch, merge,
        commit (message), test (argv array, 120s). Use repository_id from Store
        inventory. Fork official/public sources first. Read before editing;
        expected_commit guards against concurrent commits. Merge conflicts stay
        in the working tree for resolution; read files, fix, then commit.
        Create a local version with desktop_store_manage(action='tag'), then
        desktop_open(revision=...) or app_call(revision=...) to validate it.
        """
        from .app_development import develop
        from .store_manager import AppStoreManager
        try:
            return await asyncio.to_thread(develop, AppStoreManager(self._app_scope_roots()), action,
                app_id, scope, repository_id, path, files, branch, message, expected_commit, command)
        except (ValueError, OSError) as exc:
            return {"success": False, "error": str(exc)}

    @tool
    async def desktop_app_store(self, action: str, app_id: str = "", repository_id: str = "", scope: str = "user",
                                version: str = "", name: str = "", query: str = "", changelog: str = "",
                                expected_commit: str = "", target_repository_id: str = "", expected_base: str = "",
                                request_id: str = "", title: str = "", description: str = "",
                                decision: str = "", inbox: str = "all") -> dict:
        """Use public App Git repositories in Store with the user's identity.

        search(query); inspect(repository_id); fork(repository_id, version)
        creates a PRIVATE local Git fork (automatic latest-commit default unless
        the user chose another default explicitly); publish
        uploads a prepared tag and its history to this user's public repository.
        Publish only when the user asked to share/release publicly. First call
        desktop_store_manage('prepare') to review, then pass expected_commit.
        name is a unique Store slug on first publication. Subsequent releases
        use the same repository_id; existing tags cannot be changed.
        fetch imports upstream release refs without merging or changing files.

        community(app_id) lists public repositories and the designated Official
        upstream. contributions(app_id?, repository_id?, inbox='all'|'mine'|'review')
        lists requests; inspect_contribution(request_id) returns pinned diffs.
        submit(repository_id, version, target_repository_id, expected_commit,
        expected_base, title, description) proposes an already-public release.
        Submit only when asked to contribute upstream.
        Maintainers: prepare_merge(request_id, version) creates a candidate;
        checkout_review(request_id, expected_commit) puts its verified Git tree
        in a PRIVATE directory on this node, without running code or changing
        installations. Inspect and test it, then review(request_id,
        decision='approve'|'reject', expected_commit, description=review_notes).
        merge(request_id, expected_commit) explicitly PUBLISHES the approved
        merge. Only do this when authorized to merge and release upstream.
        Changed upstream/candidate commits require a fresh review.
        close_contribution(request_id) withdraws an unmerged request.
        """
        from .app_store_client import store_action
        from .store_manager import AppStoreManager
        try:
            return await store_action(AppStoreManager(self._app_scope_roots()), action, app_id,
                                      repository_id, scope, version, name, query, changelog, expected_commit,
                                      target_repository_id=target_repository_id, expected_base=expected_base,
                                      request_id=request_id, title=title, description=description, decision=decision, inbox=inbox)
        except (ValueError, OSError) as exc:
            return {"success": False, "error": str(exc)}

    async def cleanup(self):
        if self._apps_supervisor is not None:
            await self._apps_supervisor.shutdown()
        await super().cleanup()

    @tool
    async def app_call(
        self,
        app_id: str,
        method: str,
        args: dict | None = None,
        timeout_s: float = 60.0,
        revision: dict | None = None,
    ) -> dict:
        """Call a method on a packaged app's backend process.

        The backend is spawned lazily under this pod's supervisor and
        reused across calls; methods come from the app's own registration
        (see `desktop_apps` for which apps have a backend, `app_registry`
        for the exact method list). Errors carry the reason — an unknown
        method is refused from the registration table, and a crashed
        backend reports its stderr tail rather than a timeout.
        """
        try:
            pinned = None
            from .store_manager import AppStoreManager
            manager = AppStoreManager(self._app_scope_roots())
            revision = revision or await asyncio.to_thread(manager.versions.launch_default, app_id)
            if revision:
                from .store_manager import AppStoreManager
                pinned = await asyncio.to_thread(manager.versions.resolve,
                                                 app_id, revision.get('scope', ''), 'latest' if revision.get('mode') == 'latest' else revision.get('commit', ''), revision.get('repository_id', ''))
            result = await self._apps().call(app_id, method, args, timeout_s, pinned=pinned)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def app_registry(self) -> dict:
        """Every packaged-app backend: id, state, methods. Rescans scopes.

        The launchpad and Interfaces views poll this for lifecycle dots and
        the callable surface; the unconditional rescan is also the recovery
        path when an install lands between scans.
        """
        try:
            apps = await asyncio.to_thread(self._apps().scan)
            return {"success": True, "apps": apps, "instances": self._apps().instances()}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def desktop_app_registry(self) -> dict:
        """Every packaged app on this pod: manifest, directory, icon URL.

        The desktop assembled this itself — list each scope, read each
        app's atrium.json, then ask for each icon's URL — which is a couple
        of dozen round trips, one after another, while the user watches
        "Preparing the desktop". All of it is on local disk here.

        Scopes are searched workspace, then user, then builtin, and the
        first manifest to claim an id wins, which is the order the desktop
        used. A scope that cannot be listed is reported rather than
        silently treated as empty: booting with no apps and no complaint is
        the failure this replaces.
        """
        from .store_manager import AppStoreManager
        manager = AppStoreManager(self._app_scope_roots())
        roots = [*self._app_scope_roots(), (manager.records / 'installed', 'user'), (manager.branches.root, 'fork')]

        def _scan() -> tuple[list[dict], list[str], int]:
            apps: list[dict] = []
            seen: set[str] = set()
            failed: list[str] = []
            looked = 0
            for root, scope in roots:
                try:
                    if not root.is_dir():
                        continue
                    entries = sorted(p for p in root.iterdir() if p.is_dir())
                    looked += 1
                except Exception as e:
                    failed.append(f"{root}: {e}")
                    continue
                for app_dir in entries:
                    raw = next((app_dir / n for n in ("app.json", "atrium.json")
                                if (app_dir / n).is_file()), None)
                    if raw is None:
                        continue
                    try:
                        manifest = json.loads(raw.read_text())
                    except Exception:
                        continue
                    app_id = manifest.get("id")
                    frontend = (manifest.get("entry") or {}).get("frontend") or ""
                    if not app_id or app_id in seen:
                        continue
                    # Only apps with a LOADABLE frontend are desktop windows:
                    # headless services and not-yet-bundled placeholders
                    # ("ui:<id>", still compiled into the shell) stay out.
                    if not frontend or frontend.startswith("ui:"):
                        continue
                    seen.add(app_id)
                    apps.append({"manifest": manifest, "dir": str(app_dir),
                                 "scope": scope,
                                 "icon_path": (manifest.get("icon") or {}).get("path")})
            return apps, failed, looked

        try:
            apps, failed, looked = await asyncio.to_thread(_scan)
        except Exception as e:
            return {"success": False, "error": str(e)}
        # Icons are served, not read: minting the URL here saves the desktop
        # a call per app, and an app whose icon will not serve still installs.
        for app in apps:
            rel = app.pop("icon_path", None)
            if not rel:
                continue
            try:
                served = await self.serve_local_data(f"{app['dir']}/{rel}")
                if served.get("success") and served.get("url"):
                    app["icon_url"] = served["url"]
            except Exception:
                pass
        return {"success": True, "apps": apps, "scopes_read": looked,
                "unreadable": failed}

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
        reply = await self._desktop_request("desktop.apps", {})
        if reply.get("success"):
            self._apps().scan()
            for app in (reply.get("result") or {}).get("apps", []):
                app_id = app.get("app_id") or ""
                entry = self._apps().entries.get(app_id.removeprefix("pkg:"))
                frontend = ((entry.manifest.get("entry") or {}).get("frontend") or "") if entry else ""
                metadata = self._desktop_app_metadata(
                    "pkg:" + app_id.removeprefix("pkg:") if frontend and not frontend.startswith("ui:") else app_id,
                )
                if metadata:
                    app["actions"] = sorted(set(app.get("actions") or []) | set(metadata["actions"]))
                    app["controllable"] = metadata["controllable"]
                    if metadata.get("skill"):
                        app["skill"] = metadata["skill"]
        return reply

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
        for packaged-app windows), `actions` and `action_specs` (parameter names
        and descriptions; pass these parameters in desktop_call args), and `pty_session`
        for a Terminal window.

        This is how you reach windows the USER opened: find its window_id
        here, then desktop_read / desktop_update / desktop_call it exactly
        like a view you opened yourself.

        A Terminal window exposes desktop_read and desktop_call actions
        run, input, interrupt and clear. They reach the PTY shown in that
        existing window. Direct pty_write(pty_session, base64(text)) is also
        available. Files exposes its current folder and selection actions.
        Controllable describes the app's supported interface; a window
        that is still loading must finish mounting before UI calls answer.
        Status "open" describes the window's existence, not a loading
        verdict. Read its current state with desktop_read before waiting.
        """
        # Answered from the pod's own document, so this works with no desktop
        # on screen at all — the window list is a property of the machine, and
        # asking a browser for it was what produced "the desktop did not
        # answer" for a question the pod could always answer itself.
        store = self._desktop()
        store.current()          # read the record through before answering
        s = store.session
        self._apps().scan()
        windows = [
            {
                "window_id": wid,
                "app_id": (w.get("app_id") or "").removeprefix("pkg:"),
                "name": w.get("app_id"),
                **self._desktop_app_metadata(w.get("app_id"), (w.get("args") or {}).get("appRevision")),
                "title": w.get("title"),
                "path": w.get("path") or None,
                "space": w.get("space", 1),
                "minimized": bool(w.get("minimized")),
                # The session's old 'opening' value is a lifecycle marker,
                # not live frontend readiness. Do not make an agent wait on
                # a value that never changes after a Browser has rendered.
                "status": "open",
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

    @tool(exclude=True)
    async def fleet_instances(self) -> dict:
        """App windows and packaged backends owned by this Desktop node."""
        store = self._desktop()
        store.current()
        instances = []
        for wid, window in store.session.windows.items():
            revision = (window.get('args') or {}).get('appRevision') or {}
            if not isinstance(revision, dict):
                revision = {}
            instances.append({'app_id': (window.get('app_id') or '').removeprefix('pkg:'),
                              'scope': wid, 'title': window.get('title'), 'kind': 'window',
                              'version': revision.get('version'), 'health': 'open'})
        for instance in self._apps().instances():
            instances.append({'app_id': instance['id'], 'scope': instance['instance_id'],
                              'version': instance.get('version'), 'kind': 'backend',
                              'health': 'healthy', 'pid': instance.get('pid')})
        return {'success': True, 'instances': instances}

    @tool
    async def desktop_open(
        self, app: str = "", path: str = "", state: dict = {}, window_id: str = "",
        module: str = "", title: str = "", revision: dict | None = None,
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
            revision: explicit {repository_id, scope, commit} from Store resolve.
                Opens that immutable revision in a new window, leaving existing
                instances and the default untouched. Omit to use the default.

        Returns `window_id`, and `reused: true` when it landed in a window
        that was already showing that file. Cold-starting ImageJ can take
        several minutes; a failed or still-loading reply identifies the
        existing window to reuse instead of launching another instance.
        """
        if revision:
            if module or window_id or not app:
                return {"success": False, "error": "A version launch requires app and a new window"}
            from .store_manager import AppStoreManager
            try:
                resolved = await asyncio.to_thread(AppStoreManager(self._app_scope_roots()).versions.resolve,
                    app.removeprefix('pkg:'), revision.get('scope', ''), revision.get('commit', ''), revision.get('repository_id', ''))
                revision = resolved['revision']
            except (ValueError, OSError) as exc:
                return {"success": False, "error": str(exc)}
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
            {"app": app, "path": path, "state": state or {}, "window_id": window_id, "revision": revision},
            # The UI waits up to 240s for a cold ImageJ JVM and initial image.
            # Other apps report their own shorter startup deadline promptly.
            timeout=270.0)

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
        from .data_server import TunnelNotReady

        try:
            return server.url_for(mod_path)
        except TunnelNotReady:
            return None

    @tool
    async def desktop_set(self, window_id: str, state: dict) -> dict:
        """Replace a window's state wholesale — the fix-in-place for apps
        whose state IS a config (Vitessce, Gosling, IGV).

        desktop_update deep-merges, so it can add and change but never
        remove; when the config is wrong rather than incomplete, set the
        whole corrected one here instead of opening another window.
        """
        return await self._desktop_request(
            "desktop.set", {"window_id": window_id, "state": state or {}},
            timeout=self._state_request_timeout(window_id, state))

    def _state_request_timeout(self, window_id: str, state: dict | None) -> float:
        if isinstance((state or {}).get("url"), str):
            try:
                if self._desktop_window(window_id).get("app_id") == "browser":
                    return BROWSER_WINDOW_TIMEOUT_SECONDS
            except (KeyError, ValueError):
                pass  # The frontend will report the missing window.
        return 30.0

    @tool
    async def desktop_read(self, window_id: str) -> dict:
        """Read a window's current state (the same shape its skill documents).

        Works on any packaged-app window, including ones the user opened.
        """
        window_id = normalize_window_reference(window_id)
        try:
            w = self._desktop_window(window_id.split("::native:", 1)[0])
            if w.get("app_id") in {"qupath", "pkg:qupath"}:
                engine = self._browser_engine()
                state = await engine.call(engine.native_apps().read(window_id.split("::native:", 1)[0]))
                native = await self._native_target(window_id)
                return {"success": self._native_result_ok(state), "result": {**state, "window_id": window_id,
                        "native_windows": self._public_native_targets(native[2])}}
            if w.get("app_id") == "browser":
                engine = self._browser_engine()
                session = await self._resolve_control_page(engine, window_id.split("::native:", 1)[0])
                native = await self._native_target(window_id)
                return {"success": True, "result": {"window_id": window_id,
                        **await self._browser_page_info(session),
                        "native_windows": self._public_native_targets(native[2])}}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return await self._desktop_request("desktop.read", {"window_id": window_id})

    def _desktop_window(self, window_id: str) -> dict:
        window_id = normalize_window_reference(window_id)
        store = self._desktop()
        store.current()
        window = (store.session.windows or {}).get(window_id)
        if not window:
            raise KeyError(f"No such desktop window: {window_id}")
        return window

    def _desktop_app_metadata(self, app_id: str | None, revision: dict | None = None) -> dict:
        """Describe the implementation the shell actually mounted.

        Packaged ids use the install-scope catalog, including workspace
        overrides. Their host supplies a state bridge without named actions.
        Agent View mounts the same state bridge for user-authored modules.
        Other shell placeholders require their own declared bridge actions.
        """
        from pathlib import Path
        from pantheon.apps.registry import by_app_id

        app_id = app_id or ""
        if revision and app_id.startswith('pkg:'):
            from .store_manager import AppStoreManager
            resolved = AppStoreManager(self._app_scope_roots()).versions.resolve(
                app_id[4:], revision.get('scope', ''), revision.get('commit', ''), revision.get('repository_id', ''))
            manifest = resolved['manifest']
            directory = resolved['dir']
        elif app_id.startswith("pkg:"):
            app = self._apps().entries.get(app_id[4:])
            manifest = app.manifest if app else {}
            directory = app.dir if app else None
        else:
            app = by_app_id().get(app_id)
            manifest = app.manifest.model_dump() if app else {}
            directory = app.dir if app else None
        frontend = (manifest.get("entry") or {}).get("frontend") or ""
        specs = [action for action in manifest.get("actions", []) if "name" in action]
        actions = [action["name"] for action in specs]
        skill = manifest.get("skill")
        return {
            "name": manifest.get("name") or app_id.removeprefix("pkg:"),
            "actions": actions,
            "action_specs": specs,
            "skill": str(Path(directory) / skill) if skill and directory else None,
            "controllable": bool(frontend) and (
                not frontend.startswith("ui:") or frontend == "ui:agent-view" or bool(actions)
            ),
        }

    @staticmethod
    def _native_result_ok(result: dict) -> bool:
        return result.get("success", True) is not False and result.get("state") not in {
            "failed", "expired", "cancelled", "unknown",
        }

    @staticmethod
    def _public_native_targets(targets: list[dict]) -> list[dict]:
        return [{key: value for key, value in item.items() if key != "xid" and not key.startswith('_')} for item in targets]

    async def _native_target(self, window_id: str):
        window_id = normalize_window_reference(window_id)
        from .native_targets import window_targets

        parent_id = window_id.split("::native:", 1)[0]
        w = self._desktop_window(parent_id)
        if w.get("app_id") not in {"qupath", "pkg:qupath", "browser"}:
            if parent_id != window_id:
                raise ValueError("This app has no native child windows")
            return None
        engine = self._browser_engine()
        if w["app_id"] in {"qupath", "pkg:qupath"}:
            status = await engine.call(engine.native_apps().status(parent_id))
            if not status.get("running"):
                raise RuntimeError("The requested native app session is not running")
            window_class = status["window_class"]
        else:
            from .browser import PAGE_CLASS_PREFIX

            binding = self._resolve_page(engine, parent_id)
            window_class = getattr(binding, "window_class", PAGE_CLASS_PREFIX + binding.id)
        targets = await asyncio.to_thread(window_targets, engine, parent_id, window_class)
        target = next((item for item in targets if item["window_id"] == window_id), None)
        if target is None:
            raise ValueError("The requested native child window is no longer owned by this app")
        return engine, target, targets

    @tool
    async def desktop_act(self, window_id: str, actions: list[dict]) -> dict:
        """Operate an existing native desktop window, including browser chrome.

        First use desktop_screenshot for native_window pixel coordinates.
        Each action has type: click/rightclick/dblclick/move (x,y), drag
        (x,y,to_x,to_y,duration_ms<=2000), wheel (x,y,delta_x/delta_y integer
        steps), key (key='Ctrl+s'), or text (text, at most 2000 characters).
        Up to 32 actions, validated before input. Native dialog window_ids
        come from native_windows in desktop_read or desktop_screenshot.
        Coordinates are native-window pixels, NOT the default browser-frame
        screenshot. Use desktop_screenshot(source="native") to plan pixel
        input, and source="screen" to verify what the user actually sees.
        Re-read/screenshot afterward to verify the actual effect. A failed
        batch can have completed earlier actions; never retry blindly.
        """
        try:
            from .native_control import NativeWindowController

            native = await self._native_target(window_id)
            if native is None:
                raise ValueError("Use desktop_call for this app's declared actions")
            engine, target, targets = native
            result = await engine.call(NativeWindowController(engine).act(
                target["xid"], actions, allowed_xids={item["xid"] for item in targets},
                focus_parent_xids=target.get('_focus_parent_xids', ())))
            return {**result, "window_id": window_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

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
        window_id = normalize_window_reference(window_id)
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
            "desktop.update", {"window_id": window_id, "patch": patch},
            timeout=self._state_request_timeout(window_id, patch))

    @tool
    async def desktop_call(
        self, window_id: str, action: str = "", args: dict | None = None,
        kwargs: dict | None = None, _action: str = "", _args: dict | None = None,
    ) -> dict:
        """Invoke a window action. desktop_windows returns action_specs with parameters.

        Pass action parameters together in args, for example:
        desktop_call(window_id="win-1", action="add_cell",
                     args={"content": "1 + 1", "execute": True}).
        kwargs is a compatibility alias for args. Conflicting values are rejected
        before dispatch. Also accepts action "$close" to close the window.
        """
        window_id = normalize_window_reference(window_id)
        if not action:
            action = str(_action or "")
        # Older schemas exposed **kwargs as a literal object. Never discard it:
        # add_cell with a dropped payload created an empty cell and said success.
        merged = {}
        for label, payload in (("args", args), ("kwargs", kwargs), ("_args", _args)):
            if payload is None:
                continue
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    return {"success": False, "error": f"{label} must be an object"}
            if not isinstance(payload, dict):
                return {"success": False, "error": f"{label} must be an object"}
            for key, value in payload.items():
                if key in merged and merged[key] != value:
                    return {"success": False, "error": f"Conflicting action parameter '{key}' in args/kwargs"}
                merged[key] = value
        args = merged
        if not action:
            return {"success": False,
                    "error": "desktop_call needs an action name — desktop_windows() lists each "
                             "window's actions"}
        try:
            w = self._desktop_window(window_id)
            if w.get("app_id") in {"qupath", "pkg:qupath"}:
                engine = self._browser_engine()
                native_action = "close" if action == "$close" else action
                value = await engine.call(engine.native_apps().call(window_id, native_action, args or {}))
                return {"success": self._native_result_ok(value), "result": value,
                        "window_id": window_id, "action": action}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return await self._desktop_request(
            "desktop.call", {"window_id": window_id, "action": action, "args": args or {}},
            timeout=(BROWSER_WINDOW_TIMEOUT_SECONDS
                     if w.get("app_id") == "browser" and action in {"newPage", "navigate"}
                     else 60.0))

    @tool(exclude=True)
    async def report_desktop_result(
        self, request_id: str, ok: bool = True, value: Any = None, error: str = "",
    ) -> dict:
        """UI-only: the desktop answers a desktop.* request."""
        future = self._pending_desktop.get(request_id)
        if future is None or future.done():
            return {"success": False, "error": "unknown or expired request"}
        if not future.done():
            if ok:
                future.set_result(value)
            else:
                future.set_exception(DesktopRequestError(error or "desktop request failed", value))
        return {"success": True}

    @tool(exclude=True)
    async def report_snapshot(
        self,
        request_id: str,
        ok: bool,
        data_url: str | None = None,
        error: str | None = None,
        source: str | None = None,
    ) -> dict:
        """UI → backend: deliver a captured snapshot, resolving the pending
        desktop_screenshot."""
        future = self._pending_snapshots.get(request_id)
        if future is None or future.done():
            return {"success": False, "error": "unknown or expired screenshot request"}
        if ok and source != "browser-region-capture":
            future.set_exception(RuntimeError("This desktop needs a frontend refresh to use browser screen capture. No app-exported image was accepted."))
        elif ok and data_url:
            future.set_result(data_url)
        else:
            future.set_exception(RuntimeError(error or "snapshot failed"))
        return {"success": True}

    def _browser_engine(self):
        from .browser import BrowserEngine

        engine = BrowserEngine.instance()
        if getattr(engine, "on_popup_page", None) is None:
            # Seamless: a popup is its own Chromium window and gets its own
            # Atrium window — the engine tells us, we ask the desktop.
            engine.on_popup_page = self._show_popup_page
        return engine

    async def _show_popup_page(self, session) -> None:
        """Persist one host per native popup binding, even after RPC retries."""
        store = self._desktop()
        store.current()
        for window in (store.session.windows or {}).values():
            args = window.get("args") or {}
            token = ((args.get("shared") or {}).get("v") or {}).get("page") or args.get("page_id")
            if window.get("app_id") == "browser" and token == session.id:
                return
        # The machine already owns this real native window. Publishing its
        # durable host directly avoids an uncertain frontend request creating
        # duplicate shells when a callback is retried after a timeout.
        ops, _ = store.apply("open", {
            "app_id": "browser", "title": "Browser", "width": 1100, "height": 800,
            "args": {"page_id": session.id,
                     "shared": {"by": "browser", "v": {"page": session.id}}},
        })
        await self._publish_desktop({"type": "desktop.delta", "seq": store.session.seq, "ops": ops})

    def _prewarm_browser(self) -> None:
        """Launch Chromium in the background, at most once."""
        if getattr(self, "_prewarming", False):
            return
        self._prewarming = True
        engine = self._browser_engine()

        async def _run() -> None:
            try:
                await engine.call(engine._ensure_browser())
                logger.info("browser: prewarmed")
            except Exception as e:
                logger.info("browser: prewarm skipped ({})", e)

        asyncio.ensure_future(_run())

    def _resolve_page(self, engine, page_id: str = ""):
        """The addressed page — accepting a desktop Browser WINDOW id too.

        Agents naturally pass the window id they referenced (#app:win-N); a
        window is not a page, but its shared tab state names one, so the
        natural guess resolves instead of dead-ending. Failures explain the
        namespaces and the way forward.
        """
        page_id = normalize_window_reference(page_id)
        try:
            return engine.latest(page_id)
        except KeyError:
            if page_id:
                store = self._desktop()
                store.current()
                w = (store.session.windows or {}).get(page_id)
                if w and w.get("app_id") == "browser":
                    from .desktop_session import browser_page_reference

                    if (w.get("args") or {}).get("browser_binding"):
                        bound = browser_page_reference(w)
                        if bound:
                            return engine.window_binding(bound)
                        raise KeyError(f"Browser window '{page_id}' has no live page yet")
                    shared = ((w.get("args") or {}).get("shared") or {}).get("v") or {}
                    # Seamless Browser hosts one native window. Its durable
                    # page lives at `page`; pages/active is the legacy host.
                    if "page" in shared:
                        pid = shared.get("page")
                        if pid:
                            return engine.window_binding(pid)
                        raise KeyError(f"Browser window '{page_id}' has no live page yet")
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
                        "empty New Tab with no live page. Wait for this window to finish "
                        "opening and retry; do not create a replacement for an explicitly requested window.")
            known = sorted(getattr(engine, "pages", {}).keys())
            raise KeyError(
                f"no such page: {page_id!r} — page ids come from browser_open "
                f"(currently open: {known if known else 'none'}). A desktop window id "
                "only resolves for Browser windows.")

    async def _resolve_control_page(self, engine, reference: str = ""):
        reference = normalize_window_reference(reference)
        anchor = self._resolve_page(engine, reference)
        if reference:
            store = self._desktop()
            store.current()
            window = (store.session.windows or {}).get(reference)
            if window and window.get("app_id") == "browser":
                # A stable native window can contain user-created tabs which
                # were never opened through browser_open. Resolve its visible
                # tab without switching another window or changing WM_CLASS.
                return await engine.call(engine.current_window_page(anchor))
        return anchor

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
            window_id: an EXISTING Browser window to reuse. Navigates its
                current shared page, preserving its native window. An unknown
                or closed target fails without creating a replacement.

        Returns `page_id` for the other browser_* tools, plus url/title, and
        `window_id` when a desktop window was opened or reused.
        """
        try:
            from .browser import normalize_url

            engine = self._browser_engine()
            # "Use this window" navigates its existing page. Creating a new
            # native Chromium window first both loses the user's target and
            # can leave an unadopted window behind when resolution fails.
            window_id = normalize_window_reference(window_id)
            if window_id:
                session = await self._resolve_control_page(engine, window_id)
                if url:
                    await engine.call(engine.navigate(session.id, "goto", normalize_url(url)))
                return {"success": True, **await self._browser_page_info(session),
                        "window_id": window_id, "reused": True}
            session = await engine.call(engine.open_page(normalize_url(url)))
            info = await self._browser_page_info(session)
            result: dict = {"success": True, **info}
            if show:
                shown = await self._desktop_request("desktop.open", {
                    "app": "browser", "path": "",
                    "state": {"page_id": session.id}, "window_id": "",
                }, timeout=270.0)
                if shown.get("success"):
                    result["window_id"] = (shown.get("result") or {}).get("window_id")
                else:
                    result["success"] = False
                    result["shown"] = None if shown.get("completion") == "unknown" else False
                    result["error"] = shown.get("error") or "The browser page could not be shown on the desktop"
                    result["show_error"] = shown.get("error")
                    for key in ("request_id", "completion"):
                        if key in shown:
                            result[key] = shown[key]
                    if (shown.get("result") or {}).get("window_id"):
                        result["window_id"] = shown["result"]["window_id"]
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_goto(self, url: str, page_id: str = "") -> dict:
        """Navigate a browser page (the newest one unless `page_id` says
        otherwise). The user watching the window sees the navigation live."""
        try:
            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            await engine.call(engine.navigate(session.id, "goto", url))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_read(self, page_id: str = "") -> dict:
        """Read a shared browser page: title, URL, text (~8k chars), and up to
        100 interactive elements with names and exact CSS selectors. Use the
        returned element selector with browser_click/browser_type instead of
        guessing from text. Read again after navigation or DOM changes; these
        selectors describe the current main document. For canvas, iframe or
        shadow content not listed here, use browser_screenshot/browser_act."""
        try:
            from .browser import READ_LIMIT
            from .browser_snapshot import BROWSER_SNAPSHOT_JS, ELEMENT_LIMIT

            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            snapshot = await engine.call(session.page.evaluate(
                BROWSER_SNAPSHOT_JS,
                {"textLimit": READ_LIMIT, "elementLimit": ELEMENT_LIMIT},
            ))
            text = snapshot.pop("text")
            if len(text) > READ_LIMIT:
                text = text[:READ_LIMIT] + "\n… (truncated)"
            return {"success": True, **await self._browser_page_info(session),
                    "text": text, **snapshot}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_click(self, selector: str, page_id: str = "") -> dict:
        """Click an element on a browser page by CSS selector (or text=...,
        role=... — any Playwright selector). Prefer an exact selector returned
        by browser_read; text may also match explanatory paragraphs.
        5s timeout when nothing matches."""
        try:
            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            await engine.call(session.page.click(selector, timeout=5000))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_type(
        self, selector: str, text: str, submit: bool = False, page_id: str = "",
    ) -> dict:
        """Fill a field using its selector from browser_read (replaces its
        value). `submit` presses Enter afterwards."""
        try:
            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            await engine.call(session.page.fill(selector, text, timeout=5000))
            if submit:
                await engine.call(session.page.press(selector, "Enter"))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_act(self, actions: list[dict], page_id: str = "") -> dict:
        """Act on a browser page by COORDINATE — what a hand would do.

        `browser_click` needs a selector, which a canvas, a map, a PDF or a
        chart does not have. This takes the same input path the user's
        mouse and keyboard travel, so anything they can do here, so can an
        agent — and the user watches it happen, because it is one browser.

        Coordinates are CSS pixels of the page, the same ones
        `browser_screenshot` is measured in. Actions, in order:

            {"t": "click", "x": 400, "y": 220}     also dblclick, rightclick
            {"t": "move" | "down" | "up", "x":…, "y":…, "button": 0}
            {"t": "drag", "x":…, "y":…, "to_x":…, "to_y":…}
            {"t": "wheel", "dy": 300, "x":…, "y":…}
            {"t": "key", "key": "Enter"}           any Playwright key name
            {"t": "text", "text": "hello"}
            {"t": "scroll", "y": 1200}             absolute, in page pixels

        A page the viewer is rendering NATIVELY (a sandbox-local file) is a
        second copy of that file in this browser, not the copy on their
        screen: the viewer is asked to reload afterwards, so anything the
        action wrote to disk appears, but in-memory state will not.
        """
        from .browser import input_events

        try:
            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            events = input_events(list(actions or []))
            if not events:
                return {"success": False, "error": "no actions"}
            await engine.call(engine.dispatch(session.id, events))
            return {"success": True, **await self._browser_page_info(session)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def browser_scroll(
        self, dy: float = 0, dx: float = 0, to: str = "", page_id: str = "",
    ) -> dict:
        """Scroll a browser page: by `dy`/`dx` pixels, or `to` "top"/"bottom".

        A screenshot shows one screenful. This is how an agent reads the
        rest of a page it is looking at rather than parsing it — and it is
        the same scroll the user's wheel produces, on the page they can see.
        """
        try:
            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            where = str(to or "").strip().lower()
            if where in ("top", "bottom"):
                y = 0 if where == "top" else 10 ** 7
                await engine.call(engine.dispatch(session.id, [{"t": "scroll", "y": y}]))
            elif where:
                return {"success": False,
                        "error": f'to must be "top" or "bottom", not {to!r}'}
            else:
                await engine.call(engine.dispatch(
                    session.id, [{"t": "wheel", "dx": float(dx), "dy": float(dy),
                                  "x": 0, "y": 0}]))
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
            session = await self._resolve_control_page(engine, page_id)
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
        """Close the explicitly addressed browser tab.

        Accepts an exact page_id, or a desktop Browser window_id to close that
        window's active tab. Other tabs remain open; closing the last tab also
        closes its native window. Unknown or already-closed targets fail.
        """
        try:
            if not isinstance(page_id, str) or not page_id.strip():
                raise ValueError("browser_close requires a page_id or Browser window_id")
            engine = self._browser_engine()
            session = await self._resolve_control_page(engine, page_id)
            await engine.call(engine.close_page(session.id))
            return {"success": True, "page_id": session.id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── UI plumbing (excluded from the agent) ─────────────────────────────

    @tool(exclude=True)
    async def native_ui_launch(self, app_id: str = "qupath", native_session_id: str = "",
                               path: str = "", width: int = 1200,
                               height: int = 800) -> dict:
        """UI: launch or reattach an owned native app on the shared display."""
        try:
            engine = self._browser_engine()
            result = await engine.call(engine.native_apps().launch(
                app_id, native_session_id, path, width, height))
            return {"success": True, **result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def native_ui_call(self, native_session_id: str, action: str, args: dict = {}) -> dict:
        """UI: invoke the owned native session's same action adapter."""
        try:
            engine = self._browser_engine()
            value = await engine.call(engine.native_apps().call(native_session_id, action, args or {}))
            return {"success": self._native_result_ok(value), "result": value}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def native_ui_status(self, native_session_id: str) -> dict:
        """UI: check a native app without starting or replacing its process."""
        try:
            engine = self._browser_engine()
            return {"success": True,
                    **await engine.call(engine.native_apps().status(native_session_id))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def native_ui_close(self, native_session_id: str) -> dict:
        """UI: request the app's normal close, allowing Save/Cancel dialogs."""
        try:
            engine = self._browser_engine()
            return {"success": True,
                    **await engine.call(engine.native_apps().close(native_session_id))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _browser_ui_window_page(self, engine, window_id: str, url: str,
                                      operation_id: str, expected_page_id: str):
        """Ensure one initial page per shell, or perform one explicit replacement.

        The canonical Desktop service serializes callers from every viewport.
        A document CAS also protects against writers outside this process.
        """
        from .browser import normalize_url
        from .desktop_session import browser_page_reference

        lock = self._browser_creation_locks.setdefault(window_id, asyncio.Lock())
        async with lock:
            window = self._desktop_window(window_id)
            if window.get("app_id") != "browser":
                raise ValueError("The requested desktop window is not a Browser")
            current = browser_page_reference(window)
            binding = (window.get("args") or {}).get("browser_binding") or {}
            if not operation_id or operation_id == binding.get("operation_id"):
                if current:
                    session = await engine.call(engine.window_page(current, require_visible=False))
                    return session, current, binding or None
            elif current != expected_page_id:
                raise ValueError("The Browser page binding changed; read the existing window before retrying")

            # A retry of an explicit creation must carry the same operation id.
            # Ordinary boot/refresh uses one stable initial operation per shell.
            operation_id = operation_id or "initial"
            session = await engine.call(engine.open_page(normalize_url(url), wait_for_load=False))
            try:
                store = self._desktop()
                ops, result = store.apply("bind_browser", {
                    "window_id": window_id, "expected_page_id": current,
                    "page_id": session.id, "operation_id": operation_id,
                    "url": session.url,
                })
                if store._dirty:
                    raise RuntimeError("The Browser page binding could not be saved")
            except Exception:
                # Never close the previous/unknown page: only the page this
                # attempt just created belongs to its failed commit.
                await engine.call(engine.close_page(session.id))
                if operation_id == "initial":
                    winner = browser_page_reference(self._desktop_window(window_id))
                    if winner:
                        session = await engine.call(engine.window_page(winner, require_visible=False))
                        current_window = self._desktop_window(window_id)
                        return session, winner, (current_window.get("args") or {}).get("browser_binding")
                raise
            await self._publish_desktop({"type": "desktop.delta", "seq": store.session.seq, "ops": ops})
            return session, session.id, result["binding"]

    @tool(exclude=True)
    async def browser_ui_page(self, url: str = "", page_id: str = "", window_id: str = "",
                              operation_id: str = "", expected_page_id: str = "") -> dict:
        """UI → backend: create (or attach to) a page, and read its state.

        The Atrium Browser window calls this on mount: with `page_id` when the
        agent already opened the page (desktop.open state), without to start a
        fresh page. New clients provide window_id for initial creation so all
        viewports ensure the same native page. Explicit newPage/recovery sends
        operation_id and expected_page_id; retries reuse that operation id.
        Legacy clients without window_id keep their existing create behavior.
        The picture itself comes from the xpra stage, not from here.
        """
        try:
            engine = self._browser_engine()
            binding = None
            if page_id:
                session = await engine.call(engine.window_page(page_id, require_visible=False))
            elif window_id:
                session, page_id, binding = await self._browser_ui_window_page(
                    engine, window_id, url, operation_id, expected_page_id)
            else:
                from .browser import normalize_url

                session = await engine.call(engine.open_page(
                    normalize_url(url), wait_for_load=False,
                ))
            self._prewarm_browser()
            import shutil as _shutil

            return {
                "success": True,
                **await self._browser_page_info(session),
                "page_id": page_id or session.id,
                "active_page_id": session.id,
                **({"binding": binding} if binding else {}),
                "width": session.width,
                "height": session.height,
                # The stage needs the xpra binary AND a real display. Without
                # them there is no picture to show at all.
                "xpra": bool(_shutil.which("xpra")
                             and engine._xvfb_display is not None),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_nav(self, page_id: str, op: str, url: str = "") -> dict:
        """UI → backend: toolbar navigation (goto/back/forward/reload/stop)."""
        try:
            engine = self._browser_engine()
            session = await engine.call(engine.window_page(page_id))
            await engine.call(engine.navigate(session.id, op, url))
            return {"success": True, **await self._browser_page_info(session),
                    "page_id": page_id, "active_page_id": session.id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_close(self, page_id: str) -> dict:
        """UI → backend: the Browser window closed; drop its page."""
        try:
            engine = self._browser_engine()
            await engine.call(engine.close_window(page_id))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_stage(self, page_id: str, width: int = 0,
                               height: int = 0, fb_width: int = 0,
                               fb_height: int = 0) -> dict:
        """UI → backend: stream this page over xpra (sharp, low-latency).

        One page owns the display at a time — a latecomer takes it over, and
        every other viewport is told through `browser.stage` so it can fall
        back to the JPEG paths. Returns connection material for the html5
        client, or success=False when the transport is unavailable.
        """
        try:
            engine = self._browser_engine()
            from .browser import VIEW_H, VIEW_W

            info = await engine.call(engine.stage_page(
                page_id, int(width) or VIEW_W, int(height) or VIEW_H,
                int(fb_width or 0), int(fb_height or 0)))
            # Every viewer crops its own window out of one framebuffer, so a
            # layout change is everyone's business: the broadcast carries the
            # whole layout, not just who moved.
            await self._publish_desktop({
                "type": "desktop.broadcast",
                "topic": "browser.stage",
                "payload": {"page_id": page_id,
                            **{k: v for k, v in info.items()
                               if k not in ("password", "username")}},
            })
            return {"success": True, **info}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_focus(self, page_id: str) -> dict:
        """UI → backend: this Browser window has the user's attention.

        Several windows are on the display at once, so which one the
        keyboard goes to is the viewer's to say.
        """
        try:
            engine = self._browser_engine()
            await engine.call(engine.focus_stage(page_id))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_key(self, events: list | None = None) -> dict:
        """UI → backend: the viewer's keystrokes, injected on the display.

        The xpra shadow carries the picture and the pointer; its own keyboard
        injection never reaches Chromium on this image (the packets arrive,
        the keycodes resolve, XTest is called, and nothing lands), so the
        viewer sends key events here instead. `events` are
        {code, key, down} in the browser's own vocabulary.
        """
        try:
            engine = self._browser_engine()
            sent = await engine.call(engine.send_keys(list(events or [])))
            return {"success": True, "sent": sent}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool(exclude=True)
    async def browser_ui_unstage(self, page_id: str) -> dict:
        """UI → backend: this page stops using the xpra transport."""
        try:
            engine = self._browser_engine()
            await engine.call(engine.unstage_page(page_id))
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
