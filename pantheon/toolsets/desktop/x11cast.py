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

KF_INTERVAL = 600
TARGET_BITRATE = 4_000_000
CLOCK = 90_000

# Capture rate. At 30 fps a change waits up to 33 ms just to be SEEN, which
# is a third of the whole hand-to-eye budget; 60 halves that and gives the
# eye twice as many intermediate positions. The cost is real (convert +
# encode run twice as often), but drop-when-behind sheds frames
# automatically when a window is too large to keep up, so the ceiling
# degrades to what the machine can do rather than falling over.
CAPTURE_FPS = 60

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
H264_OPTS = {
    "preset": "ultrafast",
    "tune": "zerolatency",
    "profile": "baseline",
    "crf": "28",
    # INTRA-REFRESH instead of periodic keyframes. A keyframe at 9 Mpx is
    # 100-300 KB; on a ~20 Mbps tunnel that is a 100 ms stall every few
    # seconds, and every frame behind it arrives late — the shape of the
    # 234 ms outliers in the motion-to-photon tail. Intra-refresh spreads
    # those intra blocks across ordinary frames instead, so the stream has
    # no size spikes at all. keyint is left long because the refresh cycle,
    # not an IDR, is what recovers the picture.
    "x264-params": "repeat-headers=1:intra-refresh=1:keyint=600:scenecut=0",
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
        self.framerate = framerate
        self._input: Any = None
        self._stream: Any = None
        self._demux: Any = None
        self._enc: Any = None
        self._t0: float | None = None
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
        enc.bit_rate = TARGET_BITRATE
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
            for frame in packet.decode():
                # DROP WHEN BEHIND. x11grab is a live source at a fixed rate:
                # every frame it produces arrives in order, so an encode
                # slower than the frame interval does not lower the frame
                # rate — it accumulates delay, without bound. The stream
                # still measures 30 fps while drifting seconds behind the
                # user's hand, which is exactly what "smooth numbers, feels
                # laggy" means. Skipping frames we are late for keeps the
                # picture current; the JPEG path has always done this
                # (latest-wins), and the X11 path needs its own version.
                if self._t0 is None:
                    self._t0 = time.monotonic()
                self._consumed += 1
                behind = (time.monotonic() - self._t0) - self._consumed / self.framerate
                if self._count and behind > 1.0 / self.framerate:
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


def tile_rect(index: int, width: int, height: int,
              screen_w: int = 8192, screen_h: int = 4608,
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
