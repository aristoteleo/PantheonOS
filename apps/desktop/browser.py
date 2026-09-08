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
from dataclasses import dataclass
from typing import Any

from pantheon.utils.log import logger

# Schemes that carry their payload without "//": leave them untouched.
_SCHEME_NO_SLASH = re.compile(r"^(data|about|blob|view-source|file):", re.I)

# Public extension identity, not an authentication key. Chromium hashes this
# DER public key to give the bundled, local-only tab observer a stable origin.
_NATIVE_TABS_KEY = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCtwLPyoPHrg0TvFeWQNZovXNnH9jwmlxUg"
    "414N7sQbxjJQPAIwwA0QswQWV/0tLBlua7wqMJfwWjPebzut2aQWGCRwJ5rCdbBHnTw7"
    "JE47JlWpOi5I7VjhxpxUFcvHkaBnktvJajnd8ssMXRXv+TF9Uq/pmfs5e6G0KIAQJIgvSwIDAQAB"
)
_NATIVE_TABS_SCRIPT = """
// No content scripts, host permissions, external messages, or network calls.
// These events wake the worker after Chromium suspends it.
const wake = () => {};
chrome.runtime.onStartup.addListener(wake);
chrome.tabs.onActivated.addListener(wake);
chrome.tabs.onCreated.addListener(wake);
chrome.tabs.onRemoved.addListener(wake);
chrome.tabs.onAttached.addListener(wake);
chrome.tabs.onDetached.addListener(wake);
globalThis.pantheonNativeTabs = async () => {
    const tabs = await chrome.tabs.query({});
    // getTargets only reads identities. Never attach an extension debugger:
    // Playwright already owns the debugging connection.
    const targets = await chrome.debugger.getTargets();
    return tabs.map(tab => ({
        windowId: tab.windowId, tabId: tab.id, active: tab.active,
        targetIds: targets.filter(t => t.type === 'page' && t.tabId === tab.id).map(t => t.id)
    }));
};
"""


class BrowserProfileInUse(RuntimeError):
    """Another owner, or an owner we cannot safely exclude, holds a profile."""


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
# How the display is served.
#
#   "shadow"   — xpra shadows an Xvfb we start; the whole display is ONE
#                desktop window, so windows are laid out inside a framebuffer
#                and each viewer crops its own out of it.
#   "seamless" — xpra owns the display AND manages it (its own window
#                manager), so every window is its own object in the protocol:
#                the viewer adopts one per Browser window, dialogs and popups
#                are placed by a real WM, and a resize is a window resize
#                rather than a framebuffer rebuild.
#
# Seamless is where this is going; shadow is the path in production until it
# has soaked. BROWSER_XPRA_MODE picks.
#: A workspace can pick its own mode, and that beats the environment: one
#: sandbox can try seamless without a hub-wide switch turning it on for
#: everybody — which is exactly how a colleague's browser got broken once.
XPRA_MODE_FILE = ".pantheon/xpra-mode"


def xpra_mode() -> str:
    """'shadow' or 'seamless', from the workspace file or the environment."""
    for root in (os.environ.get("PANTHEON_WORKSPACE") or "", os.getcwd(),
                 str(Path.home())):
        if not root:
            continue
        try:
            value = (Path(root) / XPRA_MODE_FILE).read_text().strip().lower()
        except Exception:
            continue
        if value in ("shadow", "seamless"):
            return value
    return (os.environ.get("BROWSER_XPRA_MODE") or "shadow").strip().lower()
#: WM_CLASS we stamp on a page's window so the viewer can tell which protocol
#: window is which page (xpra forwards WM_CLASS as `class-instance`, and its
#: metadata carries no X window id).
PAGE_CLASS_PREFIX = "pantheon-page-"
#: This blank page only keeps headful Chromium alive. It is not a desktop app.
INTERNAL_KEEPER_CLASS = "pantheon-internal-keeper"

# Where a page with no URL of its own starts, and what the omnibox searches.
# A sandbox browser opening on about:blank is a white void with nothing to do
# in it; this is the browser's home. Both are overridable per deployment, and
# the search URL takes Chromium's {searchTerms} placeholder.
# How much page text browser_read hands back in one call.
READ_LIMIT = 8000

HOME_URL = os.environ.get("BROWSER_HOME_URL") or "https://duckduckgo.com/"
SEARCH_URL = (os.environ.get("BROWSER_SEARCH_URL")
              or "https://duckduckgo.com/?q={searchTerms}")
SEARCH_SUGGEST_URL = (os.environ.get("BROWSER_SEARCH_SUGGEST_URL")
                      or "https://ac.duckduckgo.com/ac/?q={searchTerms}&type=list")

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
        self.navigation_task: asyncio.Task | None = None
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


@dataclass
class BrowserWindowBinding:
    """Stable native window identity; tab identities never replace this token."""

    id: str
    window_id: int
    window_class: str
    active_page_id: str | None = None


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
        self._page_adoption_lock = asyncio.Lock()
        self._window_bindings: dict[str, BrowserWindowBinding] = {}
        self._pending_popups: dict[Any, PageSession] = {}
        self._popup_announced: set[str] = set()
        self._popup_announcing: dict[str, asyncio.Task] = {}
        self._native_extension_dir = None
        self._native_tabs_worker_url: str | None = None
        # Native applications share this display without launching Chromium.
        self._display_lock = asyncio.Lock()
        self._native_apps = None
        self.pages: dict[str, PageSession] = {}
        self._xpra_proc = None
        self._xpra_password: str | None = None
        # Every page holding a rectangle on the display, in the order they
        # took one: page_id -> (x, y, w, outer_h) in physical pixels. The
        # shadow streams one framebuffer; these are the windows inside it,
        # one per Browser window, each cropped to by its own viewer.
        self._stages: dict[str, tuple[int, int, int, int]] = {}
        # When each stage was last claimed or focused, so the display can be
        # given back to the windows someone is actually using.
        self._stage_touch: dict[str, float] = {}
        # The framebuffer, and the floor a viewer asked for (its whole
        # desktop). Every change makes the shadow tear its desktop window
        # down and build another, which reads as a stall, so it only grows.
        self._stage_fb: tuple[int, int] | None = None
        self._stage_min_fb: tuple[int, int] = (0, 0)
        # Xlib's default locks are no-ops. Window workers and the engine's
        # input loop must never share a Display's request/reply socket.
        self._xdisplay_local = threading.local()
        self._native_input_lock = threading.RLock()
        # Kept until this process exits, including Chromium relaunches. Never
        # unlink the flock file: a second inode would create a second owner.
        self._profile_lock_fd: int | None = None
        self._profile_lock_path: Path | None = None
        self._dialog_task = None  # keeps dialogs inside their own window
        self._named: set[str] = set()  # pages whose X window carries their id
        #: Seamless: set by the toolset; called with a popup's PageSession so
        #: the desktop opens a Browser window for it.
        self.on_popup_page = None

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
    def _profile_process_owners(profile: Path) -> list[int]:
        """Detect a pre-guard Chromium with this exact user-data-dir."""
        import psutil

        expected = profile.resolve()
        owners = []
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = process.info
                command = info.get("cmdline")
                name = str(info.get("name") or "").lower()
                executable = Path(command[0]).name.lower() if command else ""
                if not any(value in name or value in executable for value in ("chrome", "chromium")):
                    continue
                if not command:
                    # Zombies have no executable and cannot own a live
                    # profile. An unreadable live Chrome is not stale proof.
                    if process.status() == psutil.STATUS_ZOMBIE:
                        continue
                    raise BrowserProfileInUse("Cannot inspect a running Chromium to establish profile ownership")
                values = []
                for index, arg in enumerate(command):
                    if arg.startswith("--user-data-dir="):
                        values.append(arg.split("=", 1)[1])
                    elif arg == "--user-data-dir" and index + 1 < len(command):
                        values.append(command[index + 1])
                for value in values:
                    path = Path(value)
                    if not path.is_absolute():
                        path = Path(process.cwd()) / path
                    if path.resolve() == expected:
                        owners.append(int(info["pid"]))
                        break
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied as error:
                raise BrowserProfileInUse("Cannot inspect a Chromium profile owner") from error
        return owners

    def _acquire_profile_lock(self, profile: Path) -> None:
        """Claim a profile before policies, Xpra, cache cleanup or Chromium."""
        import fcntl

        profile = profile.resolve()
        if self._profile_lock_fd is not None:
            if self._profile_lock_path != profile:
                raise BrowserProfileInUse("This browser engine already owns a different profile")
            return
        profile.mkdir(parents=True, exist_ok=True)
        fd = os.open(profile / ".pantheon-owner.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(fd)
            raise BrowserProfileInUse("The browser profile is already owned by another Desktop process") from error
        except BaseException:
            os.close(fd)
            raise
        self._profile_lock_fd = fd
        self._profile_lock_path = profile

    @classmethod
    def _clear_stale_locks(cls, profile: Path) -> None:
        """Remove only singleton artifacts whose old owner is demonstrably gone.

        A mounted profile may be shared during a restart or by an old Desktop
        process which predates our flock. Neither "my context is empty" nor a
        PID absent from *this* container proves a foreign hostname is dead.
        """
        import socket
        import psutil

        owners = cls._profile_process_owners(profile)
        if owners:
            raise BrowserProfileInUse("The browser profile is in use by a running Chromium")
        lock = profile / "SingletonLock"
        if lock.exists() or lock.is_symlink():
            if not lock.is_symlink():
                raise BrowserProfileInUse("Cannot establish whether the existing browser SingletonLock is stale")
            target = os.readlink(lock)
            try:
                hostname, raw_pid = target.rsplit("-", 1)
                pid = int(raw_pid)
                if pid <= 0:
                    raise ValueError
            except (ValueError, TypeError) as error:
                raise BrowserProfileInUse("Cannot interpret the existing browser SingletonLock owner") from error
            exclusive_recovery = os.environ.get("PANTHEON_BROWSER_PROFILE_RECOVERY") == "exclusive-modal-sandbox-v1"
            if hostname != socket.gethostname() and not exclusive_recovery:
                raise BrowserProfileInUse(
                    "The browser SingletonLock belongs to another host; its owner must be confirmed stopped before cleanup")
            if hostname == socket.gethostname():
                try:
                    process = psutil.Process(pid)
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        # A reused PID with a readable nonmatching command is not
                        # the old Chromium. Verify its identity instead of simply
                        # treating every extant PID as a browser owner.
                        command = process.cmdline()
                        name = process.name().lower()
                        if any("chrome" in value.lower() or "chromium" in value.lower()
                               for value in [name, Path(command[0]).name if command else ""]):
                            raise BrowserProfileInUse("The browser SingletonLock still names a running Chromium")
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                except psutil.AccessDenied as error:
                    raise BrowserProfileInUse("Cannot confirm the old browser process has exited") from error
            # A foreign-host lock may be reclaimed only when the sandbox
            # launcher established exclusive volume ownership. This attested
            # mode never bypasses local process or singleton-socket checks.

        # A live local singleton socket is additional ownership evidence even
        # if SingletonLock was absent or damaged. Do not delete it.
        singleton_socket = profile / "SingletonSocket"
        if singleton_socket.exists() or singleton_socket.is_symlink():
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.settimeout(0.1)
                # Chromium uses a short /tmp socket target; the persistent
                # profile path itself can exceed AF_UNIX's pathname limit.
                client.connect(str(singleton_socket.resolve()))
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            except OSError as error:
                raise BrowserProfileInUse("Cannot confirm the browser singleton socket is stale") from error
            else:
                raise BrowserProfileInUse("The browser singleton socket still has a live owner")
            finally:
                client.close()

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
        async with self._display_lock:
            return await self._ensure_xvfb_once()

    async def ensure_native_stage(self) -> dict:
        """Connection material for native apps on the shared seamless display."""
        if xpra_mode() != "seamless":
            raise RuntimeError("Native apps require the seamless Xpra transport")
        display = await self._ensure_xvfb()
        if not display or not self._xpra_alive() or not self._xpra_password:
            raise RuntimeError("Native display is unavailable")
        import getpass

        return {"mode": "seamless", "username": getpass.getuser(),
                "password": self._xpra_password}

    def native_apps(self):
        """Native process ownership belongs to the display's daemon loop."""
        with self._start_lock:
            if self._native_apps is None:
                from .native_apps import NativeAppManager

                self._native_apps = NativeAppManager(self)
            return self._native_apps

    async def _ensure_xvfb_once(self) -> str | None:
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
        if xpra_mode() == "seamless":
            # xpra brings the display AND the window manager; Chromium is
            # launched onto it afterwards, exactly as before.
            if await asyncio.to_thread(self._start_seamless, display):
                self._xvfb_display = display
                return display
            logger.warning("browser: seamless session failed; using Xvfb")
        try:
            # Room for several 2x windows side by side, since each streamed
            # page needs a DISJOINT tile (overlapping windows capture each
            # other). The size lives with the tiling code that depends on
            # it — a literal here once drifted from it. 24-bit, no TCP
            # listener.
            self._xvfb_proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", f"{SCREEN_W}x{SCREEN_H}x24",
                 # No software cursor. Xvfb has no hardware one, so it PAINTS
                 # the pointer into the framebuffer — and a framebuffer is
                 # exactly what the shadow streams, so the viewer saw a second
                 # arrow sitting wherever the display's pointer had last been,
                 # beside their own. The client draws the real cursor from the
                 # server's cursor packets, in the right place, in the right
                 # shape.
                 "-nocursor",
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
        self._window_bindings.clear()
        self._pending_popups.clear()
        self._popup_announced.clear()
        for task in self._popup_announcing.values():
            task.cancel()
        self._popup_announcing.clear()
        self._named.clear()
        self._windows.clear()

    async def _ensure_browser(self) -> None:
        # One launch at a time. The prewarm at boot and a user's first page
        # open now race by design — the whole point is that one of them has
        # already paid for the launch — and without this both would start a
        # Chromium against the same profile, which is exactly the situation
        # the ProcessSingleton lock exists to refuse.
        # Even an existing context must pass the lock: launch publishes it
        # before init scripts and the keeper's window identity are ready.
        async with self._launch_lock:
            if self._context is not None:
                if self._xvfb_display is None or self._context.pages:
                    return
                # Headful Chromium can remain connected after its last native
                # window closes, but then refuse Target.createTarget. There is
                # no context-close event to invalidate it. Recycle only this
                # empty context; never close a context with a surviving page.
                context = self._context
                await context.close()
                if self._context is context:
                    self._context_died()
            await self._launch_browser()

    async def _launch_browser(self) -> None:
        if self._launch_error:
            raise RuntimeError(self._launch_error)
        try:
            await self._launch_browser_once()
            return
        except BrowserProfileInUse:
            # A competing owner is not a broken installation and may exit;
            # do not make this a sticky error or retry by deleting its locks.
            raise
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
        except BrowserProfileInUse:
            raise
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

    @staticmethod
    def _write_policies() -> None:
        """Give Chromium its home page and search engine, before it starts.

        A managed policy is the only way to set the omnibox's search engine
        from outside the profile — and it survives a profile that was created
        before we cared. Written to every directory this build might read;
        the extra files are inert where they are not.
        """
        import json as _json

        host = ""
        try:
            from urllib.parse import urlparse

            host = (urlparse(SEARCH_URL).hostname or "").removeprefix("www.")
        except Exception:
            pass
        policy = {
            "DefaultSearchProviderEnabled": True,
            "DefaultSearchProviderName": host or "Search",
            "DefaultSearchProviderKeyword": (host.split(".")[0] if host else "s"),
            "DefaultSearchProviderSearchURL": SEARCH_URL,
            "DefaultSearchProviderSuggestURL": SEARCH_SUGGEST_URL,
            "HomepageLocation": HOME_URL,
            "HomepageIsNewTabPage": False,
            "NewTabPageLocation": HOME_URL,
            "ShowHomeButton": True,
            # Nothing here should nag a user who cannot act on it: this
            # browser is not going to become anyone's default, and it has no
            # account to sync to.
            "DefaultBrowserSettingEnabled": False,
            "SyncDisabled": True,
            "MetricsReportingEnabled": False,
        }
        # The path is compiled into the binary and is branding-specific:
        # Chrome for Testing (what Playwright ships) reads
        # /etc/opt/chrome_for_testing — underscores. `strings <chrome> |
        # grep policies` says which, if this ever moves again.
        for base in ("/etc/opt/chrome_for_testing", "/etc/opt/chrome",
                     "/etc/chromium", "/etc/chrome"):
            try:
                d = Path(base) / "policies" / "managed"
                d.mkdir(parents=True, exist_ok=True)
                (d / "pantheon.json").write_text(_json.dumps(policy, indent=2))
            except Exception as e:
                logger.debug("browser: policy write to {} failed: {}", base, e)

    async def _launch_browser_once(self) -> None:
        from playwright.async_api import async_playwright

        t_launch = time.monotonic()
        profile = Path.home() / ".pantheon" / "browser-profile"
        await asyncio.to_thread(self._acquire_profile_lock, profile)
        await asyncio.to_thread(self._clear_stale_locks, profile)
        await asyncio.to_thread(self._write_policies)
        display = await self._ensure_xvfb()
        t_display = time.monotonic()
        if self._pw is None:
            self._pw = await async_playwright().start()
        # Off the loop: this deletes thousands of files on a NETWORK
        # volume, and a loop that stops answering for long enough is a
        # pod the hub's health check declares dead and destroys — which
        # costs the user their sandbox and minutes of waiting for
        # another. Nothing here is urgent enough to be worth that.
        await asyncio.to_thread(self._evict_volume_caches, profile)
        cache_dir = Path("/tmp/pantheon-browser-cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        t_profile = time.monotonic()
        extension = self._prepare_native_tabs_extension() if display else None
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
            # Playwright's default disables extensions. Our local observer
            # reads Chromium's selected tab without relying on emulated DOM
            # visibility; retain the remaining Playwright defaults.
            ignore_default_args=["--disable-extensions"] if extension else None,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            # Seamless: the page follows its window, which the viewer sizes
            # through the window manager. A fixed viewport pinned the page
            # at 1280x800 while the window shrank around it, so the picture
            # was a crop of a page that never changed size.
            **({"no_viewport": True} if xpra_mode() == "seamless"
               else {"viewport": {"width": VIEW_W, "height": VIEW_H}}),
            args=[
                *([f"--load-extension={extension}"] if extension else []),
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
        t_context = time.monotonic()
        ctx = self._context
        ctx.on("close",
               lambda: self._context_died() if self._context is ctx else None)
        if extension:
            await self._native_tabs_worker()
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
        logger.info("browser: startup phases display {:.0f} ms, profile {:.0f} ms, "
                    "chromium {:.0f} ms, setup {:.0f} ms",
                    (t_display - t_launch) * 1000,
                    (t_profile - t_display) * 1000,
                    (t_context - t_profile) * 1000,
                    (time.monotonic() - t_context) * 1000)

    def _prepare_native_tabs_extension(self) -> Path:
        import base64
        import hashlib
        import json
        import tempfile

        if self._native_extension_dir is None:
            self._native_extension_dir = tempfile.TemporaryDirectory(prefix="pantheon-native-tabs-")
        directory = Path(self._native_extension_dir.name)
        manifest = {
            "manifest_version": 3, "name": "Pantheon Native Tab Identity", "version": "1.0",
            "key": _NATIVE_TABS_KEY,
            # tabs.query's id/active/windowId fields need no tabs permission.
            "permissions": ["debugger"],
            "background": {"service_worker": "native-tabs.js"},
        }
        for filename, content in (("manifest.json", json.dumps(manifest)), ("native-tabs.js", _NATIVE_TABS_SCRIPT)):
            path = directory / filename
            path.write_text(content)
            path.chmod(0o600)
        digest = hashlib.sha256(base64.b64decode(_NATIVE_TABS_KEY)).hexdigest()[:32]
        extension_id = "".join(chr(ord("a") + int(char, 16)) for char in digest)
        self._native_tabs_worker_url = f"chrome-extension://{extension_id}/native-tabs.js"
        return directory

    async def _native_tabs_worker(self, *, wait: bool = True):
        if self._context is None or not self._native_tabs_worker_url:
            raise RuntimeError("The native browser tab observer is unavailable")
        expected = self._native_tabs_worker_url
        workers = [worker for worker in self._context.service_workers if worker.url == expected]
        if len(workers) > 1:
            raise RuntimeError("The native browser tab observer is ambiguous")
        if workers:
            return workers[0]
        if not wait:
            raise RuntimeError("The native browser tab observer is unavailable")
        try:
            return await self._context.wait_for_event(
                "serviceworker", predicate=lambda worker: worker.url == expected, timeout=5000)
        except Exception as error:
            raise RuntimeError("The native browser tab observer is unavailable") from error

    async def _native_active_target(self, window_id: int) -> str | None:
        """Read the browser's selected tab, even when its omnibox has focus.

        Playwright enables focus emulation on its CDP connection. Chromium
        holds that connection's capturer handle, making every page report
        visibility='visible'; another CDP session cannot undo it. Native tabs
        identify selection independently of page JS, focus, URL, and title.
        """
        try:
            tabs = await asyncio.wait_for(self._native_tab_snapshot(), timeout=3)
        except Exception as error:
            raise RuntimeError("Could not read the native browser selected tab") from error
        if not isinstance(tabs, list):
            raise RuntimeError("The native browser tab observer returned invalid state")
        selected = [tab for tab in tabs if isinstance(tab, dict) and tab.get("windowId") == window_id and tab.get("active") is True]
        if not selected:
            return None
        targets = selected[0].get("targetIds")
        if len(selected) != 1 or not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], str):
            raise RuntimeError("The requested browser window has no unique selected tab identity")
        return targets[0]

    async def _native_tab_snapshot(self) -> list[dict]:
        """Wake only our observer, then read it through a fresh CDP session.

        After MV3 suspension Playwright can retain a Worker whose execution
        context never resolves. A short-lived CDP attachment avoids that stale
        handle, and lets the worker sleep normally between requests.
        """
        import json

        context = self._context
        expected = self._native_tabs_worker_url
        if context is None or not expected or context.browser is None:
            raise RuntimeError("The native browser tab observer is unavailable")
        page = next((page for page in context.pages if not page.is_closed()), None)
        if page is None:
            raise RuntimeError("The native browser tab observer is unavailable")
        # ServiceWorker is a page CDP domain, not a browser CDP domain. This
        # command starts an exact extension scope without navigating or
        # focusing the page that carries the command.
        wake = await context.new_cdp_session(page)
        try:
            await wake.send("ServiceWorker.enable")
            await wake.send("ServiceWorker.startWorker", {"scopeURL": expected.rsplit("/", 1)[0] + "/"})
        finally:
            await wake.detach()

        cdp = await context.browser.new_browser_cdp_session()
        session_id = None
        try:
            targets = (await cdp.send("Target.getTargets"))["targetInfos"]
            matches = [target for target in targets if target.get("type") == "service_worker" and target.get("url") == expected]
            if len(matches) != 1:
                raise RuntimeError("The native browser tab observer is unavailable or ambiguous")
            session_id = (await cdp.send("Target.attachToTarget", {
                "targetId": matches[0]["targetId"], "flatten": False,
            }))["sessionId"]
            reply = asyncio.get_running_loop().create_future()

            def on_message(event):
                if event.get("sessionId") != session_id or reply.done():
                    return
                response = json.loads(event["message"])
                if response.get("id") == 1:
                    reply.set_result(response)

            cdp.on("Target.receivedMessageFromTarget", on_message)
            await cdp.send("Target.sendMessageToTarget", {"sessionId": session_id, "message": json.dumps({
                "id": 1, "method": "Runtime.evaluate", "params": {
                    "expression": "globalThis.pantheonNativeTabs()", "awaitPromise": True, "returnByValue": True,
                },
            })})
            response = await reply
            result = response.get("result", {})
            if response.get("error") or result.get("exceptionDetails"):
                raise RuntimeError("The native browser tab observer could not read identities")
            return result.get("result", {}).get("value")
        finally:
            try:
                if session_id is not None:
                    await cdp.send("Target.detachFromTarget", {"sessionId": session_id})
            finally:
                await cdp.detach()

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
            if xpra_mode() == "seamless":
                # The window manager owns geometry there, and the viewer
                # resizes the window through the protocol; touching CDP
                # window bounds under a WM hangs (see place_window).
                return
            await self.set_metrics(session, w, h, s)
            if session.id in self._stages:
                # On the display: its rectangle follows the new size, and
                # the others shuffle down to stay disjoint.
                await self._stage_place(session)
            elif session.windowed:
                # Not on the display: park it, so it cannot cover one that is.
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
        """Identify the internal blank page, and park it for shadow capture.

        Seamless viewers exclude its explicit WM_CLASS. Moving it offscreen
        with CDP there can wait forever for the window manager (see
        place_window), so only shadow mode needs physical parking. A restored
        real page is never treated as an internal keeper.
        """
        cdp = None
        try:
            keeper = next((p for p in self._context.pages  # type: ignore[union-attr]
                           if p.url == "about:blank"), None)
            if keeper is None:
                return
            if xpra_mode() == "seamless":
                await self._name_native_window(
                    keeper, "pantheon-window-keeper", INTERNAL_KEEPER_CLASS,
                )
                return
            # This session targets only the keeper. Never cache it as the
            # browser-wide command channel: closing that page invalidates it.
            cdp = await self._context.new_cdp_session(keeper)
            info = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {
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
        finally:
            if cdp is not None:
                try:
                    await cdp.detach()
                except Exception:
                    pass

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
        page.on("close", lambda: asyncio.ensure_future(self.close_page(session.id)))

        # A popup is adopted as a real page so the agent can address it, and
        # placed ON the stage rather than parked: it is its own Chromium
        # window, and the user has to finish the login in it. This is what
        # makes "Continue with Google/GitHub" work — the opener, on the SAME
        # shared browser, sees the result.
        def on_popup(popup: Any) -> None:
            async def _adopt() -> None:
                for attempt in range(3):
                    try:
                        await self._adopt_popup(session, popup)
                        return
                    except Exception as e:
                        logger.warning("browser: popup adopt failed (attempt {}): {}", attempt + 1, e)
                        if popup.is_closed():
                            return
                        await asyncio.sleep(0.2 * (attempt + 1))

            asyncio.ensure_future(_adopt())

        page.on("popup", on_popup)

    def window_binding(self, token: str) -> BrowserWindowBinding:
        binding = self._window_bindings.get(token)
        if binding is None:
            raise KeyError(f"No such native browser window: {token}")
        return binding

    async def _bind_window(self, session: PageSession, window_id: int | None = None) -> BrowserWindowBinding:
        existing = self._window_bindings.get(session.id)
        if existing is not None:
            return existing
        if window_id is None:
            if session.cdp is None:
                raise RuntimeError("A native window needs a live CDP target before binding")
            info = await asyncio.wait_for(session.cdp.send("Browser.getWindowForTarget"), timeout=2)
            window_id = info.get("windowId")
        if not isinstance(window_id, int) or isinstance(window_id, bool):
            raise RuntimeError("The browser page has no native window identity")
        owner = next((b for b in self._window_bindings.values() if b.window_id == window_id), None)
        if owner is not None:
            return owner
        binding = BrowserWindowBinding(session.id, window_id, PAGE_CLASS_PREFIX + session.id, session.id)
        self._window_bindings[binding.id] = binding
        return binding

    async def _window_members(self, binding: BrowserWindowBinding) -> list[tuple[Any, bool]]:
        if self._context is None:
            raise RuntimeError("The browser context is unavailable")
        selected_target = await self._native_active_target(binding.window_id)

        async def inspect(page):
            if page.is_closed():
                return None
            known = next((s for s in self.pages.values() if s.page is page), None)
            cdp = known.cdp if known is not None else None
            temporary = cdp is None
            try:
                if temporary:
                    cdp = await self._context.new_cdp_session(page)
                info = await cdp.send("Browser.getWindowForTarget")
                if info.get("windowId") != binding.window_id:
                    return None
                target = await cdp.send("Target.getTargetInfo")
                return page, target.get("targetInfo", {}).get("targetId") == selected_target
            except Exception:
                if not page.is_closed():
                    raise
                return None
            finally:
                if temporary and cdp is not None:
                    try:
                        await cdp.detach()
                    except Exception:
                        pass

        try:
            inspected = await asyncio.wait_for(asyncio.gather(
                *(inspect(page) for page in list(self._context.pages)), return_exceptions=True), timeout=5)
            failure = next((value for value in inspected if isinstance(value, BaseException)), None)
            if failure is not None:
                raise RuntimeError("A browser page could not be inspected") from failure
        except Exception as error:
            raise RuntimeError("Could not determine the tabs in the requested browser window") from error
        members = [member for member in inspected if member is not None]
        if selected_target and not members:
            raise RuntimeError("The native browser tab is not yet available to the controller")
        return members

    def _drop_window_binding(self, token: str) -> None:
        self._window_bindings.pop(token, None)
        self._named.discard(token)
        self._popup_announced.discard(token)
        self._stages.pop(token, None)
        self._stage_touch.pop(token, None)
        for window_id, owner in list(self._windows.items()):
            if owner == token:
                self._windows.pop(window_id, None)

    async def window_page(self, token: str, *, require_visible: bool = True) -> PageSession:
        """The current tab of the originally bound physical native window.

        Metadata/focus may use the last same-window tab during a transition.
        Agent actions require one natively selected tab and never select a
        different window, even if the original anchor tab was moved there.
        """
        async with self._page_adoption_lock:
            binding = self.window_binding(token)
            members = await self._window_members(binding)
            if not members:
                self._drop_window_binding(token)
                raise RuntimeError("The requested browser window has no remaining tabs")
            visible = [page for page, shown in members if shown]
            if len(visible) > 1 or (require_visible and not visible):
                raise RuntimeError("The requested browser window has no uniquely selected tab")
            if visible:
                page = visible[0]
            else:
                previous = self.pages.get(binding.active_page_id or "")
                page = next((page for page, _ in members if previous is not None and page is previous.page), members[0][0])
            existing = next((s for s in self.pages.values() if s.page is page), None)
            if existing is None:
                existing = self._pending_popups.pop(page, None) or PageSession(uuid.uuid4().hex[:12], page)
                existing.opener = binding.id
                if existing.cdp is None:
                    await asyncio.wait_for(self._attach(existing), timeout=5)
                self.pages[existing.id] = existing
            binding.active_page_id = existing.id
            return existing

    async def current_window_page(self, anchor: PageSession | BrowserWindowBinding) -> PageSession:
        return await self.window_page(anchor.id)

    async def _adopt_popup(self, opener: PageSession, page: Any) -> PageSession:
        """Retain incomplete adoption for retry; publish only after classification."""
        async with self._page_adoption_lock:
            if page.is_closed():
                self._pending_popups.pop(page, None)
                raise RuntimeError("The browser popup closed before it could be adopted")
            child = next((s for s in self.pages.values() if s.page is page), None)
            if child is None:
                child = self._pending_popups.get(page)
            if child is None:
                child = PageSession(uuid.uuid4().hex[:12], page)
                child.opener = opener.id
                self._pending_popups[page] = child
            if child.cdp is None:
                await asyncio.wait_for(self._attach(child), timeout=5)
            if self._xvfb_display is None:
                self.pages[child.id] = child
                self._pending_popups.pop(page, None)
                return child
            info = await asyncio.wait_for(child.cdp.send("Browser.getWindowForTarget"), timeout=2)
            window_id = info.get("windowId")
            if not isinstance(window_id, int) or isinstance(window_id, bool):
                raise RuntimeError("Cannot determine the browser popup's native window")
            binding = next((b for b in self._window_bindings.values() if b.window_id == window_id), None)
            if binding is None:
                child.windowed = True
                binding = await self._bind_window(child, window_id)
            else:
                child.windowed = binding.id == child.id
            self.pages[child.id] = child
            self._pending_popups.pop(page, None)
            if not child.windowed:
                return child
            await self.place_window(child)
            if xpra_mode() == "seamless":
                if not await self._name_window(child):
                    raise RuntimeError("The browser popup's native window could not be named")
        # A desktop callback may take a network round-trip. Never hold the
        # tab-discovery lock while waiting for a frontend to acknowledge it.
        if xpra_mode() == "seamless" and child.id not in self._popup_announced and self.on_popup_page is not None:
            task = self._popup_announcing.get(child.id)
            if task is None:
                async def announce():
                    try:
                        await self.on_popup_page(child)
                        self._popup_announced.add(child.id)
                    finally:
                        self._popup_announcing.pop(child.id, None)
                task = asyncio.create_task(announce())
                self._popup_announcing[child.id] = task
            await asyncio.shield(task)
        return child

    async def close_window(self, token: str) -> None:
        """Close tabs still in this physical window, retaining tabs dragged out."""
        binding = self._window_bindings.get(token)
        if binding is None:
            return
        members = await self._window_members(binding)
        for page, _ in members:
            if page.is_closed():
                continue
            # Membership may change while a beforeunload handler is open.
            cdp = await self._context.new_cdp_session(page)
            try:
                info = await asyncio.wait_for(cdp.send("Browser.getWindowForTarget"), timeout=2)
                if info.get("windowId") == binding.window_id:
                    await page.close()
            finally:
                try:
                    await cdp.detach()
                except Exception:
                    pass
        if self._context is not None and not await self._window_members(binding):
            self._drop_window_binding(token)

    # ── public surface (call through .call from any loop) ────────────────

    async def _open_windowed(self, url: str):
        """A page in its OWN OS window, or None to fall back to a tab.

        X11 capture can only see what is actually rendered, and a
        background TAB paints nothing — so a page that will be streamed
        from the display needs a window of its own. Tabs remain correct
        for the screencast path, hence the graceful None.
        """
        if self._xvfb_display is None:
            return None
        # SERIALIZED. Two opens racing here each snapshot the page list,
        # each see the other's new page, and one of them claims it — the
        # loser times out and falls back to a tab while its window stays
        # where Chromium put it, unmanaged and on top of a tile. That is
        # how a page ended up streaming someone else's blank window.
        async with self._open_lock:
            # The last native tab can close after open_page's initial ensure,
            # or while this request waits behind another open. Recheck while
            # holding the same lock that protects native window creation.
            await self._ensure_browser()
            if self._xvfb_display is None or self._context is None:
                return None
            page = await self._create_window_page(url)
            if page is None and xpra_mode() == "seamless":
                raise RuntimeError("could not create a separate browser window")
            return page

    async def _create_window_page(self, url: str):
        try:
            before = set(self._context.pages)
            browser = self._context.browser
            if browser is None:
                return None
            # Browser commands must outlive any particular tab. The old
            # channel was attached to the keeper page; closing that page
            # made every later createTarget fail, then silently add a tab
            # to somebody else's window through context.new_page().
            if self._browser_cdp is None:
                self._browser_cdp = await browser.new_browser_cdp_session()
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
            logger.info("browser: windowed open did not surface a page")
        except Exception as e:
            self._browser_cdp = None
            logger.info("browser: windowed open failed ({})", e)
        return None

    async def open_page(self, url: str = "", *,
                        wait_for_load: bool = True) -> PageSession:
        t_open = time.monotonic()
        # A page with nowhere to go opens at home, not on a white void.
        url = url or HOME_URL
        await self._ensure_browser()
        # Establish native identity on a blank page before navigation can
        # change its title. The destination is loaded exactly once below.
        page = await self._open_windowed("about:blank")
        windowed = page is not None
        if page is None:
            page = await self._context.new_page()  # type: ignore[union-attr]
        session = PageSession(uuid.uuid4().hex[:12], page)
        session.windowed = windowed
        self.pages[session.id] = session
        await self._attach(session)
        if windowed:
            await self._bind_window(session)
        if windowed and xpra_mode() == "seamless":
            await self._name_window(session)
        # Window placement and the page load are independent, and the user
        # is waiting on this call: run them together rather than in series.
        async def _shape() -> None:
            await self.reshape(session, session.width, session.height,
                               session.dsf)

        placing = asyncio.ensure_future(_shape()) if windowed else None
        async def _navigate_initial() -> None:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                logger.warning("browser: initial goto {} failed: {}", url, e)
            finally:
                session.loading = False

        session.loading = True
        session.navigation_task = asyncio.create_task(_navigate_initial())
        if placing is not None:
            try:
                await placing
            except Exception as e:
                logger.info("browser: window placement failed: {}", e)
        # UI mounts the native stream immediately so Chromium can display its
        # own loading progress. Agent opens retain the DOM-ready contract.
        if wait_for_load:
            await session.navigation_task
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

        Seamless has no framebuffer to keep clear and a window manager that
        owns geometry — and Browser.setWindowBounds NEVER RETURNS there,
        because Chromium waits for a WM acknowledgement that never comes for
        a position off the screen. Every stage hung on it.

        The stage is the whole picture — the framebuffer is fitted to the
        staged window at the origin — so any other window either overlaps it
        (and the shadow shows the wrong one) or sits outside the framebuffer
        and is simply not there. Park is the default.

        A popup of the staged page is the exception: it is its own Chromium
        window, and the login it carries has to be visible and clickable, so
        it goes ON the stage, inset like a popup anywhere else.
        """
        if xpra_mode() == "seamless" or not session.windowed or session.cdp is None:
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
        opener_rect = self._stages.get(session.opener or "")
        if opener_rect and self._stage_fb:
            # A popup of a staged page belongs ON its opener's rectangle:
            # it is its own Chromium window and the login it carries has to
            # be visible and clickable.
            fb_w, fb_h = self._stage_fb
            ox, oy, ow, oh = opener_rect
            left = max(0, min(ox + self.POPUP_INSET, fb_w - min(w, ow)))
            top = max(0, min(oy + self.POPUP_INSET, fb_h - min(outer_h, oh)))
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
        connection = getattr(self._xdisplay_local, "connection", None)
        if connection is None:
            from Xlib import display as _xdisplay

            connection = _xdisplay.Display(self._xvfb_display or ":97")
            self._xdisplay_local.connection = connection
        return connection

    def _reset_x_display(self) -> None:
        """Discard only this thread's connection after an X11 error."""
        connection = getattr(self._xdisplay_local, "connection", None)
        self._xdisplay_local.connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    async def send_keys(self, events: list[dict]) -> int:
        """Press/release keys on the display. Returns how many landed.

        Xlib uses a thread-local connection. The same input lock as generic
        desktop_act keeps a browser shortcut from interleaving its modifiers
        with an agent's drag or text. Waiting for that lock must not block the
        engine loop and its Playwright/Xpra control messages.
        """
        return await asyncio.to_thread(self._send_keys_locked, events)

    def _send_keys_locked(self, events: list[dict]) -> int:
        with self._native_input_lock:
            return self._send_keys_sync(events)

    def _send_keys_sync(self, events: list[dict]) -> int:
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
                self._reset_x_display()
                break
        try:
            d.sync()
        except Exception:
            self._reset_x_display()
        return sent

    def _start_seamless(self, display: str) -> bool:
        """Start an xpra session that owns and manages the display.

        Same port and the same per-boot password as the shadow, so a viewer
        connects the same way; what changes is what it receives — windows
        instead of one desktop-sized picture.
        """
        import secrets
        import shutil
        import subprocess
        import time as _t
        import urllib.error
        import urllib.request

        if shutil.which("xpra") is None:
            return False
        self._xpra_password = self._xpra_password or secrets.token_urlsafe(18)
        try:
            self._xpra_proc = subprocess.Popen(
                ["xpra", "start", display,
                 f"--bind-ws=0.0.0.0:{XPRA_PORT}",
                 "--html=on", "--daemon=no",
                 f"--ws-auth=password:value={self._xpra_password}",
                 "--sharing=yes",
                 # Nothing here has a speaker, a printer, or a bus.
                 "--notifications=no", "--pulseaudio=no", "--mdns=no",
                 "--webcam=no", "--printing=no", "--dbus-launch=",
                 # dbus-launch only disables the session bus. Without these,
                 # root Xpra still waits for/starts an unused system bus,
                 # adding a five-second missing-socket probe on first start.
                 "--dbus=no", "--dbus-control=no",
                 # The session outlives any one child: Chromium is started
                 # (and restarted) by us, not by xpra.
                 "--exit-with-children=no", "--start-new-commands=no"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning("browser: seamless launch failed: {}", e)
            return False
        for _ in range(120):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{XPRA_PORT}/", timeout=1):
                    pass
            except urllib.error.HTTPError:
                pass
            except Exception:
                _t.sleep(0.5)
                continue
            probe = subprocess.run(["xdpyinfo", "-display", display],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
            if probe.returncode == 0:
                logger.info("browser: seamless xpra session on {} (:{})",
                            display, XPRA_PORT)
                return True
            _t.sleep(0.5)
        logger.warning("browser: seamless session never came up")
        return False

    async def _name_window(self, session: PageSession) -> bool:
        """Name this page's X window after the page, so a viewer can find it.

        xpra forwards WM_CLASS (as `class-instance`) and nothing that
        identifies the X window, and its window manager reparents windows
        into its own frames — so neither geometry nor the window tree's shape
        will do. The page names ITSELF for a moment (its title is its X
        window's name), we find the window carrying that name, stamp
        WM_CLASS on it, and give the title back.
        """
        return await self._name_binding(await self._bind_window(session), session)

    async def _name_binding(self, binding: BrowserWindowBinding, session: PageSession) -> bool:
        if binding.id in self._named:
            return True
        # Do not name a moved anchor's new window with its former identity.
        info = await asyncio.wait_for(session.cdp.send("Browser.getWindowForTarget"), timeout=2)
        if info.get("windowId") != binding.window_id:
            raise RuntimeError("The browser tab moved before its window could be named")
        token = f"pantheon-window-{binding.id}"
        ok = await self._name_native_window(session.page, token, binding.window_class)
        if ok:
            self._named.add(binding.id)
        return ok

    async def _name_native_window(self, page: Any, token: str,
                                  window_class: str) -> bool:
        """Give one native window its identity before its URL starts loading."""
        try:
            await page.evaluate(
                "(t) => { window.__pantheon_title = document.title;"
                " document.title = t; }", token)
        except Exception as e:
            logger.info("browser: could not name {}: {}", window_class, e)
            return False
        ok = False
        deadline = time.monotonic() + 4.0
        try:
            while time.monotonic() < deadline:
                ok = await asyncio.to_thread(
                    self._stamp_class, token, window_class=window_class,
                )
                if ok:
                    break
                await asyncio.sleep(0.15)
        finally:
            try:
                await page.evaluate(
                    "() => { if (window.__pantheon_title !== undefined)"
                    " document.title = window.__pantheon_title; }")
            except Exception:
                pass
        if not ok:
            logger.info("browser: no X window answered to {}", token)
        return ok

    def _window_name(self, d, win) -> str:
        """A window's title, from either place it can live.

        Chromium — like everything modern — publishes `_NET_WM_NAME` (UTF-8)
        and leaves the legacy `WM_NAME` empty, and python-xlib's
        `get_wm_name()` reads only the legacy one. Reading just that found
        every Chromium window nameless, so the page could never be matched
        to its window.
        """
        try:
            prop = win.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
            if prop and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", "replace")
                return str(value)
        except Exception:
            pass
        try:
            return win.get_wm_name() or ""
        except Exception:
            return ""

    def _stamp_class(self, token: str, page_id: str = "", *,
                     window_class: str = "") -> bool:
        """Set WM_CLASS on the window whose name holds `token`.

        Bounded on purpose: this runs while a viewer waits for its stage,
        and a display with a dozen windows on it makes each pass over the
        tree cost real time.
        """
        try:
            d = self._x_display()

            def walk(win, depth=0):
                if depth > 4:
                    return None
                try:
                    kids = win.query_tree().children
                except Exception:
                    return None
                for child in kids:
                    if token in self._window_name(d, child):
                        return child
                    found = walk(child, depth + 1)
                    if found is not None:
                        return found
                return None

            target = walk(d.screen().root)
            if target is None:
                return False
            instance = window_class or f"{PAGE_CLASS_PREFIX}{page_id}"
            target.set_wm_class(instance, "Chromium-browser")
            d.sync()
            logger.info("browser: named native window {}", instance)
            return True
        except Exception as e:
            logger.info("browser: naming failed: {}", e)
            self._reset_x_display()
            return False

    def _tag_page_window(self, session: PageSession, rect) -> None:
        """Name a page's X window after the page.

        xpra's window metadata carries a title, a type and WM_CLASS — but no
        X window id — so the viewer has nothing to match a protocol window to
        the page it asked for. WM_CLASS is forwarded verbatim, so the page id
        goes there: `pantheon-page-<id>`, on the window sitting where we just
        put it.
        """
        try:
            d = self._x_display()
            root = d.screen().root
            x, y, w, h = rect
            for child in root.query_tree().children:
                try:
                    g = child.get_geometry()
                    cls = child.get_wm_class()
                except Exception:
                    continue
                if not cls or "Chromium" not in (cls[1] or ""):
                    continue
                if (g.x, g.y) != (x, y):
                    continue
                child.set_wm_class(f"{PAGE_CLASS_PREFIX}{session.id}", cls[1])
                d.sync()
                return
        except Exception as e:
            logger.info("browser: tagging {} failed: {}", session.id, e)
            self._reset_x_display()

    def _xpra_alive(self) -> bool:
        return self._xpra_proc is not None and self._xpra_proc.poll() is None

    async def _ensure_xpra(self) -> bool:
        if xpra_mode() == "seamless":
            return self._xvfb_display is not None and self._xpra_alive()
        return await self._ensure_shadow()

    async def _ensure_shadow(self) -> bool:
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

    #: How many Browser windows may hold the display at once. The framebuffer
    #: is their union and the shadow has to encode all of it, so this is a
    #: budget, not a limit of the design.
    MAX_STAGES = 6

    def _prune_stages(self) -> list[str]:
        """Drop stages nothing is using, and return whose windows to park.

        Two kinds go: a page that no longer exists (its window closed), and
        the least recently used once there are more than the display can
        carry — a viewer that went away without saying so (a reload, a
        closed laptop) otherwise keeps its rectangle forever, and the
        framebuffer grows until nothing fits.
        """
        dropped = [pid for pid in self._stages if pid not in self.pages]
        for pid in dropped:
            self._stages.pop(pid, None)
            self._stage_touch.pop(pid, None)
        while len(self._stages) > self.MAX_STAGES:
            oldest = min(self._stages, key=lambda k: self._stage_touch.get(k, 0.0))
            self._stages.pop(oldest, None)
            self._stage_touch.pop(oldest, None)
            dropped.append(oldest)
            logger.info("browser: {} gave up the display (older than the "
                        "{} windows using it)", oldest, self.MAX_STAGES)
        return dropped

    def _repack(self) -> tuple[int, int]:
        """Lay the staged windows out in one column and size the display.

        The shadow streams ONE framebuffer, so this is how several Browser
        windows are live at once: each staged page gets its own disjoint
        rectangle inside it, and each viewer crops to its own. A column,
        not a grid — windows are as wide as the viewer made them, and
        stacking keeps the arithmetic something anyone can check.

        The framebuffer is the union of those rectangles, floored at the
        viewer's whole desktop so the ordinary case (one window, resized
        by hand) never changes it: every change makes the shadow tear its
        desktop window down and build another, which the viewer sees as a
        stall.
        """
        y = 0
        packed: dict[str, tuple[int, int, int, int]] = {}
        for pid, rect in self._stages.items():
            w, h = rect[2], rect[3]
            packed[pid] = (0, y, w, h)
            y += h
        self._stages = packed
        need_w = max([r[2] for r in packed.values()] or [2])
        fb_w = min(SCREEN_W, max(need_w, self._stage_min_fb[0],
                                 (self._stage_fb or (0, 0))[0]))
        # PARK_Y is where unstaged windows wait; the framebuffer must stop
        # short of it or a parked window would be back in the picture.
        fb_h = min(PARK_Y - 2, max(y, 2, self._stage_min_fb[1],
                                   (self._stage_fb or (0, 0))[1]))
        return fb_w, fb_h & ~1

    async def _place_one(self, session: PageSession, rect) -> None:
        """Put one window on its rectangle (physical px in, DIP out)."""
        if session.cdp is None:
            return
        x, y, w, outer_h = rect
        info = await session.cdp.send("Browser.getWindowForTarget")
        await session.cdp.send("Browser.setWindowBounds", {
            "windowId": info["windowId"],
            "bounds": {
                "left": x // RASTER_SCALE, "top": y // RASTER_SCALE,
                "width": w // RASTER_SCALE,
                "height": outer_h // RASTER_SCALE,
                "windowState": "normal",
            },
        })
        session.rect = (x, y + WINDOW_CHROME_PX, w, outer_h - WINDOW_CHROME_PX)

    async def _apply_stages(self) -> None:
        """Resize the display to fit the staged windows, then place them."""
        fb = self._repack()
        if fb != self._stage_fb:
            await asyncio.to_thread(self._set_fb, *fb)
            self._stage_fb = fb
        for pid, rect in list(self._stages.items()):
            other = self.pages.get(pid)
            if other is None:
                self._stages.pop(pid, None)
                continue
            try:
                await self._place_one(other, rect)
            except Exception as e:
                logger.info("browser: placing staged {} failed: {}", pid, e)

    async def _stage_place(self, session: PageSession) -> None:
        """Re-place this page's window after a resize (reshape's hook)."""
        if session.id not in self._stages:
            return
        w = max(2, int(session.width * session.dsf))
        h = max(2, int(session.height * session.dsf))
        x, y, _, _ = self._stages[session.id]
        self._stages[session.id] = (x, y, w, (h + WINDOW_CHROME_PX) & ~1)
        await self._apply_stages()

    def stage_layout(self) -> dict:
        """What every viewer needs to crop its own window out of the stream."""
        fb = self._stage_fb or (2, 2)
        return {
            "fb_width": fb[0],
            "fb_height": fb[1],
            "chrome_px": WINDOW_CHROME_PX,
            "rects": {pid: list(r) for pid, r in self._stages.items()},
        }

    async def stage_page(self, page_id: str, width: int, height: int,
                         fb_width: int = 0, fb_height: int = 0) -> dict:
        """Give this page a visible rectangle on the display, and keep it.

        Several pages can hold one at once — one per Browser window — so a
        window the user is not looking at goes on living instead of freezing
        into its last frame. `fb_width`/`fb_height` is the viewer's whole
        desktop: the floor for the framebuffer, so a hand resize never
        rebuilds the shadow's window.

        Returns the connection material and the whole layout; raises if the
        transport is unavailable so the caller can say so.
        """
        # Resolve only the registered identity before testing the transport.
        # After a workspace replacement both the display and old page are
        # absent: callers need the exact missing-page error to offer explicit
        # recovery, rather than retrying an uninitialized display forever.
        binding = self._window_bindings.get(page_id)
        session = self.get(page_id) if binding is None else None
        seamless = xpra_mode() == "seamless"
        if seamless and (not self._xvfb_display or not self._xpra_alive()
                         or not self._xpra_password):
            # A cached display name/password does not mean its Xpra server
            # survived. Reconnecting must not restart Chromium or replace
            # retained native windows just to manufacture a working stage.
            raise RuntimeError("Native display is unavailable")
        if binding is not None:
            session = await self.window_page(page_id, require_visible=False)
        if seamless:
            # No packing, no cropping: the window IS the object the viewer
            # adopts. Size the page, name its window after itself, done.
            await self._ensure_browser()
            session.width, session.height = width, height
            await self.set_metrics(session, width, height, float(RASTER_SCALE))
            if binding is None:
                binding = await self._bind_window(session)
            if not await self._name_binding(binding, session):
                raise RuntimeError("The browser native window could not be named")
            import getpass

            return {
                "mode": "seamless",
                "password": self._xpra_password,
                "username": getpass.getuser(),
                "chrome_px": WINDOW_CHROME_PX,
                # What the viewer matches its window on (WM_CLASS -> xpra's
                # class-instance).
                "window_class": binding.window_class,
                "page_id": binding.id,
                "active_page_id": session.id,
            }
        if not await self._ensure_xpra():
            raise RuntimeError("xpra transport unavailable")
        self._stage_min_fb = (
            max(self._stage_min_fb[0], max(0, int(fb_width or 0))),
            max(self._stage_min_fb[1], max(0, int(fb_height or 0))),
        )
        w = max(2, int(width * RASTER_SCALE))
        outer_h = (max(2, int(height * RASTER_SCALE)) + WINDOW_CHROME_PX) & ~1
        first = page_id not in self._stages
        self._stages[page_id] = (0, 0, w, outer_h)
        self._stage_touch[page_id] = time.monotonic()
        for gone in self._prune_stages():
            old = self.pages.get(gone)
            if old is not None and old.windowed:
                try:
                    await self.place_window(old)   # off the framebuffer
                except Exception as e:
                    logger.info("browser: parking {} failed: {}", gone, e)
        if self._dialog_task is None:
            self._dialog_task = asyncio.ensure_future(self._dialog_keeper())
        self._tiles_release(page_id)
        await self.reshape(session, width, height, float(RASTER_SCALE))
        # Undecorate: the html5 client draws its own frame around a decorated
        # window, and window+frame then overflows the viewer's viewport.
        await asyncio.to_thread(self._strip_decorations)
        if first:
            # New to the stage: bring its tab to the front. NOT on a resize —
            # Page.bringToFront activates a tab, and the user's own tabs live
            # in the same window, so doing it every time yanked the view back
            # to our first tab on every drag of the window's edge.
            await self.focus_page(session)
        import getpass

        return {
            "mode": "shadow",
            "password": self._xpra_password,
            "username": getpass.getuser(),
            **self.stage_layout(),
        }

    def _tiles_release(self, page_id: str) -> None:
        """Forget any pending placement for a page that is now staged."""
        session = self.pages.get(page_id)
        if session is not None:
            session.rect = None

    async def focus_stage(self, page_id: str) -> dict:
        """Send the keyboard to this page's window.

        With several windows on the display at once, "which one is typed
        into" is the viewer's business, not the display's: the X input focus
        follows the Browser window the user is working in.
        """
        binding = self._window_bindings.get(page_id)
        session = await self.window_page(page_id, require_visible=False) if binding is not None else self.get(page_id)
        if xpra_mode() == "seamless":
            # No rectangles there: the window is found by the name we gave
            # it. X focus still has to be ours to set, because the keys the
            # viewer sends are injected on the display (browser_ui_key).
            if binding is None:
                binding = await self._bind_window(session)
            if not await self._name_binding(binding, session):
                raise RuntimeError("The browser native window could not be named")
            await asyncio.to_thread(self._focus_named_window, page_id)
            return {"ok": True}
        await self.focus_page(session)
        rect = self._stages.get(page_id)
        if rect is not None:
            self._stage_touch[page_id] = time.monotonic()
            await asyncio.to_thread(self._focus_x_window, rect)
        return {"ok": True}

    def _fit_dialogs(self) -> int:
        """Move Chromium's own dialogs onto the window they belong to.

        A print or save dialog is a separate X window, and with no window
        manager it opens centred on the DISPLAY — which is the union of
        every Browser window, so the viewer saw a dialog running off the
        edge of its own window, cut in half. WM_TRANSIENT_FOR says which
        window it belongs to; put it inside that one's rectangle, shrunk to
        fit if it has to be.
        """
        moved = 0
        try:
            d = self._x_display()
            root = d.screen().root
            transient = d.intern_atom("WM_TRANSIENT_FOR")
            owners = {(r[0], r[1]): r for r in self._stages.values()}
            if not owners:
                return 0
            for child in root.query_tree().children:
                try:
                    if child.get_attributes().map_state != 2:  # IsViewable
                        continue
                    g = child.get_geometry()
                    prop = child.get_property(transient, 0, 0, 1)
                except Exception:
                    continue
                if prop is None or not prop.value:
                    continue   # not a dialog
                parent_rect = None
                try:
                    parent = d.create_resource_object("window", prop.value[0])
                    pg = parent.get_geometry()
                    parent_rect = owners.get((pg.x, pg.y))
                except Exception:
                    parent_rect = None
                if parent_rect is None:
                    parent_rect = next(iter(self._stages.values()))
                px, py, pw, ph = parent_rect
                w = min(g.width, pw)
                h = min(g.height, ph)
                x = px + max(0, (pw - w) // 2)
                y = py + max(0, (ph - h) // 2)
                if (g.x, g.y, g.width, g.height) == (x, y, w, h):
                    continue
                child.configure(x=x, y=y, width=w, height=h)
                moved += 1
            if moved:
                d.sync()
        except Exception as e:
            logger.info("browser: fitting dialogs failed: {}", e)
            self._reset_x_display()
        return moved

    async def _dialog_keeper(self) -> None:
        """Keep dialogs inside their window for as long as any window is on."""
        while True:
            try:
                await asyncio.sleep(0.6)
                if not self._stages:
                    continue
                await asyncio.to_thread(self._fit_dialogs)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("browser: dialog keeper: {}", e)

    def _focus_named_window(self, page_id: str) -> None:
        """Give X focus to the window carrying this page's WM_CLASS."""
        want = f"{PAGE_CLASS_PREFIX}{page_id}"
        try:
            from Xlib import X

            d = self._x_display()

            def walk(win, depth=0):
                if depth > 4:
                    return None
                try:
                    kids = win.query_tree().children
                except Exception:
                    return None
                for child in kids:
                    try:
                        cls = child.get_wm_class()
                    except Exception:
                        cls = None
                    if cls and cls[0] == want:
                        return child
                    found = walk(child, depth + 1)
                    if found is not None:
                        return found
                return None

            target = walk(d.screen().root)
            if target is None:
                return
            d.set_input_focus(target, X.RevertToParent, X.CurrentTime)
            d.sync()
        except Exception as e:
            logger.info("browser: focusing {} failed: {}", page_id, e)
            self._reset_x_display()

    def _focus_x_window(self, rect) -> None:
        """Give X input focus to the window at `rect`'s origin."""
        try:
            from Xlib import X

            d = self._x_display()
            root = d.screen().root
            x, y = rect[0], rect[1]
            for child in root.query_tree().children:
                try:
                    g = child.get_geometry()
                    cls = child.get_wm_class()
                except Exception:
                    continue
                if not cls or "Chromium" not in (cls[1] or ""):
                    continue
                # Match the whole rectangle: with several windows on the
                # display (and parked ones sharing an origin off it), a
                # position alone picks the wrong one.
                if (g.x, g.y, g.width, g.height) == (x, y, rect[2], rect[3]):
                    d.set_input_focus(child, X.RevertToParent, X.CurrentTime)
                    d.sync()
                    return
        except Exception as e:
            logger.info("browser: X focus failed: {}", e)
            self._reset_x_display()

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
        """This page gives up its rectangle; the rest close ranks."""
        if page_id not in self._stages:
            return
        self._stages.pop(page_id, None)
        session = self.pages.get(page_id)
        if session is not None and session.windowed:
            try:
                await self.place_window(session)   # off the framebuffer
            except Exception as e:
                logger.info("browser: parking after unstage failed: {}", e)
        if self._stages:
            # Others are still on: keep the display, re-pack around the gap.
            await self._apply_stages()
            return
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
        session = self.pages.get(page_id)
        if session is None:
            return
        # Keep the live registration when Chromium rejects a close. Dropping
        # it first would make a failed public close look successful and orphan
        # a window that is still visible to the user.
        if not session.page.is_closed():
            await session.page.close()
            if not session.page.is_closed():
                raise RuntimeError("Chromium did not close the requested page")
        # The page's close callback can have completed this cleanup while we
        # awaited Chromium. Perform it exactly once.
        if self.pages.pop(page_id, None) is None:
            return
        bound = page_id in self._window_bindings
        if not bound:
            self._named.discard(page_id)
        if page_id in self._stages and not bound:
            self._stages.pop(page_id, None)
            if self._stages:
                # Others are still showing: close ranks around the gap.
                try:
                    await self._apply_stages()
                except Exception as e:
                    logger.info("browser: re-packing after close failed: {}", e)
            else:
                self._stage_fb = None
                self._stage_min_fb = (0, 0)
                try:
                    await asyncio.to_thread(self._set_fb, SCREEN_W, SCREEN_H)
                except Exception as e:
                    logger.info("browser: framebuffer restore failed: {}", e)
        if not bound:
            for wid, owner in list(self._windows.items()):
                if owner == page_id:
                    self._windows.pop(wid, None)
        # Each binding outlives any one of its tabs. An inspection failure is
        # not proof the native window disappeared; a subsequent read retries.
        for token, binding in list(self._window_bindings.items()):
            try:
                if self._context is not None and not await self._window_members(binding):
                    self._drop_window_binding(token)
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
