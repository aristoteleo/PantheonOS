"""Real callback servers with local credentials and fake provider exchange."""

import asyncio
import json
import threading
from urllib.parse import urlparse

import httpx
import pytest

from pantheon.platform.service import PlatformService
from pantheon.utils.oauth import codex, gemini


@pytest.fixture(params=['codex', 'gemini'])
def provider(request, tmp_path, monkeypatch):
    name = request.param
    module = codex if name == 'codex' else gemini
    monkeypatch.setattr(module, 'AUTH_DIR', tmp_path / 'oauth')
    monkeypatch.setattr(module, 'AUTH_FILE', tmp_path / 'oauth' / f'{name}.json')
    if name == 'codex':
        monkeypatch.setattr(codex, 'CALLBACK_PORT', 0)
        monkeypatch.setattr(codex, '_exchange_code', lambda *args: {
            'access_token': 'test-access-secret', 'refresh_token': 'test-refresh-secret', 'id_token': 'fake',
        })
        monkeypatch.setattr(codex, '_jwt_org_context', lambda token: {'chatgpt_account_id': 'test-account'})
    else:
        monkeypatch.setattr(gemini, 'GOOGLE_CALLBACK_PORT', 0)
        monkeypatch.setattr(gemini, 'resolve_oauth_client_config', lambda: ('test-client', None))
        monkeypatch.setattr(gemini, 'exchange_code_for_tokens', lambda **kwargs: {
            'access_token': 'test-access-secret', 'refresh_token': 'test-refresh-secret',
            'email': 'test@example.test', 'project_id': 'test-project', 'expires_at': 9999999999,
        })
    monkeypatch.setenv('HOME', str(tmp_path))
    return name


def login_session(provider, session_id):
    return (codex._get_session if provider == 'codex' else gemini._get_gemini_session)(session_id)


def callback(started, session):
    return started['redirect_uri'] + '?code=test-code&state=' + session.state


@pytest.mark.asyncio
async def test_timeout_preserves_paste_fallback_and_secrets_stay_private(provider):
    host = PlatformService()
    try:
        started = await host.oauth_start(provider)
        assert started['success'], started
        sid = started['session_id']
        session = login_session(provider, sid)
        thread = session.server_thread
        result = await host.oauth_wait(sid, provider, 0)
        assert result['timed_out'] and not result['success']
        result = await host.oauth_complete(sid, callback(started, session), provider)
        assert result['success'] and result['provider'] == provider, result
        assert 'secret' not in json.dumps(result)
        assert not thread.is_alive()
        # A competing wait request receives the same metadata, never exchanges twice.
        assert await host.oauth_wait(sid, provider, 0) == result
    finally:
        await host.cleanup()


@pytest.mark.asyncio
async def test_real_callback_and_parallel_waits_do_not_block_other_platform_calls(provider):
    host = PlatformService()
    try:
        started = await host.oauth_start(provider)
        sid = started['session_id']
        session = login_session(provider, sid)
        waiters = [asyncio.create_task(host.oauth_wait(sid, provider, 5)) for _ in range(3)]
        await asyncio.sleep(.05)
        assert (await asyncio.wait_for(host.platform_info(), .5))['api_version'] == 1
        url = f'http://127.0.0.1:{session.server.server_port}' + urlparse(started['redirect_uri']).path
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params={'code': 'test-code', 'state': session.state})
        assert response.status_code == 200
        results = await asyncio.wait_for(asyncio.gather(*waiters), 3)
        assert all(row['success'] and row == results[0] for row in results), results
    finally:
        await host.cleanup()


@pytest.mark.asyncio
async def test_cancel_and_shutdown_release_waiters_and_callback_threads(provider):
    host = PlatformService()
    started = await host.oauth_start(provider)
    sid = started['session_id']
    session = login_session(provider, sid)
    thread = session.server_thread
    waiting = asyncio.create_task(host.oauth_wait(sid, provider, 300))
    await asyncio.sleep(.05)
    assert (await host.oauth_cancel(sid, provider))['success']
    assert not (await asyncio.wait_for(waiting, 2))['success']
    assert not thread.is_alive()
    started = await host.oauth_start(provider)
    thread = login_session(provider, started['session_id']).server_thread
    waiting = asyncio.create_task(host.oauth_wait(started['session_id'], provider, 300))
    await asyncio.sleep(.05)
    await asyncio.wait_for(host.cleanup(), 3)
    assert not (await waiting)['success']
    assert not thread.is_alive()
    assert not host._oauth_sessions and not host._oauth_jobs
    assert not (await host.oauth_start(provider))['success']


@pytest.mark.asyncio
async def test_login_session_is_owned_by_starting_host(provider):
    first, second = PlatformService(), PlatformService()
    try:
        started = await first.oauth_start(provider)
        sid = started['session_id']
        session = login_session(provider, sid)
        assert not (await second.oauth_complete(sid, callback(started, session), provider))['success']
        await second.oauth_cancel(sid, provider)
        assert login_session(provider, sid) is session
        assert (await first.oauth_complete(sid, callback(started, session), provider))['success']
    finally:
        await first.cleanup()
        await second.cleanup()


@pytest.mark.asyncio
async def test_expiry_closes_unused_server_without_another_login(provider, monkeypatch):
    cls = codex.CodexOAuthManager if provider == 'codex' else gemini.GeminiCliOAuthManager
    start = cls.start_login
    monkeypatch.setattr(cls, 'start_login', lambda self: start(self, session_ttl_seconds=.05))
    host = PlatformService()
    try:
        started = await host.oauth_start(provider)
        session = login_session(provider, started['session_id'])
        thread = session.server_thread
        await asyncio.sleep(.8)
        assert not thread.is_alive()
        assert not host._oauth_sessions
    finally:
        await host.cleanup()


@pytest.mark.asyncio
async def test_disconnected_start_is_still_owned_and_closed_during_shutdown(provider, monkeypatch):
    cls = codex.CodexOAuthManager if provider == 'codex' else gemini.GeminiCliOAuthManager
    start = cls.start_login
    entered, release = threading.Event(), threading.Event()
    servers = []
    def slow_start(manager):
        entered.set()
        assert release.wait(5)
        result = start(manager)
        servers.append(login_session(provider, result['session_id']).server_thread)
        return result
    monkeypatch.setattr(cls, 'start_login', slow_start)
    host = PlatformService()
    pending = asyncio.create_task(host.oauth_start(provider))
    assert await asyncio.to_thread(entered.wait, 2)
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
    cleanup = asyncio.create_task(host.cleanup())
    try:
        await asyncio.sleep(.05)
        assert not cleanup.done()
        assert (await asyncio.wait_for(host.platform_info(), .5))['api_version'] == 1
    finally:
        release.set()
        await asyncio.wait_for(cleanup, 3)
    assert servers and not servers[0].is_alive()
    assert not host._oauth_sessions and not host._oauth_jobs


@pytest.mark.asyncio
async def test_shutdown_waits_for_accepted_token_write_even_if_client_disconnects(provider, monkeypatch):
    module = codex if provider == 'codex' else gemini
    name = '_exchange_code' if provider == 'codex' else 'exchange_code_for_tokens'
    exchange = getattr(module, name)
    entered, release = threading.Event(), threading.Event()
    def slow_exchange(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return exchange(*args, **kwargs)
    monkeypatch.setattr(module, name, slow_exchange)
    host = PlatformService()
    started = await host.oauth_start(provider)
    session = login_session(provider, started['session_id'])
    pending = asyncio.create_task(host.oauth_complete(started['session_id'], callback(started, session), provider))
    assert await asyncio.to_thread(entered.wait, 2)
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
    cleanup = asyncio.create_task(host.cleanup())
    try:
        await asyncio.sleep(.05)
        assert not cleanup.done()
        assert (await asyncio.wait_for(host.platform_info(), .5))['api_version'] == 1
    finally:
        release.set()
        await asyncio.wait_for(cleanup, 3)
    assert module.AUTH_FILE.exists()
    assert not host._oauth_jobs and not host._oauth_sessions


@pytest.mark.asyncio
async def test_status_refresh_does_not_block_platform_and_returns_only_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, 'CODEX_CLI_AUTH', tmp_path / 'no-codex')
    monkeypatch.setattr(gemini, 'GEMINI_CLI_AUTH', tmp_path / 'no-gemini')
    entered, release = threading.Event(), threading.Event()
    def refresh(self, auto_refresh=True):
        entered.set()
        assert release.wait(5)
        return 'private-token'
    monkeypatch.setattr(codex.CodexOAuthManager, 'get_access_token', refresh)
    monkeypatch.setattr(codex.CodexOAuthManager, 'get_account_id', lambda self: 'account')
    monkeypatch.setattr(gemini.GeminiCliOAuthManager, 'get_access_token', lambda *a, **kw: None)
    monkeypatch.setattr(gemini.GeminiCliOAuthManager, 'get_project_id', lambda self: None)
    host = PlatformService()
    pending = asyncio.create_task(host.oauth_status())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert (await asyncio.wait_for(host.platform_info(), .5))['api_version'] == 1
    finally:
        release.set()
    result = await pending
    assert result['providers']['codex']['authenticated']
    assert not result['providers']['gemini']['authenticated']
    assert 'private-token' not in json.dumps(result)
    await host.cleanup()


@pytest.mark.asyncio
async def test_legacy_login_uses_owned_session_and_releases_callback_server(provider, monkeypatch):
    host = PlatformService()
    threads = []
    def open_browser(url):
        sessions = codex._SESSIONS if provider == 'codex' else gemini._GEMINI_SESSIONS
        session = next(s for s in sessions.values() if s.auth_url == url)
        threads.append(session.server_thread)
        session.server.result = {'code': 'legacy', 'state': session.state}
        session.callback_event.set()
    monkeypatch.setattr('pantheon.platform.oauth_api.webbrowser.open', open_browser)
    try:
        result = await host.oauth_login(provider)
        assert result['success'] and result['message'], result
        assert not host._oauth_sessions
        assert not threads[0].is_alive()
    finally:
        await host.cleanup()
