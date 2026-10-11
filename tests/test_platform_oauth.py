"""Real callback servers with local credentials and fake provider exchange."""

import asyncio
import json

import pytest

from services_host import ServicesHost as PlatformService
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
async def test_legacy_login_uses_owned_session_and_releases_callback_server(provider, monkeypatch):
    host = PlatformService()
    threads = []
    def open_browser(url):
        sessions = codex._SESSIONS if provider == 'codex' else gemini._GEMINI_SESSIONS
        session = next(s for s in sessions.values() if s.auth_url == url)
        threads.append(session.server_thread)
        session.server.result = {'code': 'legacy', 'state': session.state}
        session.callback_event.set()
    monkeypatch.setattr('pantheon.chatroom.services.oauth_api.webbrowser.open', open_browser)
    try:
        result = await host.oauth_login(provider)
        assert result['success'] and result['message'], result
        assert not host._oauth_sessions
        assert not threads[0].is_alive()
    finally:
        await host.cleanup()
