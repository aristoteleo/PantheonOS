"""Real ipykernel comm tests, including idle callbacks and binary canvas data."""
import asyncio
import base64
import importlib.util
import json
import sys
from pathlib import Path
import uuid

import pytest
from jupyter_client import AsyncKernelManager

spec = importlib.util.spec_from_file_location("notebook_widgets", Path(__file__).parents[1] / "apps/notebook/widgets.py")
widgets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(widgets)


@pytest.fixture
async def kernel():
    km = AsyncKernelManager()
    km.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}']
    await km.start_kernel()
    client = km.client()
    client.start_channels()
    await client.wait_for_ready(timeout=30)
    bridge = await widgets.WidgetBridge.create(km)
    try:
        yield client, bridge
    finally:
        await bridge.close()
        client.stop_channels()
        await km.shutdown_kernel(now=True)


async def execute(client, code):
    messages = []
    reply = await client.execute_interactive(code, output_hook=messages.append, timeout=30)
    assert reply["content"]["status"] == "ok", reply['content']
    return messages


async def receive(bridge, cursor=0, until=lambda messages: bool(messages)):
    messages, parts = [], []
    for _ in range(40):
        result = await bridge.poll(cursor, bridge.generation, wait=.1)
        assert not result["reset"]
        cursor = result["cursor"]
        for frame in result["frames"]:
            if frame["index"] == 0:
                parts = []
            parts.append(frame["data"])
            if len(parts) == frame["total"]:
                messages.append(json.loads(''.join(parts)))
        if until(messages):
            return cursor, messages
    raise AssertionError(messages)


def send(bridge, comm, data, buffers=None):
    return bridge.send(bridge.generation, {
        "msg_type": "comm_msg", "msg_id": uuid.uuid4().hex,
        "content": {"comm_id": comm, "data": data}, "buffers": buffers or [],
    })


def test_canvas_batches_keep_the_correct_target_after_history_eviction():
    bridge = widgets.WidgetBridge(None)
    bridge._canvas_message({'header': {'msg_type': 'comm_open'}, 'content': {
        'comm_id': 'manager', 'data': {'state': {'_model_module': 'ipycanvas',
        '_model_module_version': '^0.14', '_model_name': 'CanvasManagerModel'}}}})
    def draw(commands, buffers=()):
        payload = json.dumps(commands).encode()
        return bridge._canvas_message({'header': {'msg_type': 'comm_msg'}, 'content': {
            'comm_id': 'manager', 'data': {'method': 'custom',
            'content': {'dtype': 'uint8', 'shape': [len(payload)]}}}, 'buffers': [payload, *buffers]})
    draw([60, ['IPY_MODEL_first']])
    switched = draw([[43, [], 0], [60, ['IPY_MODEL_second']], [32, [], 1]], [b'pixels'])
    assert json.loads(switched['buffers'][0])[0] == [60, ['IPY_MODEL_first']]
    assert switched['buffers'][1:] == [b'pixels']
    bridge.frames.clear()  # The active target survives the bounded journal.
    latest = draw([43, [], 0])
    assert json.loads(latest['buffers'][0]) == [[60, ['IPY_MODEL_second']], [43, [], 0]]
    assert latest['content']['data']['content']['shape'] == [len(latest['buffers'][0])]


@pytest.mark.asyncio
async def test_widgets_receive_input_after_cell_idle_and_stream_output(kernel):
    client, bridge = kernel
    result = await execute(client, """
import ipywidgets as w
from IPython.display import display
slider = w.IntSlider(value=3)
label = w.Label(value='3')
button = w.Button(description='Step')
out = w.Output()
def step(_):
    slider.value += 1
    label.value = str(slider.value)
    with out:
        print('clicked', slider.value)
button.on_click(step)
display(w.VBox([slider, label, button, out]))
""")
    assert any('application/vnd.jupyter.widget-view+json' in m['content'].get('data', {}) for m in result)
    cursor, messages = await receive(bridge, until=lambda ms: any(m['header']['msg_type'] == 'display_data' for m in ms))
    models = {m['content']['data']['state']['_model_name']: m['content']['comm_id']
              for m in messages if m['header']['msg_type'] == 'comm_open'}
    assert models['VBoxModel'] in await bridge.info()
    send(bridge, models['ButtonModel'], {"method": "custom", "content": {"event": "click"}})
    cursor, updates = await receive(bridge, cursor, lambda ms: any('clicked' in m['content'].get('text', '') for m in ms))
    assert any(m['content'].get('data', {}).get('state', {}).get('value') == 4 for m in updates)
    send(bridge, models['IntSliderModel'], {"method": "update", "state": {"value": 27}, "buffer_paths": []})
    result = await execute(client, "assert slider.value == 27")
    # Another reader can independently replay the same widget history.
    _, replay = await receive(bridge)
    assert replay[0]['header']['msg_type'] == 'comm_open'


@pytest.mark.asyncio
async def test_canvas_and_binary_roundtrip_chunking(kernel):
    client, bridge = kernel
    await execute(client, """
import ipywidgets as w
from ipycanvas import Canvas, hold_canvas
from IPython.display import display
canvas = Canvas(width=160, height=100)
with hold_canvas():
    canvas.fill_style = 'red'
    canvas.fill_rect(4, 4, 32, 32)
binary = w.Widget()
def echo(widget, content, buffers):
    widget.send({'echo': True}, buffers=buffers)
binary.on_msg(echo)
display(canvas)
""")
    cursor, messages = await receive(bridge, until=lambda ms: any(m['header']['msg_type'] == 'display_data' for m in ms))
    assert any(m.get('buffers') for m in messages), 'Canvas commands must keep their binary payload'
    comm = next(m['content']['comm_id'] for m in messages if m['header']['msg_type'] == 'comm_open'
                and m['content']['data']['state']['_model_name'] == 'WidgetModel')
    data = base64.b64encode(bytes(range(256)) * 1000).decode()
    send(bridge, comm, {"method": "custom", "content": {}}, [data])
    _, replies = await receive(bridge, cursor, lambda ms: any(m['content'].get('data', {}).get('content', {}).get('echo') for m in ms))
    echo = next(m for m in replies if m['content'].get('data', {}).get('content', {}).get('echo'))
    assert echo['buffers'] == [data]


@pytest.mark.asyncio
async def test_generations_bounds_and_restricted_messages(kernel):
    _, bridge = kernel
    with pytest.raises(ValueError, match='Kernel changed'):
        bridge.send('old-generation', {})
    with pytest.raises(ValueError, match='Only widget'):
        bridge.send(bridge.generation, {'msg_type': 'execute_request', 'content': {'code': 'bad'}})
    with pytest.raises(ValueError, match='Unsupported widget'):
        bridge.send(bridge.generation, {'msg_type': 'comm_open', 'content': {'comm_id': 'x', 'target_name': 'other'}})
    for i in range(widgets.MAX_FRAMES + 4):
        bridge._record({'header': {'msg_id': str(i)}, 'content': {}})
    assert (await bridge.poll(0, bridge.generation, wait=0))['reset']
    assert (await bridge.poll(bridge.cursor, 'old-generation', wait=0))['reset']
    assert len(bridge.frames) == widgets.MAX_FRAMES
    await bridge.close()
    assert all(t.done() for t in bridge.tasks)


@pytest.mark.asyncio
async def test_failed_widget_channel_start_does_not_leave_kernel_running(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from pantheon.apps.builtin.notebook import jupyter_kernel
    manager = AsyncKernelManager()
    manager.kernel_spec.argv = [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}']
    monkeypatch.setattr(jupyter_kernel, 'AsyncKernelManager', lambda **kwargs: manager)
    monkeypatch.setattr(jupyter_kernel.WidgetBridge, 'create', AsyncMock(side_effect=RuntimeError('channel unavailable')))
    toolset = jupyter_kernel.JupyterKernelToolSet('widget-failure', workdir=str(tmp_path))
    try:
        result = await toolset.create_session(kernel_session_id='failed')
        assert not result['success'] and 'channel unavailable' in result['error']
        assert not await manager.is_alive()
        assert not toolset.sessions and not toolset.clients and not toolset.widget_bridges
    finally:
        if await manager.is_alive():
            await manager.shutdown_kernel(now=True)
