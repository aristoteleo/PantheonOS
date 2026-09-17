import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pantheon.apps.lifecycle import build_artifact
from pantheon.apps.portable import execution_package


def fixture_app(root):
    root.mkdir()
    (root/'app.json').write_text(json.dumps({'id':'portable-test','name':'Portable test','version':'1.0.0',
        'entry':{'backend':'backend.py','frontend':'index.js'},'caps':{'python':{'env':'source-only'},'fs':['read','write']}}))
    (root/'backend.py').write_text('''from pathlib import Path
import asyncio
import os
def register(ctx):
    @ctx.method
    def where():
        return {'pid':os.getpid(), 'workspace':str(ctx.workspace), 'node':os.environ.get('PANTHEON_FLEET_NODE_ID')}
    @ctx.method
    async def produce():
        root=ctx.workspace/'tiles';root.mkdir(exist_ok=True)
        (root/'chunk.bin').write_bytes(b'0123456789')
        return {'url':await ctx.serve(root)}
    @ctx.method
    async def remember(value):
        ctx.state.set('value',value)
        return {'value':ctx.state.get('value')}
''')
    (root/'index.js').write_text('export function setup() {}')
    return root


def test_portable_package_is_deterministic_and_does_not_mutate_source(tmp_path):
    source=fixture_app(tmp_path/'source')
    before=(source/'app.json').read_bytes()
    def build(platform):
        with execution_package(source,platform) as root:
            return build_artifact(root,platform)
    payload,digest=build('darwin-arm64')
    assert build('darwin-arm64')==(payload,digest)
    assert build('windows-amd64')[1]!=digest
    assert (source/'app.json').read_bytes()==before
    assert not (source/'fleet.json').exists()
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        definition=json.load(archive.extractfile('fleet.json'))
        assert definition['components'][0]['argv'][0]=='${INSTALL}/venv/bin/python'
        assert '.fleet-runtime/assets/app-host.html' in archive.getnames()
        assert b'source-only' not in archive.extractfile('fleet.json').read()
    (source/'steal').symlink_to('/etc/passwd')
    with pytest.raises(ValueError,match='symbolic links'):
        build('darwin-arm64')


def test_real_backend_rpc_files_ranges_and_persistent_state(tmp_path):
    source=fixture_app(tmp_path/'source')
    with execution_package(source,'darwin-arm64') as root:
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        data=tmp_path/'data'
        proc=subprocess.Popen([sys.executable,str(root/'.fleet-runtime/host.py'),'start','--package',str(root),'--data',str(data)],
            env={**os.environ,'PANTHEON_PORT_HTTP':str(port),'PANTHEON_INSTANCE_GENERATION':'2','PANTHEON_FLEET_NODE_ID':'test-mac'},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        base=f'http://127.0.0.1:{port}'
        def rpc(method,args={}):
            with urlopen(Request(base+'/rpc',data=json.dumps({'method':method,'args':args}).encode(),headers={'Content-Type':'application/json'}),timeout=5) as res:return json.load(res)
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                try:
                    with urlopen(base+'/health',timeout=1) as response:assert json.load(response)['ready']
                    break
                except OSError:
                    if proc.poll() is not None:raise AssertionError(proc.stderr.read().decode())
                    time.sleep(.05)
            else:raise AssertionError('Backend did not start')
            where=rpc('where')['result']
            assert where['pid']==proc.pid and where['node']=='test-mac'
            assert str(data.resolve()) in where['workspace']
            assert rpc('remember',{'value':42})['result']=={'value':42}
            assert json.loads((data/'state.json').read_text())['value']==42
            url=rpc('produce')['result']['url']
            with urlopen(Request(base+url+'/chunk.bin',headers={'Range':'bytes=3-6'})) as res:
                assert res.status==206 and res.read()==b'3456'
            with pytest.raises(HTTPError) as exc:urlopen(base+url+'/../../state.json')
            assert exc.value.code==404
            with urlopen(base+'/package/index.js') as res:assert b'export function setup' in res.read()
            with urlopen(base+'/app-host.html') as res:assert b'Atrium App' in res.read()
            def fs(payload):
                with urlopen(Request(base+'/_fleet/fs', data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})) as response:
                    return json.load(response)
            assert fs({'op':'write','path':'notes.txt','content':'local to this instance'})['success']
            assert fs({'op':'read','path':'notes.txt'})['content']=='local to this instance'
            assert (data/'workspace/notes.txt').read_text()=='local to this instance'
            with pytest.raises(HTTPError):fs({'op':'read','path':'../state.json'})
            (data/'workspace/escape').symlink_to(data/'state.json')
            with pytest.raises(HTTPError):fs({'op':'read','path':'escape'})
            with pytest.raises(HTTPError):rpc('not_registered')
            with urlopen(Request(base+'/_fleet/drain',method='POST')) as res:assert json.load(res)['safe_to_stop']
            with pytest.raises(HTTPError):rpc('remember',{'value':99})
            assert json.loads((data/'state.json').read_text())['value']==42
        finally:
            proc.terminate();proc.wait(timeout=5)
