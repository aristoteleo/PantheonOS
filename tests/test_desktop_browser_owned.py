"""Explicit Desktop composition routes Browser work to ordinary Fleet Apps."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.browser import BrowserEngine
from pantheon.apps.builtin.desktop.fleet_binding import DesktopFleetBinding
from pantheon.apps.builtin.desktop.session_binding import DesktopSessionBinding
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


def bound(node='mac', generation=1):
    return {'node_id': node, 'instance_id': 'browser-'+node,
            'revision': 'version-a', 'generation': generation}


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    service = DesktopToolSet(
        session_binding=DesktopSessionBinding(tmp_path, SimpleNamespace(
            publish_stream=AsyncMock(return_value=True), close=AsyncMock())),
        fleet_binding=DesktopFleetBinding(fleet_id='fleet', node_id='mac', user_seed='owner',
            workspace=tmp_path, connection=SimpleNamespace(is_connected=True, close=AsyncMock())))
    placement = SimpleNamespace(ensure=AsyncMock(return_value=bound()), call=AsyncMock())
    monkeypatch.setattr(service, '_app_placement', lambda: placement)
    def forbidden():
        pytest.fail('Owned Desktop created an ambient Chromium engine')
    monkeypatch.setattr(BrowserEngine, 'instance', forbidden)
    return service, placement


@pytest.mark.asyncio
async def test_hidden_page_uses_same_fleet_app_and_exact_generation(desktop):
    service, placement = desktop
    service._prewarm_browser()
    placement.ensure.assert_not_awaited()
    placement.call.return_value = {'success': True, 'result': {
        'success': True, 'page_id': 'page-a', 'url': 'https://example.test'}}
    opened = await service.browser_open('https://example.test', show=False, node_id='mac')
    assert opened['success'] and opened['backend'] == bound()
    placement.ensure.assert_awaited_once_with('browser', 'mac')
    assert service._desktop().current()['windows'] == {}
    placement.call.return_value = {'success': True, 'result': {'success': True, 'text': 'same page'}}
    read = await service.browser_read()
    assert read['text'] == 'same page'
    assert placement.call.await_args.args[1:4] == (bound(), 'browser_read', {'page_id': 'page-a'})
    placement.call.return_value = {'success': False, 'error': 'stale generation'}
    rejected = await service.browser_goto('https://other.test', 'page-a')
    assert rejected['error'] == 'stale generation'
    placement.ensure.assert_awaited_once()  # no replay on a replacement backend
    placement.call.reset_mock()
    assert not (await service.browser_close(''))['success']
    assert not (await service.browser_read('unknown-page'))['success']
    assert not (await service.browser_open(window_id='win-missing'))['success']
    placement.call.assert_not_awaited()
    placement.call.return_value = {'success': True, 'result': {'success': True}}
    assert (await service.browser_close('page-a'))['success']
    assert 'page-a' not in service._stream_agent_pages


@pytest.mark.asyncio
async def test_page_inventory_and_clear_data_cover_bound_nodes_without_local_engine(desktop):
    service, placement = desktop
    service._stream_agent_pages = {'a': bound(), 'b': bound('linux')}
    async def call(app, binding, method, args, timeout):
        assert app == 'browser'
        if binding['node_id'] == 'linux':
            return {'success': False, 'error': 'node offline'}
        if method == 'browser_pages':
            return {'success': True, 'result': {'success': True, 'pages': [{'page_id': 'a', 'url': 'fixture'}]}}
        return {'success': True, 'result': {'success': True}}
    placement.call.side_effect = call
    pages = await service.browser_pages()
    assert pages['success'] and len(pages['pages']) == 1
    assert pages['unavailable_backends'] == [{'node_id': 'linux', 'error': 'node offline'}]
    result = await service.browser_clear_data()
    assert not result['success'] and len(result['backends']) == 2
    placement.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_ui_creates_one_bound_page_and_persists_window_binding(desktop):
    service, placement = desktop
    wid = service._desktop().apply('open', {'app_id': 'browser', 'args': {'appInstance': bound()}})[1]['window_id']
    placement.call.return_value = {'success': True, 'result': {'success': True,
        'page_id': 'page-a', 'binding': {'page_id': 'page-a', 'operation_id': 'initial', 'revision': 1}}}
    result = await service.browser_ui_page(window_id=wid, operation_id='initial')
    assert result['success']
    assert service._desktop().current()['windows'][wid]['args']['browser_binding'] == result['binding']
    args = placement.call.await_args.args[3]
    assert args['window_id'] == wid and args['operation_id'] == 'initial'
    placement.ensure.assert_not_awaited()
    placement.call.return_value = {'success': True, 'result': {'success': True}}
    assert (await service.browser_ui_nav('page-a', 'reload'))['success']
    assert placement.call.await_args.args[1:3] == (bound(), 'browser_ui_nav')
    assert (await service.browser_ui_stage('page-a', 1000, 700))['success']
    assert placement.call.await_args.args[3]['width'] == 1000
    assert (await service.browser_ui_unstage('page-a'))['success']
    assert (await service.browser_ui_close('page-a'))['success']
    assert 'page-a' not in service._stream_agent_pages


@pytest.mark.asyncio
async def test_keyboard_respects_explicit_focus_across_nodes(desktop):
    service, placement = desktop
    service._stream_agent_pages = {'mac-page': bound(), 'linux-page': bound('linux')}
    placement.call.return_value = {'success': True, 'result': {'success': True}}
    assert not (await service.browser_ui_key([{'key': 'A'}]))['success']
    placement.call.assert_not_awaited()
    assert (await service.browser_ui_focus('mac-page'))['success']
    assert (await service.browser_ui_key([{'key': 'A'}]))['success']
    assert placement.call.await_args.args[1] == bound()
    assert placement.call.await_args.args[3]['page_id'] == 'mac-page'
    assert (await service.browser_ui_key([{'key': 'B'}], page_id='linux-page'))['success']
    assert placement.call.await_args.args[1] == bound('linux')
