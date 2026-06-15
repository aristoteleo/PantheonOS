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
    """Toolset for opening and controlling agent-driven UI components."""

    def __init__(self, name: str = "live_view", **kwargs):
        super().__init__(name, **kwargs)
        self._views: dict[str, LiveViewSession] = {}
        # action_id -> Future, resolved by report_action_result.
        self._pending_actions: dict[str, asyncio.Future] = {}
        # request_id -> Future, resolved by report_snapshot.
        self._pending_snapshots: dict[str, asyncio.Future] = {}
        self._nats = None  # lazy NATSStreamAdapter
        self._data_server = None  # lazy LiveViewDataServer

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

    @tool
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

    @tool
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

    @tool
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

    @tool
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

    @tool
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

    @tool
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
    async def list_live_views(self) -> dict:
        """List the LiveViews currently open in this chat."""
        chat_id = self._chat_id()
        views = [
            s.snapshot() for s in self._views.values()
            if s.chat_id == chat_id and s.status != "closed"
        ]
        return {"success": True, "views": views}

    @tool
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
