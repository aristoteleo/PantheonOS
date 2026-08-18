"""A real Chromium, shared by the agent and the user's desktop.

One headless Chromium (Playwright, persistent profile) runs in the sandbox.
Every page in it is visible to BOTH sides at once:

  * the **user**, through the Atrium Browser app — frames stream out of CDP
    ``Page.startScreencast`` and are served by the LiveView data server's
    ``browser-frame`` endpoint (long-poll: a request parks until the page
    repaints, so idle pages cost nothing and busy pages feel live); pointer
    and keyboard events come back through ``browser-input``.
  * the **agent**, through the ``browser_*`` tools on the live_view toolset
    (navigate / read / click / type / screenshot).

That sharing is the point: the agent can open a page for the user, the user
can carry it past a login wall, and the agent can continue on the same,
now-authenticated page.

Threading: Playwright objects are loop-bound, but our callers are not — tool
calls run on ephemeral per-call loops (ThreadJob isolation) and endpoint
handlers run on the data server's daemon loop. So the engine owns a daemon
thread with its own loop, everything Playwright happens THERE, and both kinds
of caller marshal in via ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import base64
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from pantheon.utils.log import logger

# Schemes that carry their payload without "//": leave them untouched.
_SCHEME_NO_SLASH = re.compile(r"^(data|about|blob|view-source|file):", re.I)


def normalize_url(url: str) -> str:
    """What the address bar means: a bare host gets https, real schemes pass.

    ``"://" in url`` alone mis-handles ``data:`` / ``about:`` (no slashes), so
    it prepended https and produced ``https://data:…`` — an invalid URL.
    """
    if not url:
        return url
    if "://" in url or _SCHEME_NO_SLASH.match(url):
        return url
    return f"https://{url}"

VIEW_W, VIEW_H = 1280, 800
JPEG_QUALITY = 70
LONG_POLL_S = 20.0
READ_LIMIT = 8000

_BUTTONS = {0: "left", 1: "middle", 2: "right"}


class PageSession:
    """One Chromium page: its screencast state and navigation status."""

    def __init__(self, page_id: str, page: Any) -> None:
        self.id = page_id
        self.page = page
        self.cdp: Any = None
        self.frame: bytes = b""
        self.seq = 0
        self.width = VIEW_W
        self.height = VIEW_H
        self.loading = False
        self.can_back = False
        self.can_forward = False
        self.favicon = ""
        # page ids of popups this page spawned, not yet claimed by the UI —
        # drained onto the next frame response as X-Popup so the Browser can
        # open them as tabs (this is how "Log in with Google" pop-ups land).
        self.pending_popups: list[str] = []
        self.new_frame = asyncio.Condition()
        self.input_lock = asyncio.Lock()
        self.created_at = time.time()

    @property
    def url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""

    async def title(self) -> str:
        try:
            return await self.page.title()
        except Exception:
            return ""


class BrowserEngine:
    """The process-wide Chromium, on its own daemon loop."""

    _instance: "BrowserEngine | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "BrowserEngine":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = BrowserEngine()
            return cls._instance

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._pw = None
        self._context = None
        self._launch_error: str | None = None
        self.pages: dict[str, PageSession] = {}

    # ── the daemon loop ──────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        with self._start_lock:
            if self._loop is not None:
                return
            ready = threading.Event()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()

            self._thread = threading.Thread(
                target=_run, name="browser-engine", daemon=True,
            )
            self._thread.start()
            ready.wait(10)

    async def call(self, coro) -> Any:
        """Run `coro` on the engine loop, awaited from ANY loop (or thread)."""
        self._ensure_thread()
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(fut)

    # ── Chromium lifecycle (engine loop only) ────────────────────────────

    async def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        if self._launch_error:
            raise RuntimeError(self._launch_error)
        try:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            profile = Path.home() / ".pantheon" / "browser-profile"
            profile.mkdir(parents=True, exist_ok=True)
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                # New (not old) headless renders like a real Chrome and is far
                # less fingerprintable — old headless is what most "this browser
                # isn't secure" login blocks key off. A realistic UA + turning
                # off the AutomationControlled blink feature removes the rest of
                # the obvious tells, so site logins (incl. Google) behave like a
                # normal browser rather than a bot.
                headless=True,
                channel="chromium",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": VIEW_W, "height": VIEW_H},
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            # navigator.webdriver=true is the single biggest automation tell;
            # drop it (and normalise a couple of headless quirks) before any
            # page script runs.
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                "window.chrome = window.chrome || { runtime: {} };"
                "Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});"
            )
            # The persistent context opens with a blank page; close it so the
            # page registry is the single source of what exists.
            for p in list(self._context.pages):
                try:
                    await p.close()
                except Exception:
                    pass
            logger.info("browser: chromium up (profile {})", profile)
        except Exception as e:
            self._launch_error = f"chromium unavailable: {e}"
            logger.error("browser: launch failed: {}", e)
            raise RuntimeError(self._launch_error) from e

    async def _attach(self, session: PageSession) -> None:
        """Wire screencast + navigation events for a page."""
        page = session.page
        cdp = await self._context.new_cdp_session(page)  # type: ignore[union-attr]
        session.cdp = cdp

        def on_frame(params: dict) -> None:
            data = params.get("data")
            sid = params.get("sessionId")

            async def _handle() -> None:
                try:
                    if data:
                        session.frame = base64.b64decode(data)
                        session.seq += 1
                        async with session.new_frame:
                            session.new_frame.notify_all()
                    if sid is not None:
                        await cdp.send("Page.screencastFrameAck", {"sessionId": sid})
                except Exception:
                    pass  # page is closing

            asyncio.ensure_future(_handle())

        cdp.on("Page.screencastFrame", on_frame)
        await cdp.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": JPEG_QUALITY,
            "maxWidth": session.width,
            "maxHeight": session.height,
            "everyNthFrame": 1,
        })

        async def refresh_history() -> None:
            try:
                hist = await cdp.send("Page.getNavigationHistory")
                idx = hist.get("currentIndex", 0)
                session.can_back = idx > 0
                session.can_forward = idx < len(hist.get("entries", [])) - 1
            except Exception:
                pass

        def on_nav(frame: Any) -> None:
            if frame == page.main_frame:
                session.loading = True
                asyncio.ensure_future(refresh_history())

        async def refresh_favicon() -> None:
            try:
                href = await page.evaluate(
                    "() => { const l = document.querySelector(\"link[rel~='icon']\")"
                    " || document.querySelector(\"link[rel='shortcut icon']\");"
                    " return (l && l.href) ? l.href :"
                    " (location.origin ? location.origin + '/favicon.ico' : ''); }"
                )
                session.favicon = href or ""
            except Exception:
                pass

        def on_load(_: Any = None) -> None:
            session.loading = False
            asyncio.ensure_future(refresh_history())
            asyncio.ensure_future(refresh_favicon())

        page.on("framenavigated", on_nav)
        page.on("load", on_load)
        page.on("domcontentloaded", on_load)

        # A popup becomes its own tab: it is adopted as a real page, attached
        # (screencast + events), and announced to the UI via the opener's
        # pending_popups. This is what makes OAuth pop-ups ("Continue with
        # Google/GitHub") work — the user finishes the login in the new tab
        # and the opener, on the SAME shared browser, sees the result.
        def on_popup(popup: Any) -> None:
            async def _adopt() -> None:
                try:
                    child = PageSession(uuid.uuid4().hex[:12], popup)
                    self.pages[child.id] = child
                    await self._attach(child)
                    session.pending_popups.append(child.id)
                except Exception as e:
                    logger.warning("browser: popup adopt failed: {}", e)

            asyncio.ensure_future(_adopt())

        page.on("popup", on_popup)

    # ── public surface (call through .call from any loop) ────────────────

    async def open_page(self, url: str = "") -> PageSession:
        await self._ensure_browser()
        page = await self._context.new_page()  # type: ignore[union-attr]
        session = PageSession(uuid.uuid4().hex[:12], page)
        self.pages[session.id] = session
        await self._attach(session)
        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                logger.warning("browser: initial goto {} failed: {}", url, e)
        return session

    async def clear_data(self) -> None:
        """Sign out of every site: drop cookies and stored credentials."""
        if self._context is None:
            return
        try:
            await self._context.clear_cookies()
        except Exception as e:
            logger.warning("browser: clear_cookies failed: {}", e)
        # Storage (localStorage/IndexedDB) via CDP, per open page.
        for session in list(self.pages.values()):
            try:
                if session.cdp is not None:
                    await session.cdp.send("Storage.clearDataForOrigin", {
                        "origin": "*",
                        "storageTypes": "cookies,local_storage,indexeddb,service_workers,cache_storage",
                    })
            except Exception:
                pass

    def get(self, page_id: str) -> PageSession:
        session = self.pages.get(page_id)
        if session is None:
            raise KeyError(f"no such page: {page_id}")
        return session

    def latest(self, page_id: str = "") -> PageSession:
        """The addressed page, or the most recently opened one."""
        if page_id:
            return self.get(page_id)
        if not self.pages:
            raise KeyError("no browser pages are open")
        return max(self.pages.values(), key=lambda s: s.created_at)

    async def close_page(self, page_id: str) -> None:
        session = self.pages.pop(page_id, None)
        if session is None:
            return
        try:
            await session.page.close()
        except Exception:
            pass

    async def navigate(self, page_id: str, op: str, url: str = "") -> None:
        session = self.get(page_id)
        page = session.page
        if op == "goto":
            await page.goto(normalize_url(url), wait_until="domcontentloaded", timeout=30_000)
        elif op == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=30_000)
        elif op == "forward":
            await page.go_forward(wait_until="domcontentloaded", timeout=30_000)
        elif op == "reload":
            await page.reload(wait_until="domcontentloaded", timeout=30_000)
        elif op == "stop":
            await page.evaluate("() => window.stop()")
        else:
            raise ValueError(f"unknown op: {op}")

    async def dispatch(self, page_id: str, events: list[dict]) -> None:
        """Replay UI input events on the page, in order."""
        session = self.get(page_id)
        page = session.page
        async with session.input_lock:
            for ev in events:
                t = ev.get("t")
                try:
                    if t == "move":
                        await page.mouse.move(ev["x"], ev["y"])
                    elif t == "down":
                        await page.mouse.move(ev["x"], ev["y"])
                        await page.mouse.down(
                            button=_BUTTONS.get(ev.get("button", 0), "left"),
                            click_count=ev.get("clicks", 1),
                        )
                    elif t == "up":
                        await page.mouse.up(
                            button=_BUTTONS.get(ev.get("button", 0), "left"),
                            click_count=ev.get("clicks", 1),
                        )
                    elif t == "wheel":
                        await page.mouse.move(ev["x"], ev["y"])
                        await page.mouse.wheel(ev.get("dx", 0), ev.get("dy", 0))
                    elif t == "scroll":
                        # Absolute scroll from the UI's scrollbar-thumb drag.
                        await page.evaluate(
                            "(y) => window.scrollTo(0, y)", ev.get("y", 0)
                        )
                    elif t == "keydown":
                        await page.keyboard.down(ev["key"])
                    elif t == "keyup":
                        await page.keyboard.up(ev["key"])
                    elif t == "text":
                        await page.keyboard.insert_text(ev["text"])
                    elif t == "resize":
                        w = max(320, min(2560, int(ev["w"])))
                        h = max(240, min(1600, int(ev["h"])))
                        if (w, h) != (session.width, session.height):
                            session.width, session.height = w, h
                            await page.set_viewport_size({"width": w, "height": h})
                            if session.cdp is not None:
                                try:
                                    await session.cdp.send("Page.stopScreencast")
                                except Exception:
                                    pass
                                await session.cdp.send("Page.startScreencast", {
                                    "format": "jpeg",
                                    "quality": JPEG_QUALITY,
                                    "maxWidth": w,
                                    "maxHeight": h,
                                    "everyNthFrame": 1,
                                })
                except Exception as e:
                    logger.debug("browser: input {} failed: {}", t, e)

    async def status_headers(self, session: PageSession) -> dict[str, str]:
        """Navigation state as latin-1-safe response headers."""
        headers = {
            "X-Seq": str(session.seq),
            "X-Url": quote(session.url, safe=""),
            "X-Title": quote(await session.title(), safe=""),
            "X-Loading": "1" if session.loading else "0",
            "X-Back": "1" if session.can_back else "0",
            "X-Fwd": "1" if session.can_forward else "0",
            "X-Favicon": quote(session.favicon, safe=""),
            "X-W": str(session.width),
            "X-H": str(session.height),
        }
        # Scroll metrics for the UI's own scrollbar overlay — headless Chromium
        # paints no native scrollbar, so the frontend draws one from these.
        # `scrollY,scrollHeight,innerHeight`; cheap eval, only on repaints.
        try:
            m = await session.page.evaluate(
                "() => [Math.round(scrollY),"
                " Math.round(document.documentElement.scrollHeight),"
                " Math.round(innerHeight)]"
            )
            headers["X-Scroll"] = f"{m[0]},{m[1]},{m[2]}"
        except Exception:
            pass
        # Report each spawned popup once, then forget it — the UI opens a tab.
        if session.pending_popups:
            headers["X-Popup"] = ",".join(session.pending_popups)
            session.pending_popups = []
        return headers

    async def wait_frame(self, session: PageSession, since: int) -> bool:
        """Park until the page paints past `since` (or the poll times out)."""
        if session.seq > since:
            return True
        deadline = time.monotonic() + LONG_POLL_S
        while session.seq <= since:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            async with session.new_frame:
                try:
                    await asyncio.wait_for(
                        session.new_frame.wait(), timeout=min(remaining, 5.0),
                    )
                except asyncio.TimeoutError:
                    continue
        return True


# ── data-server endpoint handlers ────────────────────────────────────────────
# These run on the DATA SERVER's loop; every engine touch marshals over.


def make_frame_handler(engine: BrowserEngine):
    async def handler(request: web.Request) -> web.StreamResponse:
        page_id = request.query.get("page", "")
        since = int(request.query.get("since", "0") or 0)
        session = engine.pages.get(page_id)
        if session is None:
            return web.Response(status=404, text="no such page")
        fresh = await engine.call(engine.wait_frame(session, since))
        headers = await engine.call(engine.status_headers(session))
        if not fresh or not session.frame:
            return web.Response(status=204, headers=headers)
        return web.Response(
            body=session.frame,
            content_type="image/jpeg",
            headers=headers,
        )

    return handler


def make_input_handler(engine: BrowserEngine):
    async def handler(request: web.Request) -> web.StreamResponse:
        if request.method != "POST":
            return web.Response(status=405)
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")
        page_id = payload.get("page", "")
        events = payload.get("events", [])
        if page_id not in engine.pages:
            return web.Response(status=404, text="no such page")
        if events:
            await engine.call(engine.dispatch(page_id, events))
        return web.Response(status=204)

    return handler
