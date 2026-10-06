"""A killed product owner must not unlock still-running local infrastructure."""
import asyncio
import json
from pathlib import Path
import sys

import psutil
import pytest

from pantheon.platform.local_fleet import LocalFleet, LocalFleetCoordinates
from test_local_fleet import binaries, inventory
from test_local_profile import minimal_manifest, settle


PROFILE_OWNER = '''
import asyncio, json, sys
from pathlib import Path
from pantheon.platform import local_profile as host
from pantheon.platform.local_fleet import LocalFleetBinaries
async def main():
    root, workspace, manifest, phase, *paths = sys.argv[1:]
    async def report(value):
        if value['state'] == 'ready': print('CRASH_READY:'+json.dumps(value), flush=True)
    if phase == 'starting':
        original = host.LocalAppProfile.advance
        async def pause(self):
            value = await original(self)
            print('CRASH_READY:'+json.dumps(value), flush=True)
            await asyncio.Event().wait()
        host.LocalAppProfile.advance = pause
    await host.serve(root, LocalFleetBinaries(*map(Path, paths)), workspace,
        host.private_json(manifest), on_status=report)
asyncio.run(main())
'''


OWNER = '''
import asyncio, json, sys
from pathlib import Path
from pantheon.platform.local_fleet import LocalFleet, LocalFleetBinaries

async def main():
    root, workspace, phase, *paths = sys.argv[1:]
    runtime = LocalFleet(root, LocalFleetBinaries(*map(Path, paths)), workspace=workspace)
    def report():
        value = {'children': {name: process.pid for name, process in runtime._children}}
        if runtime.coordinates:
            c = runtime.coordinates
            value['coordinates'] = {name: str(getattr(c, name)) for name in (
                'controller', 'nats', 'fleet_id', 'node_id', 'credentials', 'ca_certificate')}
        print(json.dumps(value), flush=True)
    if phase == 'first-child':
        original = runtime._spawn
        async def pause(*args):
            await original(*args)
            report()
            await asyncio.Event().wait()
        runtime._spawn = pause
    async with runtime:
        report()
        await asyncio.Event().wait()
asyncio.run(main())
'''


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['first-child', 'ready'])
async def test_killed_owner_retains_profile_until_all_owned_children_exit(tmp_path, binaries, phase):
    root = tmp_path/'profile'
    children = {}
    log = (tmp_path/'owner.log').open('wb')
    owner = await asyncio.create_subprocess_exec(sys.executable, '-c', OWNER, str(root), str(tmp_path), phase,
        str(binaries.controller), str(binaries.broker), str(binaries.runner),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=log)
    try:
        async with asyncio.timeout(60):
            line = await owner.stdout.readline()
        assert line, (tmp_path/'owner.log').read_text()
        report = json.loads(line)
        # psutil records process birth identity and checks PID reuse before
        # signals; these are only the children of this isolated test owner.
        children = {name: psutil.Process(pid) for name, pid in report['children'].items()}
        owner.kill()
        await owner.wait()
        saved = {}
        if phase == 'ready':
            values = report['coordinates']
            for name in ('credentials', 'ca_certificate'): values[name] = Path(values[name])
            info = LocalFleetCoordinates(**values)
            assert 'instances' in await inventory(info)
            saved = {p: p.read_bytes() for p in (root/'nats.conf', root/'owner.creds', root/'node/runtime.json')}
        for name, child in reversed(list(children.items())):
            assert child.is_running(), name
            with pytest.raises(TimeoutError, match='busy'):
                async with LocalFleet(root, binaries, workspace=tmp_path):
                    pytest.fail('Replacement owner changed a live orphan profile')
            assert all(p.read_bytes() == value for p, value in saved.items())
            child.terminate()
            await asyncio.to_thread(child.wait, 25)
        # No Apps were started. Once every owned infrastructure process has
        # exited, the same profile can be reopened normally without lock surgery.
        async with LocalFleet(root, binaries, workspace=tmp_path) as reopened:
            assert 'instances' in await inventory(reopened.coordinates)
    finally:
        if owner.returncode is None:
            owner.kill()
            await owner.wait()
        for child in reversed(list(children.values())):
            try:
                if child.is_running():
                    child.terminate()
                    try: await asyncio.to_thread(child.wait, 25)
                    except psutil.TimeoutExpired:
                        child.kill()
                        await asyncio.to_thread(child.wait, 10)
            except psutil.NoSuchProcess:
                pass
        log.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['starting', 'ready'])
async def test_public_recovery_drains_original_profile_then_allows_explicit_reopen(tmp_path, binaries, phase):
    from pantheon.apps.owner_journal import OwnerJournal
    from pantheon.apps.resolver import AppInstanceResolver
    from pantheon.platform.local_profile import LocalAppProfile
    from pantheon.platform.local_fleet_recovery import identity
    import nats
    spec = minimal_manifest(tmp_path)
    manifest = tmp_path/'manifest.json'
    OwnerJournal(tmp_path)._write(manifest, spec)
    root = tmp_path/'profile'
    children = []
    log = (tmp_path/'owner.log').open('wb')
    owner = await asyncio.create_subprocess_exec(sys.executable, '-c', PROFILE_OWNER,
        str(root), str(tmp_path), str(manifest), phase,
        str(binaries.controller), str(binaries.broker), str(binaries.runner),
        stdout=asyncio.subprocess.PIPE, stderr=log)
    try:
        async with asyncio.timeout(60):
            while line := await owner.stdout.readline():
                if line.startswith(b'CRASH_READY:'): break
            else: pytest.fail((tmp_path/'owner.log').read_text())
        assert json.loads(line[len(b'CRASH_READY:'):])['state'] == phase
        path = root/'processes.json'
        receipt = json.loads(path.read_text())
        children = [psutil.Process(v['pid']) for v in receipt['children'].values()]
        marker = root/'retained-user-data'; marker.write_text('do not remove')
        owner.kill(); await owner.wait()
        if phase == 'ready':
            original = path.read_bytes()
            for damage in ('live-owner', 'birth', 'command', 'trust'):
                bad = json.loads(original)
                if damage == 'live-owner': bad['owner'] = identity(psutil.Process())
                elif damage == 'birth': bad['children']['runner']['created'] += 1
                elif damage == 'command': bad['children']['runner']['command_hash'] = '0'*64
                else: bad['coordinates']['ca_hash'] = '0'*64
                OwnerJournal(root)._write(path, bad)
                with pytest.raises(RuntimeError):
                    async with LocalFleet(root, binaries, workspace=tmp_path, recover=True):
                        pytest.fail('Unverified recovery was admitted')
                assert all(child.is_running() for child in children)
                assert json.loads(path.read_text()) == bad
            OwnerJournal(root)._write(path, json.loads(original))
            # A client error after successful takeover is not permission to stop
            # infrastructure underneath undrained Apps. A subsequent owner may
            # retry recovery once this failed observer has exited.
            probe = await asyncio.create_subprocess_exec(sys.executable, '-c', '''
import asyncio, sys
from pathlib import Path
from pantheon.platform.local_fleet import LocalFleet, LocalFleetBinaries
async def run():
    root, workspace, *paths = sys.argv[1:]
    async with LocalFleet(root, LocalFleetBinaries(*map(Path, paths)), workspace=workspace, recover=True):
        raise RuntimeError('Injected failure after takeover')
asyncio.run(run())
''', str(root), str(tmp_path), str(binaries.controller), str(binaries.broker), str(binaries.runner),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                async with asyncio.timeout(30): _, err = await probe.communicate()
                assert probe.returncode != 0 and b'original infrastructure remains running' in err
                assert all(child.is_running() for child in children)
            finally:
                if probe.returncode is None: probe.kill(); await probe.wait()
        command = await asyncio.create_subprocess_exec(sys.executable, '-m', 'pantheon', 'local',
            '--profile', str(root), '--workspace', str(tmp_path), '--manifest', str(manifest),
            '--controller', str(binaries.controller), '--broker', str(binaries.broker),
            '--runner', str(binaries.runner), '--recover',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(90): out, err = await command.communicate()
            assert command.returncode == 0, err.decode()
            statuses = [json.loads(line) for line in out.splitlines()]
            assert statuses[-1]['state'] == 'stopped'
            assert not any(s.get('needs_attention') or s['state'] == 'ready' for s in statuses)
        finally:
            if command.returncode is None: command.kill(); await command.wait()
        assert all(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children)
        assert json.loads(path.read_text())['phase'] == 'closed'
        assert marker.read_text() == 'do not remove'
        async with LocalFleet(root, binaries, workspace=tmp_path) as runtime:
            info = runtime.coordinates
            nc = await nats.connect(info.nats, user_credentials=str(info.credentials),
                inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver = AppInstanceResolver(info.fleet_id, info.node_id, info.fleet_id, str(tmp_path), connection=nc)
            try:
                session = LocalAppProfile(runtime, spec, resolver)
                assert (await settle(session, 'advance'))['cycle'] == 2
                assert (await settle(session, 'stop'))['state'] == 'stopped'
            finally: await resolver.close()
    finally:
        if owner.returncode is None: owner.kill(); await owner.wait()
        for child in reversed(children):
            try:
                if child.is_running():
                    child.terminate()
                    try: await asyncio.to_thread(child.wait, 25)
                    except psutil.TimeoutExpired: child.kill(); await asyncio.to_thread(child.wait, 10)
            except psutil.NoSuchProcess: pass
        log.close()
