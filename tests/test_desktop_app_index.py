"""The installed-App index the Desktop App keeps on desktop-state
(docs/os/desktop-state.md, P2): what a desktop draws its launcher from and
routes windows by while the Workspace is stopped."""
import asyncio
import base64
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.desktop.app_index import AppIndexPublisher, build_index, icon_hash
from apps.desktop.app_placement import AppPlacement
from apps.desktop.session_binding import DesktopSessionBinding
from apps.desktop.toolset import DesktopToolSet


class IndexService:
    """desktop-state's apps_put / apps_icon_put, as the controller answers them."""

    def __init__(self, granted=True):
        self.granted = granted
        self.apps = None
        self.icons = {}
        self.calls = []

    def invoke(self, method, args=None, *, timeout_seconds=60):
        args = json.loads(json.dumps(args or {}))
        self.calls.append(method)
        if not self.granted:
            from pantheonos.appkit.dependency_client import DependencyCallError
            raise DependencyCallError('Dependency RPC was rejected', status=403)
        if method == 'apps_put':
            self.apps = args['apps']
            missing = [a['id'] for a in self.apps if a.get('icon_hash') and self.icons.get(a['id']) != a['icon_hash']]
            return {'success': True, 'result': {'revision': len(self.calls), 'missing_icons': missing}}
        if method == 'apps_icon_put':
            self.icons[args['app_id']] = icon_hash(args['icon'])
            return {'success': True, 'result': {'hash': self.icons[args['app_id']]}}
        return {'success': False, 'error': f'unknown method {method}'}


def svg(size=0):
    return 'data:image/svg+xml;base64,' + base64.b64encode(b'<svg/>' + b' ' * size).decode()


def test_the_index_has_what_a_desktop_draws_and_routes_by(tmp_path):
    store = tmp_path / 'app-store' / 'installed'
    apps = [
        {'manifest': {'id': 'notes', 'name': 'Notes', 'version': '2.1.0', 'entry': {'frontend': 'main.js'},
                      'provides': {'tools': [{'name': 'x' * 1000}]}, 'readme': 'long', 'launcher': True},
         'dir': str(store / 'notes'), 'scope': 'user', 'icon_url': svg()},
        {'manifest': {'id': 'fleet', 'name': 'Fleet', 'version': '1.0.0', 'entry': {'frontend': 'ui:fleet'}},
         'dir': str(tmp_path / 'builtin' / 'fleet'), 'scope': 'builtin'},
        {'manifest': {'id': 'viewer', 'name': 'Viewer', 'version': '0.3.0', 'entry': {'frontend': 'main.js'}},
         'dir': str(tmp_path / 'user' / 'viewer'), 'scope': 'user', 'icon_url': svg(8000)},
        {'manifest': {'id': 'huge', 'name': 'Huge', 'version': '1', 'entry': {'frontend': 'main.js'}},
         'dir': str(tmp_path / 'user' / 'huge'), 'scope': 'user', 'icon_url': svg(300_000)},
    ]
    placements = {'notes': {'node_id': 'mac', 'policy': 'resident',
                            'installed': {'mac': {'digest': 'd' * 64, 'component': 'backend'}}}}
    entries, icons = build_index(apps, placements, store)
    by_id = {e['id']: e for e in entries}
    notes = by_id['notes']
    assert (notes['kind'], notes['name'], notes['version']) == ('store', 'Notes', '2.1.0')
    assert notes['placement'] == {'node_id': 'mac', 'policy': 'resident'}
    assert notes['installed'] == {'mac': {'digest': 'd' * 64, 'component': 'backend'}}
    assert notes['icon'] == svg() and 'icon_hash' not in notes
    # A launcher does not use tool schemas or the readme.
    assert notes['manifest'] == {'id': 'notes', 'name': 'Notes', 'version': '2.1.0',
                                 'entry': {'frontend': 'main.js'}, 'launcher': True}
    assert by_id['fleet']['kind'] == 'builtin' and by_id['fleet']['placement'] == {'node_id': None, 'policy': 'on_demand'}
    # A larger icon is stored beside the index, named by its hash.
    assert by_id['viewer']['kind'] == 'packaged' and 'icon' not in by_id['viewer']
    assert by_id['viewer']['icon_hash'] == icon_hash(svg(8000)) and icons == {'viewer': svg(8000)}
    # One the service would refuse is left out: the desktop draws a glyph.
    assert 'icon' not in by_id['huge'] and 'icon_hash' not in by_id['huge']


def test_the_index_is_written_when_it_changed_with_its_missing_icons():
    service = IndexService()
    index = [{'id': 'viewer', 'icon_hash': icon_hash(svg(8000))}]
    publisher = AppIndexPublisher(service, lambda: (index, {'viewer': svg(8000)}))
    assert publisher.publish()
    assert service.calls == ['apps_put', 'apps_icon_put'] and service.icons == {'viewer': icon_hash(svg(8000))}
    # Unchanged (every start, every registry read): nothing is sent.
    assert not publisher.publish() and service.calls == ['apps_put', 'apps_icon_put']
    index.append({'id': 'notes'})
    assert publisher.publish()
    assert service.calls[2:] == ['apps_put'] and [a['id'] for a in service.apps] == ['viewer', 'notes']


def test_a_deployment_bound_before_the_index_is_asked_once():
    service = IndexService(granted=False)
    publisher = AppIndexPublisher(service, lambda: ([{'id': 'notes'}], {}))
    assert not publisher.publish() and publisher.disabled
    publisher.changed()
    assert service.calls == ['apps_put']


def test_an_install_records_its_package_on_the_node(tmp_path, monkeypatch):
    changed = Mock()
    placement = AppPlacement(SimpleNamespace(records=tmp_path, lock=nullcontext), None, on_change=changed)
    placement.resolve = lambda *_: (tmp_path, {'id': 'notes', 'entry': {'backend': 'backend/__init__.py'}}, None)
    placement.nodes = AsyncMock(return_value=[{'node_id': 'mac', 'name': 'mac', 'status': 'online', 'kind': 'machine',
        'os': 'darwin', 'arch': 'arm64', 'caps': ['proc'],
        'runtimes': {'app-lifecycle': '1', 'app-services': '1', 'app-rpc': '1'}}])
    lifecycle = SimpleNamespace(stage=AsyncMock(return_value='d' * 64), submit=AsyncMock(return_value={}),
                                staged_snapshot={'instances': {}})
    monkeypatch.setattr('apps.desktop.app_placement.FleetLifecycle', lambda _: lifecycle)
    placement.save('notes', 'mac')
    asyncio.run(placement.install('notes', 'mac'))
    assert placement.defaults()['notes'] == {'node_id': 'mac',
                                             'installed': {'mac': {'digest': 'd' * 64, 'component': 'backend'}}}
    assert changed.call_count == 2
    # The same package again changes nothing.
    asyncio.run(placement.install('notes', 'mac'))
    assert changed.call_count == 2


@pytest.mark.asyncio
async def test_registry_reads_and_placement_changes_reach_the_controller(tmp_path, monkeypatch):
    monkeypatch.setattr('pathlib.Path.home', classmethod(lambda cls: tmp_path))
    roots = [(tmp_path / 'user', 'user'), (tmp_path / 'builtin', 'builtin')]
    app = roots[0][0] / 'notes'
    app.mkdir(parents=True)
    (app / 'app.json').write_text(json.dumps({'id': 'notes', 'name': 'Notes', 'version': '1.0.0', 'apiVersion': 2,
                                              'surface': 'dom', 'entry': {'frontend': 'main.js'}}))
    # Installed from the Store: the package is recorded beside the library.
    (tmp_path / 'app-store').mkdir()
    (tmp_path / 'app-store' / 'notes.json').write_text(json.dumps({'package_id': 'p1', 'origin': 'store'}))
    service = IndexService()
    state = tmp_path / 'state'
    state.mkdir()
    binding = DesktopSessionBinding(state, SimpleNamespace(publish_stream=AsyncMock(), close=AsyncMock()),
                                    desktop_state=service)
    tools = DesktopToolSet(session_binding=binding)
    monkeypatch.setattr(tools, '_app_scope_roots', lambda: roots)
    assert (await tools.desktop_app_registry())['success']
    await tools._app_index().wait()
    assert [a['id'] for a in service.apps] == ['notes'] and service.apps[0]['placement']['policy'] == 'on_demand'
    assert service.apps[0]['kind'] == 'store'
    assert (await tools.desktop_app_placement('policy', 'notes', policy='resident'))['success']
    await tools._app_index().wait()
    assert service.apps[0]['placement']['policy'] == 'resident'
    assert service.calls == ['apps_put', 'apps_put']
