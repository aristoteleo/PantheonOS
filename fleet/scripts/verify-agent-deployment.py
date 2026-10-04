"""Opt-in native Fleet acceptance; only Hub directory and model output are fixtures."""
import asyncio
from contextlib import ExitStack
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from pantheon.apps.lifecycle import FleetLifecycle, build_artifact, CHUNK_SIZE
from pantheon.apps.dependency_assembly import DependencyAuthority, DependencyStarter
from pantheon.apps.deployment import AppDeployment
from pantheon.apps.runtime_config import RuntimeCredential
from pantheon.chatroom.deployment import compose_deployment
from pantheon.chatroom.release import build_release_set
from pantheon.apps.release_set import stage_release_set
from pantheon.platform.app_preset import AppPreset, fetch_hub_preset
from pantheon.models.connector_package import build_package as build_connector
from pantheon.models.client import ModelServices
from pantheon.models.manager import ModelServiceManager
from pantheon.models.bootstrap import ModelServiceBootstrap
from pantheon.models.platform_budget import BudgetCredentialPreparer
from pantheon.models.credentials import RemoteModelCredentialVault
from pantheon.platform.dependency_control import OwnerCredentialLifecycle
from pantheon.platform.owner_credentials import provision_owner_credentials

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


async def main(fences):
    start = time.monotonic()
    starter = DependencyStarter(wire,root/'starts',DependencyAuthority(credential=RuntimeCredential(base+'/hub',key),tls_context=ssl.create_default_context()))
    deploy = AppDeployment(starter,root/'deployments')
    digest = await stage('provider-node',build_connector(root/'connector',target))
    connector_apps = {'connector': dict(node_id='provider-node',revision=digest,scope='model-native-model',generation=0,
        bindings={},components={'backend':{'values':{'connector':{'engine':'api','endpoint':engine+'/v1','secret_ref':'node-secret://platform-budget'}}}})}
    class NativeControl:
        async def lifecycle(self, node, method, **data):
            return await wire._request(node, method, **data)
        async def invoke(self, node, app, exact, method, args, timeout):
            return await wire._request(node, 'invoke', app_id=app,
                **{k:exact[k] for k in ('instance_id','revision','generation')},
                payload={'method':method,'args':args}, timeout_seconds=timeout)
    class Resolver:
        _client = NativeControl()
        async def _ensure_client(self): pass
        async def _list_nodes(self, **kwargs):
            return [dict(node_id=node, name='Native release node',
                last_seen=datetime.now(timezone.utc).isoformat(), state={'status':'online'},
                capability={'os':target.split('-')[0], 'arch':target.split('-')[1],
                            'runtimes':{'app-rpc-auth':'1','app-lifecycle':'1','model-credentials':'1'}})
                    for node in ('provider-node', 'consumer-node')]
    directory_client = ModelServices(hub=base+'/hub', token=key)
    manager = ModelServiceManager(client=directory_client, resolver=Resolver())
    login = root/'budget-login'
    login.write_text(key + '-owner-login')
    login.chmod(0o600)
    bootstrap = ModelServiceBootstrap(deploy, manager, root/'model-bootstrap',
        prepare_credentials=BudgetCredentialPreparer(hub=base, token_file=login))
    subprocess.run([sys.executable, str(repo/'apps/shell/build_managed.py'), '--output', str(root/'shell'),
                    '--os', target.split('-')[0], '--arch', target.split('-')[1]], check=True)
    shell_digest = await stage('provider-node',root/'shell')
    shell_instance = await operation('provider-node','start',shell_digest,'native-shell')
    shell = binding('provider-node',shell_instance)
    subprocess.run([sys.executable, str(repo/'apps/file/build_managed.py'), '--output', str(root/'files'), '--platform', target], check=True)
    from agent_mcp_migration import prepare, admit
    # Pin the destination from the ordinary content/scope identity before fencing
    # legacy data. The paired release below must reproduce these exact bytes.
    from pantheon.chatroom.package import build_package
    dependencies = {'shell':{'range':'^0.6.0','uses':['shell@1'],'binding':'runtime'},
                    'file-manager':{'range':'^0.6.9','uses':['fs@1'],'binding':'runtime'},
                    'mcp-gateway':{'range':'^0.8.0','uses':['mcp-tools@1'],'binding':'runtime'}}
    package = build_package(root/'agent-preview',target,version='0.7.0',
        frontend=os.environ['AGENT_APP_BUILD_DIR'],transport=os.environ['AGENT_RELEASE_TRANSPORT'],
        dependencies=dependencies)
    _, agent_revision = build_artifact(package,target)
    agent_id = hashlib.sha256('\0'.join((owner,'consumer-node',agent_revision,'native-agent')).encode()).hexdigest()[:32]
    destination = root.parent/'consumer-node'/'apps'/owner/'data'/agent_id/'agent'
    mcp, mcp_expected, migration = await prepare(root, owner=owner, platform=target,
                                                fences=fences, destination=destination)
    assert dependencies['mcp-gateway']==mcp['dependencies']['mcp-gateway']
    release_set = build_release_set(root/'release-set',version='0.7.0',frontend=os.environ['AGENT_APP_BUILD_DIR'],
        transports={target:os.environ['AGENT_RELEASE_TRANSPORT']},providers={'files':root/'files', 'mcp-provider':root/'mcp-provider'},
        dependencies=dependencies)
    placements = {name:dict(node_id='consumer-node' if name=='agent' else 'provider-node',
                           platform=target,scope='native-'+name,generation=0)
                  for name in ('agent','allocator','model-access','files','mcp-provider')}
    delivery = FleetLifecycle(Resolver())
    targets = await stage_release_set(delivery,release_set,owner=owner,placements=placements)
    assert targets['agent']['revision']==agent_revision
    # An acknowledged upload can be replayed with the same bytes before install.
    assert await stage_release_set(delivery,release_set,owner=owner,placements=placements) == targets
    files_target = targets.pop('files')
    mcp_target = targets.pop('mcp-provider')
    assert mcp_target['revision'] == mcp['artifact']['revision']
    control_setup = dict(hub=base+'/hub', key=key, owner=owner,
        node_ids=['provider-node'], ref_prefix='owner-v1', tls_context=ssl.create_default_context())
    delivered = await provision_owner_credentials(**control_setup)
    assert await provision_owner_credentials(**control_setup) == delivered
    assert key not in json.dumps(delivered)
    refs = delivered['nodes']['provider-node']
    credential_control = OwnerCredentialLifecycle(owner=owner,
        credential=RuntimeCredential(base+'/controller', key), tls_context=ssl.create_default_context())
    try:
        vault = RemoteModelCredentialVault(credential_control, owner=owner, node_id='provider-node')
        try:
            await vault.ensure_async(refs['hub']['ref'], refs['hub']['endpoint'], key + '-conflict')
        except ValueError:
            pass
        else:
            raise AssertionError('Remote delivery replaced an existing credential')
    finally:
        await credential_control.close()
    workspace = root/'workspace';workspace.mkdir()
    plugins = ('task_system','think_system','fleet_system','model_services_system','memory_system','learning_system','compression')
    agent = dict(protocol=1,namespace='native-release',projects=migration['spec']['projects'],active_project='shared',default_project='shared',
        settings={**{p:{'enabled':False} for p in plugins},'default_template_auto_update':False},
        models={'fleet_tiers':{tier:'fleet-model://native-model/example%3A8b' for tier in ('normal','high','low')}},
        dependencies={'allocator':'allocator','profiles':{'toolsets':{'shell':{'alias':'shell','functions':[
            {'name':'run_command_in_shell','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}]}},'mcp_servers':{}}})
    tool_bindings = {'shell':{'app_id':'shell','provider':shell,
        'methods':{'run_command_in_shell':{'arguments':['command'],'bound':{'timeout':2}}},
        'resource':{'kind':'shell','arguments':{'run_command_in_shell':'shell_id'}}}}
    agent['dependencies']['profiles']['toolsets']['file_manager'] = {'alias':'files','functions':[
        {'name':'write_file','parameters':{'type':'object','properties':{'content':{'type':'string'}},'required':['content']}},
        {'name':'read_file','parameters':{'type':'object','properties':{}}}]}
    tool_bindings['files'] = {'app_id':'file-manager', 'provider':{'$app':'files','component':'backend','port':'http'},
        'methods':{'write_file':{'arguments':['content'],'bound':{'file_path':'shared.txt'}},
                   'read_file':{'arguments':[],'bound':{'file_path':'shared.txt'}}}}
    agent['dependencies']['profiles']['mcp_servers'].update(mcp['profiles']['mcp_servers'])
    agent['dependencies']['defaults'] = mcp['defaults']
    tool_bindings.update(mcp['tools'])
    mcp_id = hashlib.sha256('\0'.join((owner,'provider-node',mcp_target['revision'],mcp_target['scope'])).encode()).hexdigest()[:32]
    migrated = admit(migration,mcp,owner=owner,tiers=agent['models']['fleet_tiers'],
        provider=dict(node_id='provider-node',instance_id=mcp_id,revision=mcp_target['revision'],
                      generation=2,component='backend',port='http'))
    recipe = compose_deployment(owner=owner,operation_id='native-release',targets=targets,agent=agent,tools=tool_bindings,
        models={'deployments':{'native-model':{'$model':'connector'}},'routes':{},'allow_wake':False},
        credentials={'agent':{},'allocator':refs,'model-access':{'hub':refs['hub']}},
        provider_apps={'files':{**files_target, 'bindings':{}, 'components':{'backend':{
            'values':{'files':{'workspace':str(workspace)}}, 'credentials':{}}}},
                       **mcp['provider_apps']})
    targets['files'] = files_target
    targets['mcp-provider'] = mcp_target
    for name, field in [('allocator','dependency_binding'),('model-access','model_services')]:
        recipe['apps'][name]['components']['backend']['values'][field]['trust_roots_pem'] = Path(os.environ['SSL_CERT_FILE']).read_text()
    recipe.update(kind='model-services', model_apps={'connector':{
        'app':connector_apps['connector'], 'deployment_id':'native-model', 'name':'Native connector', 'credential_source':'platform-budget',
        'models':[{'id':'example:8b','context_limit':8192}]}})
    post('/fixture/startup', recipe)
    async def load():
        return await fetch_hub_preset(base+'/api/fleet/apps/startup/default', hub=base, token=key, owner=owner)
    async def advance(**spec):
        try:
            return {'success':True,**await bootstrap.advance(**spec)}
        except Exception as error:
            print(type(error).__name__, str(error).replace(key, '<fixture-key>'), flush=True)
            raise
    startup = AppPreset(None,load=load,advance=advance,interval=.1,duration=240)
    startup.start()
    try:
        for attempt in range(2400):
            progress = startup.status()
            if attempt % 50 == 0: print(json.dumps({**progress,'seconds':round(time.monotonic()-start,1)}),flush=True)
            if progress['state']!='pending':break
            await asyncio.sleep(.1)
        else:raise AssertionError('Deployment did not become ready')
        assert progress['state']=='ready',progress
    finally:
        await startup.stop()
    ready = bootstrap.inspect(owner=owner, operation_id='native-release')
    # Re-delivery of an installed release does not run hooks or restart Apps.
    before_delivery = {n:(await wire.status(n))['operations'] for n in ('provider-node','consumer-node')}
    assert await stage_release_set(delivery,release_set,owner=owner,placements=placements) == targets
    assert {n:(await wire.status(n))['operations'] for n in before_delivery} == before_delivery
    # A restarted owner host can resume the receipt without retaining the login.
    login.unlink()
    bootstrap = ModelServiceBootstrap(deploy, manager, root/'model-bootstrap')
    assert await bootstrap.advance(**recipe)==ready
    for folder in ('model-bootstrap', 'deployments', 'starts'):
        for path in (root/folder).rglob('*.json'):
            saved = path.read_text()
            assert key+'-budget' not in saved and key+'-owner-login' not in saved
    result = deploy.inspect(owner=owner,operation_id=bootstrap.child_id(recipe,'consumers'))
    providers = deploy.inspect(owner=owner,operation_id=bootstrap.child_id(recipe,'providers'))
    state = await wire.status('provider-node')
    connector = state['instances'][providers['prepared']['connector']['instance_id']]
    model = binding('provider-node',connector)
    row = await directory_client.deployment('native-model')
    assert row['revision']==1 and row['binding']==model
    assert row['models'][0]['tools'] is True and row['models'][0]['context']==8192
    await directory_client.aclose()
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
    assert catalog['fleet_tiers']==agent['models']['fleet_tiers'],catalog
    template = dict(id='native',name='Native',agents=[dict(id='member',name='Tester',instructions='Reply once',model='normal',toolsets=[])])
    chat = await rpc(live['agent'],'agent','create_chat',chat_name='Native joint deployment',project_name='Shared',template_obj=template)
    assert chat['success'],chat
    reply = await rpc(live['agent'],'agent','chat',chat_id=chat['chat_id'],message=[{'role':'user','content':'Reply once'}])
    assert reply['success'],reply
    history = await messages(live['agent'],chat['chat_id'])
    assert history[-1]['content']=='native fleet reply',history
    # The old factory collapsed named MCP providers when unified MCP was also
    # selected. No separate docs grant/profile should be required after migration.
    first, second = {'chat_id':'legacy-a'}, {'chat_id':'legacy-b'}
    for selected, suffix in ((first,'A'),(second,'B')):
        history = await messages(live['agent'],selected['chat_id'])
        assert history[0]['content']=='preserved history '+suffix,history
        opened = await rpc(live['agent'],'agent','get_agents',chat_id=selected['chat_id'])
        assert opened['success'],opened
    with sqlite3.connect(destination/'instances/instances.sqlite3') as db:
        identities = db.execute("SELECT conversation_id, config_id, instance_id FROM instances WHERE conversation_id LIKE 'legacy-%'").fetchall()
    assert sorted(identities)==sorted((r['conversation_id'],r['config_id'],r['instance_id']) for r in migrated['members'])
    for selected,message,expected in [(first,'NATIVE_SHELL_SET','SHELL_VALUE=owner-a'),
                                      (second,'NATIVE_SHELL_READ','SHELL_VALUE=unset'),
                                      (first,'NATIVE_SHELL_READ','SHELL_VALUE=owner-a')]:
        reply = await rpc(live['agent'],'agent','chat',chat_id=selected['chat_id'],message=[{'role':'user','content':message}])
        assert reply['success'],reply
        history = await messages(live['agent'],selected['chat_id'])
        # The fixture model echoes the real tool response only after a tool call.
        final = history[-1]
        assert final['role']=='assistant' and final['content'].startswith('native tool result: '),final
        result = json.loads(final['content'].removeprefix('native tool result: '))
        assert result['success'] and result['status']=='completed' and result['output'].strip()==expected,result
    for selected, message in [(first,'NATIVE_FILES_WRITE'), (second,'NATIVE_FILES_READ')]:
        reply = await rpc(live['agent'],'agent','chat',chat_id=selected['chat_id'],message=[{'role':'user','content':message}])
        assert reply['success'],reply
        final = (await messages(live['agent'],selected['chat_id']))[-1]
        value = json.loads(final['content'].removeprefix('native tool result: '))
        assert value['success'],value
        if message.endswith('READ'):
            assert value['content']=='shared-by-owner-a' and value['node_id']=='provider-node',value
    assert (workspace/'shared.txt').read_text()=='shared-by-owner-a'
    async def check_mcp(selected):
        reply = await rpc(live['agent'],'agent','chat',chat_id=selected['chat_id'],
                          message=[{'role':'user','content':'NATIVE_MCP_CHECK'}])
        assert reply['success'],reply
        final = (await messages(live['agent'],selected['chat_id']))[-1]
        value = json.loads(final['content'].removeprefix('native tool result: '))
        assert {k:v for k,v in value.items() if k not in ('pid','count')} == mcp_expected,value
        assert type(value['pid']) is int and value['pid'] not in pids,value
        return value
    mcp_first, mcp_second = await check_mcp(first), await check_mcp(second)
    assert mcp_first['pid']==mcp_second['pid'] and mcp_second['count']==mcp_first['count']+1
    session_paths = list(root.parent.rglob('dependency-owner/sessions/*.json'))
    sessions = [json.loads(p.read_text()) for p in session_paths]
    assert len(sessions)==2 and len({s['receipt']['session_id'] for s in sessions})==2,sessions
    assert len({s['recipe']['owner_ref'] for s in sessions})==2,sessions
    assert all(s['receipt']['state']=='active' for s in sessions),sessions
    binding_paths = list(root.parent.rglob('dependency-owner/bindings/*.json'))
    assert len(binding_paths)==2,binding_paths
    file_grants = [json.loads(p.read_text())['renewals']['files'] for p in binding_paths]
    assert len({g['grant_id'] for g in file_grants})==2
    assert file_grants[0]['provider']==file_grants[1]['provider']
    assert all(set(json.loads(p.read_text())['plan']['sessions'])=={'shell'} for p in binding_paths)
    mcp_grants = [json.loads(p.read_text())['renewals']['mcp-shared'] for p in binding_paths]
    assert len({g['grant_id'] for g in mcp_grants})==2
    assert mcp_grants[0]['provider']==mcp_grants[1]['provider']=={**live['mcp-provider'],'fleet_id':owner}
    deleted = await rpc(live['agent'],'agent','delete_chat',chat_id=first['chat_id'])
    assert deleted['success'] and len(deleted['resource_outcomes'])==1,deleted
    assert all(value=='released' for resources in deleted['resource_outcomes'].values() for value in resources.values()),deleted
    assert await rpc(live['agent'],'agent','delete_chat',chat_id=first['chat_id'])==deleted
    assert sorted(json.loads(p.read_text())['receipt']['state'] for p in session_paths)==['active','released']
    rejected = await rpc(live['agent'],'agent','chat',chat_id=first['chat_id'],message=[{'role':'user','content':'NATIVE_SHELL_READ'}])
    assert not rejected['success'],rejected
    reply = await rpc(live['agent'],'agent','chat',chat_id=second['chat_id'],message=[{'role':'user','content':'NATIVE_SHELL_READ'}])
    assert reply['success'],reply
    final = (await messages(live['agent'],second['chat_id']))[-1]
    result = json.loads(final['content'].removeprefix('native tool result: '))
    assert result['success'] and result['output'].strip()=='SHELL_VALUE=unset',result
    reply = await rpc(live['agent'],'agent','chat',chat_id=second['chat_id'],message=[{'role':'user','content':'NATIVE_FILES_READ'}])
    assert reply['success'],reply
    final = (await messages(live['agent'],second['chat_id']))[-1]
    value = json.loads(final['content'].removeprefix('native tool result: '))
    assert value['success'] and value['content']=='shared-by-owner-a' and value['node_id']=='provider-node',value
    mcp_again = await check_mcp(second)
    assert mcp_again['pid']==mcp_second['pid'] and mcp_again['count']==mcp_second['count']+1
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
                # Issued grants omit state until maintenance records renewal or
                # revocation. Keep waiting for explicit revocation of every grant.
                and all(r.get('state')=='revoked' for b in bindings for r in b['renewals'].values())):break
        await asyncio.sleep(.1)
    else:raise AssertionError(('Consumer stop did not release owned sessions',sessions))
    for s in sessions:
        receipt = await rpc(shell,'shell','resource_session_get',owner_ref=s['recipe']['owner_ref'],lease_id=s['lease_id'])
        assert receipt['state']=='released',receipt
    value = await rpc(live['files'],'file-manager','read_file',file_path='shared.txt')
    assert value['success'] and value['content']=='shared-by-owner-a' and value['node_id']=='provider-node',value
    # Agent retirement revokes grants, but must not kill a shared MCP provider.
    value = await rpc(live['mcp-provider'],'mcp-gateway','docs_check')
    assert value['pid']==mcp_again['pid'] and value['count']==mcp_again['count']+1,value
    # Restart the migrated Agent and its generation-bound control providers.
    # Model Connector, Shell, Files and MCP stay alive on their original nodes.
    from pantheon.apps.deployment_restart import plan_restart
    t = targets['allocator']
    await operation(t['node_id'],'stop',t['revision'],t['scope'],live['allocator']['generation'])
    before_restart = {n:await wire.status(n) for n in ('provider-node','consumer-node')}
    restart_recipe = await plan_restart(deploy,owner=owner,
        source_operation_id=bootstrap.child_id(recipe,'consumers'),operation_id='native-agent-restart',
        apps=['agent','allocator','model-access'])
    old_agent = dict(live['agent'])
    for attempt in range(600):
        # Owner process objects are disposable; the original operation persists.
        resumed = AppDeployment(deploy.starter,deploy.root)
        restarted = await resumed.advance(owner=owner,operation_id='native-agent-restart',
            apps=restart_recipe['apps'] if attempt==0 else None)
        if restarted['state']=='ready':break
        assert restarted['state']=='pending',restarted
        await asyncio.sleep(.1)
    else:raise AssertionError('Agent restart did not become ready')
    for name in restart_recipe['apps']:
        t = targets[name]
        instance = (await wire.status(t['node_id']))['instances'][live[name]['instance_id']]
        assert instance['generation']==5 and instance['state']=='ready',instance
        assert instance['resources'][0]['pid'] not in pids,instance
        live[name] = binding(t['node_id'],instance)
    for node, before in before_restart.items():
        after = await wire.status(node)
        new_ops = after['operations'].keys()-before['operations'].keys()
        assert all(after['operations'][op]['request']['action']!='install' for op in new_ops)
        for instance_id, instance in before['instances'].items():
            if instance['state']=='ready':
                current = after['instances'][instance_id]
                assert current['generation']==instance['generation'] and current['resources']==instance['resources']
    catalog = await rpc(live['agent'],'agent','list_available_models')
    assert catalog['fleet_catalog_ready'] and catalog['fleet_models'][0]['value']==ref,catalog
    history = await messages(live['agent'],second['chat_id'])
    assert history[0]['content']=='preserved history B' and history[-1]['role']=='assistant',history
    with sqlite3.connect(destination/'instances/instances.sqlite3') as db:
        remaining = db.execute("SELECT conversation_id, config_id, instance_id FROM instances WHERE conversation_id = 'legacy-b'").fetchall()
    assert remaining==[r for r in identities if r[0]=='legacy-b'],remaining
    rejected = await rpc(live['agent'],'agent','chat',chat_id=first['chat_id'],message=[{'role':'user','content':'NATIVE_SHELL_READ'}])
    assert not rejected['success'],rejected
    for message in ('NATIVE_SHELL_READ','NATIVE_FILES_READ'):
        reply = await rpc(live['agent'],'agent','chat',chat_id=second['chat_id'],message=[{'role':'user','content':message}])
        assert reply['success'],reply
        final = (await messages(live['agent'],second['chat_id']))[-1]
        output = json.loads(final['content'].removeprefix('native tool result: '))
        assert output['success'],output
        if message=='NATIVE_SHELL_READ':assert output['output'].strip()=='SHELL_VALUE=unset',output
        else:assert output['content']=='shared-by-owner-a',output
    mcp_restart = await check_mcp(second)
    assert mcp_restart['pid']==value['pid'] and mcp_restart['count']==value['count']+1,mcp_restart
    new_sessions = list(set(root.parent.rglob('dependency-owner/sessions/*.json'))-set(session_paths))
    assert len(new_sessions)==1,new_sessions
    new_session = json.loads(new_sessions[0].read_text())
    assert new_session['receipt']['state']=='active',new_session
    assert new_session['receipt']['session_id'] not in {s['receipt']['session_id'] for s in sessions}
    assert all(json.loads(p.read_text())['receipt']['state']=='released' for p in session_paths)
    new_bindings = list(set(root.parent.rglob('dependency-owner/bindings/*.json'))-set(binding_paths))
    assert len(new_bindings)==1,new_bindings
    grants = json.loads(new_bindings[0].read_text())['renewals']
    assert all(g['consumer']['generation']==5 and g['consumer']['instance_id']==old_agent['instance_id'] for g in grants.values())
    assert grants['mcp-shared']['provider']==mcp_grants[0]['provider']
    t = targets['agent'];await operation(t['node_id'],'stop',t['revision'],t['scope'],live['agent']['generation'])
    for _ in range(400):
        new_session = json.loads(new_sessions[0].read_text())
        grants = json.loads(new_bindings[0].read_text())['renewals']
        if new_session['receipt']['state']=='released' and all(g.get('state')=='revoked' for g in grants.values()):break
        await asyncio.sleep(.1)
    else:raise AssertionError('Restarted Agent did not retire its new sessions and grants')
    t = targets['model-access'];await operation(t['node_id'],'stop',t['revision'],t['scope'],live['model-access']['generation'])
    t = targets['mcp-provider']
    stopped = await operation(t['node_id'],'stop',t['revision'],t['scope'],live['mcp-provider']['generation'])
    for _ in range(100):
        try: os.kill(value['pid'],0)
        except ProcessLookupError: break
        await asyncio.sleep(.05)
    else: raise AssertionError('Stopping the MCP App left its stdio child alive')
    # Restart through normal prepared configuration, not an ad-hoc child spawn.
    # The same reviewed vault reference must still work in the new generation.
    restart = {**mcp['provider_apps']['mcp-provider'], 'generation':stopped['generation']}
    for attempt in range(600):
        restarted = await deploy.advance(owner=owner,operation_id='native-mcp-restart',
            apps={'mcp-provider':restart} if attempt==0 else None)
        if restarted['state']=='ready': break
        assert restarted['state']=='pending',restarted
        await asyncio.sleep(.1)
    else: raise AssertionError('MCP restart did not become ready')
    state = await wire.status(t['node_id'])
    instance = state['instances'][restarted['prepared']['mcp-provider']['instance_id']]
    fresh = binding(t['node_id'],instance)
    assert fresh['generation'] > live['mcp-provider']['generation']
    after_restart = await rpc(fresh,'mcp-gateway','docs_check')
    assert {k:v for k,v in after_restart.items() if k not in ('pid','count')} == mcp_expected,after_restart
    assert after_restart['pid']!=value['pid'] and after_restart['count']==1,after_restart
    await operation(t['node_id'],'stop',t['revision'],t['scope'],fresh['generation'])
    for _ in range(100):
        try: os.kill(after_restart['pid'],0)
        except ProcessLookupError: break
        await asyncio.sleep(.05)
    else: raise AssertionError('Restarted MCP App left its stdio child alive')
    assert json.loads((destination/'migration.json').read_text())['phase']=='committed'
    # New Agent writes and chat deletion must not alter the fenced source.
    from pantheon.chatroom.migration_import import _unchanged_sources
    source_manifest = json.loads((Path(migration['backup']['directory'])/'manifest.json').read_text())
    try:
        _unchanged_sources(source_manifest)
    except ValueError:
        from pantheon.chatroom.migration_backup import _plan
        current = _plan(source_manifest['spec'],max_bytes=max(1,source_manifest['total_bytes'])*2)
        old = {f['source']:f for f in source_manifest['files']}
        new = {f['source']:f for f in current['files']}
        # Identify files/metadata only; never log backed-up credential contents.
        print(json.dumps({'changed_sources':[p for p in old.keys()|new.keys() if old.get(p)!=new.get(p)],
                          'inventory_changes':[k for k in current['inventory'] if current['inventory'][k]!=source_manifest['inventory'].get(k)],
                          'issues_before':source_manifest['inventory']['issues'],
                          'issues_after':current['inventory']['issues']}),flush=True)
        raise
    t = targets['files'];await operation(t['node_id'],'stop',t['revision'],t['scope'],live['files']['generation'])
    t = targets['allocator'];await operation(t['node_id'],'stop',t['revision'],t['scope'],live['allocator']['generation'])
    await operation('provider-node','stop',shell_digest,'native-shell',shell['generation'])
    await operation('provider-node','stop',digest,'model-native-model',model['generation'])
    for node in ('consumer-node','provider-node'):
        state = await wire.status(node)
        for instance in state['instances'].values():
            if instance['scope'].startswith(('native-', 'model-native-')):
                assert instance['state']=='stopped' and not instance.get('resources'),instance
    print(json.dumps({'ok':True,'native_apps':7,'inference':'connector + scoped HTTP gateway + SSE',
        'tools':'migrated histories and member identities; Agent restart, renewed Model Services/tool grants, shared providers and cleanup',
        'seconds':round(time.monotonic()-start,2)}),flush=True)


with ExitStack() as fences:
    asyncio.run(main(fences))
