"""An open failure must keep the window identity available for recovery."""
import asyncio
from unittest.mock import Mock
import pytest
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet

@pytest.mark.asyncio
async def test_failed_open_retains_existing_window():
    desktop = DesktopToolSet()
    desktop._presence = lambda: Mock(anchor_for=lambda _: {'viewport_id': 'v', 'reason': 'test'})
    desktop._chat_id = lambda: ''
    async def respond(event):
        await desktop.report_desktop_result(event['request_id'], ok=False,
            value={'window_id': 'win-7', 'status': 'error'}, error='image failed to load')
    desktop._publish_desktop = respond
    result = await desktop._desktop_request('desktop.open', {'app': 'image-viewer'})
    assert result == {'success': False, 'error': 'image failed to load',
                      'result': {'window_id': 'win-7', 'status': 'error'}}
    assert desktop._pending_desktop == {}

@pytest.mark.asyncio
async def test_ordinary_failure_does_not_invent_window():
    desktop = DesktopToolSet()
    future = asyncio.get_running_loop().create_future()
    desktop._pending_desktop['r'] = future
    await desktop.report_desktop_result('r', ok=False, error='unsupported action')
    with pytest.raises(RuntimeError, match='unsupported action'):
        await future


@pytest.mark.asyncio
async def test_stopped_global_control_cancels_remaining_frontend_actions():
    desktop = DesktopToolSet()
    desktop._presence = lambda: Mock(anchor_for=lambda _: {'viewport_id': 'v'})
    desktop._chat_id = lambda: ''
    sent = []
    async def publish(ev):
        sent.append(ev)
        return True
    desktop._publish_desktop = publish
    task = asyncio.create_task(desktop._desktop_request('desktop.control', {'actions': [{'type': 'launcher', 'open': True}]}))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent[0]['deadline_ms'] > 0
    assert sent[-1]['type'] == 'desktop.control.cancel'
    assert sent[0]['request_id'] == sent[-1]['request_id']
    assert not desktop._pending_desktop
