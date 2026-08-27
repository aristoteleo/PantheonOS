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
import ipaddress
import json
import logging
import os
import time
import uuid

import aiohttp
from aiohttp import WSMsgType, web

from aiortc import (
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.codecs import vpx
from aiortc.mediastreams import (
    VIDEO_CLOCK_RATE,
    VIDEO_TIME_BASE,
    MediaStreamError,
    MediaStreamTrack,
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
        await self.send_control({"events": events})

    async def send_control(self, payload: dict) -> None:
        """Anything the viewer says to the sandbox: input, keyframe, page."""
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_json(payload)
        except Exception:
            pass


class CastFeed:
    """browser-cast: the sandbox already encoded the video; we only relay.

    Per frame the pod sends one JSON meta line then one binary payload.
    Frames are in-order and inter-dependent, so dropping one costs the
    decoder its chain until the next keyframe — but a full queue must NOT
    take the feed down with it. It did: nothing consumes this queue until
    the answer is negotiated, and at 60 fps a 240-slot queue fills in the
    four seconds that takes, so every call killed its own feed and then
    failed with "RTCPeerConnection is closed". Now the oldest frames go,
    and the pod is asked for a keyframe to rebuild the chain.
    """

    def __init__(self, cast_url: str):
        self.url = cast_url
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=240)
        self.closed = asyncio.Event()
        self.status: dict = {}
        self.on_status = None
        self.started = asyncio.Event()
        # Which codec the sandbox is producing. Relayed packets are never
        # re-encoded, so this decides the answer's codec preference — and a
        # pod that predates the field is VP8, which is what it always was.
        self.codec = "vp8"
        self._chain_broken = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None

    async def run(self) -> None:
        meta: dict | None = None
        try:
            self._http = aiohttp.ClientSession()
            self._ws = await self._http.ws_connect(
                self.url, max_msg_size=16 * 1024 * 1024, heartbeat=20.0)
            async for msg in self._ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        parsed = json.loads(msg.data)
                    except Exception:
                        continue
                    if parsed.get("t") == "frame":
                        meta = parsed
                        self.codec = str(parsed.get("codec") or self.codec)
                        status = parsed.get("status")
                        if status:
                            self.status = status
                            cb = self.on_status
                            if cb is not None:
                                try:
                                    cb(status)
                                except Exception:
                                    pass
                elif msg.type == WSMsgType.BINARY:
                    if meta is None:
                        continue
                    while True:
                        try:
                            self.queue.put_nowait((meta, msg.data))
                            break
                        except asyncio.QueueFull:
                            try:
                                self.queue.get_nowait()
                            except Exception:
                                break
                            self._chain_broken = True
                    if self._chain_broken and self.queue.qsize() < 8:
                        # Caught up: ask for a clean starting point.
                        self._chain_broken = False
                        await self.request_keyframe()
                    meta = None
                    self.started.set()
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING):
                    break
        except Exception as e:
            logger.info("cast feed ended: %s", e)
        finally:
            self.closed.set()
            self.started.set()
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
            if self._http is not None:
                await self._http.close()

    async def send_input(self, events: list) -> None:
        await self.send_control({"events": events})

    async def send_control(self, payload: dict) -> None:
        """Anything the viewer says to the sandbox: input, keyframe, page."""
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send_json(payload)
        except Exception:
            pass

    async def request_keyframe(self) -> None:
        await self.send_control({"keyframe": True})


class CastTrack(MediaStreamTrack):
    """Yields av.Packet — aiortc's sender packs pre-encoded data as-is."""

    kind = "video"

    def __init__(self, feed: CastFeed):
        super().__init__()
        self.feed = feed

    async def recv(self) -> av.Packet:
        get = asyncio.ensure_future(self.feed.queue.get())
        closed = asyncio.ensure_future(self.feed.closed.wait())
        done, _ = await asyncio.wait({get, closed}, return_when=asyncio.FIRST_COMPLETED)
        if get in done:
            closed.cancel()
            meta, payload = get.result()
            packet = av.Packet(payload)
            packet.pts = meta["pts"]
            packet.time_base = VIDEO_TIME_BASE
            return packet
        get.cancel()
        raise MediaStreamError("cast feed closed")


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
        # The keyframe-forwarding poller. Owned here so it dies with the
        # session: leaked pollers are what turned a gateway that had been
        # up for hours into one that answered every offer with a dead call
        # (and filled the log with aiortc complaining about closed
        # transports). Restarting the pod "fixed" it, which is the shape of
        # a leak.
        self.pli_task: asyncio.Task | None = None

    async def close(self) -> None:
        self.feed_task.cancel()
        if self.pli_task is not None:
            self.pli_task.cancel()
            self.pli_task = None
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

    # Prefer the sandbox's own VP8 (browser-cast): ~5x fewer bytes through
    # the ~20 Mbps tunnel and no transcode here. Old pods without the
    # endpoint fall back to the JPEG feed transparently.
    feed = None
    feed_task = None
    track = None
    cast_url = stream_url.replace("browser-stream", "browser-cast")
    if cast_url != stream_url:
        cf = CastFeed(cast_url)
        cf_task = asyncio.create_task(cf.run())
        try:
            await asyncio.wait_for(cf.started.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass
        if cf.started.is_set() and not cf.closed.is_set():
            feed, feed_task, track = cf, cf_task, CastTrack(cf)
            logger.info("session %s: sandbox cast (%s)", sid, cf.codec)
        else:
            cf_task.cancel()
    if feed is None:
        bf = BrowserFeed(stream_url)
        feed_task = asyncio.create_task(bf.run())
        feed, track = bf, TunnelTrack(bf)
        logger.info("session %s: JPEG transcode fallback", sid)

    pc = RTCPeerConnection()
    pc.addTrack(track)
    session = Session(sid, pc, feed, feed_task)
    SESSIONS[sid] = session
    session_closed = asyncio.Event()

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
            # Switching tabs points the SAME capture at another page rather
            # than negotiating a new call for it: every page already has a
            # window, rendered and current, so this is a frame's work.
            page = payload.get("page")
            if page:
                asyncio.ensure_future(feed.send_control({"page": str(page)}))

    async def forward_keyframe_requests() -> None:
        """Pass the browser's 'I lost the picture' up to the sandbox.

        A receiver that misses the keyframe a call starts with — a packet
        lost during ICE, a decoder reset — asks for a new one with a PLI.
        aiortc handles that by telling ITS encoder to make a keyframe, but
        we do not encode here; the sandbox does. Without this the browser
        asks forever and shows nothing, which looks exactly like a dead
        stream. The flag is private, so read it defensively and give up
        quietly if a future aiortc renames it.
        """
        attr = "_RTCRtpSender__force_keyframe"
        while not session_closed.is_set():
            await asyncio.sleep(0.15)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                return
            for sender in pc.getSenders():
                if getattr(sender, attr, False):
                    try:
                        setattr(sender, attr, False)
                    except Exception:
                        return
                    if hasattr(feed, "request_keyframe"):
                        await feed.request_keyframe()

    @pc.on("connectionstatechange")
    async def on_state():
        logger.info("session %s: %s", sid, pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            session_closed.set()
            if SESSIONS.pop(sid, None):
                await session.close()
        elif pc.connectionState == "connected":
            # Whatever piled up before negotiation is behind us; start the
            # viewer on a keyframe rather than mid-chain.
            if hasattr(feed, "request_keyframe"):
                asyncio.ensure_future(feed.request_keyframe())
            # Once per session, never per reconnection.
            if session.pli_task is None:
                session.pli_task = asyncio.ensure_future(forward_keyframe_requests())

    # The feed dying (pod gone, page closed) must end the call, not freeze it.
    def feed_done(_):
        if SESSIONS.pop(sid, None):
            asyncio.ensure_future(session.close())
    feed_task.add_done_callback(feed_done)

    # Relayed packets are never re-encoded, so the codec the sandbox chose
    # MUST win the negotiation: aiortc packetizes either one from a raw
    # av.Packet (Vp8Encoder.pack / H264Encoder.pack), and the wrong choice
    # hands the browser H.264 bytes under a VP8 payload type — packets
    # arrive, nothing ever decodes.
    #
    # This must run BEFORE setRemoteDescription: that is where aiortc
    # freezes the negotiated codec list (rtcpeerconnection.py applies
    # preferences while handling the remote description, not at
    # createAnswer time). Setting them afterwards is silently ignored.
    wanted = f"video/{getattr(feed, 'codec', 'vp8')}".lower()
    for transceiver in pc.getTransceivers():
        if transceiver.kind == "video":
            caps = RTCRtpSender.getCapabilities("video")
            prefs = [c for c in caps.codecs
                     if c.mimeType.lower() in (wanted, "video/rtx")]
            if prefs:
                transceiver.setCodecPreferences(prefs)
            else:
                logger.warning("session %s: no %s support; leaving defaults",
                               sid, wanted)

    await pc.setRemoteDescription(remote)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    # aiortc gathers ICE during setLocalDescription and returns when complete,
    # so the answer below already carries the host candidates.
    return _cors(web.json_response({
        "sdp": reachable_candidates_only(pc.localDescription.sdp),
        "type": pc.localDescription.type,
        "session": sid,
    }))


_PRIVATE_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),   # carrier-grade NAT, k8s overlays
    ipaddress.ip_network("169.254.0.0/16"),
)


def reachable_candidates_only(sdp: str) -> str:
    """Offer only addresses a viewer on the internet could ever reach.

    This host has one public address and half a dozen cluster-internal
    ones, and aiortc advertises them all. A browser then spends its ICE
    checks on 10.x and 100.64.x addresses that cannot answer, and the pair
    that would have worked gets tried late or after the browser has given
    up — which shows as an occasional call that never connects and a window
    that silently drops to the long-poll path for the rest of its life.

    If filtering would leave nothing at all, keep the original: a stream
    with a poor candidate list beats no stream.
    """
    kept, dropped = [], 0
    for line in sdp.splitlines():
        if line.startswith("a=candidate:"):
            parts = line.split()
            try:
                addr = ipaddress.ip_address(parts[4])
            except (IndexError, ValueError):
                kept.append(line)
                continue
            if any(addr in net for net in _PRIVATE_NETS):
                dropped += 1
                continue
        kept.append(line)
    if not any(l.startswith("a=candidate:") for l in kept):
        return sdp
    if dropped:
        logger.info("gateway: dropped %d unreachable candidate(s)", dropped)
    return "\r\n".join(kept) + "\r\n"


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
