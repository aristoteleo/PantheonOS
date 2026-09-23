"""Actual HTTP fixtures and connector reopen; no real model/codec claims."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler

import httpx
import pytest

from test_model_services import connector_module, serve
from test_model_inference_jobs import wait_done
from test_model_video_protocol import movie


def body(job='video-1'):
    return {'job_id':job,'model':'test-video','operation':'video',
            'input':{'text':'Public test boat'},'parameters':{'size':'512x512','num_frames':17}}


def wait(predicate):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        if predicate(): return
        time.sleep(.01)
    pytest.fail('Video condition did not become true')


def shutdown(c):
    if c._inference_jobs: c._inference_jobs.videos.suspend()
    if c._media_store: c._media_store.close()
    c.activity.db.close()


def engine(state):
    class Engine(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,value,status=200,mime='application/json'):
            data=value if isinstance(value,bytes) else json.dumps(value).encode()
            self.send_response(status); self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data))); self.end_headers()
            try: self.wfile.write(data)
            except OSError: pass
        def do_POST(self):
            state['calls'].append(('POST',self.path))
            self.rfile.read(int(self.headers['Content-Length']))
            if state.get('lost_ack'):
                self.close_connection=True
                return
            if gate := state.get('ack_gate'):
                assert gate.wait(5)
            self.reply({'id':'engine-1','status':'queued','progress':0})
        def do_GET(self):
            state['calls'].append(('GET',self.path))
            if self.path.endswith('/content'):
                if state.get('partial_once'):
                    state['partial_once'] = False
                    data = movie()
                    self.send_response(200); self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Content-Length', str(len(data))); self.end_headers()
                    self.wfile.write(data[:37]); self.wfile.flush()
                    state['content_gate'].wait(5)
                    try: self.wfile.write(data[37:])
                    except OSError: pass
                else:
                    self.reply(movie(),mime='video/mp4')
            elif self.path.endswith('/models'):
                self.reply({'data':[{'id':'test-video'}]})
            elif state.get('missing'):
                self.reply({},404)
            else:
                self.reply({'id':'engine-1','status':'completed' if state['complete'].is_set() else 'in_progress',
                            'progress':100 if state['complete'].is_set() else 10})
        def do_DELETE(self):
            state['calls'].append(('DELETE',self.path)); self.reply({})
    return Engine


def connector(path,upstream=None):
    c=connector_module.Connector(path)
    c.module('video_worker').POLL_SECONDS=.01
    if upstream: c.configure({'engine':'sglang','endpoint':upstream})
    return c


def test_cancel_keeps_gpu_capacity_until_observed_terminal(tmp_path):
    state={'calls':[],'complete':threading.Event()}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        try:
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            wait(lambda: jobs.videos.journal.read('video-1',c.revision)['upstream_id']=='engine-1')
            jobs.cancel('video-1')
            assert jobs.status('video-1')['state']=='cancelling'
            assert jobs.status('video-1')['upstream_pending']
            assert c.activity_status()['active_calls']==1
            assert not c.drain()['safe_to_stop']
            with pytest.raises(ValueError): jobs.remove('video-1')
            with pytest.raises(ValueError): c.configure({'engine':'sglang','endpoint':upstream})
            state['complete'].set()
            result=wait_done(c,'video-1')
            assert result['state']=='cancelled' and not result['upstream_pending']
            assert result['upstream_cancel_confirmed'] is False
            assert not any(method=='DELETE' or path.endswith('/content') for method,path in state['calls'])
            assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()
            assert c.drain()['safe_to_stop']
        finally: shutdown(c)


@pytest.mark.parametrize('cancelled',[False,True])
def test_restart_restores_capacity_and_queries_same_job_without_new_post(tmp_path,cancelled):
    state={'calls':[],'complete':threading.Event()}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
        wait(lambda: jobs.videos.journal.read('video-1',c.revision)['upstream_id']=='engine-1')
        if cancelled: jobs.cancel('video-1')
        shutdown(c)
        c=connector(tmp_path)
        try:
            # Eager recovery has already reinstated capacity before any caller
            # chooses to open history or poll an existing job.
            assert c.activity_status()['active_calls']==1
            assert not c.drain()['safe_to_stop']
            state['complete'].set()
            result=wait_done(c,'video-1')
            assert result['state']==('cancelled' if cancelled else 'succeeded')
            assert sum(method=='POST' for method,_ in state['calls'])==1
            if not cancelled:
                artifact=result['result']['artifacts'][0]
                assert c.media_store().read(artifact['id'])[1]==movie()
                assert c.inference_jobs().submit(body(),c.revision)==result
            c.inference_jobs().remove('video-1')
            assert not list(c.media_store().root.glob('*.blob'))
        finally: shutdown(c)


def test_unknown_creation_is_never_replayed_or_made_free_by_restart(tmp_path):
    state={'calls':[],'complete':threading.Event(),'lost_ack':True}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
        wait(lambda: jobs.status('video-1')['state']=='unknown')
        assert jobs.status('video-1')['upstream_pending']
        shutdown(c)
        c=connector(tmp_path)
        try:
            assert c.activity_status()['active_calls']==1
            record=c.inference_jobs().submit(body(),c.revision)
            assert record['state']=='unknown' and record['upstream_pending']
            assert len(state['calls'])==1
            with pytest.raises(ValueError): c.inference_jobs().reconcile('video-1')
            with pytest.raises(ValueError): c.inference_jobs().remove('video-1')
            c.inference_jobs().cancel('video-1')
            assert c.activity_status()['active_calls']==1 and not c.drain()['safe_to_stop']
        finally: shutdown(c)


def test_explicit_reconcile_only_gets_acknowledged_id(tmp_path):
    state={'calls':[],'complete':threading.Event(),'missing':True}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        try:
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            wait(lambda: c.calls['video-1'].get('parked') and not c.calls['video-1']['worker'].is_alive())
            assert jobs.status('video-1')['reason']=='upstream_video_missing'
            state['missing']=False; state['complete'].set()
            with serve(connector_module.handler(c)) as endpoint, httpx.Client() as client:
                reply=client.post(endpoint+'/inference/jobs/video-1/reconcile',headers={'X-Model-Config':c.revision})
                assert reply.status_code==200
            assert wait_done(c,'video-1')['state']=='succeeded'
            assert sum(method=='POST' for method,_ in state['calls'])==1
        finally: shutdown(c)


def test_deadline_does_not_claim_upstream_was_cancelled(tmp_path):
    state={'calls':[],'complete':threading.Event()}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream); c.module('video_worker').DEADLINE_SECONDS=.05
        try:
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            wait(lambda: jobs.status('video-1')['state']=='cancelling')
            assert jobs.status('video-1')['upstream_pending'] and c.calls
            state['complete'].set()
            record=wait_done(c,'video-1')
            assert record['state']=='cancelled' and record['reason']=='job_deadline'
            assert record['upstream_cancel_confirmed'] is False
        finally: shutdown(c)


def test_cancel_before_creation_ack_retains_identity_and_capacity(tmp_path):
    state={'calls':[], 'complete':threading.Event(), 'ack_gate':threading.Event()}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        try:
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            wait(lambda: state['calls'])
            jobs.cancel('video-1')
            assert c.calls and jobs.status('video-1')['upstream_pending']
            state['ack_gate'].set()
            wait(lambda: jobs.videos.journal.read('video-1',c.revision)['upstream_id']=='engine-1')
            state['complete'].set()
            assert wait_done(c,'video-1')['state']=='cancelled'
            assert sum(method=='POST' for method,_ in state['calls'])==1
            assert not any(path.endswith('/content') for _,path in state['calls'])
        finally:
            state['ack_gate'].set(); shutdown(c)


def test_restart_during_download_verifies_prefix_with_different_chunk_boundaries(tmp_path):
    state={'calls':[], 'complete':threading.Event(), 'partial_once':True,
           'content_gate':threading.Event()}
    state['complete'].set()
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        try:
            jobs=c.inference_jobs(); jobs.submit(body(),c.revision)
            wait(lambda: jobs.videos.journal.read('video-1',c.revision)['phase']=='terminal')
            artifact=jobs.videos.journal.read('video-1',c.revision)['output_id']
            wait(lambda: c.media_store().row(artifact)['received']==37)
            shutdown(c)
            state['content_gate'].set()
            c=connector(tmp_path)
            record=wait_done(c,'video-1')
            assert record['state']=='succeeded'
            assert record['result']['artifacts'][0]['id']==artifact
            assert c.media_store().read(artifact)[1]==movie()
            assert sum(method=='POST' for method,_ in state['calls'])==1
            assert sum(path.endswith('/content') for _,path in state['calls'])==2
        finally:
            state['content_gate'].set(); shutdown(c)


def test_reconcile_waits_for_previous_observer_socket_cleanup(tmp_path):
    state={'calls':[], 'complete':threading.Event(), 'missing':True}
    with serve(engine(state)) as upstream:
        c=connector(tmp_path,upstream)
        settling, release = threading.Event(), threading.Event()
        try:
            jobs=c.inference_jobs(); original=jobs.videos.hold
            def hold(*args):
                original(*args)
                settling.set()
                assert release.wait(5)
            jobs.videos.hold=hold
            jobs.submit(body(),c.revision)
            assert settling.wait(5)
            worker=c.calls['video-1']['worker']
            with pytest.raises(ValueError,match='settling'):
                jobs.reconcile('video-1')
            assert c.calls['video-1']['worker'] is worker
            release.set(); worker.join(2)
            state['missing']=False; state['complete'].set()
            jobs.reconcile('video-1')
            assert wait_done(c,'video-1')['state']=='succeeded'
        finally:
            release.set(); shutdown(c)
