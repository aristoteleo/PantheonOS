"""Natural #app mentions resolve to the exact user-selected window."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.desktop_session import DesktopSessionStore
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet, normalize_window_reference


@pytest.fixture
def rig(tmp_path, monkeypatch):
    store = DesktopSessionStore(work_dir=tmp_path)
    store.load()
    window = store.apply('open', {'app_id': 'browser'})[1]['window_id']
    store.apply('set', {'window_id': window, 'patch': {'args': {
        'shared': {'v': {'page': 'selected-page'}}}}})
    desktop = DesktopToolSet()
    monkeypatch.setattr(desktop, '_desktop', lambda: store)
    selected = SimpleNamespace(id='selected-page')
    other = SimpleNamespace(id='other-page')
    def latest(reference=''):
        if reference in ('selected-page', 'other-page'):
            return selected if reference == 'selected-page' else other
        if not reference:
            return other  # Catch accidental fallback to newest/unrelated page.
        raise KeyError(reference)
    async def call(coro):
        return await coro
    engine = SimpleNamespace(latest=latest, window_binding=latest, call=call,
        pages={'selected-page': selected, 'other-page': other},
        current_window_page=AsyncMock(return_value=selected), navigate=AsyncMock(), open_page=AsyncMock())
    monkeypatch.setattr(desktop, '_browser_engine', lambda: engine)
    monkeypatch.setattr(desktop, '_browser_page_info', AsyncMock(return_value={'page_id': selected.id}))
    return desktop, engine, window


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['', 'app:', '#app:'])
async def test_browser_open_reuses_the_mentioned_native_window(rig, prefix):
    desktop, engine, window = rig
    result = await desktop.browser_open('https://example.org', window_id=prefix + window)
    assert result['success'] and result['reused']
    assert result['window_id'] == window and result['page_id'] == 'selected-page'
    engine.navigate.assert_awaited_once_with('selected-page', 'goto', 'https://example.org')
    engine.open_page.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_mentioned_window_never_falls_back_to_another_page(rig):
    desktop, engine, _ = rig
    result = await desktop.browser_open('https://example.org', window_id='#app:win-gone')
    assert not result['success']
    engine.navigate.assert_not_called()
    engine.open_page.assert_not_called()


@pytest.mark.asyncio
async def test_generic_window_action_sends_the_canonical_window_id(rig, monkeypatch):
    desktop, _, window = rig
    request = AsyncMock(return_value={'success': True})
    monkeypatch.setattr(desktop, '_desktop_request', request)
    await desktop.desktop_call('app:' + window, 'navigate', {'url': 'https://example.org'})
    assert request.await_args.args[1]['window_id'] == window


def test_mentions_do_not_reinterpret_arbitrary_page_or_app_ids():
    assert normalize_window_reference('page-123') == 'page-123'
    assert normalize_window_reference('app:browser') == 'app:browser'
    assert normalize_window_reference('#app:win-1::native:abc') == 'win-1::native:abc'


@pytest.mark.asyncio
async def test_window_inventory_does_not_report_a_permanent_loading_state(rig, monkeypatch):
    desktop, _, window = rig
    monkeypatch.setattr(desktop, '_apps', lambda: SimpleNamespace(scan=lambda: None))
    monkeypatch.setattr(desktop, '_desktop_app_metadata', lambda _: {'controllable': True})
    result = await desktop.desktop_windows()
    assert next(item for item in result['result']['windows'] if item['window_id'] == window)['status'] == 'open'
