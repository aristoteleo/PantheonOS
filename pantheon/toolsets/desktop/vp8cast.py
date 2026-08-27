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

from pantheon.utils.log import logger

# How often the cast re-reads the page's navigation state. Two CDP round
# trips each time, so not per frame.
STATUS_REFRESH_S = 0.25

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

    {"page": "<id>"} RETARGETS a live socket at another page. Every page
    already has its own window, rendered and up to date, so switching tabs
    is a matter of aiming the capture somewhere else — a frame's work. The
    client used to tear the WebRTC call down and negotiate a new one for
    each tab, which is seconds of spinner for a page that was ready the
    whole time, and which no real browser makes you wait for.
    """

    async def handler(request: web.Request) -> web.StreamResponse:
        page_id = request.query.get("page", "")
        session = engine.pages.get(page_id)
        if session is None:
            return web.Response(status=404, text="no such page")
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)

        # Built whatever the path: the X11 pump falls back to it in place
        # rather than dropping the viewer when capture fails mid-stream.
        try:
            caster = Vp8Caster()
        except Exception as e:  # av missing in an old image
            await ws.close(code=1011, message=str(e)[:100].encode())
            return ws

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        # What this socket is currently showing. Mutable: a retarget swaps
        # it and restarts the pump, without touching the WebRTC call.
        live = {"page": page_id, "session": session}
        # The capture currently running, so a keyframe request can reach it.
        current: dict[str, Any] = {"x11": None}

        # Status costs TWO CDP round trips (the page's title, and an eval for
        # scroll metrics), and it was being fetched for every single frame:
        # sixty round trips a second, all queued on the engine's one loop,
        # which held the whole cast to about five frames a second. It feeds
        # the address bar and the scrollbar overlay, so a few times a second
        # is plenty.
        cached: dict[str, str] = {}
        cached_at = 0.0

        async def send(meta: dict, payload: bytes, seq: int) -> None:
            nonlocal cached, cached_at
            now = time.monotonic()
            if now - cached_at > STATUS_REFRESH_S or not cached:
                headers = await engine.call(
                    engine.status_headers(live["session"]))
                cached = {
                    k.lower().replace("x-", "", 1): v
                    for k, v in headers.items() if k != "X-Seq"
                }
                cached_at = now
            meta["seq"] = seq
            meta["status"] = cached
            await ws.send_json(meta)
            await ws.send_bytes(payload)

        async def open_x11(sess) -> Any:
            """A capture of this page's own window, or None for the JPEG path.

            X11 capture runs at 30 fps against the screencast path's 14, and
            is the same mechanism that will stream non-browser apps.
            """
            if not (getattr(sess, "windowed", False) and getattr(sess, "rect", None)):
                logger.info("cast {}: JPEG transcode path", live["page"])
                return None
            try:
                from .x11cast import X11Caster

                display = getattr(engine, "_xvfb_display", None)
                if not display:
                    return None
                left, top, w, h = sess.rect
                x11 = X11Caster(f"{display}.0", left, top, w, h)
                await loop.run_in_executor(None, x11.open)
                logger.info("cast {}: X11 {}x{} ({:.1f} Mpx)",
                            live["page"], w, h, w * h / 1e6)
                return x11
            except Exception as e:
                logger.info("cast {}: X11 unavailable ({}); JPEG path",
                            live["page"], e)
                return None

        async def pump_x11(x11: Any, sess: Any) -> None:
            """The display drives the pace — no waiting on paint events.

            A failure here used to `break` in silence: the socket closed,
            the gateway logged 'Server disconnected', the browser showed a
            2-pixel dead track, and nothing anywhere said why. Now it says
            why, and falls back to the JPEG path instead of leaving the
            viewer with nothing.
            """
            # Compared against the SESSION's rect as written, not the
            # caster's evened copy — otherwise an odd width would look like
            # a pending move on every single frame.
            applied = tuple(sess.rect or ())
            while not stop.is_set() and not ws.closed:
                try:
                    # A resize moves the OS window; the grab has to follow it
                    # or it keeps capturing the rectangle the window left.
                    rect = tuple(getattr(sess, "rect", None) or ())
                    if not rect:
                        # The page lost its window (parked, no room, or a
                        # failed placement). Its old rectangle is now empty
                        # desk, and grabbing it would stream a flawless 30
                        # fps of nothing. The screencast still works.
                        logger.info("cast {}: window gone; JPEG path", live["page"])
                        try:
                            x11.close()
                        except Exception:
                            pass
                        await pump_jpeg(sess)
                        return
                    if rect != applied:
                        logger.info("cast {}: re-aiming {} -> {}",
                                    live["page"], applied or "(none)", rect)
                        await loop.run_in_executor(None, lambda r=rect: x11.move(*r))
                        applied = rect
                    out = await loop.run_in_executor(None, x11.next_frame)
                    if out is None:
                        continue
                    meta, payload = out
                    await send(meta, payload, sess.seq)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if ws.closed or stop.is_set():
                        return
                    logger.warning("cast {}: X11 pump failed ({}: {}); "
                                   "falling back to the JPEG path",
                                   live["page"], type(e).__name__, str(e)[:160])
                    try:
                        x11.close()
                    except Exception:
                        pass
                    await pump_jpeg(sess)
                    return

        async def pump_jpeg(sess: Any) -> None:
            # Only this path needs Chromium's screencast; the X11 path reads
            # the display and leaves the JPEG encoder off entirely.
            await engine.call(engine.acquire_viewer(sess))
            try:
                await _pump_jpeg_frames(sess)
            finally:
                await engine.call(engine.release_viewer(sess))

        async def _pump_jpeg_frames(sess: Any) -> None:
            since = 0
            while not stop.is_set() and not ws.closed:
                try:
                    fresh = await engine.call(engine.wait_frame(sess, since))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    break
                if stop.is_set() or ws.closed:
                    break
                if not fresh:
                    continue
                since = sess.seq  # latest-wins: skip straight to newest
                jpeg = sess.frame
                if not jpeg:
                    continue
                try:
                    out = await loop.run_in_executor(None, caster.encode, jpeg)
                    if out is None:
                        continue
                    meta, payload = out
                    await send(meta, payload, since)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if not (ws.closed or stop.is_set()):
                        logger.warning("cast {}: JPEG pump failed ({}: {})",
                                       live["page"], type(e).__name__, str(e)[:160])
                    break

        async def run_target() -> None:
            """Stream whatever `live` points at, until stopped or retargeted."""
            sess = live["session"]
            x11 = await open_x11(sess)
            current["x11"] = x11
            try:
                if x11 is not None:
                    await pump_x11(x11, sess)
                else:
                    await pump_jpeg(sess)
            finally:
                current["x11"] = None
                if x11 is not None:
                    try:
                        x11.close()
                    except Exception:
                        pass

        pump_task = asyncio.create_task(run_target())

        async def retarget(new_id: str) -> None:
            """Point this socket at another page, keeping the call up."""
            nonlocal pump_task, cached, cached_at
            new_session = engine.pages.get(new_id)
            if new_session is None or new_id == live["page"]:
                return
            logger.info("cast: retargeting {} -> {}", live["page"], new_id)
            live["page"], live["session"] = new_id, new_session
            cached, cached_at = {}, 0.0
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass
            # The picture is about to be a different page: the viewer needs
            # a key frame, not a delta against what it was watching.
            caster.request_keyframe()
            pump_task = asyncio.create_task(run_target())

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except Exception:
                        continue
                    new_page = payload.get("page")
                    if new_page:
                        await retarget(str(new_page))
                    if payload.get("keyframe"):
                        if current["x11"] is not None:
                            current["x11"].request_keyframe()
                        caster.request_keyframe()
                    events = payload.get("events") or []
                    if events and live["page"] in engine.pages:
                        try:
                            await engine.call(
                                engine.dispatch(live["page"], events))
                        except Exception:
                            pass
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE,
                                  web.WSMsgType.CLOSING):
                    break
        finally:
            stop.set()
            pump_task.cancel()
        return ws

    return handler
