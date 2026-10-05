"""Explicit Desktop state and event ownership with an actual NATS broker.

Viewport replies are simulated; document reducers, presence, addressed requests,
NATS transport and lifecycle are production code. This is not GUI acceptance.
"""
import asyncio
import builtins
import json
from pathlib import Path
import shutil
import socket
import subprocess
import time

import nats
import pytest

from pantheon.apps.builtin.desktop.session_binding import DesktopSessionBinding
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet
from pantheon.remote.backend.nats import NATSBackend
from pantheon.remote.streams import NamedStreamPublisher


@pytest.fixture
def broker(tmp_path):
    binary = shutil.which('nats-server') or '/opt/homebrew/bin/nats-server'
    if not Path(binary).is_file():
        pytest.skip('Requires nats-server for Desktop event integration')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = tmp_path / 'nats.conf'
    config.write_text(f'''host: 127.0.0.1
port: {port}
authorization {{ users: [
  {{ user: desktop_a, password: fixture_a, permissions: {{ publish: ["a.pantheon.stream.desktop"], subscribe: [] }} }},
  {{ user: desktop_b, password: fixture_b, permissions: {{ publish: ["b.pantheon.stream.desktop"], subscribe: [] }} }},
  {{ user: viewport, password: fixture_view, permissions: {{ publish: [], subscribe: ["*.pantheon.stream.desktop"] }} }}
] }}
''')
    with (tmp_path / 'nats.log').open('w') as log:
        process = subprocess.Popen([binary, '-c', str(config)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, (tmp_path / 'nats.log').read_text()
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, 'NATS readiness timeout'
                    time.sleep(.02)
            yield f'nats://127.0.0.1:{port}'
        finally:
            process.terminate()
            process.wait(timeout=10)


def test_binding_refuses_implicit_or_missing_state(tmp_path):
    publisher = NamedStreamPublisher()
    for root in (Path('relative'), tmp_path / 'missing'):
        with pytest.raises(ValueError, match='absolute directory'):
            DesktopSessionBinding(root, publisher)
    with pytest.raises(ValueError, match='publisher'):
        DesktopSessionBinding(tmp_path, None)


@pytest.mark.asyncio
async def test_bound_desktops_share_only_selected_document_and_event_namespace(tmp_path, broker, monkeypatch):
    monkeypatch.setenv('NATS_ENABLE_JETSTREAM', 'false')
    # Pre-import dependencies before denying ambient discovery during operations.
    import pantheon.remote.backend.base
    import pantheon.apps.builtin.desktop.presence
    import pantheon.apps.builtin.desktop.desktop_session
    original_import = builtins.__import__
    forbidden = []
    def guarded(name, *args, **kwargs):
        if name == 'pantheon.settings' or name == 'pantheon.agent' or name.startswith('pantheon.chatroom'):
            forbidden.append(name)
            raise AssertionError('Bound Desktop accessed ambient/Agent state: ' + name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    def no_fallback(*args, **kwargs):
        raise AssertionError('Explicit transport must not discover an ambient backend')
    monkeypatch.setattr('pantheon.remote.RemoteBackendFactory.create_backend', no_fallback)

    roots = {name: tmp_path / name for name in ('a', 'b')}
    for root in roots.values():
        root.mkdir()
    services, backends = [], []
    def attach(name):
        backend = NATSBackend([broker], subject_prefix=name, user='desktop_' + name,
                              password='fixture_' + name, max_reconnect_attempts=0)
        backends.append(backend)
        binding = DesktopSessionBinding(roots[name], NamedStreamPublisher(backend=backend))
        service = DesktopToolSet(session_binding=binding)
        services.append(service)
        return service

    observer = await nats.connect(broker, user='viewport', password='fixture_view')
    messages = asyncio.Queue()
    async def receive(message):
        await messages.put((message.subject, json.loads(message.data)['data']))
    await observer.subscribe('*.pantheon.stream.desktop', cb=receive)
    await observer.flush()
    async def next_event(namespace, kind):
        subject, event = await asyncio.wait_for(messages.get(), 5)
        assert subject == namespace + '.pantheon.stream.desktop'
        assert event['type'] == kind
        return event
    pending = None
    try:
        a, b = attach('a'), attach('b')
        assert (await a.desktop_presence(viewport_id='viewport-a', active=True))['success']
        await next_event('a', 'desktop.presence')
        assert (await b.desktop_presence(viewport_id='viewport-b', active=True))['success']
        await next_event('b', 'desktop.presence')
        opened = await a.desktop_intent('open', {'app_id': 'files', 'title': 'Owned files'})
        assert opened['success']
        delta = await next_event('a', 'desktop.delta')
        assert delta['ops'][0]['window']['title'] == 'Owned files'
        window = opened['window_id']
        assert (await b.desktop_session_get())['session']['windows'] == {}
        # A fresh service sees the same persisted document and viewport leases;
        # ownership is the desktop's, not a particular Agent or Python object.
        same = attach('a')
        assert window in (await same.desktop_session_get())['session']['windows']
        assert (await same.desktop_anchor())['viewport_id'] == 'viewport-a'
        pending = asyncio.create_task(same._desktop_request('desktop.read', {'window_id': window}, timeout=5))
        request = await next_event('a', 'desktop.read')
        assert request['viewport_id'] == 'viewport-a'
        assert not (await b.report_desktop_result(request['request_id'], value='wrong desktop'))['success']
        assert not pending.done()
        assert (await same.report_desktop_result(request['request_id'], value={'title': 'Owned files'}))['success']
        assert await pending == {'success': True, 'result': {'title': 'Owned files'}}
        await a.cleanup()
        assert backends[0]._nc is None
        assert await a._publish_desktop({'type': 'closed'}) is False
        # Closing one owner cannot close another owner's publisher or remove
        # shared state/presence, nor recreate an ambient bus behind its back.
        assert window in (await same.desktop_session_get())['session']['windows']
        assert (await same.desktop_anchor())['viewport_id'] == 'viewport-a'
        await same.desktop_intent('close', {'window_id': window})
        await next_event('a', 'desktop.delta')
        await b.desktop_broadcast('survives', {'ok': True})
        assert (await next_event('b', 'desktop.broadcast'))['topic'] == 'survives'
        assert not forbidden
        assert not same._pending_desktop
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        for service in reversed(services):
            await service.cleanup()
        await observer.close()
    assert all(backend._nc is None for backend in backends)
