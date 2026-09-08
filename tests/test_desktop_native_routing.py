"""Public desktop tools operate owned sessions and propagate real outcomes."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.apps.builtin.desktop.desktop_session import DesktopSessionStore


@pytest.fixture
def rig(tmp_path, monkeypatch):
    store = DesktopSessionStore(work_dir=tmp_path)
    store.load()
    win = store.apply('open', {'app_id': 'qupath'})[1]['window_id']
    ts = DesktopToolSet()
    monkeypatch.setattr(ts, '_desktop', lambda: store)
    manager = SimpleNamespace(call=AsyncMock(), read=AsyncMock())
    async def call(coro): return await coro
    engine = SimpleNamespace(call=call, native_apps=lambda: manager)
    monkeypatch.setattr(ts, '_browser_engine', lambda: engine)
    monkeypatch.setattr(ts, '_desktop_request', AsyncMock())
    return ts, manager, win


@pytest.mark.asyncio
async def test_script_targets_existing_gui_and_never_calls_frontend(rig):
    ts, manager, win = rig
    manager.call.return_value = {'state': 'running', 'request_id': 'once'}
    args = {'script': 'return 1', 'request_id': 'once', 'wait_s': 0}
    reply = await ts.desktop_call(win, 'run_script', args)
    manager.call.assert_awaited_once_with(win, 'run_script', args)
    assert reply['result']['state'] == 'running' and reply['result']['request_id'] == 'once'
    ts._desktop_request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['failed', 'expired', 'unknown'])
async def test_script_failure_is_not_a_successful_desktop_result(rig, state):
    ts, manager, win = rig
    manager.call.return_value = {'state': state, 'request_id': 'once', 'error': 'Script failed'}
    reply = await ts.desktop_call(win, 'script_status', {'request_id': 'once'})
    assert reply['success'] is False and reply['result']['request_id'] == 'once'


@pytest.mark.asyncio
async def test_closed_or_unknown_window_cannot_retarget_another_native_session(rig):
    ts, manager, _ = rig
    reply = await ts.desktop_call('win-gone', 'run_script', {'script': 'return 1'})
    assert not reply['success']
    manager.call.assert_not_called()
    ts._desktop_request.assert_not_called()


@pytest.mark.asyncio
async def test_current_gui_read_preserves_pending_and_failure(rig, monkeypatch):
    ts, manager, win = rig
    monkeypatch.setattr(ts, '_native_target', AsyncMock(return_value=(None, None, [])))
    for state in ('running', 'failed'):
        manager.read.return_value = {'state': state, 'request_id': 'state-read'}
        reply = await ts.desktop_read(win)
        assert reply['result']['state'] == state
        assert reply['success'] is (state != 'failed')
    ts._desktop_request.assert_not_called()


@pytest.mark.asyncio
async def test_builtin_skill_and_actions_are_discoverable_without_frontend(rig):
    ts, _, win = rig
    reply = await ts.desktop_windows()
    entry = next(w for w in reply['result']['windows'] if w['window_id'] == win)
    assert {'run_script', 'script_status', 'status'} <= set(entry['actions'])
    assert Path(entry['skill']).is_file()
    assert entry['controllable']
