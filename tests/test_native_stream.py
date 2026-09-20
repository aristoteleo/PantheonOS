import asyncio
import io
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from PIL import Image
from pantheon.apps.native_stream import Runtime, Session, validate_input
from pantheon.apps.native_stream.helper import Helper
from pantheon.apps.native_stream.input import batch


def fixture():
    runtime = Runtime(SimpleNamespace(app_id='browser'))
    helper = SimpleNamespace(command=AsyncMock(return_value={'windows': []}))
    session = Session('one', 'win-1', SimpleNamespace(poll=lambda: None), helper=helper, main=901)
    meta = {'wid': 1, 'windowClass': 'pantheon-page-one', 'title': 'fixture',
            'geometry': {'x': 0, 'y': 0, 'w': 480, 'h': 320}, 'transientFor': None, 'overrideRedirect': False}
    runtime.windows[1] = (session, 901, meta)
    runtime.sessions['one'] = session
    session.windows[901] = 1
    return runtime, session


def jpeg():
    output = io.BytesIO()
    Image.new('RGB', (20, 10), 'green').save(output, 'JPEG')
    return output.getvalue()


@pytest.mark.asyncio
async def test_socket_authentication_ownership_frames_and_disconnect_release():
    runtime, session = fixture()
    app = web.Application()
    app.router.add_get('/native-stream', runtime.websocket)
    async with TestServer(app) as server, ClientSession() as client:
        wrong = await client.ws_connect(server.make_url('/native-stream'))
        await wrong.send_json({'password': 'wrong'})
        await wrong.receive()
        assert wrong.close_code == 1008
        assert not runtime.clients
        ws = await client.ws_connect(server.make_url('/native-stream'))
        await ws.send_json({'password': runtime.secret})
        assert (await ws.receive_json())['event'] == 'ready'
        frame = jpeg()
        runtime.frame(session, 901, frame)
        data = await ws.receive_bytes()
        assert data[:4] == struct.pack('>I', 1) and data[4:] == frame
        runtime.frame(session, 999, b'foreign')
        assert set(session.frames) == {1}
        await ws.send_json({'op': 'close', 'wid': 999})
        assert (await ws.receive_json())['event'] == 'error'
        session.helper.command.assert_not_awaited()
        await ws.send_json({'op': 'input', 'wid': 1, 'kind': 'key', 'code': 'ShiftLeft', 'down': True})
        for _ in range(100):
            if session.helper.command.await_count: break
            await asyncio.sleep(.01)
        await ws.close()
        for _ in range(100):
            if not runtime.clients: break
            await asyncio.sleep(.01)
        assert session.helper.command.await_count == 2
        assert session.helper.command.await_args.kwargs['window'] == 901
        assert session.helper.command.await_args.kwargs['down'] is False


@pytest.mark.asyncio
async def test_inventory_removes_frames_and_reports_close_without_stopping_app():
    runtime, session = fixture()
    session.frames[1] = jpeg()
    await runtime.inventory(session)
    assert runtime.windows == {} and not session.frames and not session.windows
    session.helper.command.assert_any_await('uncapture', window=901)
    assert session.process.poll() is None


def test_latest_frame_queue_is_bounded_and_rejects_foreign_frames():
    runtime, session = fixture()
    client = {'events': asyncio.Queue(), 'frames': {}, 'wake': asyncio.Event(), 'overflow': False}
    # Runtime uses identity-hashable connection records (not a dict set).
    runtime.clients = {1: client}
    for i in range(1000): runtime.frame(session, 901, str(i).encode())
    assert client['frames'] == {1: b'999'}
    for _ in range(200): runtime.event('metadata')
    assert client['events'].qsize() == 128 and client['overflow']


@pytest.mark.asyncio
async def test_stop_guard_preserves_save_dialog():
    runtime, session = fixture()
    session.helper.command.return_value = {'windows': [{'id': 902, 'title': 'Save?'}]}
    with pytest.raises(RuntimeError, match='Save/Cancel'):
        await runtime.before_stop()


def test_native_actions_use_screenshot_coordinates_and_validate_whole_batch():
    events = batch([{'type': 'click', 'x': 10, 'y': 5}, {'type': 'key', 'key': 'Ctrl+s'}], 20, 10)
    assert events[0]['x'] == .5 and events[0]['y'] == .5
    assert [e['code'] for e in events if e['kind'] == 'key'] == ['ControlLeft', 'KeyS', 'KeyS', 'ControlLeft']
    assert events[-1]['down'] is False and events[-1]['modifiers'] == []
    with pytest.raises(ValueError, match='outside'):
        batch([{'type': 'click', 'x': 10, 'y': 5}, {'type': 'click', 'x': 30, 'y': 5}], 20, 10)
    with pytest.raises(ValueError): validate_input({'kind': 'pointer', 'phase': 'down', 'x': float('nan')})
    with pytest.raises(ValueError): validate_input({'kind': 'capture', 'window': 99})


@pytest.mark.asyncio
async def test_helper_reader_rejects_oversize_without_allocating_frame():
    output = asyncio.StreamReader()
    output.feed_data(struct.pack('>I', 999_999_999))
    errors = []
    helper = Helper(SimpleNamespace(stdout=output), lambda *_: pytest.fail('unexpected frame'), errors.append)
    await helper.reader
    assert errors == ['Invalid native capture packet length']


@pytest.mark.asyncio
async def test_helper_reader_demultiplexes_json_and_owned_frames():
    output = asyncio.StreamReader()
    frames = []
    helper = Helper(SimpleNamespace(stdout=output), lambda *args: frames.append(args), lambda _: None)
    future = asyncio.get_running_loop().create_future()
    helper.pending['1'] = future
    response = b'\x01' + json.dumps({'id': '1', 'ok': True}).encode()
    image = b'\x02' + struct.pack('>Q', 901) + jpeg()
    for packet in (response, image): output.feed_data(struct.pack('>I', len(packet)) + packet)
    output.feed_eof()
    await helper.reader
    assert (await future)['ok']
    assert frames[0][0] == 901 and frames[0][1].startswith(b'\xff\xd8')
