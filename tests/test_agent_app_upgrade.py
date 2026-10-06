"""Actual paired Agent release upgrade/rollback over the generic App workflow."""
import asyncio
import os
import platform
import sys

import nats
import pytest

from pantheon.apps.deployment_upgrade import AppUpgradePreparation
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
async def test_paired_agent_release_retains_chat_and_tools_then_rolls_back(tmp_path,binaries,release,model_endpoint,monkeypatch):
    candidate_gui=os.environ.get('AGENT_UPGRADE_GUI_DIR')
    if not candidate_gui:
        pytest.skip('Supply the paired 0.7.1 Agent GUI build for release upgrade acceptance')
    _,_,bundled,spec=await product_configuration(tmp_path,binaries,release,model_endpoint,monkeypatch)
    target=sys.platform+'-'+{'arm64':'arm64','aarch64':'arm64','x86_64':'amd64'}[platform.machine()]
    package=await asyncio.to_thread(build_package,tmp_path/'new-agent',target,version='0.7.1',
        frontend=candidate_gui,transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies={'shell':{'range':'^0.6.0','uses':['shell@1'],'binding':'runtime'}})
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
            source=deployment.inspect(owner=info.fleet_id,operation_id=source_id)
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
            original_history=await history()
            assert 'PROFILE_TOOL_OK' in original_history
            shared = {key: (item['digest'], item['generation'])
                      for key, item in (await wire.status(info.node_id))['instances'].items()
                      if item['app_id'] in ('shell', 'model-service')}
            assert len(shared) == 2
            logical=(await invoke('get_agents',chat_id=chat_id))['agents'][0]['instance']['instance_id']
            selected=['agent','allocator','model-access']
            stopper=AppDeploymentStop(deployment,tmp_path/'release-stops')
            await settled(lambda:stopper.advance(owner=info.fleet_id,operation_id='stop-original',
                source_operation_id=source_id,apps=selected),'stopped')
            await wire.stage_exact(info.node_id,payload,new_revision)
            op=await wire.submit(info.node_id,'install',new_revision)
            async def installed():return (await wire.status(info.node_id))['operations'][op['request']['operation_id']]
            await settled(installed,'succeeded')
            upgrade=AppUpgradePreparation(deployment,tmp_path/'release-upgrades')
            await settled(lambda:upgrade.advance(owner=info.fleet_id,source_operation_id=source_id,
                operation_id='new-release',apps=selected,revisions={'agent':new_revision}),'prepared')
            recipe=await upgrade.prepared_recipe(owner=info.fleet_id,operation_id='new-release')
            new=await settled(lambda:deployment.advance(**recipe),'ready')
            identity={**new['prepared']['agent'],'generation':new['prepared']['agent']['generation']+1}
            identity.pop('node_id')
            assert identity['instance_id']!=source['prepared']['agent']['instance_id']
            assert (await invoke('get_agents',chat_id=chat_id))['agents'][0]['instance']['instance_id']==logical
            assert await history()==original_history
            assert (await invoke('chat',chat_id=chat_id,message=[{'role':'user','content':'candidate release turn'}]))['success']
            candidate_history=await history()
            assert 'candidate release turn' in candidate_history
            assert candidate_history.count('PROFILE_TOOL_OK') > original_history.count('PROFILE_TOOL_OK')
            await settled(lambda:stopper.advance(owner=info.fleet_id,operation_id='stop-candidate',
                source_operation_id='new-release',apps=selected),'stopped')
            rollback=await upgrade.rollback_recipe(owner=info.fleet_id,operation_id='new-release',rollback_operation_id='restored-release')
            restored=await settled(lambda:deployment.advance(**rollback['recipe']),'ready')
            identity={**restored['prepared']['agent'],'generation':restored['prepared']['agent']['generation']+1}
            identity.pop('node_id')
            assert identity['instance_id']==source['prepared']['agent']['instance_id']
            assert await history()==original_history
            assert (await invoke('chat',chat_id=chat_id,message=[{'role':'user','content':'restored release turn'}]))['success']
            restored_history=await history()
            assert 'restored release turn' in restored_history
            assert 'candidate release turn' not in restored_history
            assert restored_history.count('PROFILE_TOOL_OK') > original_history.count('PROFILE_TOOL_OK')
            state=await wire.status(info.node_id)
            for key, (digest, generation) in shared.items():
                item=state['instances'][key]
                assert (item['digest'], item['generation'], item['state']) == (digest, generation, 'ready')
            await settled(lambda:stopper.advance(owner=info.fleet_id,operation_id='stop-restored',
                source_operation_id='restored-release',apps=selected),'stopped')
        finally:
            state=await session.wire.status(info.node_id)
            for item in sorted(state['instances'].values(),key=lambda i:i.get('app_id')!='agent'):
                if item['state']=='stopped':continue
                op=await session.wire.submit(info.node_id,'stop',item['digest'],scope=item['scope'],generation=item['generation'])
                async def stopped():return (await session.wire.status(info.node_id))['operations'][op['request']['operation_id']]
                await settled(stopped,'succeeded')
            await resolver.close()
    assert_stopped(children,info)
