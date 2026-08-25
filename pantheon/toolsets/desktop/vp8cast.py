"""Sandbox-side VP8 for the browser stream.

The tunnel out of the sandbox measures ~20 Mbps shared, so shipping
whole JPEGs caps the stream near 9 fps at Retina sizes. This module
transcodes the screencast's JPEGs into VP8 *inside the sandbox* — the
tunnel then carries a few Mbps of real video and the WebRTC gateway
repackages packets into RTP without re-encoding (aiortc's ``pack()``
path).

Design notes:

- Latest-wins happens BEFORE the encoder: a slow consumer skips straight
  to the newest paint, but every frame that IS encoded is also delivered,
  in order — the VP8 reference chain never breaks, so no drop-to-keyframe
  resync machinery is needed anywhere downstream.
- One caster per connection. Two viewers of one page cost two encoders;
  that is rare and transient, and it keeps lifecycle trivial.
- Decode+encode run in the default thread executor so neither the data
  server's loop nor the engine's loop ever blocks on codec work
  (~24 ms/frame at 2396x1320, measured in the sandbox).
"""

from __future__ import annotations

import asyncio
import fractions
import json
import time
from typing import Any

from aiohttp import web

KF_INTERVAL = 90          # forced keyframe cadence, in encoded frames
TARGET_BITRATE = 4_000_000
CLOCK = 90_000


class Vp8Caster:
    """JPEG bytes in, (meta, vp8 payload) out. Sync codec state, executor-run."""

    def __init__(self) -> None:
        import av

        self._av = av
        self._dec = av.CodecContext.create("mjpeg", "r")
        self._enc: Any = None
        self._size: tuple[int, int] | None = None
        self._t0 = time.monotonic()
        self._count = 0
        self._force_key = True

    def request_keyframe(self) -> None:
        self._force_key = True

    def _ensure_encoder(self, w: int, h: int) -> None:
        av = self._av
        if self._enc is not None and self._size == (w, h):
            return
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
        enc.thread_count = 0  # libvpx picks by resolution/cores
        self._enc = enc
        self._size = (w, h)
        self._force_key = True  # a fresh encoder opens on a keyframe anyway

    def encode(self, jpeg: bytes) -> tuple[dict, bytes] | None:
        """Runs in a worker thread. Returns (meta, payload) or None."""
        av = self._av
        frames = self._dec.decode(av.Packet(jpeg))
        if not frames:
            return None
        frm = frames[0]
        w = max(2, frm.width & ~1)
        h = max(2, frm.height & ~1)
        if frm.format.name != "yuv420p" or (frm.width, frm.height) != (w, h):
            frm = frm.reformat(width=w, height=h, format="yuv420p")
        self._ensure_encoder(w, h)
        pts = int((time.monotonic() - self._t0) * CLOCK)
        frm.pts = pts
        force = self._force_key or self._count % KF_INTERVAL == 0
        if force:
            frm.pict_type = self._av.video.frame.PictureType.I
            self._force_key = False
        else:
            # The mjpeg decoder stamps every frame I (JPEGs are intra) —
            # left as-is, libvpx would emit a keyframe per frame.
            frm.pict_type = self._av.video.frame.PictureType.NONE
        payload = b""
        key = False
        for pkt in self._enc.encode(frm):
            payload += bytes(pkt)
            key = key or bool(pkt.is_keyframe)
        self._count += 1
        if not payload:
            return None
        meta = {"t": "frame", "key": key, "pts": pts, "w": w, "h": h}
        return meta, payload


def make_cast_handler(engine):
    """browser-cast: the browser-stream contract, VP8 instead of JPEG.

    Per frame: one JSON line {t:'frame', key, pts, w, h, seq, status:{...}}
    then one binary VP8 payload. Input events ride back exactly like
    browser-stream; {"keyframe": true} forces the next frame to be a key.
    """

    async def handler(request: web.Request) -> web.StreamResponse:
        page_id = request.query.get("page", "")
        session = engine.pages.get(page_id)
        if session is None:
            return web.Response(status=404, text="no such page")
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)

        try:
            caster = Vp8Caster()
        except Exception as e:  # av missing in an old image
            await ws.close(code=1011, message=str(e)[:100].encode())
            return ws

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

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
                    continue
                since = session.seq  # latest-wins: skip straight to newest
                jpeg = session.frame
                if not jpeg:
                    continue
                try:
                    out = await loop.run_in_executor(None, caster.encode, jpeg)
                    if out is None:
                        continue
                    meta, payload = out
                    headers = await engine.call(engine.status_headers(session))
                    meta["seq"] = since
                    meta["status"] = {
                        k.lower().replace("x-", "", 1): v
                        for k, v in headers.items() if k != "X-Seq"
                    }
                    await ws.send_json(meta)
                    await ws.send_bytes(payload)
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
                    if payload.get("keyframe"):
                        caster.request_keyframe()
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
        return ws

    return handler
