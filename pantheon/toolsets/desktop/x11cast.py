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

KF_INTERVAL = 90
TARGET_BITRATE = 4_000_000
CLOCK = 90_000


class X11Caster:
    """One rectangle of one X display, encoded as a VP8 stream.

    Capture and encode are synchronous and belong in a worker thread; the
    caller pumps `next_frame()` and ships what it returns.
    """

    def __init__(self, display: str, left: int, top: int, width: int, height: int,
                 framerate: int = 30):
        import av

        self._av = av
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

    def move(self, left: int, top: int, width: int, height: int) -> None:
        """Re-aim at a new rectangle (window moved or resized)."""
        rect = (left, top, max(2, width & ~1), max(2, height & ~1))
        if rect == self.rect:
            return
        self.rect = rect
        self.close()
        self._t0 = None  # the new stream restarts its own clock
        self.open()

    # ── frames ───────────────────────────────────────────────────────────

    def _ensure_encoder(self, w: int, h: int) -> None:
        av = self._av
        enc = av.CodecContext.create("libvpx", "w")
        enc.width, enc.height = w, h
        enc.pix_fmt = "yuv420p"
        enc.bit_rate = TARGET_BITRATE
        enc.time_base = fractions.Fraction(1, CLOCK)
        enc.options = {
            "deadline": "realtime",
            "cpu-used": "-6",
            "lag-in-frames": "0",
            "minrate": str(TARGET_BITRATE),
            "maxrate": str(TARGET_BITRATE),
            "bufsize": str(TARGET_BITRATE),
        }
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
                if frame.format.name != "yuv420p" or (frame.width, frame.height) != (w, h):
                    frame = frame.reformat(width=w, height=h, format="yuv420p")
                if self._t0 is None:
                    self._t0 = time.monotonic()
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
                return {"t": "frame", "key": key, "pts": frame.pts, "w": w, "h": h}, payload
            return None
        return None


def tile_rect(index: int, width: int, height: int,
              screen_w: int = 5120, screen_h: int = 3200,
              gap: int = 8) -> tuple[int, int]:
    """Where window `index` sits so tiles never overlap.

    Overlap would make one window's capture show another's pixels. Nobody
    ever looks at this display directly, so the layout only has to be
    disjoint — it does not have to be pretty.
    """
    cols = max(1, screen_w // max(1, width + gap))
    col = index % cols
    row = index // cols
    left = col * (width + gap)
    top = row * (height + gap)
    if top + height > screen_h:
        # Out of room: stack at the origin. The last window wins the
        # pixels, which is better than silently capturing a neighbour.
        return 0, 0
    return left, top
