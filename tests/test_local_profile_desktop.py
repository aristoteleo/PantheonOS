"""Prepared Desktop supplied by an owned profile, without manual node setup."""
import base64
import json

import nats
import pytest

from pantheon.apps.builtin.desktop.build_managed import build
from pantheon.apps.local_agent import native_platform
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_local_fleet import binaries, assert_stopped
from test_local_profile import settle, offline_session


@pytest.mark.asyncio
async def test_profile_installs_desktop_and_restores_windows_after_full_restart(tmp_path, binaries):
    target = native_platform()
    package = build(tmp_path/'desktop-release', target)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    catalog = workspace/'apps'; catalog.mkdir()
    desktop = {'user_seed': 'profile-owner', 'fleet': {'auth': 'creds-base64'},
        'events': {'auth': 'creds-base64'}, 'event_prefix': {'$local': 'fleet_event_prefix'},
        'catalog': [{'path': str(catalog), 'scope': 'user'}], 'data_roots': [],
        'store': {'origin': 'https://store.invalid'}, 'data': {'mode': 'loopback'}}
    spec = dict(protocol=1, packages={'desktop': {'path': str(package),
        'revision': build_artifact(package, target)[1], 'platform': target}}, model_apps={},
        apps={'desktop': {'package': 'desktop', 'scope': 'desktop', 'bindings': {},
            'components': {'backend': {'values': {'desktop': desktop}, 'credentials': {
                'fleet': {'$local': 'fleet_credential'}, 'events': {'$local': 'fleet_credential'}}}}}})
    window = None
    first_ref = None
    for cycle in (1, 2):
        async with LocalFleet(tmp_path/'profile', binaries, workspace=workspace) as runtime:
            info = runtime.coordinates
            children = list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                                    inbox_prefix=('_INBOX_' + info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                assert (await settle(session, 'advance'))['cycle'] == cycle
                rpc = await session.bind_rpc('desktop', 'desktop')
                async def invoke(method, **args):
                    result = await rpc(method, args, 20)
                    assert not result.get('error'), result
                    return result
                refs = session._record['recipe']['apps']['desktop']['components']['backend']['credentials']
                assert refs['fleet'] == refs['events']
                if cycle == 1:
                    first_ref = refs['fleet']
                    sub = await nc.subscribe(f'fleet.{info.fleet_id}.apps.desktop.pantheon.stream.desktop')
                    await nc.flush()
                    opened = await invoke('desktop_intent', kind='open', args={'app_id': 'files', 'title': 'Profile Files'})
                    window = opened['window_id']
                    event = json.loads((await sub.next_msg(timeout=5)).data)
                    assert event['data']['type'] == 'desktop.delta'
                else:
                    assert refs['fleet'] != first_ref  # Fresh authority coordinates, no vault overwrite.
                    restored = await invoke('desktop_session_get')
                    assert window in restored['session']['windows']
                assert (await invoke('desktop_app_lifecycle', node_id=info.node_id))['success']
                encoded = base64.b64encode(info.credentials.read_bytes()).decode()
                assert encoded not in session.path.read_text()
                assert encoded not in json.dumps(spec)
                assert (await settle(session, 'stop'))['state'] == 'stopped'
                assert not list((runtime.root/'node').rglob('.app-bus-*.creds'))
            finally:
                if session.status()['state'] not in ('stopped', 'unopened'):
                    await settle(session, 'stop')
                await resolver.close()
        assert_stopped(children, info)


@pytest.mark.asyncio
async def test_profile_bus_retry_retains_original_snapshot_after_owner_renewal(tmp_path):
    runtime, spec, session = offline_session(tmp_path)
    runtime.coordinates.credentials.write_text('original-signed-credential')
    runtime.coordinates.credentials.chmod(0o600)
    spec['apps']['consumer']['components'] = {'backend': {'credentials': {'fleet': {'$local': 'fleet_credential'}}}}
    session = LocalAppProfile(runtime, spec, object())
    await session._open()
    ref = next(session._bus_references())
    runtime.coordinates.credentials.write_text('renewed-signed-credential')
    restored = LocalAppProfile(runtime, spec, object())
    await restored._open()
    assert next(restored._bus_references()) == ref
    snapshot = json.loads((session.root/'credentials'/('bus-' + ref['ref'].split('profile-bus-')[1] + '.json')).read_text())
    assert base64.b64decode(snapshot['key']) == b'original-signed-credential'


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['identity', 'permissions', 'symlink'])
async def test_profile_refuses_modified_private_bus_snapshot(tmp_path, damage):
    from pantheon.apps.dependency_assembly import AssemblyError
    runtime, spec, session = offline_session(tmp_path)
    runtime.coordinates.credentials.write_text('signed-credential')
    runtime.coordinates.credentials.chmod(0o600)
    spec['apps']['consumer']['components'] = {'backend': {'credentials': {'fleet': {'$local': 'fleet_credential'}}}}
    session = LocalAppProfile(runtime, spec, object())
    await session._open()
    ref = next(session._bus_references())
    path = session.root/'credentials'/('bus-' + ref['ref'].split('profile-bus-')[1] + '.json')
    assert list(session._bus_credentials()) == [ref['ref']]
    if damage == 'identity':
        snapshot = json.loads(path.read_text()); snapshot['node_id'] = 'different-node'
        path.write_text(json.dumps(snapshot))
    elif damage == 'permissions':
        path.chmod(0o644)
    else:
        other = path.with_suffix('.moved'); path.rename(other); path.symlink_to(other)
    with pytest.raises((AssemblyError, RuntimeError)):
        session._bus_credentials()
