"""Real ordinary HTTP host + prepared Agent + local HTTP/SSE model fixture.

No live Fleet deployment, paid model or installed-release claim. The same native
entry is used by the package, without a TCP/NATS worker or legacy composition.
"""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from test_agent_application import TEMPLATE
from test_agent_event_store import messages
from test_agent_launch import prepared
from test_agent_model_scope import endpoint as model_endpoint

ROOT = Path(__file__).resolve().parents[1]
BOOT = '''
import importlib.abc, runpy, sys
class NoLegacy(importlib.abc.MetaPathFinder):
 def find_spec(self,name,*args):
  if name in ('pantheon.chatroom.room','pantheon.chatroom.start','pantheon.platform.service','pantheon.repl'):
   raise AssertionError('Agent imported combined host: '+name)
sys.meta_path.insert(0,NoLegacy())
from pantheon.remote import RemoteBackendFactory
def forbidden(*args,**kwargs):raise AssertionError('HTTP App constructed ambient RPC transport')
RemoteBackendFactory.create_backend=forbidden
path=sys.argv.pop(1)
sys.path.insert(0,str(__import__('pathlib').Path(path).parent))
runpy.run_path(path,run_name='__main__')
'''


@contextmanager
def native_process(root, model_url):
    package = root/'package'
    package.mkdir(exist_ok=True)
    (root/'workspace').mkdir(exist_ok=True)
    (package/'app.json').write_text(json.dumps({'id':'agent-native-test','name':'Agent',
        'version':'1.0.0','entry':{'backend':'backend.py'}}))
    (package/'backend.py').write_text('from pantheon.chatroom.native import register\n')
    for source in (ROOT/'apps/desktop/app_runtime.py', ROOT/'pantheon/apps/portable_runtime/host.py'):
        shutil.copyfile(source, package/source.name)
    value = prepared(root, model_url)
    snapshot = root/'configuration.json'
    snapshot.write_text(json.dumps(value))
    snapshot.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PANTHEON_', 'NATS_', 'FLEET_'))}
    env.update(HOME=str(root/'home'), PYTHONPATH=str(ROOT),
        PANTHEON_APP_CONFIG=str(snapshot), PANTHEON_FLEET_ID=value['owner'],
        PANTHEON_NODE_ID=value['node_id'], PANTHEON_INSTANCE_ID=value['instance_id'],
        PANTHEON_APP_REVISION=value['revision'], PANTHEON_INSTANCE_GENERATION='1',
        PANTHEON_COMPONENT_NAME='backend', PANTHEON_APP_RPC_TOKEN='native-test-token',
        PANTHEON_PORT_HTTP=str(port), OPENAI_API_KEY='ambient-forbidden', LLM_FORCE_PROXY='true')
    with (root/'process.log').open('a') as log:
        child = subprocess.Popen([sys.executable,'-c',BOOT,str(package/'host.py'),'start',
            '--package',str(package),'--data',str(root/'data')],cwd=root,env=env,stdout=log,stderr=log)
        try:
            yield child, f'http://127.0.0.1:{port}'
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
                raise AssertionError('Agent host failed to drain')


async def request(base, path, body=None, token='native-test-token'):
    def send():
        req = Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type':'application/json','X-Fleet-RPC-Token':token})
        with urlopen(req,timeout=20) as response:
            return json.load(response)
    return await asyncio.to_thread(send)


@pytest.mark.asyncio
async def test_http_agent_chat_events_restart_and_clean_drain(tmp_path, model_endpoint):
    template = {**TEMPLATE, 'agents':[{**TEMPLATE['agents'][0],'toolsets':[]}]}
    chat_id = cursor = instance_id = snapshot = None
    for cycle in range(2):
        with native_process(tmp_path, model_endpoint.url) as (child, base):
            for _ in range(200):
                try:
                    health = await request(base,'/health')
                    break
                except OSError:
                    assert child.poll() is None, (tmp_path/'process.log').read_text()[-12000:]
                    await asyncio.sleep(.05)
            else:
                pytest.fail('HTTP Agent never became ready')
            assert health['ready'] and 'read_agent_events' in health['methods']
            assert not any(name in health['methods'] for name in ('fleet_app_deploy','restart'))
            async def rpc(method, **args):
                response = await request(base,'/rpc',dict(method=method,args=args,timeout_s=15))
                assert response['success'], response
                return response['result']
            assert await rpc('get_agent_app_info') == {
                'protocol': 1, 'history_protocol': 1, 'event_protocol': 1,
            }
            with pytest.raises(HTTPError) as denied:
                await request(base,'/rpc',dict(method='list_chats',args={}),token='')
            assert denied.value.code == 403
            if cycle == 0:
                created = await rpc('create_chat',chat_name='Native App',project_name='Shared',template_obj=template)
                assert created['success'], created
                chat_id = created['chat_id']
            else:
                chats = await rpc('list_chats',project_name='Shared')
                assert chat_id in {chat['id'] for chat in chats['chats']}
                resumed = await rpc('read_agent_events',chat_id=chat_id,cursor=cursor)
                assert not resumed['reset_required'] and not resumed['events']
                saved = await rpc('read_agent_history', chat_id=chat_id,
                                  snapshot_id=snapshot['snapshot_id'], part=0)
                assert 'scoped reply' in saved['json_fragment']
                await rpc('release_agent_history', chat_id=chat_id, snapshot_id=snapshot['snapshot_id'])
            agents = await rpc('get_agents',chat_id=chat_id)
            identity = agents['agents'][0]['instance']['instance_id']
            assert instance_id in (None,identity)
            instance_id = identity
            result = await rpc('chat',chat_id=chat_id,message=[{'role':'user','content':f'turn {cycle}'}])
            assert result['success'], result
            pages = []
            while True:
                page = await rpc('read_agent_events',chat_id=chat_id,cursor=cursor,limit=3)
                assert not page['reset_required']
                pages.append(page)
                cursor = page['cursor']
                if not page['has_more']:
                    break
            events = messages(pages)
            assert any(event['type']=='chunk' for event in events), events
            assert any(event['type']=='chat_finished' for event in events), events
            assert 'scoped reply' in json.dumps(events)
            assert not (await rpc('read_agent_events',chat_id='different-chat'))['events']
            snapshot = await rpc('open_agent_history', chat_id=chat_id)
            history = await rpc('read_agent_history', chat_id=chat_id,
                                snapshot_id=snapshot['snapshot_id'], part=0)
            assert snapshot['parts'] == 1
            assert 'scoped reply' in history['json_fragment']
            assert json.loads(history['json_fragment'])['running'] is False
            assert (await request(base,'/_fleet/drain',{}))['safe_to_stop']
            assert not (await request(base,'/health'))['ready']
            with pytest.raises(HTTPError):
                await rpc('create_chat',chat_name='Too late')
        assert child.returncode == 0, (tmp_path/'process.log').read_text()[-12000:]
    assert len(model_endpoint.requests) == 2
    assert all(headers['Authorization']=='Bearer process-fixture' for _,headers,_ in model_endpoint.requests)


@pytest.mark.asyncio
async def test_typescript_client_against_real_native_agent(tmp_path, model_endpoint):
    """Cross-repository protocol gate. Supply an esbuild bundle of AgentAppClient.ts.

    No browser/GUI acceptance claim: Node exercises the actual frontend client
    against the actual portable HTTP host, Agent engine and fixture model.
    """
    module = os.environ.get('PANTHEON_TEST_AGENT_APP_CLIENT')
    if not module:
        pytest.skip('Supply the frontend AgentAppClient build to run the cross-repository gate')
    assert Path(module).is_file()
    script = r'''
import { pathToFileURL } from 'node:url';
const { AgentAppClient } = await import(pathToFileURL(process.env.PANTHEON_TEST_AGENT_APP_CLIENT));
const [base, template] = JSON.parse(process.env.PANTHEON_CLIENT_FIXTURE);
async function call(method,args) {
  const response = await fetch(base+'/rpc', {method:'POST',
    headers:{'Content-Type':'application/json','X-Fleet-RPC-Token':'native-test-token'},
    body:JSON.stringify({method,args,timeout_s:15})});
  const body=await response.json();
  if (!response.ok || !body.success) throw new Error(JSON.stringify(body));
  return body.result;
}
const client=new AgentAppClient(call);
const created=await call('create_chat',{chat_name:'TS client',project_name:'Shared',template_obj:template});
const chat=created.chat_id;
const before=await client.loadHistory(chat);
const reply=await call('chat',{chat_id:chat,message:[{role:'user',content:'Reply once'}]});
if (!reply.success) throw new Error(JSON.stringify(reply));
let state=before.state;
const events=[];
for (;;) {
  const page=await client.readEvents(state);
  if (page.resetRequired) throw new Error('Unexpected journal gap');
  events.push(...page.events); state=page.nextState;
  if (!page.hasMore) break;
}
const after=await client.loadHistory(chat);
if (!events.some(event=>event.type==='chat_finished') || !JSON.stringify(events).includes('scoped reply'))
  throw new Error('Missing live reply');
if (!JSON.stringify(after.history.messages).includes('scoped reply') || after.history.inflight.length)
  throw new Error('Missing authoritative history');
console.log(JSON.stringify({messages:after.history.total,events:events.length}));
'''
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': []}]}
    with native_process(tmp_path, model_endpoint.url) as (child, base):
        for _ in range(200):
            try:
                await request(base, '/health')
                break
            except OSError:
                assert child.poll() is None, (tmp_path/'process.log').read_text()[-12000:]
                await asyncio.sleep(.05)
        else:
            pytest.fail('HTTP Agent never became ready')
        env = {**os.environ, 'PANTHEON_CLIENT_FIXTURE': json.dumps([base, template])}
        node = await asyncio.create_subprocess_exec('node', '--input-type=module', '-e', script,
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(node.communicate(), 30)
        except BaseException:
            node.kill()
            await node.wait()
            raise
        assert node.returncode == 0, stderr.decode()
        evidence = json.loads(stdout)
        assert evidence['messages'] >= 2 and evidence['events'] >= 2
    assert child.returncode == 0


@pytest.mark.asyncio
async def test_typescript_service_facade_against_real_native_agent(tmp_path, model_endpoint):
    """The shared GUI's ServiceProxy speaks ordinary authenticated App RPC."""
    module = os.environ.get('PANTHEON_TEST_AGENT_APP_CONNECTION')
    if not module:
        pytest.skip('Supply compiled AgentAppConnection for the service facade gate')
    assert Path(module).is_file()
    script = r'''
import { pathToFileURL } from 'node:url';
const { AgentAppConnection } = await import(pathToFileURL(process.env.PANTHEON_TEST_AGENT_APP_CONNECTION));
const [base, template] = JSON.parse(process.env.PANTHEON_CLIENT_FIXTURE);
let calls=0;
async function call(method,args,options) {
  calls++;
  const response = await fetch(base+'/rpc', {method:'POST',
    headers:{'Content-Type':'application/json','X-Fleet-RPC-Token':'native-test-token'},
    body:JSON.stringify({method,args,timeout_s:(options?.timeoutMs ?? 15000)/1000})});
  const body=await response.json();
  if (!response.ok || !body.success) throw new Error(JSON.stringify(body));
  return body.result;
}
const owner=new AgentAppConnection('native-acceptance',call);
const proxy=owner.createProxy();
if (!(await proxy.isConnected())) throw new Error('Not ready');
const metadata=await proxy.fetchServiceInfo();
if (metadata.service_id!=='native-acceptance') throw new Error('Wrong connection identity');
const created=await proxy.invoke('create_chat',{chat_name:'GUI facade',project_name:'Shared',template_obj:template});
if (!created.success) throw new Error(JSON.stringify(created));
const chat=created.chat_id;
const reply=await proxy.invoke('chat',{chat_id:chat,message:[{role:'user',content:'Reply once'}]},20000);
if (!reply.success) throw new Error(JSON.stringify(reply));
const chats=await proxy.invoke('list_chats',{project_name:'Shared'});
if (!chats.chats.some(c=>c.id===chat)) throw new Error('Conversation lost');
await proxy.closeConnection();
const before=calls;
try { await proxy.invoke('chat',{}); throw new Error('Closed proxy admitted a call'); }
catch (error) { if (!error.message.includes('closed')) throw error; }
if (calls!==before) throw new Error('Closed proxy sent RPC');
const replacement=owner.createProxy();
if (!(await replacement.isConnected())) throw new Error('Reconnect failed');
const snapshot=await replacement.invoke('open_agent_history',{chat_id:chat});
const part=await replacement.invoke('read_agent_history',{chat_id:chat,snapshot_id:snapshot.snapshot_id,part:0});
if (!part.json_fragment.includes('scoped reply')) throw new Error('Reply missing after reconnect');
await replacement.invoke('release_agent_history',{chat_id:chat,snapshot_id:snapshot.snapshot_id});
owner.close();
try { await replacement.invoke('list_chats'); throw new Error('Closed owner admitted a call'); }
catch (error) { if (!error.message.includes('closed')) throw error; }
console.log(JSON.stringify({chat,calls}));
'''
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': []}]}
    with native_process(tmp_path, model_endpoint.url) as (child, base):
        for _ in range(200):
            try:
                await request(base, '/health')
                break
            except OSError:
                assert child.poll() is None, (tmp_path/'process.log').read_text()[-12000:]
                await asyncio.sleep(.05)
        else:
            pytest.fail('HTTP Agent never became ready')
        env = {**os.environ, 'PANTHEON_CLIENT_FIXTURE': json.dumps([base, template])}
        node = await asyncio.create_subprocess_exec('node', '--input-type=module', '-e', script,
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(node.communicate(), 30)
        except BaseException:
            node.kill()
            await node.wait()
            raise
        assert node.returncode == 0, stderr.decode()
        evidence = json.loads(stdout)
        assert evidence['chat'] and evidence['calls'] >= 8
    assert child.returncode == 0


@pytest.mark.asyncio
async def test_typescript_replay_pump_against_real_native_agent(tmp_path, model_endpoint):
    """The actual GUI replay source consumes native snapshots and live events."""
    client_module = os.environ.get('PANTHEON_TEST_AGENT_APP_CLIENT')
    source_module = os.environ.get('PANTHEON_TEST_AGENT_REPLAY_SOURCE')
    if not client_module or not source_module:
        pytest.skip('Supply compiled AgentAppClient and AgentReplaySource for the replay pump gate')
    assert Path(client_module).is_file() and Path(source_module).is_file()
    script = r'''
import { pathToFileURL } from 'node:url';
const { AgentAppClient } = await import(pathToFileURL(process.env.PANTHEON_TEST_AGENT_APP_CLIENT));
const { AgentReplaySource } = await import(pathToFileURL(process.env.PANTHEON_TEST_AGENT_REPLAY_SOURCE));
const [base, template] = JSON.parse(process.env.PANTHEON_CLIENT_FIXTURE);
let failRead = false;
async function call(method,args) {
  if (method==='read_agent_events' && failRead) { failRead=false; throw new Error('injected transient disconnect'); }
  const response = await fetch(base+'/rpc', {method:'POST',
    headers:{'Content-Type':'application/json','X-Fleet-RPC-Token':'native-test-token'},
    body:JSON.stringify({method,args,timeout_s:15})});
  const body=await response.json();
  if (!response.ok || !body.success) throw new Error(JSON.stringify(body));
  return body.result;
}
const created=await call('create_chat',{chat_name:'Replay pump',project_name:'Shared',template_obj:template});
const chat=created.chat_id;
const states=[], events=[], histories=[];
const controller=new AbortController();
const source=new AgentReplaySource(new AgentAppClient(call),{
  pollMs:25,retryMs:25,connection:(_chat,state)=>states.push(state),
});
const context={signal:controller.signal,replaceHistory:messages=>histories.push(messages)};
let close=await source.subscribe(chat,event=>events.push(event),context);
if (histories.length!==1) throw new Error('Initial snapshot not delivered before subscribe resolved');
failRead=true;
const reply=await call('chat',{chat_id:chat,message:[{role:'user',content:'Reply once'}]});
if (!reply.success) throw new Error(JSON.stringify(reply));
for (let n=0;n<200 && !events.some(event=>event.type==='chat_finished');n++)
  await new Promise(resolve=>setTimeout(resolve,25));
if (!events.some(event=>event.type==='step_message') || !events.some(event=>event.type==='chat_finished')
    || !JSON.stringify(events).includes('scoped reply') || !states.includes('reconnecting'))
  throw new Error('Missing replay/reconnect events: '+JSON.stringify({events,states}));
await source.refresh(chat);
if (!JSON.stringify(histories.at(-1)).includes('scoped reply')) throw new Error('Refresh lost history');
close();
const oldCount=events.length;
await new Promise(resolve=>setTimeout(resolve,80));
if (events.length!==oldCount) throw new Error('Events delivered after close');
close=await source.subscribe(chat,event=>events.push(event),context);
if (!JSON.stringify(histories.at(-1)).includes('scoped reply')) throw new Error('Reopen lost history');
close();
console.log(JSON.stringify({histories:histories.length,events:events.length,states}));
'''
    template = {**TEMPLATE, 'agents': [{**TEMPLATE['agents'][0], 'toolsets': []}]}
    with native_process(tmp_path, model_endpoint.url) as (child, base):
        for _ in range(200):
            try:
                await request(base, '/health')
                break
            except OSError:
                assert child.poll() is None, (tmp_path/'process.log').read_text()[-12000:]
                await asyncio.sleep(.05)
        else:
            pytest.fail('HTTP Agent never became ready')
        env = {**os.environ, 'PANTHEON_CLIENT_FIXTURE': json.dumps([base, template])}
        node = await asyncio.create_subprocess_exec('node', '--input-type=module', '-e', script,
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(node.communicate(), 30)
        except BaseException:
            node.kill()
            await node.wait()
            raise
        assert node.returncode == 0, stderr.decode()
        evidence = json.loads(stdout)
        assert evidence['histories'] == 3 and evidence['events'] >= 2
        assert evidence['states'][-1] == 'closed'
    assert child.returncode == 0
    assert len(model_endpoint.requests) == 1
