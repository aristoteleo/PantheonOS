"""Real Fleet copy/start/rollback preserves two independent data generations."""
import asyncio
import hashlib
import json

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
async def test_native_app_release_candidate_and_retained_data_rollback(tmp_path, binaries, failed_candidate):
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
        try:
            revisions=[]
            for path in paths:
                data,revision=build_artifact(path);revisions.append(revision)
                await wire.stage_exact(info.node_id,data,revision)
                op=await wire.submit(info.node_id,'install',revision)
                async def installed():
                    return (await wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(installed,'succeeded')
            source=dict(owner=info.fleet_id,operation_id='original',apps={'sample':dict(
                node_id=info.node_id,revision=revisions[0],scope='sample',generation=0,components={},bindings={})})
            old=await settled(lambda:deploy.advance(**source),'ready')
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
            await settled(lambda:stop.advance(owner=info.fleet_id,operation_id='stop-original',
                source_operation_id='original',apps=['sample']),'stopped')
            await settled(lambda:upgrade.advance(owner=info.fleet_id,source_operation_id='original',
                operation_id='candidate',apps=['sample'],revisions={'sample':revisions[1]}),'prepared')
            recipe=await upgrade.prepared_recipe(owner=info.fleet_id,operation_id='candidate')
            if failed_candidate:
                with pytest.raises(AssemblyError):
                    await settled(lambda:deploy.advance(**recipe),'ready')
                candidate=deploy.inspect(owner=info.fleet_id,operation_id='candidate')
                state=await wire.status(info.node_id)
                assert state['operations'][deploy.operation_id(recipe,'sample','start')]['state']=='failed'
                item=state['instances'][candidate['prepared']['sample']['instance_id']]
                assert item['state']=='failed' and item['resources']
            else:
                candidate=await settled(lambda:deploy.advance(**recipe),'ready')
            candidate_id=candidate['prepared']['sample']['instance_id']
            assert candidate_id!=old_id
            assert attachment_hash(candidate_id)==original_attachment
            assert json.loads((data_root/candidate_id/'history.json').read_text())==['1.0.0','1.1.0']
            assert json.loads((data_root/old_id/'history.json').read_text())==['1.0.0']
            if failed_candidate:
                abort=AppDeploymentAbort(deploy)
                await settled(lambda:abort.advance(owner=info.fleet_id,operation_id='abort-candidate',
                    source_operation_id='candidate'),'aborted')
                item=(await wire.status(info.node_id))['instances'][candidate_id]
                assert item['state']=='stopped' and not item.get('resources') and not item.get('reservations')
                with pytest.raises(AssemblyError,match='fenced'):
                    await deploy.advance(**recipe)
            else:
                await settled(lambda:stop.advance(owner=info.fleet_id,operation_id='stop-candidate',
                    source_operation_id='candidate',apps=['sample']),'stopped')
            rollback=await upgrade.rollback_recipe(owner=info.fleet_id,operation_id='candidate',rollback_operation_id='rollback')
            assert rollback['candidate_writes']=='retained-separately'
            restored=await settled(lambda:deploy.advance(**rollback['recipe']),'ready')
            assert restored['prepared']['sample']['instance_id']==old_id
            assert attachment_hash(old_id)==attachment_hash(candidate_id)==original_attachment
            assert json.loads((data_root/old_id/'history.json').read_text())==['1.0.0','1.0.0']
            assert json.loads((data_root/candidate_id/'history.json').read_text())==['1.0.0','1.1.0']
            await settled(lambda:stop.advance(owner=info.fleet_id,operation_id='stop-restored',
                source_operation_id='rollback',apps=['sample']),'stopped')
        finally:
            # Failure cleanup stays on this explicitly owned test node.
            for item in (await wire.status(info.node_id))['instances'].values():
                if item['state']=='stopped':continue
                op=await wire.submit(info.node_id,'stop',item['digest'],scope=item['scope'],generation=item['generation'])
                async def stopped():return (await wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(stopped,'succeeded')
            await resolver.close()
    assert_stopped(children,info)
