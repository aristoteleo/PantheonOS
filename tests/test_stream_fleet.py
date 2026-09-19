import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pantheon.apps.portable import execution_package, portable_backend, stream_backend
from pantheon.apps.builtin.desktop.app_placement import AppPlacement
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet

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
    assert 'Linux' in AppPlacement.incompatibility({**node, 'os': 'darwin'}, manifest)
    assert 'Install Xpra' in AppPlacement.incompatibility({**node, 'tools': []}, manifest)
    with pytest.raises(ValueError, match='Linux'):
        with execution_package(ROOT / 'apps/browser', 'darwin-arm64'):
            pass


def test_only_trusted_native_driver_is_portable():
    trusted = {'id': 'qupath', 'entry': {'nativeDriver': 'pantheon.apps.builtin.qupath.native:NativeAppManager'}}
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
