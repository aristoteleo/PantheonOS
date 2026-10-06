"""Actual paired Agent release upgrade/rollback over the generic App workflow."""
import asyncio
from collections import Counter
from pathlib import Path
import subprocess
import json
import os
import platform
import sys

import nats
import pytest

from pantheon.apps.deployment_upgrade import AppUpgradePreparation
from pantheon.apps.deployment_abort import AppDeploymentAbort
from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.deployment_stop import AppDeploymentStop
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.resolver import AppInstanceResolver
from pantheon.chatroom.package import build_package
from pantheon.platform.local_fleet import LocalFleet
from pantheon.platform.local_profile import LocalAppProfile
from test_agent_application import TEMPLATE
from test_agent_release import release
from test_local_fleet import binaries, assert_stopped
from test_local_model_http import model_endpoint
from test_local_profile import settle
from test_local_profile_agent import product_configuration
from test_app_upgrade_native import settled


@pytest.mark.asyncio
@pytest.mark.parametrize(('failed_candidate', 'self_edit', 'render_gui', 'owner_exit'),
                         [(False, False, False, False), (True, False, False, False),
                          (False, True, False, False), (False, True, True, False),
                          (False, False, False, True), (True, False, False, True)],
                         ids=['False', 'True', 'self-edit', 'self-edit-gui', 'owner-exit', 'owner-exit-failed'])
async def test_paired_agent_release_retains_chat_and_tools_then_rolls_back(tmp_path,binaries,release,model_endpoint,monkeypatch,failed_candidate,self_edit,render_gui,owner_exit):
    ui_source = os.environ.get('AGENT_SELF_EDIT_UI_SOURCE') if render_gui else None
    if render_gui and (not ui_source or not os.environ.get('AGENT_SELF_EDIT_UI_TEST')):
        pytest.skip('Supply GUI source, build dependencies and rendered self-edit acceptance script')
    candidate_gui=os.environ.get('AGENT_UPGRADE_GUI_DIR')
    if not candidate_gui:
        pytest.skip('Supply the paired 0.7.1 Agent GUI build for release upgrade acceptance')
    _,_,bundled,spec=await product_configuration(tmp_path,binaries,release,model_endpoint,monkeypatch)
    target=sys.platform+'-'+{'arm64':'arm64','aarch64':'arm64','x86_64':'amd64'}[platform.machine()]
    package=await asyncio.to_thread(build_package,tmp_path/'new-agent',target,version='0.7.1',
        frontend=candidate_gui,transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies={'shell':{'range':'^0.6.0','uses':['shell@1'],'binding':'runtime'}})
    if failed_candidate:
        # Execute the real backend and health probe, but reject readiness in
        # this intentionally bad release. Recovery must drain the real process.
        definition=json.loads((package/'fleet.json').read_text())
        readiness=definition['components'][0]['readiness']
        readiness['argv']=['python3','-c',
            'import subprocess,sys; subprocess.run(sys.argv[1:]); raise SystemExit(1)',*readiness['argv']]
        readiness['timeout_seconds']=15
        (package/'fleet.json').write_text(json.dumps(definition))
    payload,new_revision=await asyncio.to_thread(build_artifact,package)
    async with LocalFleet(tmp_path/'profile',bundled,workspace=tmp_path/'workspace') as runtime:
        info=runtime.coordinates;children=list(runtime._children)
        nc=await nats.connect(info.nats,user_credentials=str(info.credentials),inbox_prefix=('_INBOX_'+info.fleet_id).encode())
        resolver=AppInstanceResolver(info.fleet_id,info.node_id,info.fleet_id,str(runtime.workspace),connection=nc)
        session=LocalAppProfile(runtime,spec,resolver)
        try:
            await settle(session,'advance')
            source_id=session._consumer_id(session._record['recipe'])
            deployment=session.deploy;wire=session.wire
            async def advance(kind, args, expected):
                if not owner_exit:
                    operations = dict(deploy=deployment, stop=stopper, upgrade=upgrade,
                                      abort=AppDeploymentAbort(deployment))
                    return await settled(lambda: operations[kind].advance(**args), expected)
                # Real coordinator death after acceptance, before checkpointing
                # the returned receipt. All retries reuse the existing journals.
                async with asyncio.timeout(660):
                    while True:
                        result_path = tmp_path/'owner-result.json'
                        result_path.unlink(missing_ok=True)
                        config_path = tmp_path/'owner-input.json'
                        config_path.write_text(json.dumps(dict(root=str(tmp_path),
                            journals=str(session.root), stops=str(tmp_path/'release-stops'),
                            upgrades=str(tmp_path/'release-upgrades'), nats=info.nats,
                            credentials=str(info.credentials), owner=info.fleet_id,
                            node=info.node_id, controller=info.controller,
                            owner_key=str(runtime.root/'owner.key'), ca=str(info.ca_certificate),
                            kind=kind, args=args, result=str(result_path))))
                        config_path.chmod(0o600)
                        child = await asyncio.to_thread(subprocess.run,
                            [sys.executable, str(Path(__file__).with_name('native_upgrade_owner.py')), str(config_path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60)
                        if child.returncode == 73:
                            assert not result_path.exists()
                            continue
                        assert child.returncode == 0, child.stdout + child.stderr
                        result = json.loads(result_path.read_text())
                        if 'assembly_error' in result:
                            raise AssemblyError(result['assembly_error'])
                        if result['state'] == expected:
                            return result
                        await asyncio.sleep(.1)
            source=deployment.inspect(owner=info.fleet_id,operation_id=source_id)
            from pantheon.chatroom.data_format import FORMAT_FILE
            data_root=runtime.root/'node/apps'/info.fleet_id/'data'
            original_marker=data_root/source['prepared']['agent']['instance_id']/'agent'/FORMAT_FILE
            original_format=json.loads(original_marker.read_text())
            assert original_format['id']=='pantheon-agent' and original_format['version']==1
            identity={**source['prepared']['agent'],'generation':source['prepared']['agent']['generation']+1}
            identity.pop('node_id')
            client=await resolver._ensure_client()
            async def invoke(method,**args):
                result=await client.invoke(info.node_id,'agent',identity,method,args,45)
                assert 'error' not in result,result
                assert result['response'].get('success') is not False,result
                return result['response']['result']
            template={**TEMPLATE,'agents':[{**TEMPLATE['agents'][0],'toolsets':['shell'],'model':'normal'}]}
            chat=await invoke('create_chat',chat_name='Release lifecycle',project_name='Shared',template_obj=template)
            chat_id=chat['chat_id']
            assert (await invoke('chat',chat_id=chat_id,message=[{'role':'user','content':'original release turn'}]))['success']
            async def history():
                snapshot=await invoke('open_agent_history',chat_id=chat_id)
                result=await invoke('read_agent_history',chat_id=chat_id,snapshot_id=snapshot['snapshot_id'],part=0)
                await invoke('release_agent_history',chat_id=chat_id,snapshot_id=snapshot['snapshot_id'])
                return result['json_fragment']
            if self_edit:
                from agent_self_edit import edit_candidate, render_candidate
                assert 'self_edit_revision' not in await invoke('get_agent_app_info')
                if render_gui:
                    await render_candidate(tmp_path/'source-view', release[0], invoke, chat_id, candidate=False)
                package = await edit_candidate(runtime.workspace, release[0], candidate_gui,
                                               model_endpoint, invoke, chat_id, ui_source=ui_source)
                payload,new_revision=await asyncio.to_thread(build_artifact,package)
                # Editing a working copy must not change the currently running code.
                assert 'self_edit_revision' not in await invoke('get_agent_app_info')
                assert 'AGENT_SELF_EDIT_COMPLETE' in await history()
            original_history=await history()
            assert 'PROFILE_TOOL_OK' in original_history
            shared = {key: (item['digest'], item['generation'])
                      for key, item in (await wire.status(info.node_id))['instances'].items()
                      if item['app_id'] in ('shell', 'model-service')}
            assert len(shared) == 2
            logical=(await invoke('get_agents',chat_id=chat_id))['agents'][0]['instance']['instance_id']
            selected=['agent','allocator','model-access']
            stopper=AppDeploymentStop(deployment,tmp_path/'release-stops')
            upgrade=AppUpgradePreparation(deployment,tmp_path/'release-upgrades')
            await advance('stop', dict(owner=info.fleet_id,operation_id='stop-original',
                source_operation_id=source_id,apps=selected),'stopped')
            await wire.stage_exact(info.node_id,payload,new_revision)
            op=await wire.submit(info.node_id,'install',new_revision)
            async def installed():return (await wire.status(info.node_id))['operations'][op['request']['operation_id']]
            await settled(installed,'succeeded')
            await advance('upgrade', dict(owner=info.fleet_id,source_operation_id=source_id,
                operation_id='new-release',apps=selected,revisions={'agent':new_revision}),'prepared')
            recipe=await upgrade.prepared_recipe(owner=info.fleet_id,operation_id='new-release')
            if failed_candidate:
                with pytest.raises(AssemblyError):
                    await advance('deploy', recipe, 'ready')
                state=await wire.status(info.node_id)
                assert state['operations'][deployment.operation_id(recipe,'agent','start')]['state']=='failed'
                new=deployment.inspect(owner=info.fleet_id,operation_id='new-release')
                candidate_id=new['prepared']['agent']['instance_id']
                assert state['instances'][candidate_id]['resources']
                await advance('abort', dict(owner=info.fleet_id,operation_id='abort-candidate',
                    source_operation_id='new-release'),'aborted')
                stopped=(await wire.status(info.node_id))['instances'][candidate_id]
                assert stopped['state']=='stopped' and not stopped.get('resources') and not stopped.get('reservations')
                with pytest.raises(AssemblyError,match='fenced'):
                    await deployment.advance(**recipe)
            else:
                new=await advance('deploy', recipe, 'ready')
                identity={**new['prepared']['agent'],'generation':new['prepared']['agent']['generation']+1}
                identity.pop('node_id')
                assert identity['instance_id']!=source['prepared']['agent']['instance_id']
                if self_edit:
                    assert (await invoke('get_agent_app_info'))['self_edit_revision'] == 'candidate-v1'
                assert (await invoke('get_agents',chat_id=chat_id))['agents'][0]['instance']['instance_id']==logical
                assert await history()==original_history
                if render_gui:
                    await render_candidate(tmp_path/'candidate-view', package, invoke, chat_id, candidate=True)
                assert (await invoke('chat',chat_id=chat_id,message=[{'role':'user','content':'candidate release turn'}]))['success']
                candidate_history=await history()
                assert 'candidate release turn' in candidate_history
                assert candidate_history.count('PROFILE_TOOL_OK') > original_history.count('PROFILE_TOOL_OK')
                await advance('stop', dict(owner=info.fleet_id,operation_id='stop-candidate',
                    source_operation_id='new-release',apps=selected),'stopped')
            candidate_marker=data_root/new['prepared']['agent']['instance_id']/'agent'/FORMAT_FILE
            assert json.loads(candidate_marker.read_text())==original_format
            assert json.loads(original_marker.read_text())==original_format
            rollback=await upgrade.rollback_recipe(owner=info.fleet_id,operation_id='new-release',rollback_operation_id='restored-release')
            restored=await advance('deploy', rollback['recipe'], 'ready')
            identity={**restored['prepared']['agent'],'generation':restored['prepared']['agent']['generation']+1}
            identity.pop('node_id')
            assert identity['instance_id']==source['prepared']['agent']['instance_id']
            if self_edit:
                assert 'self_edit_revision' not in await invoke('get_agent_app_info')
            assert await history()==original_history
            if render_gui:
                await render_candidate(tmp_path/'restored-view', release[0], invoke, chat_id, candidate=False)
            assert (await invoke('chat',chat_id=chat_id,message=[{'role':'user','content':'restored release turn'}]))['success']
            restored_history=await history()
            assert 'restored release turn' in restored_history
            assert 'candidate release turn' not in restored_history
            assert restored_history.count('PROFILE_TOOL_OK') > original_history.count('PROFILE_TOOL_OK')
            state=await wire.status(info.node_id)
            for key, (digest, generation) in shared.items():
                item=state['instances'][key]
                assert (item['digest'], item['generation'], item['state']) == (digest, generation, 'ready')
            await advance('stop', dict(owner=info.fleet_id,operation_id='stop-restored',
                source_operation_id='restored-release',apps=selected),'stopped')
            if owner_exit:
                accepted = [json.loads(line) for line in
                            (tmp_path/'accepted-before-owner-exit.jsonl').read_text().splitlines()]
                counts = Counter(request['operation_id'] for request in accepted)
                assert counts and all(count == 1 for count in counts.values()), counts
                assert {request['action'] for request in accepted} == {'stop', 'clone_data', 'prepare_start', 'start'}
                state = await wire.status(info.node_id)
                assert len([i for i in state['instances'].values() if i['app_id'] == 'agent']) == 2
                for request in accepted:
                    operation = state['operations'][request['operation_id']]
                    assert operation['request'] == request
                    expected = 'failed' if failed_candidate and request['action'] == 'start' and request['digest'] == new_revision else 'succeeded'
                    assert operation['state'] == expected, operation
        finally:
            state=await session.wire.status(info.node_id)
            for item in sorted(state['instances'].values(),key=lambda i:i.get('app_id')!='agent'):
                if item['state']=='stopped':continue
                op=await session.wire.submit(info.node_id,'stop',item['digest'],scope=item['scope'],generation=item['generation'])
                async def stopped():return (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(stopped,'succeeded')
            await resolver.close()
    assert_stopped(children,info)
