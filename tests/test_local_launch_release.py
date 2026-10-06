"""Saved launch adoption and interruption recovery over real local Fleet."""
import asyncio
from copy import deepcopy
import json
import shutil
import subprocess
import sys

import nats
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_launch import LaunchJournal, read_launch
from pantheon.platform.local_launch_release import operate
from pantheon.platform.local_profile import LocalAppProfile
from test_local_fleet import binaries, assert_stopped
from test_local_profile import minimal_manifest, settle


def launch_file(tmp_path):
    value = dict(protocol=1, launcher=[sys.executable, '-m', 'pantheon'],
                 bundle=str(tmp_path/'bundle1'), setup=str(tmp_path/'setup.json'),
                 profile=str(tmp_path/'profile'), workspace=str(tmp_path/'workspace'))
    (tmp_path/'workspace').mkdir()
    path = tmp_path/'launch.json'
    LaunchJournal(tmp_path)._write(path, value)
    return path, value


@pytest.mark.parametrize('damage', ['symlink', 'public', 'relative', 'extra', 'launcher'])
def test_launch_description_rejects_unsafe_or_ambiguous_inputs(tmp_path, damage):
    path, value = launch_file(tmp_path)
    if damage == 'symlink':
        path.rename(tmp_path/'original'); path.symlink_to(tmp_path/'original')
    elif damage == 'public': path.chmod(0o644)
    else:
        if damage == 'relative': value['bundle'] = 'relative'
        elif damage == 'extra': value['unknown'] = True
        else: value['launcher'] = []
        LaunchJournal(tmp_path)._write(path, value)
    with pytest.raises(AssemblyError): read_launch(path)


def test_local_release_public_dispatch_avoids_agent_execution():
    script = '''import importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name=='pantheon.agent' or name.startswith(('pantheon.chatroom','pantheon.repl','pantheon.factory')): raise AssertionError(name)
sys.meta_path.insert(0,Boundary())
import pantheon.platform.local_launch_release as release
seen=[]
release.main=lambda args:seen.append(args)
from pantheon.__main__ import main
sys.argv=['pantheon','local-release','--launch','/private/launch.json','--target-bundle','/product']
main()
assert seen==[sys.argv[2:]],seen
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_local_saved_launch_supplies_exact_startup_inputs(tmp_path, monkeypatch):
    from pantheon.platform import local_profile
    path, value = launch_file(tmp_path)
    LaunchJournal(tmp_path)._write(tmp_path/'setup.json', {})
    monkeypatch.setattr('pantheon.apps.local_agent.read_bundle', lambda p: ('binaries', p))
    monkeypatch.setattr('pantheon.apps.local_agent.compose_profile', lambda entries, setup: {'apps': {}})
    seen = []
    async def serve(*args, **kwargs): seen.append((args, kwargs))
    monkeypatch.setattr(local_profile, 'serve', serve)
    local_profile.main(['--launch', str(path)])
    assert seen[0][0][:3] == (value['profile'], 'binaries', value['workspace'])
    assert seen[0][1]['launch_guard'] == (str(path), value)
    with pytest.raises(SystemExit): local_profile.main(['--launch', str(path), '--bundle', value['bundle']])
    with pytest.raises(SystemExit): local_profile.main(['--launch', str(path), '--profile', '/different'])


@pytest.mark.asyncio
async def test_launch_changed_while_acquiring_owner_never_starts_apps(tmp_path, monkeypatch):
    from pantheon.platform import local_profile
    path, value = launch_file(tmp_path)
    class Acquired:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self):
            LaunchJournal(tmp_path)._write(path, {**value, 'bundle': str(tmp_path/'new-product')})
            return self
        async def __aexit__(self, *args): pass
        @property
        def coordinates(self): raise AssertionError('A stale launch must not connect to Fleet or start Apps')
    monkeypatch.setattr(local_profile, 'LocalFleet', Acquired)
    with pytest.raises(AssemblyError, match='Saved launch changed'):
        await local_profile.serve(value['profile'], None, value['workspace'], {}, launch_guard=(path, value))


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['none', 'before-launch-write', 'after-launch-write'])
async def test_native_saved_launch_upgrade_reopen_rollback(tmp_path, binaries, monkeypatch, interruption):
    path, launch = launch_file(tmp_path)
    source = minimal_manifest(tmp_path)
    target = deepcopy(source)
    candidate = tmp_path/'candidate'
    shutil.copytree(tmp_path/'app', candidate)
    for name in ('app.json', 'fleet.json'):
        item = json.loads((candidate/name).read_text()); item['version'] = '1.1.0'
        (candidate/name).write_text(json.dumps(item))
    target['packages']['consumer'] = {'path': str(candidate), 'revision': build_artifact(candidate)[1]}
    target_bundle = str(tmp_path/'bundle2')
    products = {launch['bundle']: source, target_bundle: target}
    # Product compilation is isolated here; processes, copy receipts, launch
    # adoption, restarts and rollback all use real Controller/Runner/NATS.
    monkeypatch.setattr('pantheon.platform.local_launch_release._product', lambda v: (binaries, products[v['bundle']]))
    original_id = candidate_id = upgrade_id = None
    for cycle in (1, 2, 3):
        selected = read_launch(path)
        spec = products[selected['bundle']]
        async with LocalFleet(launch['profile'], binaries, workspace=launch['workspace']) as runtime:
            info = runtime.coordinates; children = list(runtime._children)
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials), inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(runtime.workspace), connection=nc)
            session = LocalAppProfile(runtime, spec, resolver)
            try:
                assert (await settle(session, 'advance'))['cycle'] == cycle
                identity = session.app_binding('consumer')['instance_id']
                data = runtime.root/'node/apps'/info.fleet_id/'data'/identity
                if cycle == 1:
                    original_id = identity; (data/'history').write_text('source')
                elif cycle == 2:
                    candidate_id = identity
                    assert identity != original_id and (data/'history').read_text() == 'source'
                    (data/'history').write_text('candidate')
                else:
                    assert identity == original_id and (data/'history').read_text() == 'source'
                    assert (data.parent/candidate_id/'history').read_text() == 'candidate'
                assert (await settle(session, 'stop'))['state'] == 'stopped'
            finally: await resolver.close()
        assert_stopped(children, info)
        if cycle == 3: break
        selection = {'target_bundle': target_bundle} if cycle == 1 else {'rollback_of': upgrade_id}
        before = path.read_bytes()
        review = await operate(path, **selection)
        assert path.read_bytes() == before
        if cycle == 1: upgrade_id = review['review_id']
        with pytest.raises(AssemblyError, match='review'):
            await operate(path, approval='0'*64, **selection)
        assert path.read_bytes() == before
        if cycle == 1 and interruption != 'none':
            write = LaunchJournal._write
            def interrupted(self, output, value):
                if output == path and interruption == 'before-launch-write': raise OSError('lost launch write')
                write(self, output, value)
                if output == path: raise OSError('lost launch acknowledgement')
            monkeypatch.setattr(LaunchJournal, '_write', interrupted)
            with pytest.raises(OSError, match='lost launch'):
                await operate(path, approval=review['review_id'], **selection)
            monkeypatch.setattr(LaunchJournal, '_write', write)
        result = await operate(path, approval=review['review_id'], **selection)
        assert result['state'] == 'approved'
        assert result['launch'] == read_launch(path)
        assert result['launch']['bundle'] == (target_bundle if cycle == 1 else launch['bundle'])
        # Repeated acknowledgement observes the same source cycle/copy intent.
        assert await operate(path, approval=review['review_id'], **selection) == result
