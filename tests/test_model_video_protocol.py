"""Protocol fixtures, not real video generation or durable lifecycle acceptance."""
import io
import json
import struct
from http.server import BaseHTTPRequestHandler

import pytest

from test_model_services import connector_module, serve
from test_model_inference_jobs import close


@pytest.fixture
def video(tmp_path):
    c = connector_module.Connector(tmp_path)
    yield c.module('video')
    c.activity.db.close()


def response(value, *, status=200, mime='application/json'):
    class Response(io.BytesIO):
        headers = {'Content-Type': mime}
    r = Response(json.dumps(value).encode())
    r.status = status
    return r


def box(kind, data, *, wide=False):
    return (struct.pack('>I4sQ', 1, kind, 16+len(data)) if wide else
            struct.pack('>I4s', 8+len(data), kind)) + data


def movie():
    return box(b'ftyp', b'isom\0\0\0\0isommp42') + box(b'mdat', b'frame'*100, wide=True) + box(b'moov', b'metadata')


@pytest.mark.parametrize('chunk', [1, 2, 3, 7, 8, 9, 15, 16, 64, 65536])
def test_incremental_mp4_checks_framing_without_buffering_payload(video, chunk):
    parser = video.MP4Boxes(1024)
    data = movie()
    for offset in range(0, len(data), chunk):
        parser.feed(data[offset:offset+chunk])
        assert len(parser.header) <= 16
    parser.finish()
    assert parser.total == len(data)
    assert parser.boxes == [b'ftyp', b'mdat', b'moov']


@pytest.mark.parametrize('data', [b'', b'<html>error</html>', movie()[:-1], movie()+b'x',
    box(b'ftyp', b'isom0000')+box(b'moov', b'x'),
    box(b'ftyp', b'isom0000')+box(b'mdat', b'x'),
    movie()+box(b'ftyp', b'isom0000'),
    box(b'ftyp', b'isom0000')+struct.pack('>I4s',0,b'mdat'),
    box(b'ftyp', b'isom0000')+struct.pack('>I4sQ',1,b'mdat',2**63)])
def test_invalid_mp4_is_not_sealed(video, data):
    with pytest.raises(ValueError):
        parser=video.MP4Boxes(1024); parser.feed(data); parser.finish()


def test_bounded_mp4_budget(video):
    with pytest.raises(ValueError, match='budget'):
        video.MP4Boxes(40).feed(movie())


def test_receipt_drops_private_data_and_does_not_follow_urls(video):
    value={'id':'video-a_1','status':'queued','progress':0,'file_path':'/private/video.mp4',
           'url':'https://untrusted.test/private','prompt':'private','error':{'message':'secret'}}
    assert video.receipt(response(value)) == {'id':'video-a_1','state':'queued','progress':0}
    value['status']='completed'; value['progress']=100
    assert video.receipt(response(value), expected_id='video-a_1')['state']=='completed'
    with pytest.raises(ValueError): video.receipt(response(value),expected_id='different')


@pytest.mark.parametrize('fields', [{'id':'../secret'}, {'id':'x?token=secret'}, {'id':'https://bad.test'},
    {'status':'deleted'}, {'progress':True}, {'progress':float('nan')}, {'progress':101}])
def test_bad_receipt_cannot_redirect_or_confirm_cancellation(video, fields):
    with pytest.raises(ValueError):
        video.receipt(response({'id':'video-1','status':'queued','progress':0,**fields}))


def test_receipt_transport_and_size_are_bounded(video):
    with pytest.raises(RuntimeError,match='upstream_http_302'):
        video.receipt(response({},status=302))
    with pytest.raises(ValueError): video.receipt(response({},mime='text/html'))
    with pytest.raises(ValueError): video.receipt(response({'data':'x'*65536}))


def test_video_parameters_are_typed_and_cannot_choose_engine_files(video):
    config={'engine':'sglang','endpoint':'http://127.0.0.1:8000/v1'}
    body={'model':'test','input':{'text':'A boat sailing'},'parameters':{}}
    plan=video.prepare(config,body)
    assert plan['payload']['num_frames']==17 and plan['payload']['fps']==16
    assert plan['output']['max_size']==64*1024*1024
    for params in [{'output_path':'/private'}, {'image_path':'https://bad.test'},
        {'diffusers_kwargs':{}},{'num_frames':True},{'num_frames':241,'fps':1},
        {'num_frames':None},{'seed':None},{'size':'1920x1920'}, {'guidance_scale':float('nan')}]:
        with pytest.raises(ValueError): video.prepare(config,{**body,'parameters':params})
    with pytest.raises(ValueError): video.prepare({**config,'managed':{'mode':'managed'}},body)
    with pytest.raises(ValueError): video.prepare({**config,'endpoint':'http://localhost'},body)


def test_http_create_observe_and_binary_download_are_separate_without_delete(tmp_path):
    calls=[]
    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            calls.append(('POST',self.path))
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request['prompt']=='A boat sailing' and request['n']==1
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(json.dumps({'id':'engine-1','status':'queued','progress':0}).encode())
        def do_GET(self):
            calls.append(('GET',self.path))
            self.send_response(200)
            data=movie() if self.path.endswith('/content') else json.dumps({
                'id':'engine-1','status':'completed','progress':100,'url':'https://untrusted.test/movie',
                'file_path':'/private/movie.mp4'}).encode()
            self.send_header('Content-Type','video/mp4' if self.path.endswith('/content') else 'application/json')
            self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    c=connector_module.Connector(tmp_path)
    try:
        with serve(Engine) as upstream:
            c.configure({'engine':'sglang','endpoint':upstream})
            video=c.module('video'); store=c.media_store(); owner='inference-video-1'
            plan=video.prepare(c.config,{'model':'test','input':{'text':'A boat sailing'},'parameters':{}})
            with store.transaction():
                plan['output_id']=store.reserve_output(owner,**plan['output'])['id']
            call={'cancelled':False}
            created=video.request(c,call,plan=plan)
            observed=video.request(c,call,upstream_id=created['id'])
            assert observed['state']=='completed'
            result=video.download(c,call,created['id'],plan,store,owner)
            artifact=result['artifacts'][0]
            assert artifact['mime']=='video/mp4' and store.read(artifact['id'])[1]==movie()
            assert calls==[('POST','/v1/videos'),('GET','/v1/videos/engine-1'),
                           ('GET','/v1/videos/engine-1/content')]
            assert 'private' not in json.dumps(result) and 'untrusted' not in json.dumps(result)
    finally:
        close(c)
