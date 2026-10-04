"""Opt-in native Fleet acceptance; only Hub directory and model output are fixtures."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact, CHUNK_SIZE
from pantheon.apps.dependency_assembly import DependencyAuthority, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.deployment import compose_deployment
from pantheon.chatroom.package import build_package as build_agent
from pantheon.platform.dependency_package import build_package as build_allocator
from pantheon.platform.model_dependency_package import build_package as build_access

base, key, owner, engine, directory = sys.argv[1:]
root = Path(directory)
root.mkdir(mode=0o700)
repo = Path(__file__).resolve().parents[2]
target = sys.platform + '-' + {'arm64':'arm64','aarch64':'arm64','x86_64':'amd64'}[platform.machine()]


def post(path, body):
    req = Request(base+path, data=json.dumps(body).encode(), headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urlopen(req, timeout=100) as response: result = json.load(response)
    if result.get('error'): raise RuntimeError(result['error'])
    return result


class Wire(FleetLifecycle):
    async def _request(self, node, method, **data):
        return await asyncio.to_thread(post, '/fixture/node/'+node, dict(type='app_lifecycle',protocol=1,method=method,**data))


wire = Wire(None)


async def stage(node, package):
    payload, digest = await asyncio.to_thread(build_artifact, package, target)
    for offset in range(0,len(payload),CHUNK_SIZE):
        await wire._request(node,'stage',digest=digest,offset=offset,data=base64.b64encode(payload[offset:offset+CHUNK_SIZE]).decode())
    return digest


async def operation(node, action, digest, scope, generation=0):
    op = await wire.submit(node,action,digest,scope=scope,generation=generation)
    for _ in range(600):
        state = await wire.status(node)
        current = state['operations'][op['request']['operation_id']]
        if current['state']=='succeeded':
            return next(i for i in state['instances'].values() if i['digest']==digest and i['scope']==scope)
        assert current['state'] in ('queued','running'), current
        await asyncio.sleep(.1)
    raise AssertionError('Native lifecycle operation timed out')


async def rpc(binding, app, method, **args):
    response = await wire._request(binding['node_id'],'invoke',app_id=app,
        **{k:binding[k] for k in ('instance_id','revision','generation')},
        payload={'method':method,'args':args},timeout_seconds=60)
    result = response['response']
    if app == 'model-service': return result
    assert result['success'],result
    return result['result']


def binding(node, instance):
    return dict(node_id=node,instance_id=instance['instance_id'],revision=instance['digest'],generation=instance['generation'],component='backend',port='http')


async def messages(agent, chat_id):
    snapshot = await rpc(agent,'agent','open_agent_history',chat_id=chat_id)
    try:
        parts = [await rpc(agent,'agent','read_agent_history',chat_id=chat_id,
                           snapshot_id=snapshot['snapshot_id'],part=i) for i in range(snapshot['parts'])]
        raw = ''.join(p['json_fragment'] for p in parts).encode('ascii')
        assert len(raw)==snapshot['size'] and hashlib.sha256(raw).hexdigest()==snapshot['sha256']
        return json.loads(raw)['messages']
    finally:
        await rpc(agent,'agent','release_agent_history',chat_id=chat_id,snapshot_id=snapshot['snapshot_id'])


async def main():
    start = time.monotonic()
    digest = await stage('provider-node',repo/'apps/model-service')
    connector = await operation('provider-node','start',digest,'native-model')
    model = binding('provider-node',connector)
    configured = await rpc(model,'model-service','configure',config={'engine':'ollama','endpoint':engine})
    discovered = await rpc(model,'model-service','discover')
    assert discovered['models'][0]['id']=='example:8b'
    row = dict(deployment_id='native-model',name='Native connector',node_id='provider-node',node_name='Native provider',engine='ollama',state='ready',revision=1,
        config_revision=configured['config_revision'],binding=model,models=[dict(id='example:8b',operations=['text'],tools=True,context=8192)])
    post('/fixture/directory',{'deployments':[row]})
    subprocess.run([sys.executable, str(repo/'apps/shell/build_managed.py'), '--output', str(root/'shell'),
                    '--os', target.split('-')[0], '--arch', target.split('-')[1]], check=True)
    shell_digest = await stage('provider-node',root/'shell')
    shell_instance = await operation('provider-node','start',shell_digest,'native-shell')
    shell = binding('provider-node',shell_instance)
    packages = {'agent':build_agent(root/'agent',target,version='0.7.0',frontend=os.environ['AGENT_APP_BUILD_DIR'],transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies={'shell':{'range':'^0.6.0','uses':['shell@1'],'binding':'runtime'}}),
        'allocator':build_allocator(root/'allocator',target),'model-access':build_access(root/'access',target)}
    targets = {}
    for name,package in packages.items():
        node = 'consumer-node' if name=='agent' else 'provider-node'
        targets[name] = dict(node_id=node,revision=await stage(node,package),scope='native-'+name,generation=0)
    for name in ('hub','controller'):
        post('/fixture/secret',dict(Node='provider-node',Ref='node-secret://'+name,Endpoint=base+'/'+name,Key=key))
    refs = {n:{'ref':'node-secret://'+n,'endpoint':base+'/'+n} for n in ('hub','controller')}
    workspace = root/'workspace';workspace.mkdir()
    plugins = ('task_system','think_system','fleet_system','model_services_system','memory_system','learning_system','compression')
    agent = dict(protocol=1,namespace='native-release',projects=[dict(id='shared',name='Shared',path=str(workspace))],active_project='shared',default_project='shared',
        settings={**{p:{'enabled':False} for p in plugins},'default_template_auto_update':False},models={},
        dependencies={'allocator':'allocator','profiles':{'toolsets':{'shell':{'alias':'shell','functions':[
            {'name':'run_command_in_shell','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}]}},'mcp_servers':{}}})
    tool_bindings = {'shell':{'app_id':'shell','provider':shell,
        'methods':{'run_command_in_shell':{'arguments':['command'],'bound':{'timeout':2}}},
        'resource':{'kind':'shell','arguments':{'run_command_in_shell':'shell_id'}}}}
    recipe = compose_deployment(owner=owner,operation_id='native-release',targets=targets,agent=agent,tools=tool_bindings,
        models={'deployments':{'native-model':model},'routes':{},'allow_wake':False},
        credentials={'agent':{},'allocator':refs,'model-access':{'hub':refs['hub']}})
    for name, field in [('allocator','dependency_binding'),('model-access','model_services')]:
        recipe['apps'][name]['components']['backend']['values'][field]['trust_roots_pem'] = Path(os.environ['SSL_CERT_FILE']).read_text()
    starter = DependencyStarter(wire,root/'starts',DependencyAuthority(credential=RuntimeCredential(base+'/hub',key),tls_context=ssl.create_default_context()))
    deploy = AppDeployment(starter,root/'deployments')
    for attempt in range(1800):
        result = await deploy.advance(owner=owner,operation_id='native-release',apps=recipe['apps'] if attempt==0 else None)
        if attempt % 50 == 0: print(json.dumps({'phase':result['phase'],'app':result['app'],'seconds':round(time.monotonic()-start,1)}),flush=True)
        if result['state']=='ready':break
        await asyncio.sleep(.1)
    else:raise AssertionError('Deployment did not become ready')
    assert await deploy.advance(owner=owner,operation_id='native-release')==result
    live = {}
    pids = {connector['resources'][0]['pid'],shell_instance['resources'][0]['pid']}
    assert len(pids)==2
    for name,t in targets.items():
        state = await wire.status(t['node_id']);instance = state['instances'][result['prepared'][name]['instance_id']]
        assert instance['state']=='ready' and instance['generation']==2,instance
        pid = instance['resources'][0]['pid']
        assert pid not in pids, 'Apps unexpectedly share a backend process'
        pids.add(pid)
        live[name] = binding(t['node_id'],instance)
    catalog = await rpc(live['agent'],'agent','list_available_models')
    assert catalog['fleet_catalog_ready'] and len(catalog['fleet_models'])==1,catalog
    ref = catalog['fleet_models'][0]['value']
    assert ref=='fleet-model://native-model/example%3A8b' and not catalog['fleet_models'][0]['disabled'],catalog
    template = dict(id='native',name='Native',agents=[dict(id='member',name='Tester',instructions='Reply once',model=ref,toolsets=[])])
    chat = await rpc(live['agent'],'agent','create_chat',chat_name='Native joint deployment',project_name='Shared',template_obj=template)
    assert chat['success'],chat
    reply = await rpc(live['agent'],'agent','chat',chat_id=chat['chat_id'],message=[{'role':'user','content':'Reply once'}])
    assert reply['success'],reply
    history = await messages(live['agent'],chat['chat_id'])
    assert history[-1]['content']=='native fleet reply',history
    template['agents'][0]['toolsets'] = ['shell']
    first = await rpc(live['agent'],'agent','create_chat',chat_name='Shell owner A',project_name='Shared',template_obj=template)
    second = await rpc(live['agent'],'agent','create_chat',chat_name='Shell owner B',project_name='Shared',template_obj=template)
    assert first['success'] and second['success'],(first,second)
    for selected,message,expected in [(first,'NATIVE_SHELL_SET','SHELL_VALUE=owner-a'),
                                      (second,'NATIVE_SHELL_READ','SHELL_VALUE=unset'),
                                      (first,'NATIVE_SHELL_READ','SHELL_VALUE=owner-a')]:
        reply = await rpc(live['agent'],'agent','chat',chat_id=selected['chat_id'],message=[{'role':'user','content':message}])
        assert reply['success'],reply
        history = await messages(live['agent'],selected['chat_id'])
        # The fixture model echoes the real tool response only after a tool call.
        final = history[-1]
        assert final['role']=='assistant' and final['content'].startswith('native shell result: '),final
        result = json.loads(final['content'].removeprefix('native shell result: '))
        assert result['success'] and result['status']=='completed' and result['output'].strip()==expected,result
    session_paths = list(root.parent.rglob('dependency-owner/sessions/*.json'))
    sessions = [json.loads(p.read_text()) for p in session_paths]
    assert len(sessions)==2 and len({s['receipt']['session_id'] for s in sessions})==2,sessions
    assert len({s['recipe']['owner_ref'] for s in sessions})==2,sessions
    assert all(s['receipt']['state']=='active' for s in sessions),sessions
    binding_paths = list(root.parent.rglob('dependency-owner/bindings/*.json'))
    assert len(binding_paths)==2,binding_paths
    await operation('provider-node','stop',targets['model-access']['revision'],targets['model-access']['scope'],live['model-access']['generation'])
    catalog = await rpc(live['agent'],'agent','list_available_models')
    assert not catalog['fleet_models'] and not catalog['fleet_catalog_ready'],catalog
    t = targets['agent'];await operation(t['node_id'],'stop',t['revision'],t['scope'],live['agent']['generation'])
    # Keep the shared provider and allocator alive: only owner maintenance may
    # retire these sessions. Stopping Shell itself would hide a cleanup bug.
    for _ in range(400):
        sessions = [json.loads(p.read_text()) for p in session_paths]
        bindings = [json.loads(p.read_text()) for p in binding_paths]
        if (all(s['phase']=='terminal' and s['receipt']['state']=='released' for s in sessions)
                and all(r['state']=='revoked' for b in bindings for r in b['renewals'].values())):break
        await asyncio.sleep(.1)
    else:raise AssertionError(('Consumer stop did not release owned sessions',sessions))
    for s in sessions:
        receipt = await rpc(shell,'shell','resource_session_get',owner_ref=s['recipe']['owner_ref'],lease_id=s['lease_id'])
        assert receipt['state']=='released',receipt
    t = targets['allocator'];await operation(t['node_id'],'stop',t['revision'],t['scope'],live['allocator']['generation'])
    await operation('provider-node','stop',shell_digest,'native-shell',shell['generation'])
    await operation('provider-node','stop',digest,'native-model',model['generation'])
    for node in ('consumer-node','provider-node'):
        state = await wire.status(node)
        for instance in state['instances'].values():
            if instance['scope'].startswith('native-'):
                assert instance['state']=='stopped' and not instance.get('resources'),instance
    print(json.dumps({'ok':True,'native_apps':5,'inference':'connector + scoped HTTP gateway + SSE',
        'tools':'two isolated logical owners through one native Shell App; released after consumer stop',
        'seconds':round(time.monotonic()-start,2)}),flush=True)


asyncio.run(main())
