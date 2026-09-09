"""A user-visible screenshot must travel through the anchored browser viewport."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.apps.builtin.desktop import toolset


@pytest.fixture
def desktop(monkeypatch):
    instance = DesktopToolSet()
    monkeypatch.setattr(instance, '_presence', lambda: SimpleNamespace(
        anchor_for=lambda _: {'viewport_id': 'viewport', 'reason': 'hosts this chat'}))
    monkeypatch.setattr(instance, '_chat_id', lambda: '')
    monkeypatch.setattr(instance, '_package_screenshot', lambda data, stem, **kwargs: {'success': True, 'pixels': data})
    monkeypatch.setattr(instance, '_native_target', AsyncMock(side_effect=AssertionError('must capture the user browser')))
    return instance


@pytest.mark.asyncio
async def test_screenshot_waits_for_browser_authorization_then_returns_its_frame(desktop, monkeypatch):
    sent = []
    wait_for = asyncio.wait_for
    async def scaled_wait(future, timeout):
        return await wait_for(future, timeout / 1000)
    monkeypatch.setattr(asyncio, 'wait_for', scaled_wait)
    async def publish(event):
        sent.append(event)
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    pending = asyncio.create_task(desktop.desktop_screenshot('win-1'))
    await asyncio.sleep(0.04)  # Beyond the old 25 second budget, scaled down.
    assert not pending.done()
    event = sent[0]
    assert event['viewport_id'] == 'viewport'
    assert event['timeout_ms'] == 170000
    assert (await desktop.report_snapshot(event['request_id'], True, 'data:image/png;base64,frame', source='browser-region-capture'))['success']
    result = await pending
    assert result['source'] == 'browser-region-capture'
    assert result['pixels'] == 'data:image/png;base64,frame'
    assert result['window_id'] == 'win-1'
    assert not desktop._pending_snapshots
    desktop._native_target.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_authorization_does_not_fall_back_to_native_export(desktop, monkeypatch):
    async def publish(event):
        if event['type'] == 'desktop.snapshot':
            await desktop.report_snapshot(event['request_id'], False, error='User declined sharing')
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    assert await desktop.desktop_screenshot('win-1') == {'success': False, 'error': 'User declined sharing'}
    desktop._native_target.assert_not_awaited()
    assert not desktop._pending_snapshots


@pytest.mark.asyncio
@pytest.mark.parametrize('end', ['timeout', 'cancel'])
async def test_expired_or_stopped_tool_dismisses_frontend_authorization(desktop, monkeypatch, end):
    sent = []
    async def publish(event):
        sent.append(event)
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    monkeypatch.setattr(toolset, 'SNAPSHOT_TIMEOUT_SECONDS', .01)
    task = asyncio.create_task(desktop.desktop_screenshot('win-1'))
    await asyncio.sleep(0)
    if end == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert not (await task)['success']
    assert not desktop._pending_snapshots
    assert sent[-1]['type'] == 'desktop.snapshot.cancel'
    assert sent[0]['request_id'] == sent[-1]['request_id']
    late = await desktop.report_snapshot(sent[0]['request_id'], True, 'late pixels')
    assert not late['success']


@pytest.mark.asyncio
async def test_undelivered_request_does_not_wait_for_permission(desktop, monkeypatch):
    monkeypatch.setattr(desktop, '_publish_desktop', AsyncMock(return_value=False))
    assert 'could not be delivered' in (await desktop.desktop_screenshot('win-1'))['error']
    assert not desktop._pending_snapshots


@pytest.mark.asyncio
async def test_old_app_canvas_export_is_not_labelled_as_a_browser_screenshot(desktop, monkeypatch):
    async def publish(event):
        if event['type'] == 'desktop.snapshot':
            await desktop.report_snapshot(event['request_id'], True, 'data:image/png;base64,export')
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    result = await desktop.desktop_screenshot('win-1')
    assert not result['success']
    assert 'frontend refresh' in result['error']


@pytest.mark.asyncio
async def test_native_input_export_requires_explicit_source(desktop, monkeypatch):
    from pantheon.apps.builtin.desktop.native_control import NativeWindowController
    async def call(coro):
        return await coro
    monkeypatch.setattr(desktop, '_native_target', AsyncMock(return_value=(
        SimpleNamespace(call=call), {'xid': 123}, [])))
    monkeypatch.setattr(NativeWindowController, 'screenshot', AsyncMock(return_value={'data_url': 'native-image'}))
    result = await desktop.desktop_screenshot('win-1', source='native')
    assert result['source'] == 'native-application-export'
    assert result['coordinate_space'] == 'native-window-pixels'
    assert result['pixels'] == 'native-image'
