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
import json
import os
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
# Chromium's own tab strip + toolbar, in DEVICE pixels — the band an X11
# grab must skip to show the page rather than the browser around it (our
# UI draws its own chrome, so this must never reach the viewer), and the
# height a window needs ON TOP of its viewport so the page is not
# clipped. Measured against the sandbox's Chromium by streaming an
# all-red page and finding the first red row: 180 at
# --force-device-scale-factor=2. Tied to that flag — the browser UI
# scales with it. Too small and the stream wears a sliver of tab strip.
WINDOW_CHROME_PX = 180
# Chromium rasters everything at this scale (the launch flag below), and
# Browser.setWindowBounds speaks DIP — bounds divided by it. Passing
# physical pixels there made every window exactly twice its intended size:
# the page then occupied the top-left quarter of it, the X11 grab took a
# rectangle that was mostly chrome and empty desk, and a scroll changed
# almost nothing on screen. Capture rectangles stay in physical pixels,
# because that is what x11grab reads.
RASTER_SCALE = 2
JPEG_QUALITY = 70
# The tunnel out of the sandbox caps at ~20 Mbps (measured; shared across
# connections, so striping cannot help) — fps is bytes-bound. Dense (Retina)
# frames carry 4x the pixels, so they trade JPEG quality for rate; the
# artifacts hide in the pixel density.
JPEG_QUALITY_DENSE = 55


def _cast_quality(dsf: float) -> int:
    return JPEG_QUALITY if dsf <= 1.2 else JPEG_QUALITY_DENSE


# What the capture pipeline can actually keep up with, in device pixels per
# frame. Measured in this sandbox on a scrolling page, per frame: grab
# ~10 ms, colour convert ~13 ms, H.264 encode ~8 ms at 11 Mpx — 30 fps with
# room to spare, where VP8's 29 ms encode held it to 18. 9 Mpx leaves that
# headroom for the agent's own work while keeping a full-size window near
# 2x. The client applies the same budget; the pod enforces it, because an
# old or buggy client must not be able to ask for a stream nobody can
# encode.
CAST_PIXEL_BUDGET = 9_000_000


def _clamp_debt(v: float) -> float:
    return max(-WHEEL_MAX_DEBT_PX, min(WHEEL_MAX_DEBT_PX, v))


def cap_density(width: int, height: int, dsf: float) -> float:
    """Lower the density until the frame fits the encoder's budget."""
    if width <= 0 or height <= 0:
        return dsf
    fit = (CAST_PIXEL_BUDGET / float(width * height)) ** 0.5
    return max(1.0, min(dsf, round(fit, 2)))
# Wheel smoothing: the size of one step of owed scroll, and how long the
# drain task waits between steps. 40 px every 10 ms is ~4000 px/s — faster
# than anyone scrolls, so the debt never grows, while still giving the
# compositor several intermediate positions per notch.
WHEEL_STEP_PX = 40
WHEEL_STEP_S = 0.010
# A flick can owe more than a screen; past that, catching up matters more
# than showing every pixel of the journey.
WHEEL_MAX_DEBT_PX = 4000

# How long a page keeps painting after its last viewer leaves. Long enough
# that a long-poll's next request finds the screencast still running,
# short enough that a page nobody watches goes quiet.
SCREENCAST_LINGER_S = 8.0

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
        # Rendering density, set by the viewing client from its display
        # (capped at 2). Pixels scale by it; CSS-pixel geometry — viewport,
        # input coordinates, agent screenshots — does not.
        self.dsf = 1.0
        # This page owns an OS window (so X11 capture can see it), and where
        # that window sits on the virtual display.
        self.windowed = False
        self.rect: tuple[int, int, int, int] | None = None
        # How many consumers want screencast frames right now. Zero means
        # the JPEG encoder is off — the X11 path never turns it on at all.
        self.viewers = 0
        self.casting = False
        self.linger: Any = None
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
        # Wheel motion still owed to the page. Wheels ACCUMULATE rather than
        # queue: scrolling down and then up must cancel, not play back in
        # order (see the drain task in dispatch()).
        self.wheel_dx = 0.0
        self.wheel_dy = 0.0
        self.wheel_at = (0.0, 0.0)
        self.wheel_task: Any = None
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
        self._browser_cdp = None
        self._xvfb_display: str | None = None
        self._xvfb_proc = None
        from .x11cast import TilePool

        self._tiles = TilePool()
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

    @staticmethod
    def _clear_stale_locks(profile: Path) -> None:
        """Drop the previous sandbox's ProcessSingleton files.

        The profile lives on a volume that OUTLIVES the sandbox, so a pod
        that dies without shutting Chromium down leaves its lock behind and
        the next pod's Chromium refuses to start at all ("Failed to create
        a ProcessSingleton for your profile directory"). Any lock we find
        here is stale by construction: this process is the only one that
        launches Chromium in this container, and it has not yet.
        """
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            path = profile / name
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
                    logger.info("browser: cleared stale {}", name)
            except Exception as e:
                logger.warning("browser: could not clear {}: {}", name, e)

    async def _ensure_xvfb(self) -> str | None:
        """Start a virtual X display and return DISPLAY, or None to stay
        headless.

        Headful-under-Xvfb is the foundation for native capture: headless
        Chromium has no tab-capture stack at all (getDisplayMedia and
        chrome.tabCapture both die with NotReadableError), and a real
        display is also what lets arbitrary X11 GUI apps render for the
        display-streaming path. Missing Xvfb (an older image) degrades to
        headless — everything current keeps working.
        """
        if self._xvfb_display is not None:
            return self._xvfb_display
        import shutil
        import subprocess

        if shutil.which("Xvfb") is None:
            logger.info("browser: no Xvfb on this image; staying headless")
            return None
        display = ":97"
        try:
            # Room for several 2x windows side by side, since each streamed
            # page needs a DISJOINT tile (overlapping windows capture each
            # other). 8192x4608 fits nine 2200x1500 tiles and costs ~150 MB
            # of framebuffer, which this sandbox has to spare. 24-bit, no
            # TCP listener.
            self._xvfb_proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "8192x4608x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(50):
                probe = subprocess.run(
                    ["xdpyinfo", "-display", display],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if probe.returncode == 0:
                    self._xvfb_display = display
                    logger.info("browser: Xvfb up on {}", display)
                    return display
                await asyncio.sleep(0.1)
            logger.warning("browser: Xvfb never answered; staying headless")
        except Exception as e:
            logger.warning("browser: Xvfb failed ({}); staying headless", e)
        return None

    def _context_died(self) -> None:
        """The browser process went away (crash, OOM kill, X hiccup).

        Without this, `_context` stays truthy and every later call answers
        'browser has been closed' until the sandbox itself is replaced —
        one bad moment during a busy boot bricked the browser for the
        pod's whole life. Dropping the state here lets the next call
        relaunch from scratch.
        """
        logger.warning("browser: context died; will relaunch on next use")
        self._context = None
        self._browser_cdp = None
        self.pages.clear()

    async def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        if self._launch_error:
            raise RuntimeError(self._launch_error)
        try:
            from playwright.async_api import async_playwright

            display = await self._ensure_xvfb()
            self._pw = await async_playwright().start()
            profile = Path.home() / ".pantheon" / "browser-profile"
            profile.mkdir(parents=True, exist_ok=True)
            self._clear_stale_locks(profile)
            self._context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                # Headful under Xvfb when the image carries one (the capture
                # stack needs a real display; headful is also the least
                # fingerprintable form there is). Otherwise: new headless,
                # which renders like a real Chrome and passes most login
                # checks. A realistic UA + turning off the
                # AutomationControlled blink feature removes the remaining
                # obvious tells either way.
                headless=display is None,
                env={**os.environ, "DISPLAY": display} if display else None,
                # A pod's first minutes are a boot storm (pip prewarms, app
                # installs); a headful first paint under that load can blow
                # playwright's default 30s.
                timeout=120_000,
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
                    # Raster every surface at 2x. The screencast captures the
                    # raster (it ignores Emulation.setDeviceMetricsOverride —
                    # verified empirically), so this is what makes Retina-
                    # density frames possible at all. Per-viewer density then
                    # only picks the screencast's max dims: 2x viewers get the
                    # raster 1:1, 1x viewers get a supersampled downscale.
                    # Coordinates stay CSS pixels throughout.
                    "--force-device-scale-factor=2",
                ],
            )
            ctx = self._context
            ctx.on("close",
                   lambda: self._context_died() if self._context is ctx else None)
            # navigator.webdriver=true is the single biggest automation tell;
            # drop it (and normalise a couple of headless quirks) before any
            # page script runs.
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                "window.chrome = window.chrome || { runtime: {} };"
                "Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});"
            )
            # The persistent context opens with a blank page. Headless: close
            # it so the page registry is the single source of what exists.
            # HEADFUL: keep it — closing the last window makes Chrome refuse
            # Target.createTarget ("Failed to open a new tab") or exit
            # outright, which bricked every later page open. The blank page
            # is never registered, never streamed, and holds the window open.
            if display is None:
                for p in list(self._context.pages):
                    try:
                        await p.close()
                    except Exception:
                        pass
            logger.info("browser: chromium up (profile {}, display {})",
                        profile, display or "headless")
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
        # NOT started here. A screencast runs the JPEG encoder continuously
        # for as long as it is on, and every page used to start one the
        # moment it opened and keep it forever — so N open pages cost N
        # encoders whether or not anyone was watching, and at 2x density
        # each frame is four times the work. That is why opening the fifth
        # page took seconds. Consumers acquire it now (acquire_viewer).

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

    async def _open_windowed(self, url: str):
        """A page in its OWN OS window, or None to fall back to a tab.

        X11 capture can only see what is actually rendered, and a
        background TAB paints nothing — so a page that will be streamed
        from the display needs a window of its own. Tabs remain correct
        for the screencast path, hence the graceful None.
        """
        if self._xvfb_display is None or self._context is None:
            return None
        try:
            before = set(self._context.pages)
            keeper = next(iter(before), None)
            if keeper is None:
                return None
            # One CDP session for the life of the browser: opening a fresh
            # one per page costs a round trip on the path the user waits on.
            if self._browser_cdp is None:
                self._browser_cdp = await self._context.new_cdp_session(keeper)
            cdp = self._browser_cdp
            await cdp.send("Target.createTarget", {
                "url": url or "about:blank", "newWindow": True,
                "width": VIEW_W, "height": VIEW_H,
            })
            for _ in range(200):
                fresh = [p for p in self._context.pages if p not in before]
                if fresh:
                    return fresh[0]
                await asyncio.sleep(0.01)
        except Exception as e:
            logger.info("browser: windowed open failed ({}); using a tab", e)
        return None

    async def open_page(self, url: str = "") -> PageSession:
        t_open = time.monotonic()
        await self._ensure_browser()
        page = await self._open_windowed(url)
        windowed = page is not None
        if page is None:
            page = await self._context.new_page()  # type: ignore[union-attr]
        session = PageSession(uuid.uuid4().hex[:12], page)
        session.windowed = windowed
        self.pages[session.id] = session
        await self._attach(session)
        # Window placement and the page load are independent, and the user
        # is waiting on this call: run them together rather than in series.
        async def _shape() -> None:
            # Density first, then a window sized to match it.
            await self.set_metrics(session, session.width, session.height,
                                   session.dsf)
            await self.place_window(session)

        placing = asyncio.ensure_future(_shape()) if windowed else None
        if url:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                logger.warning("browser: initial goto {} failed: {}", url, e)
        if placing is not None:
            try:
                await placing
            except Exception as e:
                logger.info("browser: window placement failed: {}", e)
        logger.info("browser: page {} open in {:.0f} ms ({})",
                    session.id, (time.monotonic() - t_open) * 1000,
                    "window" if windowed else "tab")
        return session

    async def acquire_viewer(self, session: PageSession) -> None:
        """Someone wants frames: make sure the screencast is running."""
        if session.linger is not None:
            session.linger.cancel()
            session.linger = None
        session.viewers += 1
        if session.viewers == 1 and not session.casting:
            await self._set_screencast(session, True)

    async def release_viewer(self, session: PageSession) -> None:
        """The last consumer left — stop, but not instantly.

        The long-poll acquires for one frame at a time, so stopping the
        moment it returns would start and stop the screencast on every
        single poll: thrash, and a first frame that has to wait for the
        next repaint. Linger instead, and let a page nobody is watching go
        quiet a few seconds later.
        """
        session.viewers = max(0, session.viewers - 1)
        if session.viewers:
            return
        if session.linger is not None:
            session.linger.cancel()

        async def _stop_soon() -> None:
            try:
                await asyncio.sleep(SCREENCAST_LINGER_S)
            except asyncio.CancelledError:
                return
            if session.viewers == 0:
                await self._set_screencast(session, False)

        session.linger = asyncio.ensure_future(_stop_soon())

    async def _set_screencast(self, session: PageSession, on: bool) -> None:
        if session.cdp is None:
            return
        try:
            if on:
                await session.cdp.send("Page.startScreencast", {
                    "format": "jpeg",
                    "quality": _cast_quality(session.dsf),
                    "maxWidth": min(4096, int(session.width * session.dsf)),
                    "maxHeight": min(4096, int(session.height * session.dsf)),
                    "everyNthFrame": 1,
                })
            else:
                await session.cdp.send("Page.stopScreencast")
            session.casting = on
        except Exception as e:
            logger.info("browser: screencast {} failed: {}",
                        "start" if on else "stop", e)

    async def _park_window(self, session: PageSession, window_id: int,
                           w: int, h: int) -> None:
        """Move a window off the display so it cannot cover a tile."""
        from .x11cast import PARK_Y, SCREEN_H, SCREEN_W

        try:
            await session.cdp.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {
                    "left": 0, "top": PARK_Y // RASTER_SCALE,
                    "width": min(w, SCREEN_W) // RASTER_SCALE,
                    "height": min(h, SCREEN_H) // RASTER_SCALE,
                    "windowState": "normal",
                },
            })
        except Exception as e:
            logger.info("browser: parking failed: {}", e)

    async def place_window(self, session: PageSession) -> tuple[int, int, int, int] | None:
        """Park this page's OS window on its own tile and remember the rect.

        Tiles are disjoint because an X11 grab of overlapping windows would
        capture whichever is on top — someone else's pixels.
        """
        if not session.windowed or session.cdp is None:
            return None
        from .x11cast import tile_rect

        w = max(2, int(session.width * session.dsf))
        h = max(2, int(session.height * session.dsf))
        try:
            info = await session.cdp.send("Browser.getWindowForTarget")
            # The window carries the viewport PLUS Chromium's own chrome;
            # sized to the viewport alone, the page would be clipped by the
            # height of the tab strip.
            outer_h = h + WINDOW_CHROME_PX
            slot = self._tiles.acquire(session.id)
            spot = tile_rect(slot, w, outer_h)  # physical pixels
            if spot is None:
                # No disjoint room left. A window with nowhere to go must be
                # PARKED off the bottom of the display, not left where
                # Chromium first put it: the default position lands on top of
                # tile 0, and an X11 grab takes whatever is on top — so the
                # tiled page streamed the parked page's blank window, and
                # scrolling appeared to do nothing at all.
                self._tiles.release(session.id)
                session.windowed = False
                session.rect = None
                await self._park_window(session, info["windowId"], w, outer_h)
                logger.info("browser: no tile for {}; parked, JPEG path",
                            session.id)
                return None
            left, top = spot
            await session.cdp.send("Browser.setWindowBounds", {
                "windowId": info["windowId"],
                "bounds": {
                    # DIP, not pixels — see RASTER_SCALE.
                    "left": left // RASTER_SCALE, "top": top // RASTER_SCALE,
                    "width": w // RASTER_SCALE,
                    "height": outer_h // RASTER_SCALE,
                    "windowState": "normal",
                },
            })
            # Capture the page area only — below the tab strip and toolbar.
            session.rect = (left, top + WINDOW_CHROME_PX, w, h)
            return session.rect
        except Exception as e:
            logger.info("browser: window placement failed: {}", e)
            self._tiles.release(session.id)
            session.windowed = False
            session.rect = None
            return None

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
        # Give the tile back, or a busy session walks the slot index off
        # the display and every later window loses the fast path.
        self._tiles.release(page_id)
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
                        # Wheels ADD to what the page still owes and return
                        # immediately; a separate task pays it off in small
                        # steps. Two reasons, both learned the hard way:
                        #
                        # Chromium scrolls INSTANTLY for a synthesized wheel
                        # (it animates real ones; --enable-smooth-scrolling
                        # changes nothing, measured), so one 120 px notch is a
                        # teleport and 20 notches a second left 80% of streamed
                        # frames identical to the one before. Small steps fix
                        # that.
                        #
                        # But stepping INSIDE this loop made it worse in a way
                        # no frame counter shows: the sleeps held the input
                        # path, events queued, and the picture kept scrolling
                        # down for a while after the user had already flicked
                        # back up. Accumulating instead means a reversal
                        # CANCELS what is still owed, which is what a real
                        # wheel does.
                        session.wheel_dx = _clamp_debt(
                            session.wheel_dx + float(ev.get("dx", 0) or 0))
                        session.wheel_dy = _clamp_debt(
                            session.wheel_dy + float(ev.get("dy", 0) or 0))
                        session.wheel_at = (ev["x"], ev["y"])
                        if session.wheel_task is None or session.wheel_task.done():
                            session.wheel_task = asyncio.ensure_future(
                                self._drain_wheel(session))
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
                        s = max(1.0, min(2.0, float(ev.get("s") or 1)))
                        s = cap_density(w, h, s)
                        prev_s = session.dsf
                        if (w, h, s) != (session.width, session.height, prev_s):
                            session.width, session.height = w, h
                            session.dsf = s
                            await page.set_viewport_size({"width": w, "height": h})
                            if session.windowed:
                                # The OS window must follow, or the X11 grab
                                # keeps aiming at the old rectangle.
                                await self.place_window(session)
                            if session.viewers:
                                await self._set_screencast(session, False)
                                await self._set_screencast(session, True)
                except Exception as e:
                    logger.debug("browser: input {} failed: {}", t, e)

    async def _drain_wheel(self, session: PageSession) -> None:
        """Pay off the page's owed scroll in small steps.

        Runs OUTSIDE the input lock, so a wheel event never waits on this —
        it just adds to the debt (or cancels it) and returns.
        """
        page = session.page
        try:
            while True:
                dx, dy = session.wheel_dx, session.wheel_dy
                if abs(dx) < 1 and abs(dy) < 1:
                    session.wheel_dx = session.wheel_dy = 0.0
                    return
                # One step: at most WHEEL_STEP_PX, and never more than what
                # is owed (so a small trackpad delta lands in one go).
                scale = min(1.0, WHEEL_STEP_PX / max(abs(dx), abs(dy)))
                sx, sy = dx * scale, dy * scale
                session.wheel_dx -= sx
                session.wheel_dy -= sy
                try:
                    x, y = session.wheel_at
                    await page.mouse.move(x, y)
                    await page.mouse.wheel(sx, sy)
                except Exception:
                    session.wheel_dx = session.wheel_dy = 0.0
                    return
                await asyncio.sleep(WHEEL_STEP_S)
        finally:
            session.wheel_task = None

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
        await engine.call(engine.acquire_viewer(session))
        try:
            fresh = await engine.call(engine.wait_frame(session, since))
        finally:
            await engine.call(engine.release_viewer(session))
        headers = await engine.call(engine.status_headers(session))
        if not fresh or not session.frame:
            return web.Response(status=204, headers=headers)
        return web.Response(
            body=session.frame,
            content_type="image/jpeg",
            headers=headers,
        )

    return handler


def make_stream_handler(engine: BrowserEngine):
    """One WebSocket = one page's live feed plus its input backchannel.

    The long-poll endpoint pays a full HTTP round trip per frame, which is
    the whole reason the remote browser feels like a slideshow. Here frames
    PUSH as the page paints (latest-wins: a consumer that falls behind skips
    straight to the newest), a JSON status line precedes each frame, and
    input events ride back on the same socket. The WebRTC gateway is the
    primary consumer; anything that prefers push over poll may connect.
    """

    async def handler(request: web.Request) -> web.StreamResponse:
        page_id = request.query.get("page", "")
        session = engine.pages.get(page_id)
        if session is None:
            return web.Response(status=404, text="no such page")
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        await engine.call(engine.acquire_viewer(session))

        stop = asyncio.Event()

        async def pump() -> None:
            since = 0
            while not stop.is_set() and not ws.closed:
                try:
                    fresh = await engine.call(engine.wait_frame(session, since))
                except Exception:
                    break
                if stop.is_set() or ws.closed:
                    break
                if not fresh:
                    continue  # poll window elapsed with no paint; heartbeats cover us
                since = session.seq
                frame = session.frame
                if frame is None:
                    continue
                try:
                    headers = await engine.call(engine.status_headers(session))
                    status = {
                        k.lower().replace("x-", "", 1): v
                        for k, v in headers.items() if k != "X-Seq"
                    }
                    status.update({"t": "status", "seq": since})
                    await ws.send_json(status)
                    await ws.send_bytes(frame)
                except Exception:
                    break

        pump_task = asyncio.create_task(pump())
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except Exception:
                        continue
                    events = payload.get("events") or []
                    if events and page_id in engine.pages:
                        try:
                            await engine.call(engine.dispatch(page_id, events))
                        except Exception:
                            pass
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE, web.WSMsgType.CLOSING):
                    break
        finally:
            stop.set()
            pump_task.cancel()
            await engine.call(engine.release_viewer(session))
        return ws

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
