"""X11 capture: a window's own rectangle, straight to VP8.

The screencast path (Chromium JPEGs -> our VP8) tops out near 14 fps at
Retina sizes because Chromium's own JPEG encoder is the metronome. Reading
the X display instead skips that entirely: measured in the sandbox at
**30 fps, ~27 ms CPU/frame** for a 1400x900 window.

It is also the general mechanism. Nothing here knows what drew the pixels,
so the same capture serves a browser window, QuPath, ImageJ, or any other
X11 program — which is why this module takes a plain rectangle and not a
page.

Two constraints come with it:

- Only what is RENDERED can be captured. A background Chromium tab paints
  nothing, so every page that streams needs its own OS window (the engine
  tiles them on the virtual display; see browser.py).
- Windows must not overlap, or the grab picks up whoever is on top. The
  display is far larger than any viewer, so tiles never need to fight.

Frames carry the same (meta, payload) contract as vp8cast.Vp8Caster, so the
browser-cast endpoint and the gateway are unchanged.
"""

from __future__ import annotations

import fractions
import time
from typing import Any

KF_INTERVAL = 120
TARGET_BITRATE = 4_000_000
CLOCK = 90_000

# What the encoder is allowed to put on the wire, in bits per second. The
# tunnel out of the sandbox measures ~20 Mbps and is SHARED with everything
# else the pod is doing, so this leaves room rather than claiming the lot.
LINK_BITRATE = 8_000_000

# Capture rate. At 30 fps a change waits up to 33 ms just to be SEEN, which
# is a third of the whole hand-to-eye budget; 60 halves that and gives the
# eye twice as many intermediate positions. The cost is real (convert +
# encode run twice as often), but drop-when-behind sheds frames
# automatically when a window is too large to keep up, so the ceiling
# degrades to what the machine can do rather than falling over.
CAPTURE_FPS = 60


def capture_fps(width: int, height: int) -> int:
    """Frames a second worth ASKING for at this size.

    x11grab hands over raw BGRA: a 9 Mpx window is 35 MB per frame, so 60
    fps there is 2 GB/s of memory traffic before a single pixel is
    encoded — the pipeline drowns and the picture crawls. Small windows
    keep the full rate; large ones drop to 30, which they could not exceed
    anyway.
    """
    area = max(1, width * height)
    return CAPTURE_FPS if area <= 4_000_000 else 30
# How old a captured frame may be before it is not worth encoding. Two
# frame intervals at 30 fps: enough slack that ordinary jitter does not
# throw work away, tight enough that the picture stays current.
STALE_AFTER_S = 0.066

# H.264 over VP8, measured in this sandbox on a scrolling text page:
#
#   11.2 Mpx   VP8  encode 28.9 ms/frame -> 18.8 fps, 34 KB/frame
#              H264 encode  7.6 ms/frame -> 30.1 fps, 21 KB/frame
#
# Nearly 4x cheaper and smaller, which is what lets a full-size Retina
# window hold 30 fps at all. x264's ultrafast+zerolatency has no B-frames
# and no lookahead, Baseline keeps every browser happy, and headers repeat
# with each keyframe so a viewer joining mid-stream can decode. VP8 stays
# as the fallback for gateways that predate the codec field.
#
# Rate control is a HARD CAP, not a target. Under plain CRF the encoder
# ignores bit_rate entirely: a still page cost 8.6 Mbps and a scrolling one
# demanded several times the ~20 Mbps the tunnel actually has. The socket
# then blocked, captured frames aged out, and a stream that could encode at
# 130 fps was delivering five — the "scrolling is choppy" report, start to
# finish. With VBV the encoder spends quality instead of bandwidth when the
# picture moves: a scroll goes momentarily softer and stays 30 fps, and the
# text sharpens again the instant it stops, which is when it is read.
# bufsize stays short (a quarter second) — a large buffer smooths bitrate
# by letting latency grow, and latency is the thing we are protecting.
# Shorter than that and x264 panics on hard content, throwing quality away
# far below the cap it was given.
H264_OPTS = {
    "preset": "ultrafast",
    "tune": "zerolatency",
    "profile": "baseline",
    "crf": "28",
    # Keyframes every ~2 s, and NO intra-refresh. Refresh does remove the
    # keyframe size spikes (a 9 Mpx IDR is 100-300 KB, a visible stall on a
    # 20 Mbps tunnel) — but it also removes IDRs, and a browser that misses
    # the one at the start of the call then has nothing to sync to. That is
    # exactly what happened: the track ended, the video element sat at 2 px,
    # and no counter anywhere said why. Recovery beats smoothness; the
    # gateway also forwards the browser's keyframe requests now, so a lost
    # start is repaired in a frame rather than in seconds.
    "x264-params": (
        "repeat-headers=1:keyint=120:scenecut=0"
        f":vbv-maxrate={LINK_BITRATE // 1000}:vbv-bufsize={LINK_BITRATE // 4000}"
    ),
}
VP8_OPTS = {
    "deadline": "realtime",
    "cpu-used": "-6",
    "lag-in-frames": "0",
    "minrate": str(TARGET_BITRATE),
    "maxrate": str(TARGET_BITRATE),
    "bufsize": str(TARGET_BITRATE),
}


class X11Caster:
    """One rectangle of one X display, encoded as a VP8 stream.

    Capture and encode are synchronous and belong in a worker thread; the
    caller pumps `next_frame()` and ships what it returns.
    """

    def __init__(self, display: str, left: int, top: int, width: int, height: int,
                 framerate: int = CAPTURE_FPS, codec: str = "h264"):
        import av

        self._av = av
        self.codec = codec
        self.display = display
        # vpx wants even dimensions; crop rather than resample.
        self.rect = (left, top, max(2, width & ~1), max(2, height & ~1))
        self.framerate = min(framerate, capture_fps(*self.rect[2:]))
        self._input: Any = None
        self._stream: Any = None
        self._demux: Any = None
        self._enc: Any = None
        self._t0: float | None = None
        self._media0 = 0.0
        self._count = 0
        self._consumed = 0
        self._dropped = 0
        self._force_key = True

    # ── lifecycle ────────────────────────────────────────────────────────

    def open(self) -> None:
        av = self._av
        left, top, w, h = self.rect
        self._input = av.open(
            f"{self.display}+{left},{top}", format="x11grab",
            options={
                "video_size": f"{w}x{h}",
                "framerate": str(self.framerate),
                "draw_mouse": "0",
            },
        )
        self._stream = self._input.streams.video[0]
        self._demux = self._input.demux(self._stream)
        self._ensure_encoder(w, h)

    def close(self) -> None:
        for attr in ("_input",):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self._demux = None
        self._enc = None

    def request_keyframe(self) -> None:
        self._force_key = True

    @property
    def stats(self) -> dict:
        return {"encoded": self._count, "dropped": self._dropped,
                "consumed": self._consumed}

    def move(self, left: int, top: int, width: int, height: int) -> None:
        """Re-aim at a new rectangle (window moved or resized)."""
        rect = (left, top, max(2, width & ~1), max(2, height & ~1))
        if rect == self.rect:
            return
        self.rect = rect
        self.framerate = min(CAPTURE_FPS, capture_fps(rect[2], rect[3]))
        self.close()
        self._t0 = None  # the new stream restarts its own clock
        self._consumed = 0
        self.open()

    # ── frames ───────────────────────────────────────────────────────────

    def _ensure_encoder(self, w: int, h: int) -> None:
        av = self._av
        wanted = "libx264" if self.codec == "h264" else "libvpx"
        try:
            enc = av.CodecContext.create(wanted, "w")
        except Exception:
            wanted, self.codec = "libvpx", "vp8"
            enc = av.CodecContext.create(wanted, "w")
        enc.width, enc.height = w, h
        enc.pix_fmt = "yuv420p"
        # H.264 runs constrained-quality (CRF under a VBV cap), so its
        # ceiling is the link budget; VP8 here is plain target-rate.
        enc.bit_rate = LINK_BITRATE if wanted == "libx264" else TARGET_BITRATE
        enc.time_base = fractions.Fraction(1, CLOCK)
        enc.options = dict(H264_OPTS if wanted == "libx264" else VP8_OPTS)
        enc.thread_count = 0
        self._enc = enc
        self._force_key = True

    def next_frame(self) -> tuple[dict, bytes] | None:
        """Blocking. Returns (meta, vp8 payload), or None to try again."""
        if self._demux is None:
            self.open()
        _, _, w, h = self.rect
        for packet in self._demux:  # type: ignore[union-attr]
            # Staleness is decided on the PACKET, before decode. Decoding a
            # 9 Mpx BGRA frame just to read its timestamp and throw it away
            # was most of the work the pipeline was doing when it fell
            # behind, which is why a big window crawled at 5 fps.
            if self._count and packet.pts is not None and packet.time_base:
                now = time.monotonic()
                if self._t0 is None:
                    self._t0, self._media0 = now, float(packet.pts * packet.time_base)
                age = (now - self._t0) - (float(packet.pts * packet.time_base) - self._media0)
                if age > STALE_AFTER_S:
                    self._consumed += 1
                    self._dropped += 1
                    continue
            for frame in packet.decode():
                # SKIP STALE FRAMES. x11grab is a live source: every frame
                # it produces arrives in order, so an encode slower than the
                # frame interval does not lower the frame rate — it
                # accumulates delay, without bound, and the stream measures
                # a fine 30 fps while drifting seconds behind the user's
                # hand.
                #
                # The test is how old THIS frame is, taken from the capture
                # clock the source stamps it with. Comparing consumption
                # against the nominal rate instead — which is what this did
                # first — makes a pipeline that cannot keep up fall behind
                # by a little more every second, so every frame looks late
                # forever and the stream goes black. Staleness self-corrects:
                # skipping old frames reaches the newest one, and then
                # nothing is stale.
                now = time.monotonic()
                if self._t0 is None:
                    self._t0 = now
                    self._media0 = float(frame.time or 0.0)
                self._consumed += 1
                age = (now - self._t0) - (float(frame.time or 0.0) - self._media0)
                if self._count and age > STALE_AFTER_S:
                    self._dropped += 1
                    continue
                if frame.format.name != "yuv420p" or (frame.width, frame.height) != (w, h):
                    frame = frame.reformat(width=w, height=h, format="yuv420p")
                frame.pts = int((time.monotonic() - self._t0) * CLOCK)
                frame.time_base = fractions.Fraction(1, CLOCK)
                force = self._force_key or self._count % KF_INTERVAL == 0
                frame.pict_type = (
                    self._av.video.frame.PictureType.I if force
                    else self._av.video.frame.PictureType.NONE
                )
                self._force_key = False
                payload = b""
                key = False
                for pkt in self._enc.encode(frame):  # type: ignore[union-attr]
                    payload += bytes(pkt)
                    key = key or bool(pkt.is_keyframe)
                self._count += 1
                if not payload:
                    return None
                return ({"t": "frame", "key": key, "pts": frame.pts,
                         "w": w, "h": h, "codec": self.codec}, payload)
            return None
        return None


# Big enough that several full-size Retina windows get disjoint tiles: a
# 2300x1350 window at 2x is 4600x2700 physical, so 8192x4608 fit exactly
# ONE — every window after the first fell back to the slow screencast path
# without saying so. Four fit here, and many more once windows are smaller.
# The cost is Xvfb's framebuffer (w*h*4 = 340 MB), paid only in sandboxes
# that actually open a browser.
SCREEN_W, SCREEN_H = 12288, 6912
# Where windows with no tile go: almost entirely off the bottom edge, so
# they still render (the JPEG path needs that) without covering a tile.
PARK_Y = SCREEN_H - 80


def tile_rect(index: int, width: int, height: int,
              screen_w: int = SCREEN_W, screen_h: int = SCREEN_H,
              gap: int = 8) -> tuple[int, int] | None:
    """Where slot `index` sits, or None when the display has no room.

    Overlap would make one window's capture show another's pixels — the
    failure looks like a black or wrong-page stream, so running out of
    room has to be an explicit None the caller can fall back on, never a
    silent stack at the origin. Nobody ever looks at this display
    directly, so the layout only has to be disjoint, not pretty.
    """
    cols = max(1, screen_w // max(1, width + gap))
    col = index % cols
    row = index // cols
    left = col * (width + gap)
    top = row * (height + gap)
    if top + height > screen_h or left + width > screen_w:
        return None
    return left, top


class TilePool:
    """Hands out disjoint slots and takes them back when windows close.

    Without reuse, a session that opens and closes pages walks the slot
    index off the display and every later window falls back to the slower
    path for no reason.
    """

    def __init__(self) -> None:
        self._slots: dict[str, int] = {}

    def acquire(self, key: str) -> int:
        if key in self._slots:
            return self._slots[key]
        taken = set(self._slots.values())
        slot = 0
        while slot in taken:
            slot += 1
        self._slots[key] = slot
        return slot

    def release(self, key: str) -> None:
        self._slots.pop(key, None)
