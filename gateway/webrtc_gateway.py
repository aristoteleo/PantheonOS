"""Pantheon browser WebRTC gateway.

The sandbox's Chromium paints JPEG frames onto a WebSocket
(``browser-stream`` on the pod's data server, reachable over the Modal
tunnel). This gateway terminates WebRTC for a client: it consumes that
socket, re-encodes the frames as VP8 video, and forwards the client's
DataChannel input events back up the same socket. NATS never carries a
pixel; the tunnel leg is datacenter-to-datacenter; the WebRTC leg is a
public-IP server to a browser, which needs no TURN for typical NATs.

One process serves many sessions. Deliberately self-contained — a single
file with pip-installable deps — so it can run as a ConfigMap-mounted
script on the DO cluster today and on fleet relay hosts as edge gateways
tomorrow, unchanged.

Signaling: POST /offer {sdp, type, stream_url} -> {sdp, type}.
The stream_url IS the capability: it is the pod's tunnel URL, unguessable
and already held only by an authenticated desktop client. GET /healthz for
probes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

import aiohttp
from aiohttp import WSMsgType, web

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.codecs import vpx
from aiortc.mediastreams import (
    VIDEO_CLOCK_RATE,
    VIDEO_TIME_BASE,
    MediaStreamError,
)
import av

# Desktop text at aiortc's default 500 kbps VP8 — hard-capped at 1.5 Mbps
# even under REMB — smears into mush. The constants are read at call time,
# so patching the module raises both the starting rate and the clamp. The
# tunnel leg is datacenter-grade and sessions are few: spend bandwidth on
# legibility.
vpx.DEFAULT_BITRATE = 4_000_000
vpx.MIN_BITRATE = 1_000_000
vpx.MAX_BITRATE = 10_000_000

logger = logging.getLogger("gateway")

MAX_SESSIONS = int(os.environ.get("GATEWAY_MAX_SESSIONS", "32"))
PORT = int(os.environ.get("GATEWAY_PORT", "8089"))


class BrowserFeed:
    """One page's stream socket: latest frame in, input events out."""

    def __init__(self, stream_url: str):
        self.url = stream_url
        self.latest: bytes | None = None
        self.fresh = asyncio.Event()
        self.closed = asyncio.Event()
        self.status: dict = {}
        # Called with each status dict — the gateway forwards it down the
        # DataChannel so the client gets url/title/scroll without a second
        # HTTP channel of its own.
        self.on_status = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None

    async def run(self) -> None:
        try:
            self._http = aiohttp.ClientSession()
            self._ws = await self._http.ws_connect(
                self.url, max_msg_size=16 * 1024 * 1024, heartbeat=20.0)
            async for msg in self._ws:
                if msg.type == WSMsgType.BINARY:
                    self.latest = msg.data
                    self.fresh.set()
                elif msg.type == WSMsgType.TEXT:
                    try:
                        self.status = json.loads(msg.data)
                    except Exception:
                        continue
                    cb = self.on_status
                    if cb is not None:
                        try:
                            cb(self.status)
                        except Exception:
                            pass
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING):
                    break
        except Exception as e:
            logger.info("feed ended: %s", e)
        finally:
            self.closed.set()
            self.fresh.set()  # wake any recv() so it can raise
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            if self._http is not None:
                await self._http.close()

    async def next_frame(self) -> bytes:
        await self.fresh.wait()
        if self.closed.is_set():
            raise MediaStreamError("browser stream closed")
        self.fresh.clear()
        frame = self.latest
        if frame is None:
            raise MediaStreamError("browser stream produced no frame")
        return frame

    async def send_input(self, events: list) -> None:
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_json({"events": events})
        except Exception:
            pass


class TunnelTrack(VideoStreamTrack):
    """VP8 out of the feed's JPEGs. Frame-driven: output fps = paint rate."""

    def __init__(self, feed: BrowserFeed):
        super().__init__()
        self.feed = feed
        self._t0: float | None = None
        # JPEG -> yuv entirely in libavcodec; the PIL/numpy path decoded to
        # RGB and swscaled back to yuv, which at Retina frame sizes was the
        # fps ceiling.
        self._dec = av.CodecContext.create("mjpeg", "r")

    async def recv(self) -> av.VideoFrame:
        # A frame that fails to decode is skipped, not fatal: aiortc's
        # sender coroutine dies silently on a recv() exception, so only the
        # feed closing (MediaStreamError) may propagate.
        while True:
            data = await self.feed.next_frame()
            try:
                frames = self._dec.decode(av.Packet(data))
                if not frames:
                    continue
                frm = frames[0]
                # vpx wants even yuv420p; a size change mid-stream is fine —
                # the encoder re-inits itself, so a window resize stays
                # sharp instead of being squeezed through old geometry.
                w = max(2, frm.width & ~1)
                h = max(2, frm.height & ~1)
                if frm.format.name != "yuv420p" or (frm.width, frm.height) != (w, h):
                    frm = frm.reformat(width=w, height=h, format="yuv420p")
                # Honest wall-clock pts. Frames are paint-driven and arrive
                # at any cadence; VideoStreamTrack.next_timestamp() would
                # stamp a fixed 30fps timeline (sleeping to enforce it
                # during bursts), which is exactly wrong for this track.
                if self._t0 is None:
                    self._t0 = time.monotonic()
                frm.pts = int((time.monotonic() - self._t0) * VIDEO_CLOCK_RATE)
                frm.time_base = VIDEO_TIME_BASE
                return frm
            except Exception as e:
                logger.warning("track: frame error: %r", e)
                continue


class Session:
    def __init__(self, sid: str, pc: RTCPeerConnection, feed: BrowserFeed,
                 feed_task: asyncio.Task):
        self.sid = sid
        self.pc = pc
        self.feed = feed
        self.feed_task = feed_task

    async def close(self) -> None:
        self.feed_task.cancel()
        try:
            await self.pc.close()
        except Exception:
            pass


SESSIONS: dict[str, Session] = {}


def _cors(resp: web.StreamResponse) -> web.StreamResponse:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "content-type"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


async def offer(request: web.Request) -> web.StreamResponse:
    if request.method == "OPTIONS":
        return _cors(web.Response(status=204))
    if len(SESSIONS) >= MAX_SESSIONS:
        return _cors(web.json_response({"error": "gateway full"}, status=503))
    try:
        body = await request.json()
        stream_url = str(body["stream_url"])
        remote = RTCSessionDescription(sdp=body["sdp"], type=body["type"])
    except Exception:
        return _cors(web.json_response({"error": "bad offer"}, status=400))
    if not stream_url.startswith(("ws://", "wss://", "http://", "https://")):
        return _cors(web.json_response({"error": "bad stream_url"}, status=400))
    stream_url = stream_url.replace("http://", "ws://").replace("https://", "wss://")

    sid = uuid.uuid4().hex[:12]
    feed = BrowserFeed(stream_url)
    feed_task = asyncio.create_task(feed.run())
    pc = RTCPeerConnection()
    pc.addTrack(TunnelTrack(feed))
    session = Session(sid, pc, feed, feed_task)
    SESSIONS[sid] = session

    @pc.on("datachannel")
    def on_datachannel(channel):
        # Status rides DOWN this channel (url/title/scroll per paint) and
        # input events ride UP it — the client needs no other connection.
        def push_status(status: dict) -> None:
            if channel.readyState == "open":
                try:
                    channel.send(json.dumps(status))
                except Exception:
                    pass
        feed.on_status = push_status
        if feed.status:
            push_status(feed.status)

        @channel.on("message")
        def on_message(message):
            try:
                payload = json.loads(message)
            except Exception:
                return
            events = payload.get("events") or []
            if events:
                asyncio.ensure_future(feed.send_input(events))

    @pc.on("connectionstatechange")
    async def on_state():
        logger.info("session %s: %s", sid, pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            if SESSIONS.pop(sid, None):
                await session.close()

    # The feed dying (pod gone, page closed) must end the call, not freeze it.
    def feed_done(_):
        if SESSIONS.pop(sid, None):
            asyncio.ensure_future(session.close())
    feed_task.add_done_callback(feed_done)

    await pc.setRemoteDescription(remote)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    # aiortc gathers ICE during setLocalDescription and returns when complete,
    # so the answer below already carries the host candidates.
    return _cors(web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
        "session": sid,
    }))


async def healthz(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "sessions": len(SESSIONS)})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = web.Application()
    app.router.add_route("*", "/offer", offer)
    app.router.add_get("/healthz", healthz)

    async def on_shutdown(_app):
        for session in list(SESSIONS.values()):
            await session.close()
        SESSIONS.clear()

    app.on_shutdown.append(on_shutdown)
    logger.info("gateway on :%d (max %d sessions)", PORT, MAX_SESSIONS)
    web.run_app(app, port=PORT, access_log=None)


if __name__ == "__main__":
    main()
