"""Real Fleet copy/start/rollback preserves two independent data generations."""
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import nats
import pytest

from pantheon.apps.dependency_assembly import AssemblyError, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.deployment_abort import AppDeploymentAbort
from pantheon.apps.deployment_stop import AppDeploymentStop
from pantheon.apps.deployment_upgrade import AppUpgradePreparation
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.platform.local_fleet import LocalFleet
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import consumer_package


async def settled(call, expected):
    async with asyncio.timeout(60):
        while True:
            result = await call()
            if result['state'] == expected: return result
            await asyncio.sleep(.05)


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_candidate', [False, True])
@pytest.mark.parametrize('recovery', ['none', 'lost-reply', 'owner-exit'])
async def test_native_app_release_candidate_and_retained_data_rollback(tmp_path, binaries, failed_candidate, recovery):
    reply_loss = recovery != 'none'
    paths=[]
    for version in ('1.0.0', '1.1.0'):
        path=tmp_path/version; consumer_package(path)
        for file in ('app.json','fleet.json'):
            value=json.loads((path/file).read_text());value['version']=version
            if file=='fleet.json':
                value['components'][0]['argv'].append('${DATA}/history.json')
                if version == '1.1.0' and failed_candidate:
                    value['components'][0]['readiness'] = {
                        'argv': ['python3', '-c', 'raise SystemExit(1)'], 'timeout_seconds': 1}
            (path/file).write_text(json.dumps(value))
        code=(path/'server.py').read_text()
        code=code.replace('import os',f'import os,sys,json\nfrom pathlib import Path\np=Path(sys.argv[1])\nv=json.loads(p.read_text()) if p.exists() else []\nv.append({version!r})\np.write_text(json.dumps(v))',1)
        (path/'server.py').write_text(code);paths.append(path)
    async with LocalFleet(tmp_path/'profile',binaries,workspace=tmp_path) as runtime:
        info=runtime.coordinates; children=list(runtime._children)
        nc=await nats.connect(info.nats,user_credentials=str(info.credentials),inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        resolver=AppInstanceResolver(info.fleet_id,info.node_id,info.fleet_id,str(tmp_path),connection=nc)
        wire=FleetLifecycle(resolver)
        deploy=AppDeployment(DependencyStarter(wire,tmp_path/'starts'),tmp_path/'deployments')
        stop=AppDeploymentStop(deploy,tmp_path/'stops')
        upgrade=AppUpgradePreparation(deploy,tmp_path/'upgrades')
        real_submit = wire.submit
        lost, submissions = {}, Counter()

        class LostLifecycleReply(TimeoutError):
            pass

        async def lose_reply(node, action, revision, **kwargs):
            # Submit to the real Runner first. Only the coordinator's receipt
            # is discarded; no node state or operation result is simulated.
            result = await real_submit(node, action, revision, **kwargs)
            operation_id = kwargs['operation_id']
            submissions[operation_id] += 1
            if operation_id not in lost:
                lost[operation_id] = result['request']
                raise LostLifecycleReply(operation_id)
            return result

        async def advance(kind, args, expected):
            nonlocal deploy, stop, upgrade
            async with asyncio.timeout(60):
                while True:
                    try:
                        if recovery == 'owner-exit':
                            result_path = tmp_path/'owner-result.json'
                            result_path.unlink(missing_ok=True)
                            config_path = tmp_path/'owner-input.json'
                            config_path.write_text(json.dumps(dict(root=str(tmp_path), nats=info.nats,
                                credentials=str(info.credentials), owner=info.fleet_id, node=info.node_id,
                                kind=kind, args=args, result=str(result_path))))
                            config_path.chmod(0o600)
                            child = await asyncio.to_thread(subprocess.run,
                                [sys.executable, str(Path(__file__).with_name('native_upgrade_owner.py')), str(config_path)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
                            if child.returncode == 73:
                                assert not result_path.exists(), 'Crash returned a successful result'
                                continue
                            assert child.returncode == 0, child.stdout + child.stderr
                            result = json.loads(result_path.read_text())
                            if 'assembly_error' in result:
                                raise AssemblyError(result['assembly_error'])
                        else:
                            target = dict(deploy=deploy, stop=stop, upgrade=upgrade,
                                          abort=AppDeploymentAbort(deploy))[kind]
                            result = await target.advance(**args)
                        if result['state'] == expected:
                            return result
                    except LostLifecycleReply:
                        # Discard every owner coordinator and its in-memory
                        # state. Recovery must load the saved intent and ledger.
                        deploy = AppDeployment(DependencyStarter(wire, tmp_path/'starts'), tmp_path/'deployments')
                        stop = AppDeploymentStop(deploy, tmp_path/'stops')
                        upgrade = AppUpgradePreparation(deploy, tmp_path/'upgrades')
                    await asyncio.sleep(.05)

        try:
            revisions=[]
            for path in paths:
                data,revision=build_artifact(path);revisions.append(revision)
                await wire.stage_exact(info.node_id,data,revision)
                if reply_loss and path == paths[0]:
                    continue  # Exercise the deployment's own initial installation.
                op=await wire.submit(info.node_id,'install',revision)
                async def installed():
                    return (await wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(installed,'succeeded')
            if recovery == 'lost-reply':
                wire.submit = lose_reply
            source=dict(owner=info.fleet_id,operation_id='original',apps={'sample':dict(
                node_id=info.node_id,revision=revisions[0],scope='sample',generation=0,components={},bindings={})})
            old=await advance('deploy', source, 'ready')
            old_id=old['prepared']['sample']['instance_id']
            data_root=runtime.root/'node/apps'/info.fleet_id/'data'
            assert json.loads((data_root/old_id/'history.json').read_text())==['1.0.0']
            # An owned conversation/attachment store larger than the former
            # 64 MiB copy limit must survive both healthy and failed upgrades.
            with (data_root/old_id/'attachments.bin').open('wb') as stream:
                block=b'conversation-data' * 65536
                for _ in range(80): stream.write(block)
            def attachment_hash(instance):
                with (data_root/instance/'attachments.bin').open('rb') as stream:
                    return hashlib.file_digest(stream,'sha256').hexdigest()
            original_attachment=attachment_hash(old_id)
            await advance('stop', dict(owner=info.fleet_id,operation_id='stop-original',
                source_operation_id='original',apps=['sample']),'stopped')
            await advance('upgrade', dict(owner=info.fleet_id,source_operation_id='original',
                operation_id='candidate',apps=['sample'],revisions={'sample':revisions[1]}),'prepared')
            recipe=await upgrade.prepared_recipe(owner=info.fleet_id,operation_id='candidate')
            if failed_candidate:
                with pytest.raises(AssemblyError):
                    await advance('deploy', recipe, 'ready')
                candidate=deploy.inspect(owner=info.fleet_id,operation_id='candidate')
                state=await wire.status(info.node_id)
                assert state['operations'][deploy.operation_id(recipe,'sample','start')]['state']=='failed'
                item=state['instances'][candidate['prepared']['sample']['instance_id']]
                assert item['state']=='failed' and item['resources']
            else:
                candidate=await advance('deploy', recipe, 'ready')
            candidate_id=candidate['prepared']['sample']['instance_id']
            assert candidate_id!=old_id
            assert attachment_hash(candidate_id)==original_attachment
            assert json.loads((data_root/candidate_id/'history.json').read_text())==['1.0.0','1.1.0']
            assert json.loads((data_root/old_id/'history.json').read_text())==['1.0.0']
            if failed_candidate:
                await advance('abort', dict(owner=info.fleet_id,operation_id='abort-candidate',
                    source_operation_id='candidate'),'aborted')
                item=(await wire.status(info.node_id))['instances'][candidate_id]
                assert item['state']=='stopped' and not item.get('resources') and not item.get('reservations')
                with pytest.raises(AssemblyError,match='fenced'):
                    await deploy.advance(**recipe)
            else:
                await advance('stop', dict(owner=info.fleet_id,operation_id='stop-candidate',
                    source_operation_id='candidate',apps=['sample']),'stopped')
            rollback=await upgrade.rollback_recipe(owner=info.fleet_id,operation_id='candidate',rollback_operation_id='rollback')
            assert rollback['candidate_writes']=='retained-separately'
            restored=await advance('deploy', rollback['recipe'], 'ready')
            assert restored['prepared']['sample']['instance_id']==old_id
            assert attachment_hash(old_id)==attachment_hash(candidate_id)==original_attachment
            assert json.loads((data_root/old_id/'history.json').read_text())==['1.0.0','1.0.0']
            assert json.loads((data_root/candidate_id/'history.json').read_text())==['1.0.0','1.1.0']
            await advance('stop', dict(owner=info.fleet_id,operation_id='stop-restored',
                source_operation_id='rollback',apps=['sample']),'stopped')
            if recovery == 'owner-exit':
                for line in (tmp_path/'accepted-before-owner-exit.jsonl').read_text().splitlines():
                    request = json.loads(line)
                    operation_id = request['operation_id']
                    lost[operation_id] = request
                    submissions[operation_id] += 1
            if reply_loss:
                assert {request['action'] for request in lost.values()} == {
                    'install', 'prepare_start', 'start', 'stop', 'clone_data'}
                state = await wire.status(info.node_id)
                assert len(state['instances']) == 2, state['instances']
                for operation_id, request in lost.items():
                    operation = state['operations'][operation_id]
                    assert operation['request'] == request
                    expected = 'failed' if failed_candidate and request['action'] == 'start' and request['digest'] == revisions[1] else 'succeeded'
                    assert operation['state'] == expected, operation
                assert all(count == 1 for count in submissions.values()), submissions
        finally:
            wire.submit = real_submit
            # Failure cleanup stays on this explicitly owned test node.
            for item in (await wire.status(info.node_id))['instances'].values():
                if item['state']=='stopped':continue
                op=await wire.submit(info.node_id,'stop',item['digest'],scope=item['scope'],generation=item['generation'])
                async def stopped():return (await wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(stopped,'succeeded')
            await resolver.close()
    assert_stopped(children,info)
