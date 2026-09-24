"""Hub SQL + coordinator + ordinary consumer + real rank-zero HTTP streams.

Fleet resources, peer supervisor and SGLang are controlled fixtures. The local
transport substitutes only for Fleet grant/gateway delivery; this is not GPU,
private-network or deployed gateway acceptance.
"""
import asyncio
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip('pantheon_hub', reason='Cross-repository acceptance needs Hub checkout')
from pantheon_hub.api.fleet import _fleet_id_for_user
from pantheon.models.group_coordinator import GroupCoordinator
from pantheon.models.group_creation import topology_for
from pantheon.models.group_hub import HubGroupJournal
from pantheon.models.client import model_ref
from pantheon.models.http_pool import HTTPPool
from test_model_group_creation_hub import durable
from test_model_group_connector import module, group, leader, plan, record, TOKEN, redirect_engine
from test_model_services import serve
import test_model_groups as resources
import test_model_group_security as security_fixture
import test_model_group_overlay as overlay_fixture


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_published_consumer_stream_drains_before_peer_cleanup(durable, module, tmp_path, monkeypatch, cancel):
    await durable.reopen()
    owner = _fleet_id_for_user('alice')
    value = plan()
    value.update(owner=owner, group_id='test')
    for rank, member in enumerate(value['members']):
        member.update(node_id=('node-a', 'node-b')[rank], generation=2)
    security = dict(ca_sha256='c'*64, ready=False, closed=False,
                    topology=topology_for(value).document(), network=overlay_fixture.network())
    monkeypatch.setattr(resources, 'OWNER', owner)
    monkeypatch.setattr(overlay_fixture, 'OWNER', owner)
    monkeypatch.setattr(overlay_fixture, 'security', lambda: security)
    monkeypatch.setattr(security_fixture, 'security', lambda: security)
    journal = HubGroupJournal(durable.client, owner)
    row = await journal.create('test', resources.targets(), peer_security=security,
                               inference=dict(context_length=value['context_length'], parallel=value['parallel']))
    binding = row['inference']['binding']
    fleet = overlay_fixture.OverlayFleet()
    run = SimpleNamespace(ready=lambda: not fleet.closed,
        engine=SimpleNamespace(listener_identity=lambda _: ('original-test-engine',)))
    identity = {key: binding[key] for key in ('instance_id', 'generation')}
    connector = module.GroupConnector(tmp_path / 'leader', run, value, record(), identity, TOKEN)
    assert connector.revision == row['inference']['config_revision']
    release = threading.Event()
    received = asyncio.Event()
    engine_requests = []

    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            engine_requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n')
            self.wfile.flush()
            if not release.wait(10):
                return
            try:
                self.wfile.write(b'data: {"choices":[{"delta":{"content":" last"}}],"usage":{"total_tokens":5}}\n\ndata: [DONE]\n\n')
                self.wfile.flush()
            except OSError:
                pass

    class Gateway(httpx.AsyncBaseTransport):
        """Real TCP, fixture-only credential translation done by Fleet in production."""
        def __init__(self):
            self.transport = httpx.AsyncHTTPTransport()
        async def handle_async_request(self, request):
            assert request.headers['authorization'] == 'Bearer fixture-grant'
            del request.headers['authorization']
            request.headers['X-Pantheon-App-Token'] = TOKEN
            return await self.transport.handle_async_request(request)
        async def aclose(self):
            await self.transport.aclose()

    with serve(Engine) as upstream, leader(module, connector) as endpoint:
        redirect_engine(monkeypatch, upstream)
        async with httpx.AsyncClient(timeout=3) as control:
            async def inference(original, method, args):
                assert original == binding
                saved = await journal.load('test')
                assert saved['inference']['activation_sent' if method == 'resume' else 'drain_sent']
                response = await control.post(endpoint+'/rpc', headers={'X-Fleet-RPC-Token': TOKEN},
                    json=dict(method=method, args=args))
                response.raise_for_status()
                return response.json()
            fleet.group_inference = inference
            controller = GroupCoordinator(journal, fleet)
            await resources.drive(controller, 'ready')
            assert not (await durable.client.deployments())[0]['models']
            await controller.advance('test')
            await controller.advance('test')
            sources, models = await durable.client.catalog()
            assert len(models) == 1 and sources[0]['compute'] == 'Fleet model group · 2 nodes'
            published = (await durable.client.deployments())[0]
            assert published['binding'] == binding
            ref = model_ref(published['deployment_id'], connector.model)
            gateway = Gateway()
            consumer = durable.client
            await consumer.cancel_http.aclose()
            consumer.cancel_http = HTTPPool(timeout=3, connections=4, keepalive=1, transport=gateway)
            @asynccontextmanager
            async def connection(row, policy='relay_allowed', grant=None):
                assert row['binding'] == binding and row['config_revision'] == connector.revision
                async with httpx.AsyncClient(transport=gateway, timeout=5) as http:
                    yield http, dict(origin=endpoint, access_token='fixture-grant'), 'fixture-tcp'
            monkeypatch.setattr(consumer, 'connection', connection)
            async def chunk(delta):
                if delta.get('content') == 'first':
                    received.set()
            task = asyncio.create_task(consumer.complete(ref, messages=[dict(role='user', content='test')], process_chunk=chunk))
            try:
                await until(lambda: received.is_set() or task.done())
                if task.done():
                    await task
                assert received.is_set()
                await controller.stop('test')
                assert not (await consumer.catalog())[1]
                with pytest.raises(ValueError, match='not published'):
                    await consumer.complete(ref, messages=[])
                await controller.advance('test')  # Persist drain claim.
                row = await controller.advance('test')  # Real HTTP drain reports still busy.
                assert connector.drained and not row['inference']['drained']
                assert all(v['state'] == 'pinned' for v in fleet.overlays.values())
                assert not any(r['action'] == 'stop' for _, r in fleet.calls)
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    release.set()
                    result = await task
                    assert result['content'] == 'first last' and result['usage']['total_tokens'] == 5
                    assert result['route']['compute'] == 'Fleet model group · 2 nodes'
                    assert result['route']['compute_location'] == 'node'
                    assert result['route']['billing_account'] == 'local'
                await until(lambda: not connector.calls)
                assert len(engine_requests) == 1
                activity = connector.activity_status()['requests'][0]
                assert activity['state'] == ('cancelled' if cancel else 'completed')
                # Recreate Hub/Agent persistence after drain, then finish exact cleanup.
                await durable.reopen()
                journal = HubGroupJournal(durable.client, owner)
                controller = GroupCoordinator(journal, fleet)
                row = await controller.advance('test')
                assert row['inference']['drained']
                assert all(v['state'] == 'pinned' for v in fleet.overlays.values())
                await resources.drive(controller, 'stopped')
                assert all(v['state'] == 'closed' for v in fleet.overlays.values())
                assert (await durable.client.deployments())[0]['state'] == 'stopped'
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await until(lambda: not connector.calls)
                await gateway.aclose()
