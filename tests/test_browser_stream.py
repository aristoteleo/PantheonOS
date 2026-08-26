"""The browser-stream WebSocket: frames push, input rides back."""

import asyncio
import json

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from pantheon.toolsets.desktop.browser import make_stream_handler


class FakeSession:
    def __init__(self):
        self.frame = b"\xff\xd8jpeg-one"
        self.seq = 1
        self.new_frame = asyncio.Condition()


class FakeEngine:
    def __init__(self, session):
        self.pages = {"pg-1": session}
        self.dispatched = []
        self._session = session

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


async def push_frame(session, data):
    session.frame = data
    session.seq += 1
    async with session.new_frame:
        session.new_frame.notify_all()


async def run_scenario():
    session = FakeSession()
    engine = FakeEngine(session)
    app = web.Application()
    app.router.add_get("/stream", make_stream_handler(engine))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/stream?page=pg-1")

        # The first paint arrives as status JSON + frame bytes.
        status = json.loads((await ws.receive(timeout=5)).data)
        assert status["t"] == "status" and status["seq"] == 1
        frame = await ws.receive(timeout=5)
        assert frame.type == WSMsgType.BINARY and frame.data.startswith(b"\xff\xd8")

        # Input rides back on the same socket.
        await ws.send_str(json.dumps({"events": [{"t": "move", "x": 1, "y": 2}]}))

        # A new paint pushes without any request.
        await push_frame(session, b"\xff\xd8jpeg-two")
        status2 = json.loads((await ws.receive(timeout=5)).data)
        assert status2["seq"] == 2
        frame2 = await ws.receive(timeout=5)
        assert frame2.data.endswith(b"jpeg-two")

        await ws.close()
        # Give the input path a beat to land.
        for _ in range(20):
            if engine.dispatched:
                break
            await asyncio.sleep(0.05)
        assert engine.dispatched and engine.dispatched[0]["t"] == "move"

        # An unknown page 404s before upgrading.
        resp = await client.get("/stream?page=nope")
        assert resp.status == 404
    finally:
        await client.close()


def test_stream_pushes_frames_and_accepts_input():
    asyncio.run(run_scenario())
