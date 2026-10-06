"""Real Fleet processes and original Model Services directory on startup abort."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import nats
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import profile_manifest, settle


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['none', 'consumer', 'provider', 'registration-reply'])
async def test_real_composed_startup_abort_retains_data_and_stops_publications(tmp_path, binaries, model_endpoint, failure):
    spec = profile_manifest(tmp_path, model_endpoint.url)
    if failure in ('consumer', 'provider'):
        package = 'consumer' if failure == 'consumer' else 'connector'
        root = Path(spec['packages'][package]['path'])
        path = root/'fleet.json'
        definition = json.loads(path.read_text())
        probe = definition['components'][0]['readiness']
        # Run the original check before rejecting actual readiness, so this is
        # a live backend with a terminal start failure, not a missing executable.
        probe['argv'] = [probe['argv'][0], '-c',
            'import subprocess,sys;subprocess.run(sys.argv[1:],check=True);raise SystemExit(1)',
            *probe['argv']]
        probe['timeout_seconds'] = 2
        path.write_text(json.dumps(definition))
        spec['packages'][package]['revision'] = build_artifact(root)[1]
    workspace = tmp_path/'workspace'; workspace.mkdir()
    async with LocalFleet(tmp_path/'profile', binaries, workspace=workspace) as runtime:
        info = runtime.coordinates; children = list(runtime._children)
        nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                               inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(workspace), connection=nc)
        session = LocalAppProfile(runtime, spec, resolver)
        if failure == 'registration-reply':
            original = session.manager.register_prepared
            async def lost(*args):
                await original(*args)
                raise TimeoutError('lost real registration acknowledgement')
            session.manager.register_prepared = lost
        try:
            if failure == 'none': await settle(session, 'advance')
            else:
                with pytest.raises((AssemblyError, TimeoutError)):
                    await settle(session, 'advance')
            before = await session.directory.deployments()
            state = await session.wire.status(info.node_id)
            if failure == 'provider': assert not before
            else: assert len(before) == 1 and before[0]['state'] == 'ready'
            data = []
            for identity in state['instances']:
                root = runtime.root/'node/apps'/info.fleet_id/'data'/identity
                root.mkdir(parents=True, exist_ok=True)
                file = root/'abort-preserves-user-data'
                file.write_text('retained')
                data.append(file)
            args = dict(owner=info.fleet_id, source_operation_id=session._record['recipe']['operation_id'],
                        operation_id='native-startup-abort')
            async with asyncio.timeout(90):
                while True:
                    result = await session.bootstrap.abort(**args)
                    if result['state'] == 'aborted': break
                    await asyncio.sleep(.05)
            with pytest.raises(AssemblyError, match='fenced'):
                await session.bootstrap.advance(**session._record['recipe'])
            state = await session.wire.status(info.node_id)
            assert all(i['state'] == 'stopped' and not i.get('resources') and not i.get('reservations')
                       for i in state['instances'].values())
            after = await session.directory.deployments()
            if before:
                assert after[0]['state'] == 'stopped'
                assert after[0]['models'] == before[0]['models']
                binding = after[0]['binding']
                assert state['instances'][binding['instance_id']]['generation'] == binding['generation']
            operations = deepcopy(state['operations'])
            assert await session.bootstrap.abort(**args) == result
            assert (await session.wire.status(info.node_id))['operations'] == operations
            assert await session.directory.deployments() == after
            assert all(p.read_text() == 'retained' for p in data)
        finally:
            await resolver.close()
    assert_stopped(children, info)
