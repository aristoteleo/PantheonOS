"""Real loopback view transport, artifact pairing and bounded native control."""
import asyncio
import json
from pathlib import Path
import sys

import httpx
import pytest

from pantheon.apps.dependency_assembly import AssemblyError
from pantheon.apps.lifecycle import build_artifact
from pantheon.platform.local_desktop import DesktopView, snapshot_frontend, MAX_REQUEST


@pytest.mark.asyncio
async def test_view_scopes_calls_persists_selection_and_denies_other_origins(tmp_path):
    assets = tmp_path/'assets'; assets.mkdir()
    (assets/'index.js').write_text('export const paired = true;')
    calls = []
    async def invoke(method, args, timeout):
        calls.append((method, args, timeout))
        return {'ok': True}
    async with DesktopView(assets, invoke, tmp_path/'view.json') as view:
        async with httpx.AsyncClient(trust_env=False) as client:
            page = await client.get(view.url, headers={'Sec-Fetch-Site': 'cross-site'})
            assert page.status_code == 200  # Native shell embeds the capability URL.
            assert page.headers['referrer-policy'] == 'no-referrer'
            assert '__NONCE__' not in page.text
            headers = {'Origin': view.origin, 'X-Pantheon-View': '1'}
            body = {'method': 'get_agent_app_info', 'args': {}, 'timeout_s': 30}
            assert (await client.post(view.url+'rpc', headers=headers, json=body)).json()['result'] == {'ok': True}
            for bad in ({}, {'Origin': 'https://elsewhere.example', 'X-Pantheon-View': '1'},
                        {'Origin': view.origin}, {**headers, 'Host': 'attacker.example'}):
                assert (await client.post(view.url+'rpc', headers=bad, json=body)).status_code == 403
            assert len(calls) == 1
            assert (await client.get(view.origin+'/view/wrong/package/index.js')).status_code == 404
            assert (await client.get(view.url+'package/%2e%2e/view.json')).status_code == 404
            assert (await client.get(view.url+'package/index.js')).text == 'export const paired = true;'
            for bad in ({**body, 'timeout_s': float('inf')}, {**body, 'method': 'other/app'},
                        {**body, 'instance_id': 'other'}, {**body, 'args': []}):
                assert (await client.post(view.url+'rpc', headers={**headers, 'Content-Type': 'application/json'},
                                         content=json.dumps(bad))).status_code == 400
            try:
                oversized = await client.post(view.url+'rpc', headers=headers, json={'padding':'x'*MAX_REQUEST})
                assert oversized.status_code == 400
            except httpx.ReadError:
                pass  # Early rejection may close the socket while the body is still being sent.
            assert len(calls) == 1
            assert (await client.post(view.url+'state', headers=headers, json={'chatId':'chat-a'})).status_code == 200
            assert (tmp_path/'view.json').stat().st_mode & 0o077 == 0
            assert (await client.post(view.url+'state', headers=headers, json={'read':True})).json()['result'] == {'chatId':'chat-a'}
            assert (await client.post(view.url+'state', headers=headers, json={'root':'/etc'})).status_code == 400
    async with DesktopView(assets, invoke, tmp_path/'view.json') as reopened:
        assert reopened.token != view.token
        assert await reopened.request('state', {'read':True}) == {'chatId':'chat-a'}


@pytest.mark.asyncio
async def test_closed_view_joins_pending_client_without_replaying_or_exposing_errors(tmp_path):
    entered, exited = asyncio.Event(), asyncio.Event()
    calls = []
    async def invoke(method, args, timeout):
        calls.append(method)
        entered.set()
        try: await asyncio.Future()
        finally: exited.set()
    view = DesktopView(tmp_path, invoke, tmp_path/'view.json')
    async with httpx.AsyncClient(trust_env=False) as client:
        async with view:
            request = asyncio.create_task(client.post(view.url+'rpc',
                headers={'Origin':view.origin, 'X-Pantheon-View':'1'},
                json={'method':'chat','args':{},'timeout_s':60}))
            await asyncio.wait_for(entered.wait(), 3)
        result = await request
        assert result.status_code == 502 and exited.is_set() and not view.tasks
        assert calls == ['chat']
        with pytest.raises(AssemblyError): await view.request('rpc', {})


def test_gui_is_snapshot_of_paired_digest_and_never_backend_files(tmp_path):
    root = tmp_path/'package'; (root/'frontend').mkdir(parents=True)
    (root/'app.json').write_text(json.dumps({'id':'agent', 'version':'1.0.0','entry':{'frontend':'frontend/index.js'}}))
    (root/'fleet.json').write_text(json.dumps({'protocol':1,'app_id':'agent','version':'1.0.0'}))
    (root/'frontend/index.js').write_text('export const version = 1;')
    (root/'backend.py').write_text('PRIVATE_BACKEND_CODE')
    _, revision = build_artifact(root)
    spec = {'path':str(root), 'revision':revision}
    destination = tmp_path/'view'
    snapshot_frontend(spec, destination)
    assert (destination/'index.js').read_text() == 'export const version = 1;'
    assert not (destination/'backend.py').exists()
    (root/'frontend/index.js').write_text('export const version = 2;')
    assert (destination/'index.js').read_text() == 'export const version = 1;'
    with pytest.raises(AssemblyError): snapshot_frontend(spec, tmp_path/'new-view')


@pytest.mark.asyncio
@pytest.mark.parametrize('command', [b'{"protocol":1,"command":"retry"}\n', b'{"protocol":2,"command":"kill"}\n'])
async def test_native_pipe_eof_requests_drain_and_does_not_import_agent(command):
    source = '''import asyncio, sys
from pantheon.platform.local_desktop import control_input
async def main():
 q=asyncio.Queue()
 await control_input(q)
 print([q.get_nowait() for _ in range(q.qsize())])
 assert not any(n.startswith('pantheon.chatroom') or n == 'pantheon.repl' for n in sys.modules)
asyncio.run(main())
'''
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', source,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(command), 5)
    assert proc.returncode == 0, err.decode()
    assert (b"['retry', 'stop']" if b'"retry"' in command else b"['stop']") in out


@pytest.mark.asyncio
async def test_close_during_gui_snapshot_joins_writer_before_removing_assets(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    from pantheon.platform import local_desktop
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    destinations = []
    def snapshot(package, destination):
        destinations.append(destination)
        entered.set()
        assert release.wait(5)
        (destination/'index.js').write_text('late writer')
        completed.set()
    async def bind(alias, interface):
        async def invoke(*args): return {'protocol':1}
        return invoke
    session = SimpleNamespace(app_binding=lambda alias: {}, bind_rpc=bind,
        spec={'packages':{'agent':{}}, 'apps':{'agent':{'package':'agent'}}},
        runtime=SimpleNamespace(root=tmp_path), root=tmp_path)
    monkeypatch.setattr(local_desktop, 'snapshot_frontend', snapshot)
    task = asyncio.create_task(local_desktop.desktop_view(session, 'agent'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done() and destinations[0].exists()
        task.cancel()
        await asyncio.sleep(.05)
        assert not task.done() and destinations[0].exists()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    assert completed.is_set() and not destinations[0].exists()
