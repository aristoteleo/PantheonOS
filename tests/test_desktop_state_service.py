"""The Desktop App on the controller's desktop-state service (docs/os/desktop-state.md).

The service is stood in for by the Python reducer it ports, behind the
dependency client's interface (`invoke` -> {success, result | error}); the Go
service and its bucket are tested in os/internal/hosted.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheonos.appkit.builtin.desktop.desktop_session import (
    DesktopSessionStore, RemoteDesktopSession, boot_token,
)
from pantheonos.appkit.builtin.desktop.session_binding import DesktopSessionBinding
from pantheonos.appkit.builtin.desktop.toolset import DesktopToolSet

ROOT = Path(__file__).resolve().parents[1]


class FakeService:
    """desktop-state's tool contract over the reducer it ports."""

    def __init__(self, tmp_path):
        self.store = DesktopSessionStore(work_dir=tmp_path / 'service')
        self.calls = []
        self.imports = []
        self.down = False

    def invoke(self, method, args=None, *, timeout_seconds=60):
        if self.down:
            from pantheonos.appkit.dependency_client import DependencyCallError
            raise DependencyCallError('Dependency RPC failed; outcome may be unknown', outcome_unknown=True)
        args = json.loads(json.dumps(args or {}))
        self.calls.append(method)
        if method == 'import':
            self.imports.append(args)
            return {'success': True, 'result': {'imported': {}, 'dropped': []}}
        if method == 'get':
            return {'success': True, 'result': {'session': self.store.current()}}
        if method == 'intent':
            try:
                ops, result = self.store.apply(args['kind'], args.get('args') or {})
            except KeyError as e:
                return {'success': False, 'error': e.args[0]}
            except ValueError as e:
                return {'success': False, 'error': str(e)}
            return {'success': True, 'result': {'seq': self.store.session.seq, 'ops': ops, **result}}
        return {'success': False, 'error': f'unknown method {method}'}


def publisher():
    return SimpleNamespace(publish_stream=AsyncMock(return_value=True), close=AsyncMock())


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / 'workspace'
    (root / '.pantheon/atrium/users/u1').mkdir(parents=True)
    (root / '.pantheon/atrium/users/u2').mkdir(parents=True)
    (root / '.pantheon/atrium/desktop.json').write_text(json.dumps({'version': 2, 'wallpaper': 'dunes'}))
    old, new = root / '.pantheon/atrium/users/u1/appearance.json', root / '.pantheon/atrium/users/u2/appearance.json'
    old.write_text(json.dumps({'version': 1, 'preference': 'light'}))
    new.write_text(json.dumps({'version': 1, 'preference': 'dark'}))
    os.utime(old, (1, 1))
    return root


def test_without_the_service_the_desktop_stays_in_files(tmp_path):
    binding = DesktopSessionBinding(tmp_path, publisher())
    assert isinstance(binding.document, DesktopSessionStore)
    with pytest.raises(ValueError, match='dependency client'):
        DesktopSessionBinding(tmp_path, publisher(), desktop_state=object())


def test_the_managed_desktop_uses_the_service_only_when_the_deployment_binds_it():
    from pantheonos.appkit.builtin.desktop.managed import _desktop_state
    from pantheonos.appkit.dependency_client import DependencyClient
    from pantheonos.appkit.runtime_config import RuntimeCredential
    assert _desktop_state(SimpleNamespace(credentials={})) is None
    client = _desktop_state(SimpleNamespace(credentials={
        'desktop_state': RuntimeCredential('https://h123.apps.example/rpc', 'a' * 64)}))
    assert isinstance(client, DependencyClient)


@pytest.mark.asyncio
async def test_agent_tools_and_viewports_share_the_service_session(tmp_path, workspace):
    state = tmp_path / 'state'
    (state / '.pantheon').mkdir(parents=True)
    record = {'v': 2, 'boot': 'old-life', 'seq': 3, 'nominal_w': 1600, 'nominal_h': 1000, 'next_id': 2,
              'windows': [{'id': 'win-1', 'app_id': 'files', 'title': 'Files'}]}
    (state / '.pantheon/desktop.json').write_text(json.dumps(record))
    service = FakeService(tmp_path)
    tools = DesktopToolSet(session_binding=DesktopSessionBinding(
        state, publisher(), desktop_state=service, workspace=workspace))

    opened = await tools.desktop_intent('open', {'app_id': 'terminal', 'title': 'Terminal'})
    assert opened['success'] and opened['window_id'] == 'win-1' and opened['seq'] == 1
    assert opened['host']['record'] == 'desktop-state'
    # The Workspace files went to the service once, before the first intent.
    assert service.calls[:2] == ['import', 'intent'] and len(service.imports) == 1
    sent = service.imports[0]
    assert sent['session'] == record and sent['boot'] == boot_token()
    assert sent['settings'] == {'version': 2, 'wallpaper': 'dunes'}
    assert sent['appearance'] == {'preference': 'dark'}   # the newest user file

    # Another viewport's change (straight to the service) is what the Agent reads.
    service.store.apply('move', {'window_id': 'win-1', 'x': 420, 'y': 99})
    got = await tools.desktop_session_get()
    assert got['session']['windows']['win-1']['x'] == 420
    windows = await tools.desktop_windows()
    assert [w['window_id'] for w in windows['result']['windows']] == ['win-1']
    assert service.imports and len(service.imports) == 1

    # Refusals keep their shape; the record is no longer written.
    missing = await tools.desktop_intent('move', {'window_id': 'win-404', 'x': 1, 'y': 1})
    # (as the file store's KeyError reads)
    assert missing == {'success': False, 'error': str(KeyError("no window 'win-404'"))}
    assert json.loads((state / '.pantheon/desktop.json').read_text()) == record
    assert not (workspace / '.pantheon/desktop.json').exists()


def test_a_down_service_fails_the_call_instead_of_forking_the_desktop(tmp_path, workspace):
    service = FakeService(tmp_path)
    remote = RemoteDesktopSession(service, record=tmp_path / 'none.json', workspace=workspace)
    service.down = True
    from pantheonos.appkit.dependency_client import DependencyCallError
    with pytest.raises(DependencyCallError):
        remote.apply('open', {'app_id': 'files'})
    assert not (tmp_path / 'none.json').exists() and not (tmp_path / '.pantheon').exists()
    # Once it is back, the import that never happened happens first.
    service.down = False
    _, result = remote.apply('open', {'app_id': 'files'})
    assert result['window_id'] == 'win-1' and service.calls == ['import', 'intent']


def test_an_unreadable_record_does_not_block_the_rest(tmp_path, workspace):
    class Picky(FakeService):
        def invoke(self, method, args=None, **kw):
            if method == 'import' and 'session' in (args or {}):
                return {'success': False, 'error': 'session record: invalid number'}
            return super().invoke(method, args, **kw)
    record = tmp_path / 'desktop.json'
    record.write_text(json.dumps({'v': 2, 'spaces': 'many'}))
    service = Picky(tmp_path)
    RemoteDesktopSession(service, record=record, workspace=workspace).current()
    assert service.imports[0]['settings'] == {'version': 2, 'wallpaper': 'dunes'}
    assert 'session' not in service.imports[0]


def test_the_profile_binds_the_desktop_to_the_hosted_service():
    from pantheonos.general_team.release_profile import desktop_state
    apps = desktop_state({'desktop': {'package': 'desktop', 'scope': 'shared-desktop', 'intent': 'running'}})
    assert apps['desktop-state'] == {'hosted': 'desktop-state', 'scope': 'desktop-state', 'intent': 'running'}
    binding = apps['desktop']['bindings']['desktop_state']
    assert binding['$app'] == 'desktop-state' and binding['app_id'] == 'desktop-state'
    # Every granted method and argument is in the controller's contract.
    manifest = json.loads((ROOT / 'os/internal/hosted/manifests/desktop-state.json').read_text())
    tools = {t['name']: t['params'] for t in manifest['provides']['tools']}
    interface = next(i for i in manifest['provides']['interfaces'] if i['name'] == 'desktop-state')
    for method, rule in binding['methods'].items():
        assert method in interface['tools']
        params = {p['name']: p.get('required', True) for p in tools[method]}
        assert set(rule['arguments']) <= params.keys()
        assert {name for name, required in params.items() if required} <= set(rule['arguments'])
    # A deployment without a Desktop gets no desktop-state.
    assert 'desktop-state' not in desktop_state({'agent': {}})


def test_the_desktop_package_declares_the_optional_dependency(tmp_path):
    from pantheonos.appkit.builtin.desktop.build_managed import build
    from pantheonos.appkit.schema import parse_manifest
    out = build(tmp_path / 'desktop', 'linux-amd64', data_mode='gateway')
    manifest = json.loads((out / 'app.json').read_text())
    assert manifest['dependencies'] == {'desktop-state': {
        'range': '^1.0.0', 'uses': ['desktop-state@1'], 'binding': 'optional'}}
    parse_manifest(manifest)
    hosted = json.loads((ROOT / 'os/internal/hosted/manifests/desktop-state.json').read_text())
    assert hosted['version'].startswith('1.')
    execution = json.loads((out / 'fleet.json').read_text())
    assert execution['components'][0]['configuration']['credentials']['desktop_state'] == {'required': False}
    assert (out / 'backend/_vendor/pantheonos/appkit/dependency_client.py').is_file()
