import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from pantheon.chatroom import llm_playground as pg
from pantheon.chatroom import llm_playground_media as media


@pytest.fixture
def provider(monkeypatch):
    route = pg.Route("openrouter", "BYOK", "Your account", "openai", "https://provider.test/v1", "private", True)
    monkeypatch.setattr(pg, "routes", lambda: {"openrouter": route})
    original = httpx.AsyncClient
    def transport(handler):
        monkeypatch.setattr(media.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return route, transport


@pytest.mark.asyncio
async def test_image_once_chunked_and_no_base64_in_result(provider):
    route, transport = provider
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/images"
        assert json.loads(request.content) == {"model": "acme/image", "prompt": "circle", "n": 1}
        assert request.headers["authorization"] == "Bearer private"
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}], "usage": {"cost": .01}})
    transport(handler)
    runner = pg.Playground()
    result = await runner.run("image-request-01", "openrouter", "openrouter/acme/image", "circle", operation="image", parameters={"n": 1})
    assert result["success"] and result["reported_cost_usd"] == .01
    assert len(calls) == 1 and len(result["media"]) == 1
    assert "b64_json" not in json.dumps(result)
    chunk = runner.media.read(result["media"][0]["id"], 0)
    assert base64.b64decode(chunk["data"]) == b"image-bytes" and chunk["done"]


@pytest.mark.asyncio
async def test_provider_returned_image_url_never_gets_api_key(provider):
    route, transport = provider
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.test/output.png"}]})
        assert request.url.host == "cdn.test"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
    transport(handler)
    result = await media.complete(route, "openrouter/image/model", "image", "image", {}, media.MediaStore(), {})
    assert result["media"][0]["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_video_submit_once_poll_own_endpoint_and_download(provider, monkeypatch):
    route, transport = provider
    sleep = AsyncMock()
    monkeypatch.setattr(media.asyncio, "sleep", sleep)
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(202, json={"id": "video-123", "status": "pending", "polling_url": "https://evil.test/steal"})
        if request.url.path.endswith("/content"):
            return httpx.Response(302, headers={"location": "https://cdn.test/video.mp4"})
        if request.url.host == "cdn.test":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"video", headers={"content-type": "video/mp4"})
        return httpx.Response(200, json={"id": "video-123", "status": "completed", "usage": {"cost": .2}})
    transport(handler)
    progress = {}
    result = await media.complete(route, "openrouter/acme/video", "cloud", "video", {"duration": 4}, media.MediaStore(), progress)
    assert result["success"] and result["media"][0]["type"] == "video"
    assert calls == [("POST", "/v1/videos"), ("GET", "/v1/videos/video-123"), ("GET", "/v1/videos/video-123/content"), ("GET", "/video.mp4")]
    assert progress["job_id"] == "video-123"


@pytest.mark.asyncio
async def test_speech_binary_and_transcription_upload(provider):
    route, transport = provider
    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/speech"):
            assert body["voice"] == "alloy" and body["response_format"] == "mp3"
            return httpx.Response(200, content=b"audio", headers={"content-type": "audio/mpeg"})
        assert body["input_audio"] == {"data": base64.b64encode(b"sound").decode(), "format": "wav"}
        assert "audio_asset" not in body
        return httpx.Response(200, json={"text": "hello", "usage": {"cost": .001}})
    transport(handler)
    store = media.MediaStore()
    spoken = await media.complete(route, "openrouter/acme/tts", "hello", "speech", {"voice": "alloy"}, store, {})
    assert spoken["media"][0]["type"] == "audio"
    asset = store.upload("", base64.b64encode(b"sound").decode(), 0, "input.wav")
    transcript = await media.complete(route, "openrouter/acme/stt", "", "transcription", {"audio_asset": asset["id"]}, store, {})
    assert transcript["output"] == "hello"


@pytest.mark.asyncio
async def test_embedding_structured_output(provider):
    route, transport = provider
    transport(lambda request: httpx.Response(200, json={"data": [{"embedding": [.1, .2]}], "usage": {"prompt_tokens": 1}}))
    result = await media.complete(route, "openrouter/acme/embedding", "hello", "embedding", {}, media.MediaStore(), {})
    assert result["data"][0]["embedding"] == [.1, .2]
    assert result["reported_cost_usd"] is None


@pytest.mark.asyncio
async def test_cancel_video_keeps_job_id_and_does_not_claim_remote_cancel(provider, monkeypatch):
    began = asyncio.Event()
    async def slow(route, model, prompt, operation, params, store, progress):
        progress.update(job_id="video-123", status="in_progress")
        began.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(media, "complete", slow)
    runner = pg.Playground()
    task = asyncio.create_task(runner.run("video-request-cancel", "openrouter", "openrouter/acme/video", "cloud", operation="video"))
    await began.wait()
    assert runner.status("video-request-cancel")["job_id"] == "video-123"
    runner.cancel("video-request-cancel")
    result = await task
    assert result["cancelled"] and result["job_id"] == "video-123"
    assert "may continue" in result["message"]
    assert not runner.tasks and not runner.progress


@pytest.mark.parametrize("operation,params", [
    ("image", {"api_key": "bad"}), ("image", {"n": 99}), ("video", {"duration": 100}),
    ("text", {"base_url": "bad"}), ("speech", {}), ("speech", {"voice": "a", "response_format": "pcm"}),
    ("transcription", {}), ("embedding", {"dimensions": 20000}),
])
def test_invalid_media_parameters(operation, params):
    with pytest.raises(ValueError):
        media.validate(operation, params)


def test_store_upload_bounds_and_expiry():
    store = media.MediaStore()
    asset = store.upload("", base64.b64encode(b"abc").decode(), 0, "../../file.wav")
    assert asset["name"] == "input.wav"
    with pytest.raises(ValueError):
        store.upload(asset["id"], "YWJj", 0, "file.wav")
    with pytest.raises(ValueError):
        store.read(asset["id"], -1)
    store.items[asset["id"]]["created"] = time.monotonic() - 3601
    with pytest.raises(ValueError, match="expired"):
        store.read(asset["id"], 0)
    store.prune()
    assert not store.items


def test_platform_limits_are_explicit():
    assert not media.route_reason("platform", "openai", "image")
    for operation in ("video", "speech", "transcription"):
        assert "OpenRouter BYOK" in media.route_reason("platform", "openai", operation)


@pytest.mark.asyncio
async def test_proxy_reported_cost_header_is_preserved(provider):
    route, transport = provider
    transport(lambda request: httpx.Response(200, json={"data": []}, headers={"x-litellm-response-cost": "0.02"}))
    result = await media.complete(route, "openrouter/acme/embedding", "hello", "embedding", {}, media.MediaStore(), {})
    assert result["reported_cost_usd"] == .02


@pytest.mark.asyncio
async def test_media_catalog_cache_keeps_partial_results_and_price_units(provider, monkeypatch):
    _, transport = provider
    monkeypatch.setattr(media, "_catalog_cache", {})
    monkeypatch.setattr(media, "_catalog_lock", asyncio.Lock())
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, json={"data": [{"id": "acme/model", "name": "Media", "pricing": {"prompt": "0.05"}, "architecture": {"input_modalities": ["text"]}}]})
    transport(handler)
    cards, warnings = await media.media_catalog()
    assert len(cards) == 10 and not warnings
    assert cards[0]["pricing"] == {"prompt": "0.05"} and "input_price" not in cards[0]
    await media.media_catalog()
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_fleet_video_runs_once_and_observes_unknown_outstanding_work(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock
    from pantheon.models import client as client_module
    session=MagicMock(deployment='gpu',route={'transport_policy':'direct_only'})
    session.submit=AsyncMock(return_value={'state':'unknown','upstream_pending':True})
    session.status=AsyncMock(return_value={'state':'succeeded','upstream_pending':False,
        'job_id':'video-fleet-test','ref':'fleet-job://gpu/video-fleet-test','model':'movie',
        'result':{'artifacts':[], 'usage':{}}})
    @asynccontextmanager
    async def inference(model,operation):
        assert model=='fleet-model://gpu/movie' and operation=='video'
        yield session
    monkeypatch.setattr(client_module,'get_client',lambda: MagicMock(inference=inference))
    runner=pg.Playground()
    params={'size':'256x256','fps':8,'num_frames':17,'num_inference_steps':4}
    result=await runner.run('video-fleet-test','fleet:gpu','fleet-model://gpu/movie','Boat',
                            operation='video',parameters=params)
    assert result['success'] and not result['upstream_pending']
    session.submit.assert_awaited_once_with({'text':'Boat'},request_id='video-fleet-test',parameters=params)
    session.status.assert_awaited_once_with('video-fleet-test', wait_seconds=2)
    assert result['job_policy']=='direct_only'
    for unsupported in [{'seconds':4},{'output_path':'/private'},{'generate_audio':True}]:
        with pytest.raises(ValueError): media.validate('video',unsupported,fleet=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['succeeded', 'failed', 'cancelled'])
async def test_fleet_request_timing_includes_resolution_and_observation(monkeypatch, state):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from pantheon.models import client as client_module

    # Independent client/service clocks: service time may even come from an
    # earlier deduplicated request, so it must never be substituted for wall time.
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr(pg, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    record = {'state': state, 'job_id': 'timing-fleet-job',
              'ref': 'fleet-job://gpu/timing-fleet-job', 'elapsed_ms': 12000,
              'model': 'movie', 'result': {'artifacts': []}}
    async def submit(*args, **kwargs):
        clock.now += 2
        return {'state': 'running'}
    async def status(request_id, *, wait_seconds):
        clock.now += 3
        return record
    session = MagicMock(deployment='gpu', route={'transport_policy': 'direct_only'})
    session.submit = AsyncMock(side_effect=submit)
    session.status = AsyncMock(side_effect=status)
    @asynccontextmanager
    async def inference(*args):
        clock.now += 1
        yield session
    monkeypatch.setattr(client_module, 'get_client', lambda: MagicMock(inference=inference))
    runner = pg.Playground()
    result = await runner.run('timing-fleet-job', 'fleet:gpu', 'fleet-model://gpu/movie',
                              'A public boat', operation='video', parameters={})
    assert result['success'] == (state == 'succeeded')
    assert result['elapsed_ms'] == 6000
    assert result['service_elapsed_ms'] == 12000
    assert result['timings'] == {'resolve_ms': 1000, 'submit_ms': 2000, 'observe_ms': 3000}
    session.submit.assert_awaited_once()
    session.status.assert_awaited_once_with('timing-fleet-job', wait_seconds=2)
