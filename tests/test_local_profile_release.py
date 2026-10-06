"""Saved profile releases retain data across whole-Fleet lifetimes and rollback."""
from copy import deepcopy
import asyncio
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
from pantheon.platform.local_profile import LocalAppProfile
from pantheon.platform.local_profile_release import release_target,review_release,approve_release,ReleaseJournal
from test_local_fleet import binaries,assert_stopped
from test_local_profile import profile_manifest,minimal_manifest,settle
from test_local_model_http import model_endpoint


def test_release_control_imports_without_agent():
    proc=subprocess.run([sys.executable,'-c',
        "import sys; import pantheon.platform.local_profile_release; assert not any(n=='pantheon.agent' or n.startswith('pantheon.chatroom') for n in sys.modules)"],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr


@pytest.mark.parametrize('change',['settings','scope','topology','model','unchanged'])
def test_release_does_not_silently_change_owner_choices(tmp_path,change):
    source=minimal_manifest(tmp_path);target=deepcopy(source)
    target['packages']['consumer']['revision']='c'*64
    if change=='settings':target['apps']['consumer']['components']={'backend':{'values':{'secret':'not-exposed'}}}
    elif change=='scope':target['apps']['consumer']['scope']='elsewhere'
    elif change=='topology':target['apps']['other']=deepcopy(target['apps']['consumer'])
    elif change=='model':target['model_apps']={'new':{}}
    else:target=source
    with pytest.raises(AssemblyError):release_target(source,target)


async def approve(session,target,review_id,**kwargs):
    async with asyncio.timeout(90):
        while True:
            result=await approve_release(session,target,review_id,**kwargs)
            if result['state']=='approved':return result
            assert result['state']=='pending'
            await asyncio.sleep(.05)


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_ack',[False,True])
async def test_native_profile_release_reopen_and_retained_rollback(tmp_path,binaries,model_endpoint,monkeypatch,lost_ack):
    source=profile_manifest(tmp_path,model_endpoint.url)
    target=deepcopy(source)
    for version,spec in [('1.0.0',source),('1.1.0',target)]:
        path=tmp_path/'consumer' if version=='1.0.0' else tmp_path/'candidate'
        if version!='1.0.0':shutil.copytree(tmp_path/'consumer',path)
        for file in ('app.json','fleet.json'):
            value=json.loads((path/file).read_text());value['version']=version
            if file=='fleet.json':
                value['components'][0]['argv']=value['components'][0]['argv'][:2]+['${DATA}/history.json']
                value['data_schema']={'id':'profile-history','version':1,'accepts':[1]}
            else:value['dataSchema']={'id':'profile-history','version':1,'accepts':[1]}
            (path/file).write_text(json.dumps(value))
        # On the second copy replace the injected source version, not its server.
        code=(path/'server.py').read_text()
        if version=='1.0.0':
            code=code.replace('import os',"import os,sys,json\nfrom pathlib import Path\np=Path(sys.argv[1])\nv=json.loads(p.read_text()) if p.exists() else []\nv.append('1.0.0')\np.write_text(json.dumps(v))",1)
        else:code=code.replace("v.append('1.0.0')","v.append('1.1.0')")
        (path/'server.py').write_text(code)
        spec['packages']['consumer']={'path':str(path),'revision':build_artifact(path)[1]}
    original_id=candidate_id=review_id=None
    for cycle,current in enumerate((source,target,target,source),1):
        async with LocalFleet(tmp_path/'profile',binaries,workspace=tmp_path) as runtime:
            info=runtime.coordinates;children=list(runtime._children)
            nc=await nats.connect(info.nats,user_credentials=str(info.credentials),inbox_prefix=('_INBOX_'+info.fleet_id).encode())
            resolver=AppInstanceResolver(info.fleet_id,info.node_id,info.fleet_id,str(tmp_path),connection=nc)
            session=LocalAppProfile(runtime,current,resolver)
            try:
                assert (await settle(session,'advance'))['cycle']==cycle
                identity=session.app_binding('consumer')['instance_id']
                data=runtime.root/'node/apps'/info.fleet_id/'data'
                if cycle==1:original_id=identity
                elif cycle==2:candidate_id=identity;assert candidate_id!=original_id
                elif cycle==3:assert identity==candidate_id
                else:assert identity==original_id
                expected=[['1.0.0'],['1.0.0','1.1.0'],['1.0.0','1.1.0','1.1.0'],['1.0.0','1.0.0']][cycle-1]
                assert json.loads((data/identity/'history.json').read_text())==expected
                if cycle>1:
                    assert json.loads((data/candidate_id/'history.json').read_text())==(['1.0.0','1.1.0'] if cycle==2 else ['1.0.0','1.1.0','1.1.0'])
                assert (await settle(session,'stop'))['state']=='stopped'
                if cycle in (1,3):
                    next_spec=target if cycle==1 else source
                    kwargs={} if cycle==1 else {'rollback_of':review_id}
                    review=review_release(session,next_spec,**kwargs)
                    before=session.path.read_bytes()
                    if cycle==1 and lost_ack:
                        original=ReleaseJournal._write
                        def interrupted(journal,path,value):
                            original(journal,path,value)
                            if path.name=='approved-release.json':raise OSError('lost approval acknowledgement')
                        monkeypatch.setattr(ReleaseJournal,'_write',interrupted)
                        with pytest.raises(OSError,match='lost approval'):await approve(session,next_spec,review['review_id'])
                        monkeypatch.setattr(ReleaseJournal,'_write',original)
                        session=LocalAppProfile(runtime,current,resolver)
                    result=await approve(session,next_spec,review['review_id'],**kwargs)
                    assert result['data_policy']==('copy-source-data' if cycle==1 else 'retained-source-data')
                    if cycle==1:review_id=review['review_id']
                    assert session.path.read_bytes()==before
                    state=await session.wire.status(info.node_id)
                    assert all(v['state']=='stopped' and not v.get('resources') and not v.get('reservations') for v in state['instances'].values())
            finally:
                await resolver.close()
        assert_stopped(children,info)
