"""SGLang image transport fixtures; these are not real diffusion model runs."""
import hashlib
import io
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from pantheon.models.jobs import InferenceSession
from test_model_services import connector_module, serve
from test_model_inference_jobs import close, wait_done


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x20\x50\x90' * 64) * 64)) + chunk(b'IEND', b''))


def body(**updates):
    return {'job_id':'image-1', 'model':'local-image', 'operation':'image',
            'input':{'text':'A small blue test square'}, 'parameters':{'size':'64x64', 'seed':42}, **updates}


def engine(calls, *, url='/v1/images/image-safe_1/content', data=None, content_type='image/png', entered=None, release=None, redirect=False):
    data = png() if data is None else data
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            calls.append(('POST', self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200)
            self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({'data':[{'url':url,'file_path':'/private/engine/file.png','revised_prompt':'private'}]}).encode())
        def do_GET(self):
            calls.append(('GET', self.path))
            self.send_response(302 if redirect else 200)
            if redirect: self.send_header('Location','http://127.0.0.1:1/private')
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length',str(len(data))); self.end_headers()
            if entered: entered.set()
            if release: release.wait(5)
            try: self.wfile.write(data)
            except OSError: pass
    return Engine


@pytest.mark.asyncio
async def test_image_binary_receipt_restart_and_removal(tmp_path):
    calls, c = [], connector_module.Connector(tmp_path)
    try:
        with serve(engine(calls)) as upstream:
            c.configure({'engine':'sglang','endpoint':upstream})
            with serve(connector_module.handler(c)) as endpoint:
                async with httpx.AsyncClient() as http:
                    session=InferenceSession(http,{'deployment_id':'image-node','config_revision':c.revision},
                        {'origin':endpoint,'access_token':''},'direct',model='local-image',operation='image')
                    await session.submit(body()['input'], request_id='image-1', parameters=body()['parameters'])
                    assert wait_done(c,'image-1')['state']=='succeeded'
                    result=await session.status('image-1')
                    artifact=result['result']['artifacts'][0]
                    assert artifact['kind']=='image' and artifact['mime']=='image/png'
                    assert artifact['sha256']==hashlib.sha256(png()).hexdigest()
                    assert artifact['ref'].startswith('fleet-artifact://image-node/')
                    assert 'private' not in json.dumps(result) and 'url' not in artifact
                    output=io.BytesIO(); await session.wire.download(artifact['ref'],output)
                    assert output.getvalue()==png()
                    assert (await session.submit(body()['input'],request_id='image-1',parameters=body()['parameters']))==result
                    assert len(calls)==2 and calls[0][1]=='/v1/images/generations'
                    assert calls[0][2]['response_format']=='url' and calls[0][2]['output_format']=='png'
        close(c); c=connector_module.Connector(tmp_path)
        assert c.inference_jobs().status('image-1')['state']=='succeeded'
        assert c.media_store().read(artifact['id'])[1]==png()
        c.inference_jobs().remove('image-1')
        assert not list(c.media_store().root.glob('*.blob'))
    finally: close(c)


@pytest.mark.parametrize('url', ['https://example.com/image.png','//evil.test/v1/images/x/content',
    '/v1/images/../content','/v1/images/x/content?key=private','/v1/images/%2e%2e/content',
    '/v1/images/a/b/content'])
def test_image_never_follows_upstream_url(tmp_path,url):
    calls,c=[],connector_module.Connector(tmp_path)
    try:
        with serve(engine(calls,url=url)) as upstream:
            c.configure({'engine':'sglang','endpoint':upstream})
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            assert wait_done(c,'image-1')['state']=='unknown'
            assert len(calls)==1
            assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
    finally: close(c)


@pytest.mark.parametrize('mode',['cancel','redirect','mime','header','png_truncated'])
def test_image_failures_release_reserved_output(tmp_path,mode):
    calls,c=[],connector_module.Connector(tmp_path)
    entered,release=threading.Event(),threading.Event()
    try:
        with serve(engine(calls,redirect=mode=='redirect',content_type='text/html' if mode=='mime' else 'image/png',
                data=b'bad png'*100 if mode=='header' else png()[:-12] if mode=='png_truncated' else None,
                entered=entered,release=release if mode=='cancel' else None)) as upstream:
            c.configure({'engine':'sglang','endpoint':upstream})
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            if mode=='cancel':
                assert entered.wait(2); jobs.cancel('image-1'); release.set()
            record=wait_done(c,'image-1')
            assert record['state'] in {'cancelled','unknown','failed'}
            assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
            assert jobs.submit(body(),c.revision)==record and len(calls)==2
    finally: release.set(); close(c)


def test_image_budget_and_sampling_rejected_before_generation(tmp_path):
    c=connector_module.Connector(tmp_path); calls=[]
    try:
        with serve(engine(calls)) as upstream:
            c.configure({'engine':'sglang','endpoint':upstream})
            for params in [{'size':'99999x99999'},{'seed':True},{'n':2},{'guidance_scale':float('inf')},
                {'output_format':'svg'},{'diffusers_kwargs':{'arbitrary':'value'}}]:
                with pytest.raises(ValueError): c.inference_jobs().submit(body(parameters=params),c.revision)
            c.media_store().quota=100
            with pytest.raises(ValueError,match='budget'): c.inference_jobs().submit(body(),c.revision)
            assert not calls and not c.inference_jobs().list()
    finally: close(c)
