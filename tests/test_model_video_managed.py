"""Managed video admission against an HTTP fixture, not GPU acceptance."""
import threading

import pytest

from test_model_diffusion_managed import config
from test_model_services import connector_module, serve
from test_model_video_jobs import body, engine, shutdown, wait
from test_model_inference_jobs import wait_done


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_APP_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setenv('PANTHEON_APP_SCOPE', 'model-video-test')
    state = {'calls': [], 'complete': threading.Event()}
    with serve(engine(state)) as upstream:
        c = connector_module.Connector(tmp_path / 'connector')
        monkeypatch.setattr(c.module('engines'), 'native_platform', lambda: 'linux-amd64')
        c.module('video_worker').POLL_SECONDS = .01
        managed = config('video')
        resources = managed.pop('resources')
        managed.update(memory_bytes=resources['memory_bytes'], scope='engine-video-test')
        c.configure(dict(engine='sglang', endpoint=upstream), managed=managed)
        models = c.module('diffusion_models')
        request = {**body(), 'model': models.served_name(models.model(managed['model_recipe_id']))}
        try:
            yield c, state, request
        finally:
            state['complete'].set()
            shutdown(c)
            if c._model_control:
                c._model_control.owner.__exit__(None, None, None)
                c._model_control.db.close()
            c.downloads().close()


def test_wrong_owned_identity_never_posts_or_retains_capacity(owned):
    c, state, request = owned
    jobs = c.inference_jobs()
    jobs.submit({**request, 'model': 'someone-elses-model'}, c.revision)
    result = wait_done(c, request['job_id'])
    assert result['state'] == 'failed' and not result['upstream_pending']
    assert state['calls'] == []
    assert c.drain()['safe_to_stop']
    assert not c.media_store().db.execute('SELECT 1 FROM media').fetchone()


def test_owned_cancel_preserves_gpu_ownership_until_terminal(owned):
    c, state, request = owned
    jobs = c.inference_jobs()
    jobs.submit(request, c.revision)
    wait(lambda: jobs.videos.journal.read(request['job_id'], c.revision)['upstream_id'] == 'engine-1')
    jobs.cancel(request['job_id'])
    assert not c.drain()['safe_to_stop']
    state['complete'].set()
    result = wait_done(c, request['job_id'])
    assert result['state'] == 'cancelled' and not result['upstream_pending']
    assert c.drain()['safe_to_stop']
    assert [method for method, _ in state['calls']].count('POST') == 1
    assert all(method != 'DELETE' and not path.endswith('/content') for method, path in state['calls'])


def test_owned_video_collects_output_without_repeating_generation(owned):
    c, state, request = owned
    state['complete'].set()
    jobs = c.inference_jobs()
    jobs.submit(request, c.revision)
    result = wait_done(c, request['job_id'])
    assert result['state'] == 'succeeded'
    assert result['result']['artifacts'][0]['mime'] == 'video/mp4'
    assert jobs.submit(request, c.revision) == result
    assert [method for method, _ in state['calls']].count('POST') == 1
    assert c.drain()['safe_to_stop']


def test_cancel_during_owned_guard_never_submits(owned, monkeypatch):
    c, state, request = owned
    entered, proceed = threading.Event(), threading.Event()
    control = c.model_control()
    original = control.inference_model
    def guarded(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(control, 'inference_model', guarded)
    jobs = c.inference_jobs()
    try:
        jobs.submit(request, c.revision)
        assert entered.wait(5)
        jobs.cancel(request['job_id'])
    finally:
        proceed.set()
    assert wait_done(c, request['job_id'])['state'] == 'cancelled'
    assert state['calls'] == []
    assert c.drain()['safe_to_stop']


def test_video_rejects_image_and_text_managed_recipes_without_changing_attached_limits():
    from test_model_engines import load
    video = load('video')
    request = {**body(), 'parameters': {'size': '1280x720', 'num_frames': 81, 'fps': 24}}
    attached = dict(engine='sglang', endpoint='http://127.0.0.1:30000/v1')
    assert video.prepare(attached, request)['payload']['size'] == '1280x720'
    for recipe in ['sglang-diffusion-0.5.20-linux-amd64', 'sglang-0.5.20-linux-amd64']:
        with pytest.raises(ValueError, match='compatible SGLang'):
            video.prepare({**attached, 'managed': {**config('video'), 'recipe_id': recipe}}, request)
    plan = video.prepare({**attached, 'managed': config('video')}, request)
    assert plan['payload']['num_frames'] == 81 and plan['payload']['size'] == '1280x720'


def test_restarted_owned_connector_requires_owner_resume_before_new_work(owned):
    c, state, request = owned
    if c._model_control:
        c._model_control.owner.__exit__(None, None, None)
        c._model_control.db.close()
        c._model_control = None
    c.downloads().close()
    fresh = connector_module.Connector(c.data)
    fresh.module('video_worker').POLL_SECONDS = .01
    try:
        assert fresh.lifetime_pending
        with pytest.raises(ValueError, match='unavailable'):
            fresh.inference_jobs().submit(request, fresh.revision)
        assert state['calls'] == []
        assert fresh.resume(fresh.revision)['accepting']
        state['complete'].set()
        fresh.inference_jobs().submit(request, fresh.revision)
        assert wait_done(fresh, request['job_id'])['state'] == 'succeeded'
        assert [method for method, _ in state['calls']].count('POST') == 1
    finally:
        shutdown(fresh)
        if fresh._model_control:
            fresh._model_control.owner.__exit__(None, None, None)
            fresh._model_control.db.close()
        fresh.downloads().close()
