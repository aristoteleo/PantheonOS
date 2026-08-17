"""LiveViewToolSet — open and control agent-driven UI components.

A "LiveView" is a UI component the agent can open, drive, and observe. The
authoritative state lives here in a per-view ``LiveViewSession``. Two kinds of
client mutate / read it:

  * the **agent**, via the ``@tool`` methods (open / update / set_state /
    call / get_state) — these also broadcast the change to the UI;
  * the **UI**, via the ``@tool(exclude=True)`` methods (report_view_state /
    report_action_result) — when the user interacts with the component, or
    an agent-invoked action finishes, the UI reports back so the agent's
    next get_state / the pending call sees it.

Transport: state-change events publish on the existing NATS chat stream
(``pantheon.stream.chat_<chat_id>``) with ``live_view.*`` event types. The
component side speaks the matching bridge protocol via live-view-sdk.js.

This toolset is generic. Nothing is "built in" except the LiveView SDK
runtime (live-view-sdk.js + app-host.html in the UI). Every viewer is a
**plugin**: a folder ``skills/live_view/<name>/`` with an ``adapter.js``
module exporting ``setup(lv, root)`` (e.g. ``skills/live_view/vitessce/``).
``open_live_view`` either loads an agent-supplied ``module_url`` or resolves
a named viewer plugin to its served module. The component's "state" is opaque here — for
Vitessce it is the Vitessce view config; a patch deep-merges into it.
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

# How long live_view_call waits for the component to return an action result.
ACTION_TIMEOUT_SECONDS = 30

# How long live_view_screenshot waits for the UI to render and return a frame.
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


@dataclass
class LiveViewSession:
    """Authoritative state for one open LiveView."""

    view_id: str
    view_type: str
    title: str
    chat_id: str
    module_url: str = ""  # served URL of the component module to load
    state: dict[str, Any] = field(default_factory=dict)
    status: str = "opening"  # opening | ready | error | closed
    error: str | None = None
    # Console errors / warnings captured from the component (most recent last).
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    # User has collapsed this view's UI tab. The session stays alive and the
    # agent can still drive it; only the visible tab is gone. Distinct from
    # `status == "closed"` (permanent destroy, agent-initiated via
    # close_live_view). The user reopens via the UI's hidden-views drawer.
    ui_hidden: bool = False

    def snapshot(self) -> dict[str, Any]:
        """Structured state the agent reads to decide its next action."""
        return {
            "view_id": self.view_id,
            "view_type": self.view_type,
            "title": self.title,
            "module_url": self.module_url,
            "status": self.status,
            "state": self.state,
            "error": self.error,
            "diagnostics": self.diagnostics,
            "ui_hidden": self.ui_hidden,
            "updated_at": self.updated_at,
        }


class LiveViewToolSet(ToolSet):
    """The desktop plane: the agent's hands and eyes on Atrium windows.

    Agent-facing surface: desktop_windows / desktop_open / desktop_read /
    desktop_update / desktop_call (+ serve_local_data and the generic data
    endpoints). The legacy live_view_* tools are hidden from agents
    (exclude=True) — they drove the old Pantheon-UI sidebar; Atrium windows
    are the one mechanism now — but remain callable so existing UI paths and
    old transcripts keep working.
    """

    def __init__(self, name: str = "live_view", **kwargs):
        super().__init__(name, **kwargs)
        self._views: dict[str, LiveViewSession] = {}
        # action_id -> Future, resolved by report_action_result.
        self._pending_actions: dict[str, asyncio.Future] = {}
        # request_id -> Future, resolved by report_snapshot.
        self._pending_snapshots: dict[str, asyncio.Future] = {}
        # request_id -> Future, resolved by report_desktop_result.
        self._pending_desktop: dict[str, asyncio.Future] = {}
        # chats whose desktop has answered a ping — subscription is live.
        self._desktop_ready: set[str] = set()
        self._nats = None  # lazy NATSStreamAdapter
        self._desktop_store = None  # lazy DesktopSessionStore
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

    async def _publish(self, chat_id: str, event: dict[str, Any]) -> None:
        """Broadcast a live_view.* event to the UI over the NATS chat stream."""
        if not chat_id:
            logger.warning("live_view: no chat_id, cannot publish {}", event.get("type"))
            return
        if self._nats is None:
            from pantheon.chatroom.stream import NATSStreamAdapter

            self._nats = NATSStreamAdapter()
        try:
            await self._nats.publish(chat_id, event["type"], event)
        except Exception as e:  # streaming is best-effort
            logger.error("live_view: publish failed: {}", e)

    # ── the desktop session ───────────────────────────────────────────────

    def _desktop(self):
        """The window document, loaded from the volume on first use."""
        if self._desktop_store is None:
            from .desktop_session import DesktopSessionStore

            self._desktop_store = DesktopSessionStore()
            self._desktop_store.load()
        return self._desktop_store

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
        return {"success": True, "session": self._desktop().snapshot()}

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
            store.save()
        return {"success": True, "seq": store.session.seq, "ops": ops, **result}

    def _require(self, view_id: str) -> LiveViewSession:
        session = self._views.get(view_id)
        if session is None:
            raise KeyError(f"No LiveView with id '{view_id}'")
        return session

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

    async def _resolve_viewer(
        self, name: str,
    ) -> tuple[str | None, dict | None, str | None]:
        """Resolve a viewer-plugin name to a served module URL (+ demo).

        A viewer plugin is a folder ``skills/live_view/<name>/`` holding
        ``adapter.js`` (a setup(lv, root) module) and an optional
        ``demo.json`` — a ready, verified config used when open_live_view is
        called with no state.

        Returns ``(url, demo_or_None, None)`` on success, or
        ``(None, None, error)`` if no such plugin exists.
        """
        import json as _json
        from pantheon.settings import get_settings

        s = get_settings()
        candidates = [
            s.skills_dir / "live_view" / name / "adapter.js",
            s.global_skills_dir / "live_view" / name / "adapter.js",
            s.factory_skills_dir / "live_view" / name / "adapter.js",
        ]
        adapter = next((p for p in candidates if p.exists()), None)
        if adapter is None:
            return None, None, (
                f"Unknown viewer '{name}': no plugin at "
                f"skills/live_view/{name}/adapter.js. For a bespoke "
                f"component, open a view with view_type='custom' and your "
                f"own module_url."
            )
        server = await self._ensure_data_server()
        url = server.url_for(adapter.resolve())
        if url is None:
            return None, None, f"viewer plugin '{name}' is outside the served roots"

        demo: dict | None = None
        demo_file = adapter.parent / "demo.json"
        if demo_file.exists():
            try:
                demo = _json.loads(demo_file.read_text())
            except Exception as e:  # noqa: BLE001
                logger.warning("live_view: bad demo config {}: {}", demo_file, e)
        return url, demo, None

    # ── agent-facing tools ────────────────────────────────────────────────

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def open_live_view(
        self,
        view_type: str,
        title: str,
        state: dict | None = None,
        module_url: str = "",
    ) -> dict:
        """Open an interactive visualization component in the Pantheon UI sidebar.

        Every LiveView is a JS module exporting `setup(lv, root)`, loaded by
        the generic host. There are two ways to pick that module:

          * a **viewer plugin** — pass `view_type` as the plugin name (e.g.
            "vitessce"); it resolves to `skills/live_view/<name>/adapter.js`.
          * an **agent-generated component** — pass `view_type="custom"` and
            `module_url`, the served URL (from serve_local_data) of the JS
            module you wrote.

        QUICK DEMO: to open a viewer's built-in demo, call it with NO `state`
        — e.g. open_live_view("vitessce", "Demo"). If the viewer ships a demo
        config it loads automatically; do NOT search for or build one.

        The component renders in the right sidebar; drive and observe it
        afterwards with the other live_view tools.

        Args:
            view_type: A viewer-plugin name (e.g. "vitessce"), or "custom"
                for an agent-generated component (then `module_url` required).
            title: Short title shown on the sidebar tab.
            state: The component's initial state. For "vitessce" this is a
                Vitessce *view config* JSON object (keys: version, name,
                datasets, coordinationSpace, layout, initStrategy); data file
                URLs must be browser-reachable (public, or served via
                serve_local_data). For "custom" it is the component's own
                initial state. Omit it to load the viewer's bundled demo.
            module_url: For view_type="custom", the served URL of the
                component module. Ignored for viewer plugins.

        Returns:
            dict with success, view_id (use it for the other tools), and the
            initial state snapshot.
        """
        chat_id = self._chat_id()
        if not chat_id:
            return {"success": False, "error": "No active chat context"}

        # module_url is a view field, not component state — keep state clean.
        state = state or {}
        resolved_url = module_url or state.get("module_url") or ""
        component_state = {k: v for k, v in state.items() if k != "module_url"}

        if not resolved_url:
            if view_type == "custom":
                return {
                    "success": False,
                    "error": (
                        "view_type 'custom' requires module_url — serve your "
                        "component module with serve_local_data and pass its "
                        "URL as module_url."
                    ),
                }
            resolved_url, demo_state, err = await self._resolve_viewer(view_type)
            if err:
                return {"success": False, "error": err}
            # No state given — fall back to the viewer's bundled demo config.
            if not component_state:
                if demo_state is not None:
                    component_state = demo_state
                    logger.info("live_view: using bundled demo for '{}'", view_type)
                else:
                    return {
                        "success": False,
                        "error": (
                            f"view_type '{view_type}' needs a config in "
                            f"`state`, and ships no bundled demo. Build a "
                            f"config (see the {view_type} skill) and pass it."
                        ),
                    }

        view_id = f"lv-{uuid.uuid4().hex[:12]}"
        session = LiveViewSession(
            view_id=view_id,
            view_type=view_type,
            title=title,
            chat_id=chat_id,
            module_url=resolved_url,
            state=component_state,
        )
        self._views[view_id] = session

        await self._publish(chat_id, {
            "type": "live_view.open",
            "view_id": view_id,
            "view_type": view_type,
            "title": title,
            "module_url": resolved_url,
            "state": session.state,
        })
        logger.info("live_view: opened {} ({}) for chat {}", view_id, view_type, chat_id)
        return {"success": True, "view_id": view_id, "state": session.snapshot()}

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def live_view_update(self, view_id: str, patch: dict) -> dict:
        """Apply a partial-state patch to an open LiveView (drive the component).

        The patch is deep-merged into the component's current state. This is
        the main way to drive a view incrementally.

        For "vitessce", the state is the view config and coordination values
        live under `coordinationSpace`. Example — zoom a spatial view:
            patch = {"coordinationSpace": {"spatialZoom": {"A": 4}}}
        Other Vitessce coordination types: spatialTargetX / spatialTargetY,
        obsColorEncoding, featureSelection, obsSetSelection, obsHighlight.

        Args:
            view_id: id returned by open_live_view.
            patch: a partial state object to deep-merge into the current state.

        Returns:
            dict with success and the resulting state snapshot.
        """
        try:
            session = self._require(view_id)
        except KeyError as e:
            return {"success": False, "error": str(e)}

        session.state = _deep_merge(session.state, patch or {})
        session.updated_at = time.time()
        await self._publish(session.chat_id, {
            "type": "live_view.patch",
            "view_id": view_id,
            "patch": patch or {},
        })
        return {"success": True, "state": session.snapshot()}

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def live_view_set_state(self, view_id: str, state: dict) -> dict:
        """Replace an open LiveView's whole state.

        Use for structural changes (different dataset / layout). Prefer
        live_view_update for incremental changes.

        Args:
            view_id: id returned by open_live_view.
            state: the new full component state.

        Returns:
            dict with success and the resulting state snapshot.
        """
        try:
            session = self._require(view_id)
        except KeyError as e:
            return {"success": False, "error": str(e)}

        session.state = state or {}
        session.updated_at = time.time()
        await self._publish(session.chat_id, {
            "type": "live_view.set",
            "view_id": view_id,
            "state": session.state,
        })
        return {"success": True, "state": session.snapshot()}

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def live_view_call(self, view_id: str, action: str, args: dict = {}) -> dict:
        """Invoke a named action the component exposes, and wait for its result.

        Components register actions via the LiveView SDK's defineAction(). Not
        all view types expose actions — "vitessce" is driven through
        live_view_update; agent-generated components typically expose actions.

        Args:
            view_id: id returned by open_live_view.
            action: the action name.
            args: arguments passed to the action handler.

        Returns:
            dict with success and `result` (the action's return value), or an
            error if the action failed / timed out.
        """
        try:
            session = self._require(view_id)
        except KeyError as e:
            return {"success": False, "error": str(e)}

        action_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_actions[action_id] = future

        await self._publish(session.chat_id, {
            "type": "live_view.action",
            "view_id": view_id,
            "action_id": action_id,
            "name": action,
            "args": args or {},
        })
        try:
            result = await asyncio.wait_for(future, timeout=ACTION_TIMEOUT_SECONDS)
            return {"success": True, "result": result}
        except asyncio.TimeoutError:
            return {"success": False, "error": f"action '{action}' timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._pending_actions.pop(action_id, None)

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def live_view_get_state(self, view_id: str) -> dict:
        """Read an open LiveView's current state, status, and diagnostics.

        The returned snapshot has:
          - `status`: "ready" means the component module loaded without
            throwing — it does NOT guarantee it rendered correctly.
          - `diagnostics`: console errors / warnings captured from the
            component (uncaught errors, Vue/React warnings, etc.). A
            non-empty list means something is wrong even if status is
            "ready" — inspect it.
          - `state`: the latest component state, including changes the
            *user* made by interacting with the component directly.

        Do NOT treat reading back a value you just set with
        live_view_update as "verification" — that only echoes your own
        write. To truly verify, check `diagnostics` and use
        live_view_screenshot.

        Args:
            view_id: id returned by open_live_view.
        """
        try:
            session = self._require(view_id)
        except KeyError as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "state": session.snapshot()}

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def live_view_screenshot(self, view_id: str) -> dict:
        """Capture what an open LiveView currently looks like, as an image.

        Renders the component (an html2canvas DOM snapshot) and hands it back
        as an INLINE image you can SEE directly in this tool result — no
        separate observe_images call needed. Use it to verify a view after
        opening or driving it (`status: ready` alone does not mean it looks
        right). On providers without inline-image support the image is only
        saved to `path`; view that with observe_images.

        IMPORTANT — the snapshot is an html2canvas DOM render. It captures
        HTML and SVG faithfully, but does NOT capture WebGL or <canvas>
        pixel content: Vitessce, deck.gl, and canvas-based plots appear
        BLANK or incomplete in the image. For those, rely on
        live_view_get_state (status + diagnostics) and the user — not this
        screenshot.

        Args:
            view_id: id returned by open_live_view.

        Returns:
            dict with success, the screenshot shown inline (on vision-capable
            models), path (the saved image, for observe_images on other
            providers), and note (the WebGL caveat).
        """
        try:
            session = self._require(view_id)
        except KeyError as e:
            return {"success": False, "error": str(e)}

        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_snapshots[request_id] = future
        await self._publish(session.chat_id, {
            "type": "live_view.snapshot",
            "view_id": view_id,
            "request_id": request_id,
        })
        try:
            data_url = await asyncio.wait_for(
                future, timeout=SNAPSHOT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return {"success": False, "error": "screenshot timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            self._pending_snapshots.pop(request_id, None)

        try:
            import base64
            from pathlib import Path
            from pantheon.settings import get_settings

            header, _, b64 = str(data_url).partition(",")
            ext = "jpg" if "jpeg" in header else "png"
            snap_dir = get_settings().pantheon_dir / "live_view_snapshots"
            snap_dir.mkdir(parents=True, exist_ok=True)
            path = snap_dir / f"{view_id}-{int(time.time())}.{ext}"
            Path(path).write_bytes(base64.b64decode(b64))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"failed to save snapshot: {e}"}

        result: dict = {
            "success": True,
            "path": str(path),
            "note": (
                "Screenshot of the live view. The dominant WebGL/canvas surface "
                "(e.g. the spatial-transcriptomics deck.gl 3D viewer) IS captured "
                "— LOOK at this image to verify what's shown (gene colouring = a "
                "smooth viridis gradient; cluster/cell-type = many discrete "
                "colours). A FEW surfaces (the Vitessce viewer, a GLB mesh) can "
                "still come out blank; only if THIS image is blank, fall back to "
                "live_view_get_state + diagnostics."
            ),
        }

        # If the running model can receive images in tool results (Anthropic,
        # Gemini, OpenAI Responses), hand the screenshot back INLINE so the
        # agent SEES it in THIS result — no separate observe_images round trip
        # (which has been brittle: a wrong/relative path → "Path is not a
        # file"). The file is still saved above as a fallback. The framework
        # (agent.py) strips content_blocks, builds a text summary from the
        # rest, and persists the data URI via ImageStore.
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

    def _package_screenshot(self, data_url: str, stem: str) -> dict:
        """Save a captured data URL and hand it back, inline when the model
        can see images in tool results — shared by live_view_screenshot and
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
        chat_id = self._chat_id()
        if not chat_id:
            return {"success": False, "error": "no chat context — cannot reach the desktop"}
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_snapshots[request_id] = future
        await self._publish(chat_id, {
            "type": "desktop.snapshot",
            "window_id": window_id,
            "request_id": request_id,
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

        Use this to make data servable before referencing it from a Vitessce
        view config, or to serve an agent-written component module before
        opening it with open_live_view(view_type="custom").

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
        module_name = f"_pantheon_live_view_endpoint_{uuid.uuid4().hex}"
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

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def list_live_views(self) -> dict:
        """List the LiveViews currently open in this chat."""
        chat_id = self._chat_id()
        views = [
            s.snapshot() for s in self._views.values()
            if s.chat_id == chat_id and s.status != "closed"
        ]
        return {"success": True, "views": views}

    @tool(exclude=True)  # superseded by the desktop plane; UI/back-compat only
    async def close_live_view(self, view_id: str) -> dict:
        """Close an open LiveView and remove its sidebar tab.

        Args:
            view_id: id returned by open_live_view.
        """
        session = self._views.get(view_id)
        if session is None:
            return {"success": False, "error": f"No LiveView with id '{view_id}'"}
        session.status = "closed"
        await self._publish(session.chat_id, {
            "type": "live_view.close",
            "view_id": view_id,
        })
        return {"success": True}

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
        logger.info("live_view: data endpoint set to {}", tunnel_base)
        return {"success": True}

    # ── the desktop plane (app-spec: the agent interface, normalized) ──────
    #
    # live_view_* drives views the AGENT opened, by view_id. These five drive
    # THE DESKTOP: any window, whoever opened it — the user's double-click
    # included — by window_id, plus app-or-file opening with the same routing
    # the desktop itself uses. Same transport (chat-stream events, answered by
    # the connected desktop via report_desktop_result); an Atrium must be
    # connected and showing this chat for them to answer.

    async def _desktop_ping(self, chat_id: str) -> bool:
        """Handshake until the desktop's stream subscription is live.

        The UI subscribes to a chat's stream when its window opens; a fresh
        chat's very first desktop request can beat that subscription and the
        event is simply lost (streams do not replay). Pinging is idempotent,
        so retry it until an answer proves the pipe — then send the real
        request once.
        """
        if chat_id in self._desktop_ready:
            return True
        for _ in range(4):
            request_id = uuid.uuid4().hex
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            self._pending_desktop[request_id] = future
            await self._publish(chat_id, {"type": "desktop.ping", "request_id": request_id})
            try:
                await asyncio.wait_for(future, timeout=2.0)
                self._desktop_ready.add(chat_id)
                return True
            except Exception:
                continue
            finally:
                self._pending_desktop.pop(request_id, None)
        return False

    async def _desktop_request(self, event_type: str, payload: dict, timeout: float = 30.0) -> dict:
        chat_id = self._chat_id()
        if not chat_id:
            return {"success": False, "error": "no chat context — cannot reach the desktop"}
        if not await self._desktop_ping(chat_id):
            return {"success": False,
                    "error": "the desktop did not answer — is an Atrium window open on this chat?"}
        request_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_desktop[request_id] = future
        await self._publish(chat_id, {
            "type": event_type,
            "request_id": request_id,
            **payload,
        })
        try:
            value = await asyncio.wait_for(future, timeout=timeout)
            return {"success": True, "result": value}
        except asyncio.TimeoutError:
            return {"success": False,
                    "error": "the desktop did not answer — is an Atrium window open on this chat?"}
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
        for packaged-app windows), and `actions` it exposes.

        This is how you reach windows the USER opened: find its window_id
        here, then desktop_read / desktop_update / desktop_call it exactly
        like a view you opened yourself.
        """
        # Answered from the pod's own document, so this works with no desktop
        # on screen at all — the window list is a property of the machine, and
        # asking a browser for it was what produced "the desktop did not
        # answer" for a question the pod could always answer itself.
        s = self._desktop().session
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
                `.pantheon/apps/<id>/` and open it by `app` id instead. (This
                replaces the retired open_live_view custom path.)
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
        you have rather than opening another one."""
        return await self._desktop_request(
            "desktop.update", {"window_id": window_id, "patch": patch or {}})

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
    async def report_view_state(
        self,
        view_id: str,
        state: dict | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> dict:
        """UI → backend: report the component's state after a user interaction
        (or a lifecycle change), keeping the authoritative state in sync."""
        session = self._views.get(view_id)
        if session is None:
            return {"success": False, "error": f"No LiveView with id '{view_id}'"}
        if state is not None:
            session.state = state
        if status is not None:
            session.status = status
        if error is not None:
            session.error = error
        session.updated_at = time.time()
        return {"success": True}

    @tool(exclude=True)
    async def report_action_result(
        self,
        view_id: str,
        action_id: str,
        ok: bool,
        value: Any = None,
        error: str | None = None,
    ) -> dict:
        """UI → backend: deliver the result of an agent-invoked action,
        resolving the pending live_view_call."""
        future = self._pending_actions.get(action_id)
        if future is not None and not future.done():
            if ok:
                future.set_result(value)
            else:
                future.set_exception(RuntimeError(error or "action failed"))
        return {"success": True}

    @tool(exclude=True)
    async def report_diagnostic(
        self, view_id: str, level: str, message: str,
    ) -> dict:
        """UI → backend: record a console error / warning the host captured
        from the component, so the agent sees it via live_view_get_state."""
        session = self._views.get(view_id)
        if session is None:
            return {"success": False, "error": f"No LiveView with id '{view_id}'"}
        session.diagnostics.append({
            "level": level,
            "message": message,
            "ts": time.time(),
        })
        if len(session.diagnostics) > MAX_DIAGNOSTICS:
            session.diagnostics = session.diagnostics[-MAX_DIAGNOSTICS:]
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
        live_view_screenshot."""
        future = self._pending_snapshots.get(request_id)
        if future is not None and not future.done():
            if ok and data_url:
                future.set_result(data_url)
            else:
                future.set_exception(RuntimeError(error or "snapshot failed"))
        return {"success": True}

    @tool(exclude=True)
    async def hide_view_for_ui(self, view_id: str) -> dict:
        """UI → backend: collapse a view's tab without destroying it. The
        session stays alive and the agent can still drive it; the user can
        later restore the tab from the hidden-views drawer. Distinct from
        the agent-only `close_live_view`, which is permanent."""
        session = self._views.get(view_id)
        if session is None:
            return {"success": False, "error": f"No LiveView with id '{view_id}'"}
        session.ui_hidden = True
        session.updated_at = time.time()
        await self._publish(session.chat_id, {
            "type": "live_view.hide",
            "view_id": view_id,
        })
        return {"success": True}

    @tool(exclude=True)
    async def unhide_view_for_ui(self, view_id: str) -> dict:
        """UI → backend: restore a previously hidden view's tab."""
        session = self._views.get(view_id)
        if session is None:
            return {"success": False, "error": f"No LiveView with id '{view_id}'"}
        session.ui_hidden = False
        session.updated_at = time.time()
        await self._publish(session.chat_id, {
            "type": "live_view.unhide",
            "view_id": view_id,
        })
        return {"success": True}

    @tool(exclude=True)
    async def list_views_for_ui(self, chat_id: str | None = None) -> dict:
        """UI → backend: fetch open views (e.g. to restore them after reload).
        Includes hidden views — the UI keeps them in its hidden-views drawer."""
        cid = chat_id or self._chat_id()
        views = [
            s.snapshot() for s in self._views.values()
            if s.chat_id == cid and s.status != "closed"
        ]
        return {"success": True, "views": views}

    # ══ Browser — a real Chromium, shared by the agent and the user ═══════════
    #
    # One headless Chromium runs in this sandbox (see browser.py). Pages are
    # visible to both sides at once: the user's Atrium Browser window streams
    # frames from the browser-frame endpoint and sends input back through
    # browser-input; the agent drives the SAME pages with the tools below.

    def _browser_engine(self):
        from .browser import BrowserEngine

        return BrowserEngine.instance()

    async def _browser_urls(self) -> tuple[str, str]:
        """Register the stream endpoints (once) and return their URLs."""
        server = await self._ensure_data_server()
        from .browser import make_frame_handler, make_input_handler

        engine = self._browser_engine()
        if not self._browser_endpoints:
            await server.register_endpoint("browser-frame", make_frame_handler(engine))
            await server.register_endpoint("browser-input", make_input_handler(engine))
            self._browser_endpoints = True
        frame = server.url_for_endpoint("browser-frame")
        inp = server.url_for_endpoint("browser-input")
        if not frame or not inp:
            raise RuntimeError(
                "the data server has no browser-reachable URL yet (tunnel not set)")
        return frame, inp

    async def _browser_page_info(self, session) -> dict:
        engine = self._browser_engine()
        return {
            "page_id": session.id,
            "url": session.url,
            "title": await engine.call(session.title()),
        }

    @tool
    async def browser_open(self, url: str = "", show: bool = True) -> dict:
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

        Returns `page_id` for the other browser_* tools, plus url/title, and
        `window_id` when a desktop window was opened.
        """
        try:
            from .browser import normalize_url

            engine = self._browser_engine()
            session = await engine.call(engine.open_page(normalize_url(url)))
            info = await self._browser_page_info(session)
            result: dict = {"success": True, **info}
            if show:
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
            session = engine.latest(page_id)
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
            session = engine.latest(page_id)
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
            session = engine.latest(page_id)
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
            session = engine.latest(page_id)
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
            session = engine.latest(page_id)
            rel = path or f"browser-shot-{int(_time.time())}.jpg"
            out = _Path(rel)
            if not out.is_absolute():
                out = _Path.cwd() / out
            out.parent.mkdir(parents=True, exist_ok=True)
            data = await engine.call(session.page.screenshot(type="jpeg", quality=80))
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
