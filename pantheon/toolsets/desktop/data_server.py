"""CORS static file server for LiveView components.

LiveView components run in the browser and fetch things over HTTP: their
data (Zarr / OME-TIFF), and — for both agent-generated components and viewer
*plugins* — their own JS code. Those files live on local disk (the
workspace, or a skills/plugins directory), which a browser cannot fetch by
path. This lazily starts a localhost, CORS-enabled static HTTP server that
exposes one or more local directories.

The server runs in a **dedicated daemon thread with its own event loop**,
not on the caller's loop: the live_view toolset's tool calls execute under
ThreadJob isolation (ephemeral per-call loops), so a server bound to the
calling loop would die the moment the tool call returns. The daemon thread
keeps the server alive for the whole backend process.

Multiple roots are supported — each is mounted under a short, stable
hash prefix (`/<hash>/...`) so unrelated directory trees (workspace,
skills dir) can be served by one process. Localhost-bound. Supports HTTP
range requests (needed for OME-TIFF / sharded Zarr).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import os
import re
import socket
import threading
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web

from pantheon.utils.log import logger


EndpointHandler = Callable[
    [web.Request], web.StreamResponse | Awaitable[web.StreamResponse],
]


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _prefix_for(root: Path) -> str:
    """A short, stable URL prefix for a root (order-independent)."""
    return hashlib.sha1(str(root).encode()).hexdigest()[:10]


@web.middleware
async def _cors_middleware(request: web.Request, handler):
    """Allow the LiveView iframe (a different origin/port) to fetch."""
    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    # Custom response headers are invisible to cross-origin JS unless named
    # here — the Browser app reads its page's nav state (url/title/loading/
    # back/forward, and the frame sequence) off the X-* headers on each
    # browser-frame response, and a bare "*" is NOT honoured for exposed
    # headers by browsers, so they must be listed explicitly.
    resp.headers["Access-Control-Expose-Headers"] = (
        "X-Seq, X-Url, X-Title, X-Loading, X-Back, X-Fwd, X-Favicon, X-Popup, "
        "X-Scroll, X-W, X-H"
    )
    # Never let the browser cache anything this server returns. Two reasons:
    #  1. Viewer modules (adapter.js) are edited during development — an ES module
    #     pinned in the document's module map by a stale URL renders/snapshots
    #     wrong even after the file on disk changes.
    #  2. A spatial dataset (Visium/Xenium) fires hundreds of concurrent small
    #     Zarr-chunk fetches; Chrome tries to write each cacheable response to its
    #     disk cache and, under that load, throws net::ERR_CACHE_WRITE_FAILURE,
    #     which aborts the fetch and breaks Vitessce. no-store = nothing to cache,
    #     nothing to fail. (Range requests still work; this only disables caching.)
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


class LiveViewDataServer:
    """Lazily-started localhost CORS static server over one or more roots.

    One server per backend process. Runs in a daemon thread so it outlives
    the tool call that started it.
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._roots: dict[str, Path] = {}  # prefix -> root dir
        self._base_url: str | None = None  # local bind URL (also the "started" sentinel)
        self._lock = threading.Lock()
        self._endpoint_lock = threading.Lock()
        self._endpoints: dict[str, EndpointHandler] = {}
        # ── Server mode ──────────────────────────────────────────────────────
        # In a hub/server deployment the agent runs in a Modal sandbox and the
        # browser cannot reach 127.0.0.1. The hub exposes a fixed port via a
        # Modal encrypted tunnel and injects a path token; we bind 0.0.0.0:<port>
        # and gate requests under /d/<token>/. `url_for` then emits the public
        # tunnel URL (set post-create via set_tunnel_base) instead of 127.0.0.1.
        # See docs/2026-06-10-live-view-server-mode.md (pantheon-hub).
        self._token: str | None = os.environ.get("LIVE_VIEW_DATA_TOKEN") or None
        self._fixed_port: int = int(os.environ.get("LIVE_VIEW_DATA_PORT", "0") or 0)
        self._server_mode: bool = bool(self._token)
        self._tunnel_base: str | None = None  # public https base, delivered by the hub

    @staticmethod
    def validate_endpoint_name(name: str) -> None:
        """Validate a dynamic endpoint URL segment."""
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(
                "Endpoint name must contain only letters, numbers, '_' and '-'",
            )

    async def ensure_started(self, roots: list[Path]) -> str:
        """Start the server (once) serving `roots`; return its base URL.

        The first call fixes the served roots. Started on a dedicated daemon
        thread; this coroutine waits (off the calling loop) for it to come up.
        """
        if self._base_url is not None:
            return self._base_url
        resolved = [Path(r).resolve() for r in roots if Path(r).exists()]
        await asyncio.get_event_loop().run_in_executor(
            None, self._start_blocking, resolved,
        )
        return self._base_url  # type: ignore[return-value]

    def _start_blocking(self, roots: list[Path]) -> None:
        with self._lock:
            if self._base_url is not None:
                return
            ready = threading.Event()
            err: dict[str, BaseException] = {}

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    app = web.Application(middlewares=[_cors_middleware])
                    mounts: dict[str, Path] = {}
                    for root in roots:
                        prefix = _prefix_for(root)
                        if prefix in mounts:
                            continue
                        mounts[prefix] = root
                        # Local mode mounts each root as a static route; server
                        # mode serves everything through one token-gated handler
                        # (registered once, below).
                        if not self._server_mode:
                            app.router.add_static(
                                f"/{prefix}/", str(root),
                                show_index=False, follow_symlinks=False,
                            )
                    self._roots = mounts
                    app.router.add_route("*", "/api/{name}", self._serve_endpoint)
                    app.router.add_route(
                        "*", "/api/{name}/{tail:.*}", self._serve_endpoint,
                    )
                    if self._server_mode:
                        # /d/<token>/<prefix>/<rel> — constant-time token check,
                        # then FileResponse (Range-aware) from the mounted root.
                        app.router.add_get("/d/{token}/{tail:.*}", self._serve_token_gated)
                    runner = web.AppRunner(app)
                    loop.run_until_complete(runner.setup())
                    if self._server_mode:
                        host, port = "0.0.0.0", (self._fixed_port or _free_port())
                    else:
                        host, port = "127.0.0.1", _free_port()
                    site = web.TCPSite(runner, host, port)
                    loop.run_until_complete(site.start())
                    self._base_url = f"http://{host}:{port}"
                except BaseException as e:  # noqa: BLE001
                    err["e"] = e
                finally:
                    ready.set()
                if "e" not in err:
                    loop.run_forever()  # keep the server alive

            self._thread = threading.Thread(
                target=_run, daemon=True, name="live-view-data-server",
            )
            self._thread.start()
            if not ready.wait(timeout=10):
                raise RuntimeError("LiveView data server failed to start (timeout)")
            if "e" in err:
                raise err["e"]
            logger.info(
                "live_view: data server at {} serving {} root(s)",
                self._base_url, len(self._roots),
            )

    async def register_endpoint(
        self, name: str, handler: EndpointHandler,
    ) -> str:
        """Register or replace a dynamic endpoint handler.

        The aiohttp router is fixed at server startup. Runtime registration
        updates this registry; requests to /api/<name>/... dispatch through it.
        """
        self.validate_endpoint_name(name)
        if self._base_url is None:
            raise RuntimeError("LiveView data server has not been started")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._endpoint_lock:
            self._endpoints[name] = handler
        url = self.url_for_endpoint(name)
        if url is None:
            raise RuntimeError("Endpoint is not browser-reachable yet")
        return url

    def url_for_endpoint(self, name: str) -> str | None:
        """Return the browser URL for a registered dynamic endpoint."""
        self.validate_endpoint_name(name)
        if self._base_url is None:
            return None
        if self._server_mode:
            if not self._tunnel_base:
                logger.warning(
                    "live_view: server mode but no tunnel base delivered yet; "
                    "cannot emit a browser-reachable endpoint URL for {}", name,
                )
                return None
            return f"{self._tunnel_base}/api/{name}/?token={self._token}"
        return f"{self._base_url}/api/{name}/"

    def list_endpoints(self) -> list[dict[str, str]]:
        """List all registered dynamic endpoints with their URLs.

        Returns:
            List of dicts with 'name' and 'url' keys for each endpoint.
        """
        with self._endpoint_lock:
            endpoints = list(self._endpoints.keys())
        result = []
        for name in endpoints:
            url = self.url_for_endpoint(name)
            if url:
                result.append({"name": name, "url": url})
        return result

    def unregister_endpoint(self, name: str) -> bool:
        """Unregister a dynamic endpoint by name.

        Args:
            name: The endpoint name to remove.

        Returns:
            True if the endpoint was removed, False if it didn't exist.
        """
        self.validate_endpoint_name(name)
        with self._endpoint_lock:
            if name in self._endpoints:
                del self._endpoints[name]
                return True
            return False

    def endpoint_exists(self, name: str) -> bool:
        """Check if an endpoint is currently registered.

        Args:
            name: The endpoint name to check.

        Returns:
            True if the endpoint exists, False otherwise.
        """
        try:
            self.validate_endpoint_name(name)
        except ValueError:
            return False
        with self._endpoint_lock:
            return name in self._endpoints

    def _valid_api_token(self, request: web.Request) -> bool:
        if not self._server_mode:
            return True
        supplied = request.query.get("token")
        auth = request.headers.get("Authorization", "")
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        return bool(
            self._token and supplied
            and hmac.compare_digest(str(supplied), self._token)
        )

    async def _serve_endpoint(self, request: web.Request) -> web.StreamResponse:
        """Dispatch /api/<name>/... to a registered dynamic endpoint."""
        if request.method == "OPTIONS":
            return web.Response()
        if not self._valid_api_token(request):
            return web.Response(status=403, text="forbidden")
        name = request.match_info.get("name", "")
        try:
            self.validate_endpoint_name(name)
        except ValueError:
            return web.Response(status=404, text="unknown endpoint")
        with self._endpoint_lock:
            handler = self._endpoints.get(name)
        if handler is None:
            return web.Response(status=404, text="unknown endpoint")
        try:
            result = handler(request)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001
            logger.exception("live_view: endpoint '{}' failed", name)
            # Return generic error message to avoid leaking internal details
            return web.Response(status=500, text="Internal server error")
        if not isinstance(result, web.StreamResponse):
            logger.error(
                "live_view: endpoint '{}' handler returned invalid type: {}",
                name, type(result).__name__,
            )
            return web.Response(
                status=500,
                text="Internal server error",
            )
        return result

    async def _serve_token_gated(self, request: web.Request) -> web.StreamResponse:
        """Serve /d/<token>/<prefix>/<rel> in server mode.

        The token rides in the URL *path* (not a query param) so viewer
        sub-requests — e.g. zarr fetching ``base + "/0.0.0"`` — keep carrying it.
        Constant-time compared against the env token; FileResponse handles Range.
        """
        token = request.match_info.get("token", "")
        if not self._token or not hmac.compare_digest(token, self._token):
            return web.Response(status=403, text="forbidden")
        tail = request.match_info.get("tail", "")
        prefix, _, rel = tail.partition("/")
        root = self._roots.get(prefix)
        if root is None:
            return web.Response(status=404, text="unknown prefix")
        target = (root / rel).resolve()
        try:
            target.relative_to(root)  # path-traversal guard
        except ValueError:
            return web.Response(status=403, text="forbidden")
        if not target.is_file():
            return web.Response(status=404, text="not found")
        return web.FileResponse(target)

    def set_tunnel_base(self, tunnel_base: str) -> None:
        """Record the public HTTPS base the hub exposed for this sandbox's port
        (e.g. ``https://ta-….w.modal.host``). Until set, server-mode url_for has
        no browser-reachable base to emit."""
        self._tunnel_base = (tunnel_base or "").rstrip("/") or None

    @property
    def base_url(self) -> str | None:
        # Public base in server mode (what a browser uses); local bind otherwise.
        if self._server_mode:
            return self._tunnel_base
        return self._base_url

    @property
    def roots(self) -> list[Path]:
        return list(self._roots.values())

    def url_for(self, abs_path: Path) -> str | None:
        """Map an absolute path under any served root to its browser URL.

        Local mode: ``http://127.0.0.1:<port>/<prefix>/<rel>``.
        Server mode: ``<tunnel_base>/d/<token>/<prefix>/<rel>`` (tunnel_base must
        have been delivered by the hub via set_tunnel_base).
        """
        if self._base_url is None:
            return None
        target = Path(abs_path).resolve()
        for prefix, root in self._roots.items():
            try:
                rel = target.relative_to(root)
            except ValueError:
                continue
            rel_url = f"{prefix}/{rel.as_posix()}"
            if self._server_mode:
                if not self._tunnel_base:
                    logger.warning(
                        "live_view: server mode but no tunnel base delivered yet; "
                        "cannot emit a browser-reachable URL for {}", target,
                    )
                    return None
                return f"{self._tunnel_base}/d/{self._token}/{rel_url}"
            return f"{self._base_url}/{rel_url}"
        return None
