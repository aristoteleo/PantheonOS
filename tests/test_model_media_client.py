"""Client/connector binary roundtrips, with control grants supplied by a fixture.

These exercise the real connector HTTP server, not a live Fleet gateway. The
gateway Direct/Relay transport needs its separate installed acceptance.
"""
from contextlib import asynccontextmanager
import hashlib
import io
import json

import httpx
import pytest

from pantheon.models.client import ModelServices
from pantheon.models.media import CHUNK, artifact_ref, parse_artifact_ref
from test_model_services import connector_module, deployment, serve


@asynccontextmanager
async def service(tmp_path, intercept=None):
    connector = connector_module.Connector(tmp_path)
    connector.configure({'engine': 'api', 'endpoint': 'https://provider.invalid/v1'})
    row = deployment()
    row['config_revision'] = connector.revision
    row['engine'] = 'api'
    observed = []
    try:
        with serve(connector_module.handler(connector)) as origin:
            async with httpx.AsyncHTTPTransport() as wire:
                async def transport(request):
                    observed.append((request.method, request.url.path))
                    if request.url.host == 'hub.test':
                        assert request.headers['Authorization'] == 'Bearer test-fleet-identity'
                        if request.url.path == '/api/model-services':
                            return httpx.Response(200, json={'deployments': [row]})
                        assert request.url.path == '/api/fleet/apps/workload-connect'
                        assert json.loads(request.content) == row['binding']
                        return httpx.Response(200, json={'origin': 'https://instance.apps.test',
                                                        'access_token': 'test-instance-grant'})
                    assert request.url.host == 'instance.apps.test'
                    assert request.headers['Authorization'] == 'Bearer test-instance-grant'
                    assert request.headers['X-Model-Config'] == row['config_revision']
                    assert 'test-fleet-identity' not in str(request.headers)
                    if intercept:
                        result = await intercept(request, connector)
                        if result is not None:
                            return result
                    request.url = httpx.URL(origin).copy_with(path=request.url.path)
                    return await wire.handle_async_request(request)
                client = ModelServices('https://hub.test', 'test-fleet-identity', httpx.MockTransport(transport))
                try:
                    yield client, connector, observed
                finally:
                    await client.aclose()
    finally:
        if connector._media_store:
            connector._media_store.close()


class BoundedSource(io.BytesIO):
    def read(self, size=-1):
        assert 0 < size <= CHUNK
        return super().read(size)


@pytest.mark.asyncio
async def test_large_binary_roundtrip_and_explicit_lost_ack_resume(tmp_path):
    body = bytes(range(256)) * (CHUNK // 256 + 13)
    declaration = dict(request_key='resume-input', kind='audio', mime='audio/wav')
    lose_ack = True
    async def lost_ack(request, connector):
        nonlocal lose_ack
        if request.method == 'PUT' and lose_ack:
            lose_ack = False
            connector.media_store().append(request.url.path.split('/')[-1],
                int(request.headers['Upload-Offset']), request.content)
            raise httpx.ReadError('acknowledgement lost', request=request)
    async with service(tmp_path, lost_ack) as (client, connector, observed):
        async with client.media('mac') as media:
            with pytest.raises(httpx.ReadError):
                await media.upload(BoundedSource(body), **declaration)
            # No mutation replay or automatic fresh upload after ambiguous ACK.
            assert sum(method == 'PUT' for method, _ in observed) == 1
            result = await media.upload(BoundedSource(body), **declaration)
            assert result['ref'].startswith('fleet-artifact://mac/')
            assert result['sha256'] == hashlib.sha256(body).hexdigest()
            assert result['size'] == len(body)
            assert sum(method == 'PUT' for method, _ in observed) == 2
            assert await media.upload(BoundedSource(body), **declaration) == result
            assert sum(method == 'PUT' for method, _ in observed) == 2
            destination = io.BytesIO()
            assert await media.download(result['ref'], destination) == result
            assert destination.getvalue() == body
            assert media.transport == 'fleet_relay'
            with pytest.raises(ValueError, match='another service'):
                await media.metadata(artifact_ref('other', result['id']))
            await media.remove(result['ref'])
            with pytest.raises(RuntimeError, match='HTTP 400'):
                await media.metadata(result['ref'])
        assert sum(path == '/api/fleet/apps/workload-connect' for _, path in observed) == 1
        assert not connector.calls and connector.media_transfers == 0


@pytest.mark.asyncio
async def test_directory_changes_do_not_move_media_to_another_node(tmp_path):
    async with service(tmp_path) as (client, connector, observed):
        async with client.media('mac') as media:
            result = await media.upload(io.BytesIO(b'image'), request_key='fixed', kind='image', mime='image/png')
            connector.configure({'engine': 'api', 'endpoint': 'https://new-provider.invalid/v1'})
            with pytest.raises(RuntimeError, match='HTTP 409'):
                await media.download(result['ref'], io.BytesIO())
        assert sum(path == '/api/model-services' for _, path in observed) == 1


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['range', 'digest', 'oversize', 'compression', 'truncated', 'redirect'])
async def test_download_rejects_corrupt_or_unbounded_content(tmp_path, fault):
    async def corrupt(request, connector):
        if request.url.path.endswith('/content'):
            headers = {'Content-Range': 'bytes 0-3/4', 'ETag': '"' + hashlib.sha256(b'data').hexdigest() + '"'}
            body = b'data'
            status = 206
            if fault == 'range':
                headers['Content-Range'] = 'bytes 1-4/5'
            elif fault == 'digest':
                body = b'evil'
            elif fault == 'oversize':
                body += b'one-too-many'
            elif fault == 'compression':
                headers['Content-Encoding'] = 'gzip'
            elif fault == 'truncated':
                body = b'dat'
            else:
                status = 302
                headers['Location'] = 'http://private-node.invalid/secret'
            return httpx.Response(status, headers=headers, stream=Chunks(body))
    async with service(tmp_path, corrupt) as (client, _, observed):
        async with client.media('mac') as media:
            result = await media.upload(io.BytesIO(b'data'), request_key='corrupt', kind='audio', mime='audio/wav')
            with pytest.raises((ValueError, RuntimeError)):
                await media.download(result['ref'], io.BytesIO())
        assert sum(path.endswith('/content') for _, path in observed) == 1


@pytest.mark.asyncio
async def test_direct_only_does_not_silently_relay_artifacts(tmp_path):
    async with service(tmp_path) as (client, _, observed):
        from pantheon.models.direct import DirectUnavailable
        with pytest.raises(DirectUnavailable):
            async with client.media('mac', 'direct_only'):
                pytest.fail('Direct transport was not available')
        assert observed == [('GET', '/api/model-services')]


@pytest.mark.parametrize('ref', ['https://node/file', 'fleet-artifact://mac/../private',
    'fleet-artifact://mac/' + '0' * 32 + '?token=secret', 'fleet-artifact://other@mac/' + '0' * 32])
def test_media_reference_is_not_a_path_or_url(ref):
    with pytest.raises(ValueError):
        parse_artifact_ref(ref)
