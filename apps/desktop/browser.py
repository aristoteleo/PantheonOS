"""A real Chromium, shared by the agent and the user's desktop.

One headless Chromium (Playwright, persistent profile) runs in the sandbox.
Every page in it is visible to BOTH sides at once:

  * the **user**, through the Atrium Browser app — one page at a time holds
    "the stage": the X framebuffer is fitted to that page's Chromium window
    and an xpra shadow of the display streams it, native chrome and all, to
    the html5 client in the app's iframe. Input goes straight into that
    client, so the user drives Chromium itself.
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
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pantheon.utils.log import logger

# Schemes that carry their payload without "//": leave them untouched.
_SCHEME_NO_SLASH = re.compile(r"^(data|about|blob|view-source|file):", re.I)


#: Browser `KeyboardEvent.code` -> X keysym name. Position, not meaning: the
#: display's keymap decides what the key produces, so shift/altgr behave the
#: way they do on a real keyboard.
KEYSYM_BY_CODE: dict[str, str] = {
    **{f"Key{c}": c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{f"Digit{d}": d for d in "0123456789"},
    **{f"Numpad{d}": f"KP_{d}" for d in "0123456789"},
    **{f"F{i}": f"F{i}" for i in range(1, 25)},
    "Enter": "Return", "NumpadEnter": "KP_Enter", "Tab": "Tab",
    "Space": "space", "Backspace": "BackSpace", "Escape": "Escape",
    "Delete": "Delete", "Insert": "Insert",
    "Home": "Home", "End": "End", "PageUp": "Prior", "PageDown": "Next",
    "ArrowUp": "Up", "ArrowDown": "Down", "ArrowLeft": "Left",
    "ArrowRight": "Right",
    "Minus": "minus", "Equal": "equal", "BracketLeft": "bracketleft",
    "BracketRight": "bracketright", "Backslash": "backslash",
    "Semicolon": "semicolon", "Quote": "apostrophe", "Backquote": "grave",
    "Comma": "comma", "Period": "period", "Slash": "slash",
    "NumpadAdd": "KP_Add", "NumpadSubtract": "KP_Subtract",
    "NumpadMultiply": "KP_Multiply", "NumpadDivide": "KP_Divide",
    "NumpadDecimal": "KP_Decimal",
    "ShiftLeft": "Shift_L", "ShiftRight": "Shift_R",
    "ControlLeft": "Control_L", "ControlRight": "Control_R",
    "AltLeft": "Alt_L", "AltRight": "Alt_R",
    # A Mac user's ⌘ is the natural "browser shortcut" key, and Chromium on
    # Linux listens to Control — so Meta arrives as Control here.
    "MetaLeft": "Control_L", "MetaRight": "Control_R",
    "OSLeft": "Control_L", "OSRight": "Control_R",
    "CapsLock": "Caps_Lock", "ContextMenu": "Menu",
}


def input_events(actions: list[dict]) -> list[dict]:
    """Expand what an agent means into what the input path replays.

    The engine speaks the viewer's vocabulary — move, down, up, wheel,
    keydown, keyup, text — because that is what a hand produces. An agent
    thinks in whole gestures, so a click is one action here and three
    events there, and a key is one action and two events. Writing the
    expansion out at the call site is how a "click" ends up missing its
    mouse-up in one place and not another.

    Coordinates are CSS pixels of the page, the same ones a screenshot is
    measured in and the same ones the viewer sends.
    """
    out: list[dict] = []
    for a in actions:
        t = str(a.get("t") or a.get("type") or "").lower()
        x, y = a.get("x"), a.get("y")
        button = int(a.get("button", 0) or 0)
        if t in ("click", "dblclick", "rightclick"):
            if x is None or y is None:
                raise ValueError(f"{t} needs x and y")
            clicks = 2 if t == "dblclick" else 1
            btn = 2 if t == "rightclick" else button
            out.append({"t": "move", "x": x, "y": y})
            out.append({"t": "down", "x": x, "y": y, "button": btn, "clicks": clicks})
            out.append({"t": "up", "x": x, "y": y, "button": btn, "clicks": clicks})
        elif t in ("move", "down", "up"):
            if x is None or y is None:
                raise ValueError(f"{t} needs x and y")
            out.append({"t": t, "x": x, "y": y, "button": button,
                        "clicks": int(a.get("clicks", 1) or 1)})
        elif t == "drag":
            for k in ("x", "y", "to_x", "to_y"):
                if a.get(k) is None:
                    raise ValueError("drag needs x, y, to_x and to_y")
            out.append({"t": "move", "x": x, "y": y})
            out.append({"t": "down", "x": x, "y": y, "button": button, "clicks": 1})
            out.append({"t": "move", "x": a["to_x"], "y": a["to_y"]})
            out.append({"t": "up", "x": a["to_x"], "y": a["to_y"],
                        "button": button, "clicks": 1})
        elif t == "wheel":
            out.append({"t": "wheel", "dx": float(a.get("dx", 0) or 0),
                        "dy": float(a.get("dy", 0) or 0),
                        "x": x if x is not None else 0,
                        "y": y if y is not None else 0})
        elif t == "key":
            key = a.get("key")
            if not key:
                raise ValueError("key needs a key name, e.g. Enter or ArrowDown")
            out.append({"t": "keydown", "key": key})
            out.append({"t": "keyup", "key": key})
        elif t == "text":
            out.append({"t": "text", "text": str(a.get("text", ""))})
        elif t == "scroll":
            out.append({"t": "scroll", "y": float(a.get("y", 0) or 0)})
        else:
            raise ValueError(f"unknown action: {t or a!r}")
    return out


def surviving_favicon(current: str, new_url: str) -> str:
    """The icon a tab keeps as it navigates: only its own site's.

    A favicon used to survive until the NEXT page finished loading and an
    evaluate came back with its icon, so a tab already titled "Google"
    wore Wikipedia's W for a second or two. Empty means "no icon yet" and
    the tab falls back to its letter chip, which is honest.
    """
    if not current:
        return ""
    try:
        from urllib.parse import urlsplit

        return current if urlsplit(current).netloc == urlsplit(new_url).netloc else ""
    except Exception:
        return ""


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
WINDOW_CHROME_PX = 90
# Chromium rasters everything at this scale (the launch flag below), and
# Browser.setWindowBounds speaks DIP — bounds divided by it. Passing
# physical pixels there made every window exactly twice its intended size:
# the page then occupied the top-left quarter of it, the X11 grab took a
# rectangle that was mostly chrome and empty desk, and a scroll changed
# almost nothing on screen. Capture rectangles stay in physical pixels,
# because that is what x11grab reads.
RASTER_SCALE = 1
# The virtual display. Big enough that any window fits at any size the
# viewer's desktop can be, with a row at the bottom to park the windows
# that are not on the stage — off the framebuffer, so the shadow cannot
# show them.
SCREEN_W, SCREEN_H = 12288, 6912
PARK_Y = SCREEN_H - 80


def _clamp_debt(v: float) -> float:
    return max(-WHEEL_MAX_DEBT_PX, min(WHEEL_MAX_DEBT_PX, v))
# Wheel smoothing: the size of one step of owed scroll, and how long the
# drain task waits between steps. 40 px every 10 ms is ~4000 px/s — faster
# than anyone scrolls, so the debt never grows, while still giving the
# compositor several intermediate positions per notch.
WHEEL_STEP_PX = 40
WHEEL_STEP_S = 0.010
# A flick can owe more than a screen; past that, catching up matters more
# than showing every pixel of the journey.
WHEEL_MAX_DEBT_PX = 4000

# xpra shadow of the Xvfb display: the html5 client rides the sandbox
# tunnel directly, so page text stays picture-sharp (webp, no chroma
# subsampling) and there is no gateway hop. Shadow serves the WHOLE
# display as one desktop window, so staging shrinks the framebuffer to
# exactly the staged window — every other window lands outside it. One
# staged page at a time; every other window is parked off the framebuffer.
XPRA_PORT = 14500
XPRA_PASSWORD_FILE = "/tmp/pantheon-xpra-pass"

_BUTTONS = {0: "left", 1: "middle", 2: "right"}


class PageSession:
    """One Chromium page: the window it lives in, and its nav status."""

    def __init__(self, page_id: str, page: Any) -> None:
        self.id = page_id
        self.page = page
        self.cdp: Any = None
        self.width = VIEW_W
        self.height = VIEW_H
        # Rendering density, set by the viewing client from its display
        # (capped at 2). Pixels scale by it; CSS-pixel geometry — viewport,
        # input coordinates, agent screenshots — does not.
        self.dsf = 1.0
        # This page owns an OS window of its own (rather than being a tab in
        # someone else's), which is what makes it stageable.
        self.windowed = False
        self.rect: tuple[int, int, int, int] | None = None
        # The page that opened this one, if it arrived as a popup: a popup of
        # the staged page belongs ON the stage (that is how a login window
        # behaves), not parked off the framebuffer with everything else.
        self.opener: str | None = None
        self.loading = False
        self.can_back = False
        self.can_forward = False
        self.favicon = ""
        self.input_lock = asyncio.Lock()
        # Sizing the page and sizing its window are two awaits apart, and a
        # second resize arriving in between used to interleave with the
        # first: the newer call set the page's metrics, the older one then
        # set the window to the size it had captured, and the page rendered
        # into the corner of a window meant for something else.
        self.shape_lock = asyncio.Lock()
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
        self._open_lock = asyncio.Lock()
        self._xvfb_display: str | None = None
        self._xvfb_proc = None
        # Which page owns which OS window, so a page that opened as a
        # TAB in another page's window never moves that window.
        self._windows: dict[int, str] = {}
        # The first window after a launch may arrive late; later
        # ones either come quickly or are not coming at all.
        self._cold_start = True
        self._launch_lock = asyncio.Lock()
        self.pages: dict[str, PageSession] = {}
        # xpra shadow state: one staged page owns the (shrunken) display.
        self._xpra_proc = None
        self._xpra_password: str | None = None
        self._staged_page: str | None = None
        # The framebuffer while a stage lasts, and the floor the viewer asked
        # for it (its whole desktop, typically). Every framebuffer change
        # makes the shadow tear its desktop window down and re-create it —
        # seconds of stale picture in the corner of the frame — so within a
        # stage it only ever grows; a resize then moves the Chromium window,
        # which is plain damage.
        self._stage_fb: tuple[int, int] | None = None
        self._stage_min_fb: tuple[int, int] = (0, 0)
        self._xdisplay = None  # X connection for key injection

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

    @staticmethod
    def _evict_volume_caches(profile: Path) -> None:
        """Delete cache directories the profile left on the volume.

        The profile lives on a network volume so logins survive a restart.
        Chromium's caches do not need to survive anything, and they are the
        overwhelming majority of it: a profile measured in a real sandbox
        was 250 MB, of which 232 MB was Cache and Code Cache. Every
        navigation then read and wrote them over the network, which is why
        opening a page took nine seconds there and under one where the
        profile sat on local disk. Chromium is pointed at a local cache
        directory now, so anything still here is dead weight — and it is
        walked at startup whether it is used or not.
        """
        import shutil

        for rel in ("Default/Cache", "Default/Code Cache", "Default/GPUCache",
                    "ShaderCache", "GrShaderCache", "GraphiteDawnCache"):
            path = profile / rel
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                    logger.info("browser: evicted {} from the volume", rel)
            except Exception as e:
                logger.warning("browser: could not evict {}: {}", rel, e)

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
            # other). The size lives with the tiling code that depends on
            # it — a literal here once drifted from it. 24-bit, no TCP
            # listener.
            self._xvfb_proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", f"{SCREEN_W}x{SCREEN_H}x24",
                 "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(50):
                probe = subprocess.run(
                    ["xdpyinfo", "-display", display],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if probe.returncode == 0:
                    self._xvfb_display = display
                    logger.info("browser: Xvfb up on {} at {}x{}",
                                display, SCREEN_W, SCREEN_H)
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
        self._cold_start = True
        self.pages.clear()

    async def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        # One launch at a time. The prewarm at boot and a user's first page
        # open now race by design — the whole point is that one of them has
        # already paid for the launch — and without this both would start a
        # Chromium against the same profile, which is exactly the situation
        # the ProcessSingleton lock exists to refuse.
        async with self._launch_lock:
            if self._context is not None:
                return
            await self._launch_browser()

    async def _launch_browser(self) -> None:
        if self._launch_error:
            raise RuntimeError(self._launch_error)
        try:
            await self._launch_browser_once()
            return
        except Exception as e:
            # Restart overlap: the OLD sandbox's Chromium can still hold the
            # volume profile's ProcessSingleton while this one boots. That is
            # a moment, not a state — wait it out, clear the locks it left,
            # and go again.
            if not any(k in str(e) for k in ("ProcessSingleton", "SingletonLock")):
                self._launch_error = f"chromium unavailable: {e}"
                logger.error("browser: launch failed: {}", e)
                raise RuntimeError(self._launch_error) from e
            logger.warning(
                "browser: profile locked by a previous sandbox; retrying once")
        await asyncio.sleep(3.0)
        try:
            await self._launch_browser_once()
        except Exception as e:
            if any(k in str(e) for k in ("ProcessSingleton", "SingletonLock")):
                # NOT sticky: the old holder dies within seconds of its
                # sandbox — the next attempt may simply find it gone.
                logger.error("browser: profile still locked: {}", e)
                raise RuntimeError(
                    "the browser profile is still locked by a previous "
                    "sandbox; it usually frees within seconds — try again"
                ) from e
            self._launch_error = f"chromium unavailable: {e}"
            logger.error("browser: launch failed: {}", e)
            raise RuntimeError(self._launch_error) from e

    async def _launch_browser_once(self) -> None:
        from playwright.async_api import async_playwright

        display = await self._ensure_xvfb()
        if self._pw is None:
            self._pw = await async_playwright().start()
        profile = Path.home() / ".pantheon" / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        self._clear_stale_locks(profile)
        # Off the loop: this deletes thousands of files on a NETWORK
        # volume, and a loop that stops answering for long enough is a
        # pod the hub's health check declares dead and destroys — which
        # costs the user their sandbox and minutes of waiting for
        # another. Nothing here is urgent enough to be worth that.
        await asyncio.to_thread(self._evict_volume_caches, profile)
        cache_dir = Path("/tmp/pantheon-browser-cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
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
                # 1x, matched end-to-end: the xpra iframe presents the
                # framebuffer 1:1 — every scaling trick between them
                # (transform:scale broke the client's canvas painting,
                # zoom broke its devicePixelRatio accounting) put the
                # window out of view. RASTER_SCALE and WINDOW_CHROME_PX
                # above are matched to this flag.
                "--force-device-scale-factor=1",
                # Occlusion detection stays ON. Turning it off kept
                # every open tab painting at full rate, and five tabs —
                # three of them animated interstitials — starved the
                # encoder down to 9 fps on the one being watched. The
                # window that IS watched is brought to front when the
                # capture aims at it, which is what tells Chromium to
                # keep painting it; the rest may sleep, exactly as they
                # would in a browser on a desk.
                "--disable-features=CalculateNativeWinOcclusion",
                # Caches on LOCAL disk, never on the volume. The profile
                # is on a network volume so logins survive a restart;
                # caches need to survive nothing and are nearly all of
                # its bulk, and every navigation pays for them twice
                # over the network. Measured in a real sandbox: nine
                # seconds to open a page with the cache on the volume.
                f"--disk-cache-dir={cache_dir / 'http'}",
                f"--media-cache-dir={cache_dir / 'media'}",
            ],
        )
        ctx = self._context
        ctx.on("close",
               lambda: self._context_died() if self._context is ctx else None)
        # Warm the xpra shadow so the first stage_page doesn't wait on
        # its startup; a missing binary makes this a cheap no-op.
        asyncio.ensure_future(self._ensure_xpra())
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
        else:
            await self._park_keeper()
        logger.info("browser: chromium up (profile {}, display {})",
                    profile, display or "headless")

    async def reshape(self, session: PageSession,
                      w: int, h: int, s: float) -> None:
        """Give the page a size and its window a matching one, atomically.

        The two are several awaits apart. A second resize arriving in the
        middle interleaved with the first: the newer call set the page's
        metrics, the older one then sized the window to what it had
        captured, and the page rendered into the corner of a window meant
        for another size — the picture filling a fraction of the frame,
        with bare desk around it.
        """
        async with session.shape_lock:
            session.width, session.height = w, h
            session.dsf = s
            await self.set_metrics(session, w, h, s)
            if self._staged_page == session.id:
                # Staged for xpra: the window lives at the origin and the
                # framebuffer is cut to it — never back onto a tile.
                await self._stage_place(session)
            elif session.windowed:
                # The OS window must follow, or the X11 grab keeps aiming
                # at the rectangle the window has left.
                await self.place_window(session)

    async def focus_page(self, session: PageSession) -> None:
        """Bring this page's window to the front, so Chromium keeps painting it.

        Nothing else tells it which window a person is looking at: there is
        no window manager here, and the viewer is a capture of a rectangle.
        Without this, the page being streamed can be backgrounded and the
        stream shows a picture that has stopped updating.
        """
        if session.cdp is None:
            return
        try:
            await session.cdp.send("Page.bringToFront")
        except Exception as e:
            logger.info("browser: could not focus {}: {}", session.id, e)

    async def set_metrics(self, session: PageSession,
                          w: int, h: int, s: float) -> None:
        """Set the page's size and its raster density together.

        Playwright's set_viewport_size pins deviceScaleFactor to the
        context default of 1, silently overriding the launch flag for that
        page: the page then rasters at 1x inside a window sized for 2x and
        sits in the top-left corner of a blank rectangle — the "content is
        smaller than the window" report, exactly. One CDP call sets both,
        so the two cannot disagree.

        This method was called from two places and defined in none of
        them; every page open raised AttributeError into a log nobody was
        reading, so the density was never applied at all.
        """
        if session.cdp is None:
            return
        # NO Emulation.setDeviceMetricsOverride. It pinned the page's
        # viewport to the size we last computed, so a window that had grown
        # showed the page at its old width with bare white beside it —
        # Chromium's own chrome spanned the new width, the page did not.
        # The window IS the size now (stage/park place it); let the page
        # follow its window the way it does in any browser.
        try:
            await session.cdp.send("Emulation.clearDeviceMetricsOverride")
        except Exception as e:
            logger.debug("browser: clearing metrics override failed: {}", e)

    async def _park_keeper(self) -> None:
        """Get the blank window that keeps Chromium alive off the display.

        Chromium opens it at its own default position — +20+20, full
        default size — which is exactly where tile 0 lives. An X11 grab
        takes whatever is topmost in the rectangle it was given, so a page
        tiled at the origin streamed the keeper's empty white window
        instead of itself: 30 fps of a picture that never changed, tab
        switches that appeared to do nothing, scrolling that appeared to do
        nothing. Nothing in the pipeline was wrong; it was pointed at the
        wrong window. Park it in the same off-screen row as any other
        window that has no tile, and the overlap cannot happen at all.
        """
        try:
            keeper = next(iter(self._context.pages), None)  # type: ignore[union-attr]
            if keeper is None:
                return
            if self._browser_cdp is None:
                self._browser_cdp = await self._context.new_cdp_session(keeper)
            info = await self._browser_cdp.send("Browser.getWindowForTarget")
            await self._browser_cdp.send("Browser.setWindowBounds", {
                "windowId": info["windowId"],
                "bounds": {  # DIP, not pixels — see RASTER_SCALE.
                    "left": 0, "top": PARK_Y // RASTER_SCALE,
                    "width": 160, "height": 120, "windowState": "normal",
                },
            })
        except Exception as e:
            # Worth saying out loud: if this fails, streams may show the
            # wrong window, and that is a confusing symptom to chase.
            logger.warning("browser: could not park the keeper window: {}", e)

    async def _attach(self, session: PageSession) -> None:
        """Wire navigation events for a page."""
        page = session.page
        cdp = await self._context.new_cdp_session(page)  # type: ignore[union-attr]
        session.cdp = cdp

        async def refresh_history() -> None:
            try:
                hist = await cdp.send("Page.getNavigationHistory")
                idx = hist.get("currentIndex", 0)
                session.can_back = idx > 0
                session.can_forward = idx < len(hist.get("entries", [])) - 1
            except Exception:
                pass

        def on_nav(frame: Any) -> None:
            if frame != page.main_frame:
                return
            session.loading = True
            # An icon belongs to a site, not to a tab.
            session.favicon = surviving_favicon(session.favicon, frame.url)
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

        # A popup is adopted as a real page so the agent can address it, and
        # placed ON the stage rather than parked: it is its own Chromium
        # window, and the user has to finish the login in it. This is what
        # makes "Continue with Google/GitHub" work — the opener, on the SAME
        # shared browser, sees the result.
        def on_popup(popup: Any) -> None:
            async def _adopt() -> None:
                try:
                    child = PageSession(uuid.uuid4().hex[:12], popup)
                    child.opener = session.id
                    self.pages[child.id] = child
                    await self._attach(child)
                    await self.place_window(child)
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
        # SERIALIZED. Two opens racing here each snapshot the page list,
        # each see the other's new page, and one of them claims it — the
        # loser times out and falls back to a tab while its window stays
        # where Chromium put it, unmanaged and on top of a tile. That is
        # how a page ended up streaming someone else's blank window.
        async with self._open_lock:
            return await self._create_window_page(url)

    async def _create_window_page(self, url: str):
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
            res = await cdp.send("Target.createTarget", {
                "url": url or "about:blank", "newWindow": True,
                "width": VIEW_W, "height": VIEW_H,
            })
            # How long to wait depends on whether Chromium is warm. Cold,
            # it has just spent half a minute starting and the first window
            # arrives late; five seconds expired, the page opened as a TAB,
            # and a tab cannot be captured on its own, so it took the slow
            # screencast path for the rest of its life. Warm, a window that
            # has not appeared in a few seconds is not coming, and waiting
            # twenty for it pushes the whole open past the caller's timeout
            # — which turned a working page into no page at all.
            budget = 2000 if self._cold_start else 500  # 10 ms ticks
            t0 = time.monotonic()
            for _ in range(budget):
                fresh = [p for p in self._context.pages if p not in before]
                if fresh:
                    waited = time.monotonic() - t0
                    self._cold_start = False
                    if waited > 1.0:
                        logger.info("browser: the new window took {:.1f}s to "
                                    "appear", waited)
                    return fresh[0]
                await asyncio.sleep(0.01)
            # Never showed up: close it rather than leave a window nobody
            # manages sitting on the display.
            target_id = (res or {}).get("targetId")
            if target_id:
                try:
                    await cdp.send("Target.closeTarget", {"targetId": target_id})
                except Exception:
                    pass
            logger.info("browser: windowed open did not surface a page; using a tab")
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
            await self.reshape(session, session.width, session.height,
                               session.dsf)

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

    async def _park_window(self, session: PageSession, window_id: int,
                           w: int, h: int) -> None:
        """Move a window off the display so it cannot cover a tile."""
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

    # Where a popup opens inside the staged window: far enough in to read as
    # a window of its own, close enough that it cannot fall outside the crop.
    POPUP_INSET = 48

    async def place_window(self, session: PageSession) -> None:
        """Put a non-staged window where it belongs: off the framebuffer.

        The stage is the whole picture — the framebuffer is fitted to the
        staged window at the origin — so any other window either overlaps it
        (and the shadow shows the wrong one) or sits outside the framebuffer
        and is simply not there. Park is the default.

        A popup of the staged page is the exception: it is its own Chromium
        window, and the login it carries has to be visible and clickable, so
        it goes ON the stage, inset like a popup anywhere else.
        """
        if not session.windowed or session.cdp is None:
            return
        w = max(2, int(session.width * session.dsf))
        h = max(2, int(session.height * session.dsf))
        outer_h = (h + WINDOW_CHROME_PX) & ~1
        try:
            info = await session.cdp.send("Browser.getWindowForTarget")
        except Exception as e:
            logger.info("browser: no window for {}: {}", session.id, e)
            return
        owner = self._windows.get(info["windowId"])
        if owner is not None and owner != session.id:
            # A TAB in another page's window. Moving it would drag that
            # page's window off the stage, so leave it exactly where it is.
            session.windowed = False
            session.rect = None
            return
        self._windows[info["windowId"]] = session.id
        staged = self._staged_page
        if staged and session.opener == staged and self._stage_fb:
            fb_w, fb_h = self._stage_fb
            left = min(self.POPUP_INSET, max(0, fb_w - w))
            top = min(self.POPUP_INSET, max(0, fb_h - outer_h))
            try:
                await session.cdp.send("Browser.setWindowBounds", {
                    "windowId": info["windowId"],
                    "bounds": {  # DIP, not pixels — see RASTER_SCALE.
                        "left": left // RASTER_SCALE, "top": top // RASTER_SCALE,
                        "width": min(w, fb_w) // RASTER_SCALE,
                        "height": min(outer_h, fb_h) // RASTER_SCALE,
                        "windowState": "normal",
                    },
                })
                session.rect = (left, top + WINDOW_CHROME_PX, w, h)
                logger.info("browser: popup {} placed on the stage", session.id)
                return
            except Exception as e:
                logger.info("browser: popup placement failed: {}", e)
        session.rect = None
        await self._park_window(session, info["windowId"], w, outer_h)

    # ── xpra shadow (engine loop only) ───────────────────────────────────

    # ── keyboard (engine loop only) ──────────────────────────────────────
    # The xpra shadow receives the client's key-actions, resolves them to the
    # right keycodes and calls XTest — and nothing arrives in Chromium. The
    # same XTest calls from a plain X client on the same display, with the
    # same focus, do arrive. Rather than keep guessing at someone else's
    # keyboard stack, the viewer sends us its key events and we inject them
    # here. Physical keys (event.code), so the display's own layout decides
    # what a key means, exactly like a real keyboard.
    def _x_display(self):
        if self._xdisplay is None:
            from Xlib import display as _xdisplay

            self._xdisplay = _xdisplay.Display(self._xvfb_display or ":97")
        return self._xdisplay

    async def send_keys(self, events: list[dict]) -> int:
        """Press/release keys on the display. Returns how many landed.

        Runs on the engine loop, like everything else that touches the
        display: an Xlib connection belongs to one thread, and two key
        batches in flight would otherwise share it from two.
        """
        if self._xvfb_display is None:
            return 0
        from Xlib import X, XK
        from Xlib.ext import xtest

        d = self._x_display()
        sent = 0
        for ev in events or []:
            name = KEYSYM_BY_CODE.get(str(ev.get("code") or ""))
            if not name:
                # Not a key we know by position: fall back to the character
                # the viewer says it produced. Latin-1 codepoints ARE their
                # own keysyms, which covers every printable ASCII key.
                ch = str(ev.get("key") or "")
                if len(ch) == 1 and 0x20 <= ord(ch) <= 0xFF:
                    keysym = ord(ch)
                else:
                    continue
            else:
                keysym = XK.string_to_keysym(name)
            if not keysym:
                continue
            keycode = d.keysym_to_keycode(keysym)
            if not keycode:
                continue
            try:
                xtest.fake_input(
                    d, X.KeyPress if ev.get("down") else X.KeyRelease, keycode)
                sent += 1
            except Exception as e:
                logger.info("browser: key inject failed: {}", e)
                self._xdisplay = None
                break
        try:
            d.sync()
        except Exception:
            self._xdisplay = None
        return sent

    def _xpra_alive(self) -> bool:
        return self._xpra_proc is not None and self._xpra_proc.poll() is None

    async def _ensure_xpra(self) -> bool:
        """Start (or confirm) the xpra shadow of the Xvfb display.

        No binary on the image (or no display) means no stage, and the
        Browser app says so instead of showing a picture.
        """
        import shutil

        if self._xvfb_display is None or shutil.which("xpra") is None:
            return False
        if self._xpra_alive():
            return True
        import secrets
        import subprocess

        if not self._xpra_password:
            self._xpra_password = secrets.token_urlsafe(18)
        try:
            self._xpra_proc = subprocess.Popen(
                ["xpra", "shadow", self._xvfb_display,
                 f"--bind-ws=0.0.0.0:{XPRA_PORT}",
                 "--html=on", "--daemon=no",
                 # TLS ends at the tunnel edge; the socket here is plain ws.
                 f"--ws-auth=password:value={self._xpra_password}",
                 # The framebuffer is this engine's to size (stage/unstage);
                 # a client resize must not fight it.
                 "--resize-display=no",
                 # Two viewports (a restored window, a second screen) must
                 # coexist; without this the server boots the earlier client
                 # whenever a new one connects.
                 "--sharing=yes",
                 "--notifications=no", "--pulseaudio=no", "--mdns=no",
                 "--webcam=no", "--printing=no"],
                env={**os.environ, "DISPLAY": self._xvfb_display},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Ready when the WebSocket port answers HTTP.
            import urllib.request
            for _ in range(60):
                if not self._xpra_alive():
                    break
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{XPRA_PORT}/", timeout=1)
                    logger.info("browser: xpra shadow up on :{}", XPRA_PORT)
                    return True
                except Exception:
                    await asyncio.sleep(0.5)
            logger.warning("browser: xpra shadow never answered")
        except Exception as e:
            logger.warning("browser: xpra launch failed: {}", e)
        return False

    def _set_fb(self, w: int, h: int) -> None:
        """Resize the X framebuffer (physical px). Shrinking is always legal
        (the Xvfb was born at full tile size, which is RANDR's maximum);
        xrandr may still grumble about the output's crtc on stderr while the
        framebuffer itself resizes — verify the result, not the exit code."""
        import subprocess

        subprocess.run(
            ["xrandr", "-d", self._xvfb_display or ":97",
             "--fb", f"{w}x{h}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        probe = subprocess.run(
            ["xrandr", "-d", self._xvfb_display or ":97"],
            capture_output=True, text=True, check=False,
        )
        if f"current {w} x {h}" not in (probe.stdout or ""):
            raise RuntimeError(f"framebuffer did not take {w}x{h}")

    async def _stage_place(self, session: PageSession) -> None:
        """Cut the framebuffer to this page's window and pin it at the origin.

        The shadow serves the whole display as ONE desktop window, so the
        display must BE the staged window: everything else (parked windows,
        other pages' tiles) falls outside the framebuffer and out of frame.
        """
        w = max(2, int(session.width * session.dsf))
        h = max(2, int(session.height * session.dsf))
        outer_h = (h + WINDOW_CHROME_PX) & ~1
        cur = self._stage_fb or (0, 0)
        fb_w = min(SCREEN_W, max(w, cur[0], self._stage_min_fb[0]))
        fb_h = min(SCREEN_H, max(outer_h, cur[1], self._stage_min_fb[1] & ~1))
        if (fb_w, fb_h) != cur:
            await asyncio.to_thread(self._set_fb, fb_w, fb_h)
            self._stage_fb = (fb_w, fb_h)
        info = await session.cdp.send("Browser.getWindowForTarget")
        await session.cdp.send("Browser.setWindowBounds", {
            "windowId": info["windowId"],
            "bounds": {
                "left": 0, "top": 0,
                "width": w // RASTER_SCALE,
                "height": outer_h // RASTER_SCALE,
                "windowState": "normal",
            },
        })

    async def stage_page(self, page_id: str, width: int, height: int,
                         fb_width: int = 0, fb_height: int = 0) -> dict:
        """Make this page THE xpra-visible one (one stage; latecomer wins).

        `fb_width`/`fb_height` is the floor for the framebuffer — the viewer's
        whole desktop, so the window can grow up to it without the
        framebuffer (and the shadow's window) ever changing. Returns what
        the viewer needs to connect and crop; raises if the transport is
        unavailable so the caller can fall back to JPEG paths.
        """
        session = self.get(page_id)
        if not await self._ensure_xpra():
            raise RuntimeError("xpra transport unavailable")
        self._stage_min_fb = (
            max(self._stage_min_fb[0], max(0, int(fb_width or 0))),
            max(self._stage_min_fb[1], max(0, int(fb_height or 0))),
        )
        previous = self._staged_page
        self._staged_page = page_id
        if previous and previous != page_id:
            old = self.pages.get(previous)
            if old is not None and old.windowed:
                # The outgoing page's window sits AT THE ORIGIN — exactly
                # where the incoming one goes. Park it off the framebuffer,
                # or whichever is higher in the X stacking order is what the
                # shadow shows, and that has been the wrong one.
                old.rect = None
                try:
                    info_old = await old.cdp.send("Browser.getWindowForTarget")
                    await self._park_window(
                        old, info_old["windowId"],
                        max(2, int(old.width * old.dsf)),
                        max(2, int(old.height * old.dsf)) + WINDOW_CHROME_PX)
                except Exception as e:
                    logger.info("browser: parking staged-out {} failed: {}",
                                previous, e)
        session.rect = None
        await self.reshape(session, width, height, float(RASTER_SCALE))
        # Undecorate the staged window. The html5 client adds its own frame
        # around a decorated window; window+frame then exceeds the viewer's
        # frame-sized viewport and the client CENTERS the overflow — the
        # viewer sees the middle of the page (an all-white nothing for
        # about:blank) instead of the window. No decorations, no overflow.
        await asyncio.to_thread(self._strip_decorations)
        if previous != page_id:
            # Only when the stage CHANGES page. Page.bringToFront activates a
            # tab, and the user's own tabs (opened inside the streamed
            # Chromium, which this side never hears about) live in the same
            # window — so doing it on every geometry re-stage yanked the view
            # back to our first tab on each window resize.
            await self.focus_page(session)
        import getpass

        fb = self._stage_fb or (
            max(2, int(width * RASTER_SCALE)),
            (max(2, int(height * RASTER_SCALE)) + WINDOW_CHROME_PX) & ~1)
        return {
            "password": self._xpra_password,
            "username": getpass.getuser(),
            "chrome_px": WINDOW_CHROME_PX,
            "fb_width": fb[0],
            "fb_height": fb[1],
            # The window inside that framebuffer, for the viewer's crop.
            "win_width": max(2, int(width * RASTER_SCALE)),
            "win_height": (max(2, int(height * RASTER_SCALE))
                           + WINDOW_CHROME_PX) & ~1,
        }

    def _strip_decorations(self) -> None:
        """Set _MOTIF_WM_HINTS decorations=0 on the window at the origin.

        CDP window ids are not X ids, so the staged window is found by what
        makes it unique: a Chrome window sitting at +0+0. Best-effort — a
        miss only means the client draws its frame and the picture rides a
        few dozen pixels low.
        """
        import re
        import subprocess

        display = self._xvfb_display or ":97"
        try:
            out = subprocess.run(
                ["xwininfo", "-root", "-children", "-display", display],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout or ""
            for line in out.splitlines():
                if "Chrome" not in line:
                    continue
                m = re.search(r"^\s*(0x[0-9a-f]+).*\+0\+0\s*$", line)
                if not m:
                    continue
                subprocess.run(
                    ["xprop", "-display", display, "-id", m.group(1),
                     "-f", "_MOTIF_WM_HINTS", "32c",
                     "-set", "_MOTIF_WM_HINTS", "0x2, 0x0, 0x0, 0x0, 0x0"],
                    capture_output=True, timeout=10, check=False,
                )
                logger.info("browser: stripped decorations from {}", m.group(1))
        except Exception as e:
            logger.info("browser: strip decorations failed: {}", e)

    async def unstage_page(self, page_id: str) -> None:
        """This page stops owning the display; restore the tiled world."""
        if self._staged_page != page_id:
            return
        self._staged_page = None
        self._stage_fb = None
        self._stage_min_fb = (0, 0)
        try:
            await asyncio.to_thread(self._set_fb, SCREEN_W, SCREEN_H)
        except Exception as e:
            logger.warning("browser: framebuffer restore failed: {}", e)
        session = self.pages.get(page_id)
        if session is not None and session.windowed:
            try:
                await self.place_window(session)   # back off the framebuffer
            except Exception as e:
                logger.info("browser: parking after unstage failed: {}", e)

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
        if self._staged_page == page_id:
            # The staged page owned the (shrunken) display; give the tiled
            # world its framebuffer back for whoever streams next.
            self._staged_page = None
            self._stage_fb = None
            self._stage_min_fb = (0, 0)
            try:
                await asyncio.to_thread(self._set_fb, SCREEN_W, SCREEN_H)
            except Exception as e:
                logger.info("browser: framebuffer restore failed: {}", e)
        for wid, owner in list(self._windows.items()):
            if owner == page_id:
                self._windows.pop(wid, None)
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
