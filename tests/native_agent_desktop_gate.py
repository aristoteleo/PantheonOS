"""Browser lifecycle harness for the existing seven-App native acceptance gate."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from test_dependency_owner_host import credentials


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


class NativeDesktopGate:
    def __init__(self, root, base, key, owner):
        self.root, self.base, self.key = root, base, key
        self.root.mkdir()
        self.procs = []
        self.phase = 'running'
        self.events = set()
        self.lock = threading.Lock()
        self.log = (root/'desktop.log').open('w')
        self.server = None
        self.browser = None
        self.thread = None
        self.owner = owner

    async def start(self):
        await asyncio.to_thread(self._start)

    def _start(self):
        from pantheon.utils.misc import generate_service_id
        repo = Path(__file__).resolve().parents[1]
        build = Path(os.environ['PLATFORM_DESKTOP_BUILD_DIR']).resolve()
        port, ws_port = free_port(), free_port()
        op, account, accjwt, creds = credentials(['service.native-desktop.>', '_INBOX.>', '$JS.API.>'])
        jwt = re.search(r'BEGIN NATS USER JWT-----\n(.*?)\n',creds)[1]
        seed = re.search(r'BEGIN USER NKEY SEED-----\n(.*?)\n',creds)[1]
        (self.root/'operator.jwt').write_text(op)
        conf = self.root/'nats.conf'
        conf.write_text(f'host: 127.0.0.1\nport: {port}\noperator: "{self.root / "operator.jwt"}"\n'
            f'resolver: MEMORY\nresolver_preload: {{ {account}: "{accjwt}" }}\n'
            f'websocket {{ host: 127.0.0.1; port: {ws_port}; no_tls: true }}\n')
        info = dict(service_id='absent-legacy-agent', platform_service_id=generate_service_id('native-desktop-platform'),
                    nats_url='wss://atrium.test/desktop-nats', nats_jwt=jwt, nats_seed=seed,
                    nats_subject_prefix='service.native-desktop', browser_stream_base='http://127.0.0.1:1')
        gate = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def respond(self, status, raw, content_type='application/json'):
                self.send_response(status);self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                path = self.path.split('?')[0]
                if path == '/api/gate':
                    with gate.lock: phase=gate.phase
                    self.respond(200,json.dumps({'phase':phase}).encode());return
                values = {'/api/chatroom':info,
                    '/api/chatroom/pod-status':dict(has_assignment=True,nats_healthy=True,phase='running',chatroom_id=info['service_id']),
                    '/api/auth/nats-credentials':dict(jwt=jwt,seed=seed,nats_url=info['nats_url']),
                    '/api/model-services/modal-gpu':{'services':[]},
                    '/api/auth/prewarm':{'success':True},'/api/chatroom/heartbeat':{'success':True},'/api/billing/pricing':{}}
                if path in values: self.respond(200,json.dumps(values[path]).encode());return
                if path.startswith(('/api/model-services','/api/fleet/apps/')):
                    raw=self.rfile.read(int(self.headers.get('Content-Length','0'))) if self.command=='POST' else None
                    req=Request(gate.base+'/hub'+self.path,data=raw,method=self.command,
                                headers={'Authorization':'Bearer '+gate.key,'Content-Type':'application/json'})
                    try:
                        with urlopen(req,timeout=90) as response:self.respond(response.status,response.read())
                    except HTTPError as error:self.respond(error.code,error.read())
                    return
                file=(build / (path.lstrip('/') or 'desktop.html')).resolve()
                if build not in file.parents or not file.is_file():self.respond(404,b'{}');return
                self.respond(200,file.read_bytes(),mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
            def do_POST(self):
                if self.path.startswith('/api/gate/'):
                    with gate.lock:gate.events.add(self.path.removeprefix('/api/gate/'))
                    self.respond(200,b'{}');return
                self.do_GET()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        url=f'http://127.0.0.1:{self.server.server_port}'
        req=Request(self.base+'/fixture/desktop',data=json.dumps({'url':url,'bus':f'http://127.0.0.1:{ws_port}'}).encode(),
                    headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        with urlopen(req,timeout=10) as response:assert response.status==200
        workspace=self.root/'workspace';workspace.mkdir()
        (workspace/'independent-platform.txt').write_text('Files remain available while Agent is uninstalled.\n')
        config=self.root/'control.json';config.write_text(json.dumps({'base':self.base,'key':self.key,'owner':self.owner}));config.chmod(0o600)
        env={k:v for k,v in os.environ.items() if not k.startswith(('FLEET_','PANTHEON_','NATS_'))}
        env.update(HOME=str(self.root),PYTHONPATH=os.pathsep.join((str(repo/'tests'),str(repo))),
            NATS_SERVERS=f'nats://127.0.0.1:{port}',NATS_JWT=jwt,NATS_SEED=seed,NATS_SUBJECT_PREFIX='service.native-desktop',
            NATS_ENABLE_JETSTREAM='false',PANTHEON_REMOTE_BACKEND='nats',PANTHEON_HUB_URL=url,FLEET_KEY=self.key,
            NATIVE_DESKTOP_CONFIG=str(config),PANTHEON_TEST_IMPORT_AUDIT=str(self.root/'import-audit'))
        self.procs.append(subprocess.Popen([shutil.which('nats-server'),'-c',str(conf)],stdout=self.log,stderr=self.log))
        self.procs.append(subprocess.Popen([sys.executable,str(repo/'tests/native_agent_desktop_host.py')],cwd=workspace,env=env,stdout=self.log,stderr=self.log))
        deadline=time.monotonic()+30
        while not (workspace/'services.json').exists():
            assert all(p.poll() is None for p in self.procs), (self.root/'desktop.log').read_text()[-10000:]
            assert time.monotonic()<deadline,'Desktop services did not start'
            time.sleep(.1)
        info['platform_service_id'] = json.loads((workspace/'services.json').read_text())['platform']
        browser_env={**os.environ,'NATIVE_DESKTOP_CONTROLLER':self.base,'NATIVE_DESKTOP_ARTIFACTS':str(self.root)}
        self.browser=subprocess.Popen(['node',os.environ['PANTHEON_TEST_NATIVE_DESKTOP']],env=browser_env,stdout=self.log,stderr=self.log)
        self.procs.append(self.browser)

    async def wait(self, name, timeout=90):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with self.lock: arrived=name in self.events
            if arrived:return
            assert self.browser and self.browser.poll() is None,(self.root/'desktop.log').read_text()[-15000:]
            await asyncio.sleep(.2)
        raise AssertionError(f'Desktop did not reach {name}: '+(self.root/'desktop.log').read_text()[-15000:])

    async def finish(self):
        code = await asyncio.to_thread(self.browser.wait, timeout=15)
        assert code == 0, (self.root/'desktop.log').read_text()[-15000:]

    def advance(self, phase):
        with self.lock:self.phase=phase

    def close(self):
        for filename in ('desktop.log','failed-desktop.png','restored-desktop.png','independent-notebook.png'):
            source=self.root/filename
            if source.is_file():shutil.copyfile(source,Path('/tmp')/('native-agent-'+filename))
        for proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        if self.server:self.server.shutdown();self.server.server_close()
        if self.thread:self.thread.join(timeout=3)
        self.log.close()
