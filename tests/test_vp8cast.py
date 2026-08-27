"""The browser-cast WebSocket: VP8 out of screencast JPEGs, in-order."""

import asyncio
import io
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from pantheon.toolsets.desktop.vp8cast import Vp8Caster, make_cast_handler


def jpeg_frame(w=320, h=200, shade=200):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (shade, shade, shade)).save(buf, "JPEG", quality=70)
    return buf.getvalue()


class FakeSession:
    def __init__(self):
        self.frame = jpeg_frame(shade=210)
        self.seq = 1
        self.new_frame = asyncio.Condition()


class FakeEngine:
    def __init__(self, session):
        self.pages = {"pg-1": session}
        self.dispatched = []
        self.dispatched_pages = []

    async def call(self, coro):
        return await coro

    async def wait_frame(self, session, since):
        while session.seq <= since:
            async with session.new_frame:
                await asyncio.wait_for(session.new_frame.wait(), timeout=5)
        return True

    async def status_headers(self, session):
        return {"X-Url": "https%3A%2F%2Fexample.com", "X-Seq": str(session.seq)}

    async def acquire_viewer(self, session):
        self.viewers = getattr(self, "viewers", 0) + 1

    async def release_viewer(self, session):
        self.viewers = max(0, getattr(self, "viewers", 0) - 1)

    async def dispatch(self, page_id, events):
        self.dispatched.extend(events)
        self.dispatched_pages.append(page_id)


async def push_frame(session, data):
    session.frame = data
    session.seq += 1
    async with session.new_frame:
        session.new_frame.notify_all()


def decode_vp8(payloads):
    """Feed raw VP8 frames through libvpx to prove the stream is valid."""
    import av

    dec = av.CodecContext.create("libvpx", "r")
    out = []
    for p in payloads:
        out.extend(dec.decode(av.Packet(p)))
    return out


async def run_scenario():
    session = FakeSession()
    engine = FakeEngine(session)
    app = web.Application()
    app.router.add_get("/cast", make_cast_handler(engine))
    client = TestClient(TestServer(app))
    await client.start_server()
    results = {}
    try:
        ws = await client.ws_connect("/cast?page=pg-1")

        meta1 = json.loads((await ws.receive(timeout=5)).data)
        pay1 = (await ws.receive(timeout=5)).data
        results["meta1"] = meta1
        results["pay1"] = pay1

        # Input events dispatch through the same socket.
        await ws.send_json({"events": [{"t": "wheel", "dy": 60}]})

        # A second paint streams as a delta frame.
        await push_frame(session, jpeg_frame(shade=60))
        meta2 = json.loads((await ws.receive(timeout=5)).data)
        pay2 = (await ws.receive(timeout=5)).data
        results["meta2"] = meta2
        results["pay2"] = pay2

        # An explicit keyframe request flips the next frame back to a key.
        await ws.send_json({"keyframe": True})
        await asyncio.sleep(0.05)
        await push_frame(session, jpeg_frame(shade=120))
        meta3 = json.loads((await ws.receive(timeout=5)).data)
        pay3 = (await ws.receive(timeout=5)).data
        results["meta3"] = meta3
        results["pay3"] = pay3

        await asyncio.sleep(0.05)
        results["dispatched"] = list(engine.dispatched)
        await ws.close()
    finally:
        await client.close()
    return results


def test_cast_stream_contract():
    r = asyncio.run(run_scenario())

    assert r["meta1"]["t"] == "frame"
    assert r["meta1"]["key"] is True
    assert r["meta1"]["status"]["url"] == "https%3A%2F%2Fexample.com"
    assert r["meta1"]["w"] == 320 and r["meta1"]["h"] == 200

    assert r["meta2"]["key"] is False  # delta frame, chain unbroken
    assert r["meta2"]["seq"] > r["meta1"]["seq"]
    assert r["meta3"]["key"] is True   # honoured the keyframe request

    frames = decode_vp8([r["pay1"], r["pay2"], r["pay3"]])
    assert len(frames) == 3
    assert frames[0].width == 320 and frames[0].height == 200

    assert r["dispatched"] == [{"t": "wheel", "dy": 60}]


def test_cast_404_on_unknown_page():
    async def run():
        engine = FakeEngine(FakeSession())
        app = web.Application()
        app.router.add_get("/cast", make_cast_handler(engine))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/cast?page=nope")
            return resp.status
        finally:
            await client.close()

    assert asyncio.run(run()) == 404


def test_caster_resize_reinits_on_keyframe():
    caster = Vp8Caster()
    m1, _ = caster.encode(jpeg_frame(320, 200))
    assert m1["key"] is True
    m2, _ = caster.encode(jpeg_frame(320, 200, shade=90))
    assert m2["key"] is False
    # A size change re-inits the encoder — the next frame MUST be a key.
    m3, _ = caster.encode(jpeg_frame(480, 300))
    assert m3["key"] is True
    assert (m3["w"], m3["h"]) == (480, 300)


def test_caster_odd_dimensions_even_cropped():
    caster = Vp8Caster()
    meta, payload = caster.encode(jpeg_frame(321, 201))
    assert (meta["w"], meta["h"]) == (320, 200)
    assert decode_vp8([payload])[0].width == 320


async def run_retarget():
    """One socket, two pages: switching tabs must not need a new call."""
    a = FakeSession()
    a.frame = jpeg_frame(320, 200, shade=210)
    b = FakeSession()
    b.frame = jpeg_frame(480, 300, shade=40)
    engine = FakeEngine(a)
    engine.pages = {"pg-a": a, "pg-b": b}
    app = web.Application()
    app.router.add_get("/cast", make_cast_handler(engine))
    client = TestClient(TestServer(app))
    await client.start_server()
    out = {}
    try:
        ws = await client.ws_connect("/cast?page=pg-a")
        out["first"] = json.loads((await ws.receive(timeout=5)).data)
        await ws.receive(timeout=5)  # its payload

        await ws.send_json({"page": "pg-b"})
        await asyncio.sleep(0.1)
        await push_frame(b, jpeg_frame(480, 300, shade=90))
        out["second"] = json.loads((await ws.receive(timeout=5)).data)
        await ws.receive(timeout=5)

        # Input after a retarget must reach the NEW page, not the old one.
        await ws.send_json({"events": [{"t": "wheel", "dy": 40}]})
        await asyncio.sleep(0.05)
        out["dispatched_to"] = list(engine.dispatched_pages)
        out["closed"] = ws.closed
        await ws.close()
    finally:
        await client.close()
    return out


def test_a_live_socket_can_be_pointed_at_another_page():
    r = asyncio.run(run_retarget())

    assert (r["first"]["w"], r["first"]["h"]) == (320, 200)
    # The second page's frames, on the same socket that never closed.
    assert (r["second"]["w"], r["second"]["h"]) == (480, 300)
    assert r["second"]["key"] is True, "a different page needs a key frame"
    assert r["closed"] is False
    assert r["dispatched_to"] == ["pg-b"], "input followed the retarget"
