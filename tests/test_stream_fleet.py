import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pantheonos.appkit.portable import execution_package, portable_backend, stream_backend
from pantheonos.appkit.builtin.desktop.app_placement import AppPlacement
from pantheonos.appkit.builtin.desktop.toolset import DesktopToolSet

ROOT = Path(__file__).resolve().parents[1]


def test_browser_artifact_is_self_contained_and_has_two_node_local_ports():
    with execution_package(ROOT / 'apps/browser', 'linux-amd64') as root:
        manifest = json.loads((root / 'app.json').read_text())
        definition = json.loads((root / 'fleet.json').read_text())
        assert manifest['entry']['backend'] == '.stream-runtime/__init__.py'
        assert definition['components'][0]['ports'] == {'http': 0, 'stream': 0}
        assert (root / '.stream-runtime/browser.py').read_bytes() == (ROOT / 'apps/desktop/browser.py').read_bytes()
        assert (root / '.stream-runtime/native_control.py').is_file()
        assert 'playwright' in (root / 'requirements.txt').read_text()
    assert not (ROOT / 'apps/browser/fleet.json').exists()


def test_stream_node_filter_does_not_mistake_mac_display_for_xpra():
    manifest = json.loads((ROOT / 'apps/browser/app.json').read_text())
    node = {'status': 'online', 'os': 'linux', 'arch': 'amd64', 'caps': ['proc'],
            'runtimes': {'app-lifecycle': '1', 'app-services': '1', 'app-rpc': '1'},
            'tools': ['xpra', 'Xvfb', 'xdpyinfo']}
    assert AppPlacement.incompatibility(node, manifest) == ''
    assert 'native capture helper' in AppPlacement.incompatibility({**node, 'os': 'darwin'}, manifest)
    assert 'Install Xpra' in AppPlacement.incompatibility({**node, 'tools': []}, manifest)
    for os_name in ('darwin', 'windows'):
        native = {**node, 'os': os_name, 'tools': [], 'runtimes': {**node['runtimes'], 'native-capture': '1'}}
        assert 'permissions' in AppPlacement.incompatibility(native, manifest)
        native['runtimes']['native-capture-ready'] = '1'
        assert AppPlacement.incompatibility(native, manifest) == ''
        with execution_package(ROOT / 'apps/browser', os_name + '-arm64') as root:
            assert (root / '.stream-runtime/helper.py').is_file()
            assert (root / '.stream-runtime/input.py').is_file()
            requirements = (root / 'requirements.txt').read_text()
            assert 'aiohttp' in requirements and 'python-xlib' not in requirements


def test_only_trusted_native_driver_is_portable():
    trusted = {'id': 'qupath', 'entry': {'nativeDriver': 'pantheonos.appkit.builtin.qupath.native:NativeAppManager'}}
    assert stream_backend(trusted) and portable_backend(trusted)
    assert not stream_backend({'id': 'qupath', 'entry': {'nativeDriver': 'arbitrary:Driver'}})


@pytest.mark.asyncio
async def test_stream_calls_keep_exact_binding_and_reject_another_node():
    bound = {'node_id': 'node-a', 'instance_id': 'one', 'revision': 'digest', 'generation': 2}
    placement = SimpleNamespace(call=AsyncMock(return_value={'success': True, 'result': {'success': True, 'page_id': 'p'}}))
    service = object.__new__(DesktopToolSet)
    service._app_placement = lambda: placement
    service._desktop_window = lambda _: {'app_id': 'browser', 'args': {'appInstance': bound}}
    result = await service.desktop_stream_call('browser', bound, 'browser_ui_nav', {'page_id': 'p', 'op': 'reload'}, 'win-1')
    assert result['success'] and result['page_id'] == 'p'
    assert 'result' not in result
    assert placement.call.await_args.args[1] == bound
    placement.call.reset_mock()
    denied = await service.desktop_stream_call('browser', {**bound, 'node_id': 'node-b'}, 'browser_ui_key', {'events': []}, 'win-1')
    assert not denied['success']
    placement.call.assert_not_called()


@pytest.mark.asyncio
async def test_created_stream_page_returns_the_published_desktop_revision(tmp_path):
    from pantheonos.appkit.builtin.desktop.desktop_session import DesktopSessionStore
    store = DesktopSessionStore(tmp_path)
    bound = {'node_id': 'node-a', 'instance_id': 'one', 'revision': 'digest', 'generation': 2}
    wid = store.apply('open', {'app_id': 'browser', 'args': {'appInstance': bound}})[1]['window_id']
    local = {'page_id': 'page-1', 'operation_id': 'initial', 'revision': 1}
    placement = SimpleNamespace(call=AsyncMock(return_value={'success': True,
        'result': {'success': True, 'page_id': 'page-1', 'binding': local}}))
    service = object.__new__(DesktopToolSet)
    service._desktop = lambda: store
    service._app_placement = lambda: placement
    service._publish_desktop = AsyncMock(return_value=True)
    result = await service.desktop_stream_call('browser', bound, 'browser_ui_page', {}, wid)
    saved = store.current()['windows'][wid]['args']['browser_binding']
    assert result['success'] and result['binding'] == saved
    assert saved['revision'] > local['revision']
    # Idempotent replies must not regress the revision on another viewport.
    retry = await service.desktop_stream_call('browser', bound, 'browser_ui_page', {}, wid)
    assert retry['binding'] == saved
    service._publish_desktop.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_page_reattaches_through_the_browser_instance_after_a_reload(tmp_path):
    """An App page (`/app/browser?page_id=`) keeps the page id in its URL, not
    the binding, and its Agent toolset holds no page list after a reload or a
    restart. The page asks the Browser App's instance (desktop_stream_call with
    the instance binding, no window), which still holds the Chromium page; a
    page the instance does not hold is an exact "no such" answer, which the
    page replaces with a fresh one (BrowserApp.vue)."""
    from tests.test_browser_window_creation import Engine
    engine = Engine()
    node = DesktopToolSet()
    node._browser_engine = lambda: engine
    node._prewarm_browser = Mock()
    bound = {'node_id': 'ws', 'instance_id': 'browser-1', 'revision': 'digest', 'generation': 1}

    async def call(app_id, binding, method, args, timeout):
        assert (app_id, binding) == ('browser', bound)
        return {'success': True, 'result': await getattr(node, method)(**args)}

    def agent():
        service = DesktopToolSet()
        service._fleet_binding = object()
        service._app_placement = lambda: SimpleNamespace(call=call)
        return service

    opened = await agent().desktop_stream_call('browser', bound, 'browser_ui_page', {'url': 'https://example.com/'})
    assert opened['success'] and engine.created == [opened['page_id']]
    # A fresh Agent toolset, as after a reload: the bare page id is unknown to it...
    fresh = agent()
    assert 'No bound Browser page' in (await fresh.browser_ui_page(page_id=opened['page_id']))['error']
    # ...and the instance binding reaches the same Chromium page, creating none.
    again = await fresh.desktop_stream_call('browser', bound, 'browser_ui_page', {'page_id': opened['page_id']})
    assert again['success'] and again['page_id'] == opened['page_id'] and engine.created == [opened['page_id']]
    gone = await fresh.desktop_stream_call('browser', bound, 'browser_ui_page', {'page_id': 'gone'})
    assert not gone['success'] and 'no such page: gone' in gone['error']
