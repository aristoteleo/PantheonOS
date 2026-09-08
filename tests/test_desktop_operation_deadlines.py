"""Cold native operations must outlive the former 30/60s desktop deadline."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


@pytest.fixture
def desktop(monkeypatch):
    instance = DesktopToolSet()
    monkeypatch.setattr(instance, '_presence', lambda: SimpleNamespace(
        anchor_for=lambda _: {'viewport_id': 'viewport', 'reason': 'hosts this chat'}))
    monkeypatch.setattr(instance, '_chat_id', lambda: '')
    monkeypatch.setattr(instance, '_desktop_window', lambda _: {'app_id': 'browser'})
    return instance


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['newPage', 'navigate', 'update', 'set'])
async def test_cold_browser_finishes_after_the_old_request_deadline(desktop, monkeypatch, operation):
    # Scale only the outer deadline: an 80ms response represents 80s, past
    # both old budgets. Exercise the real pending-request/report handshake.
    wait_for = asyncio.wait_for
    async def scaled_wait(future, timeout):
        return await wait_for(future, timeout=timeout / 1000)
    monkeypatch.setattr(asyncio, 'wait_for', scaled_wait)
    tasks = []
    async def publish(event):
        async def finish():
            await asyncio.sleep(0.08)
            return await desktop.report_desktop_result(event['request_id'], value={'page_id': 'same-page'})
        tasks.append(asyncio.create_task(finish()))
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    try:
        if operation == 'update':
            result = await desktop.desktop_update('win-1', {'url': 'https://example.org'})
        elif operation == 'set':
            result = await desktop.desktop_set('win-1', {'url': 'https://example.org'})
        else:
            result = await desktop.desktop_call('win-1', operation, {'url': 'https://example.org'})
        assert result == {'success': True, 'result': {'page_id': 'same-page'}}
        assert desktop._pending_desktop == {}
        assert (await tasks[0])['success'] is True
    finally:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_publish_failure_returns_immediately_and_cleans_request(desktop):
    desktop._nats = SimpleNamespace(publish_stream=AsyncMock(side_effect=ConnectionError('disconnected')))
    result = await desktop._desktop_request('desktop.call', {'window_id': 'win-1'}, timeout=0.01)
    assert not result['success'] and 'could not be delivered' in result['error']
    assert 'completion' not in result  # No action was delivered.
    assert not desktop._pending_desktop


@pytest.mark.asyncio
async def test_timeout_preserves_unknown_outcome_and_rejects_late_reply(desktop, monkeypatch):
    sent = []
    async def publish(event):
        sent.append(event)
        return True
    monkeypatch.setattr(desktop, '_publish_desktop', publish)
    result = await desktop._desktop_request('desktop.call', {'window_id': 'win-1', 'action': 'newPage'}, timeout=0.001)
    assert not result['success']
    assert result['completion'] == 'unknown'
    assert result['window_id'] == 'win-1' and result['action'] == 'newPage'
    assert result['request_id'] == sent[0]['request_id']
    assert 'before retrying' in result['error']
    assert not desktop._pending_desktop
    late = await desktop.report_desktop_result(result['request_id'], value={'page_id': 'eventually-opened'})
    assert late['success'] is False
    assert len(sent) == 1  # Never automatically repeat a timed-out mutation.


@pytest.mark.asyncio
async def test_cancelled_pending_future_is_not_acknowledged_as_success(desktop):
    future = asyncio.get_running_loop().create_future()
    desktop._pending_desktop['cancelled'] = future
    future.cancel()
    assert (await desktop.report_desktop_result('cancelled'))['success'] is False


@pytest.mark.asyncio
async def test_page_created_but_not_shown_is_not_reported_as_success(desktop, monkeypatch):
    async def call(coro):
        return await coro
    engine = SimpleNamespace(call=call, open_page=AsyncMock(return_value=SimpleNamespace(id='page-1')))
    monkeypatch.setattr(desktop, '_browser_engine', lambda: engine)
    monkeypatch.setattr(desktop, '_browser_page_info', AsyncMock(return_value={'page_id': 'page-1', 'url': 'about:blank'}))
    monkeypatch.setattr(desktop, '_desktop_request', AsyncMock(return_value={
        'success': False, 'error': 'The stream could not attach', 'result': {'window_id': 'win-1'}}))
    result = await desktop.browser_open(show=True)
    assert result['success'] is False and result['shown'] is False
    assert result['page_id'] == 'page-1' and result['window_id'] == 'win-1'
    assert result['error'] == 'The stream could not attach'
    engine.open_page.assert_awaited_once()
