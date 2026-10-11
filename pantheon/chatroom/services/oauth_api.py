"""User-scoped OAuth flows, independent of Agent execution.

Callback sessions belong to the host that started them. Waiting polls provider
events without occupying a worker thread; token exchange and disk/network I/O
run off the RPC loop. Accepted operations remain owned until shutdown drains
them, even when an RPC caller disconnects.
"""

import asyncio
from dataclasses import dataclass, field
import time
import webbrowser

from pantheon.toolset import tool
from pantheon.utils.log import logger


@dataclass
class _Session:
    provider: str
    manager: object
    expires_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: dict | None = None
    timer: asyncio.Task | None = None


class OAuthAPI:
    def _oauth_state(self):
        # Accessed only on the host's event loop, never in provider threads.
        if not hasattr(self, '_oauth_sessions'):
            self._oauth_sessions = {}
            self._oauth_jobs = set()
            self._oauth_timers = set()
            self._oauth_starting = 0
            self._oauth_stopping = False

    @staticmethod
    def _oauth_manager(provider):
        from pantheon.utils.oauth import CodexOAuthManager, GeminiCliOAuthManager
        if provider == 'codex':
            return CodexOAuthManager()
        if provider == 'gemini':
            return GeminiCliOAuthManager()
        raise ValueError(f'Unsupported OAuth provider: {provider}')

    def _track_oauth_job(self, task):
        self._oauth_jobs.add(task)
        def done(job):
            self._oauth_jobs.discard(job)
            if not job.cancelled():
                job.exception()  # observe failures after a disconnected caller
        task.add_done_callback(done)

    async def _run_oauth(self, fn, *args):
        self._oauth_state()
        if self._oauth_stopping:
            return {'success': False, 'error': 'OAuth service is stopping'}
        task = asyncio.create_task(fn(*args))
        self._track_oauth_job(task)
        try:
            return await asyncio.shield(task)
        except Exception as error:
            return {'success': False, 'error': str(error)}

    async def _stop_oauth(self):
        self._oauth_state()
        self._oauth_stopping = True
        timers = list(self._oauth_timers)
        for timer in timers:
            timer.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        for session_id in list(self._oauth_sessions):
            await self._close_oauth_session(session_id)
        # Late start operations observe _oauth_stopping and close their newly
        # created callback server. Never snapshot while a token write continues.
        await asyncio.gather(*list(self._oauth_jobs), return_exceptions=True)

    async def _close_oauth_session(self, session_id):
        session = self._oauth_sessions.get(session_id)
        if session is None:
            return
        async with session.lock:
            if self._oauth_sessions.get(session_id) is not session:
                return
            await asyncio.to_thread(session.manager.cancel_login, session_id)
            self._oauth_sessions.pop(session_id, None)
            if session.timer is not None and session.timer is not asyncio.current_task():
                session.timer.cancel()

    async def _expire_oauth_session(self, session_id, session):
        await asyncio.sleep(max(0, session.expires_at - time.time()))
        # Shield cleanup as a tracked job, so shutdown cannot orphan its thread.
        task = asyncio.create_task(self._close_oauth_session(session_id))
        self._track_oauth_job(task)
        await asyncio.shield(task)

    async def _start_oauth(self, provider):
        manager = self._oauth_manager(provider)
        if len(self._oauth_sessions) + self._oauth_starting >= 16:
            raise ValueError('Too many pending OAuth sessions; finish or cancel an existing login')
        self._oauth_starting += 1
        try:
            started = await asyncio.to_thread(manager.start_login)
        finally:
            self._oauth_starting -= 1
        session_id = started['session_id']
        session = _Session(provider, manager, started['expires_at'])
        self._oauth_sessions[session_id] = session
        if self._oauth_stopping:
            await self._close_oauth_session(session_id)
            raise RuntimeError('OAuth service is stopping')
        session.timer = asyncio.create_task(self._expire_oauth_session(session_id, session))
        self._oauth_timers.add(session.timer)
        def expired(timer):
            self._oauth_timers.discard(timer)
            if not timer.cancelled() and timer.exception() is not None:
                logger.error('OAuth callback cleanup failed; retained for shutdown retry')
        session.timer.add_done_callback(expired)
        return {'success': True, 'provider': provider, **started}

    def _owned_oauth_session(self, session_id, provider):
        if provider not in ('codex', 'gemini'):
            raise ValueError(f'Unsupported OAuth provider: {provider}')
        session = self._oauth_sessions.get(session_id)
        if session is None or session.provider != provider:
            raise ValueError('OAuth session not found on this service; start login again')
        return session

    @staticmethod
    def _oauth_success(session):
        from pantheon.utils.model_selector import reset_model_selector
        reset_model_selector()  # legacy Agent consumers; platform selectors are scoped
        manager = session.manager
        result = {'success': True, 'provider': session.provider}
        if session.provider == 'codex':
            result['account_id'] = manager.get_account_id()
        else:
            project_id = manager.get_project_id()
            result.update(email=manager.get_email(), project_id=project_id,
                          runtime_ready=bool(project_id))
        return result

    async def _finish_oauth(self, session_id, session, callback_url=None):
        async with session.lock:
            if self._oauth_sessions.get(session_id) is not session:
                raise ValueError('OAuth session was cancelled or expired')
            if session.result is not None:
                return session.result
            if time.time() >= session.expires_at:
                raise ValueError('OAuth session expired; start login again')
            try:
                if callback_url is None:
                    await asyncio.to_thread(session.manager.wait_login, session_id, 0)
                else:
                    await asyncio.to_thread(session.manager.complete_login_from_url,
                                            session_id, callback_url)
                session.result = await asyncio.to_thread(self._oauth_success, session)
                return session.result
            except Exception as error:
                # A zero-wait poll is not a failed login. Preserve paste fallback.
                if callback_url is None and 'Timed out' in str(error):
                    return None
                raise

    async def _wait_oauth(self, session_id, provider, timeout_seconds):
        session = self._owned_oauth_session(session_id, provider)
        timeout = max(0, min(float(timeout_seconds), 600))
        deadline = asyncio.get_running_loop().time() + timeout
        while not self._oauth_stopping:
            result = await self._finish_oauth(session_id, session)
            if result is not None:
                return result
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return {'success': False, 'error': 'Timed out waiting for OAuth callback', 'timed_out': True}
            await asyncio.sleep(min(.2, remaining))
        return {'success': False, 'error': 'OAuth service is stopping'}

    @tool
    async def oauth_status(self) -> dict:
        """Get provider authentication metadata without blocking platform RPCs."""
        async def read():
            return await asyncio.to_thread(self._oauth_status_sync)
        return await self._run_oauth(read)

    @tool
    async def oauth_start(self, provider: str = 'codex') -> dict:
        """Prepare PKCE and a callback server; the frontend opens auth_url."""
        return await self._run_oauth(self._start_oauth, provider)

    @tool
    async def oauth_wait(self, session_id: str, provider: str = 'codex', timeout_seconds: int = 300) -> dict:
        """Wait asynchronously; timeout leaves the session available for paste fallback."""
        return await self._run_oauth(self._wait_oauth, session_id, provider, timeout_seconds)

    @tool
    async def oauth_complete(self, session_id: str, callback_url: str, provider: str = 'codex') -> dict:
        """Complete this host's login from a pasted callback URL."""
        async def complete():
            session = self._owned_oauth_session(session_id, provider)
            return await self._finish_oauth(session_id, session, callback_url)
        return await self._run_oauth(complete)

    @tool
    async def oauth_cancel(self, session_id: str, provider: str = 'codex') -> dict:
        """Cancel this host's pending login and release its callback server."""
        async def cancel():
            if provider not in ('codex', 'gemini'):
                raise ValueError(f'Unsupported OAuth provider: {provider}')
            session = self._oauth_sessions.get(session_id)
            if session is not None and session.provider == provider:
                await self._close_oauth_session(session_id)
            return {'success': True}
        return await self._run_oauth(cancel)

    @tool
    async def oauth_import(self, provider: str = 'codex') -> dict:
        """Import native CLI credentials on explicit request."""
        async def import_credentials():
            return await asyncio.to_thread(self._oauth_import_sync, provider)
        return await self._run_oauth(import_credentials)

    @tool
    async def oauth_login(self, provider: str = 'codex') -> dict:
        """Compatibility wrapper for a browser on the backend host."""
        async def login():
            started = await self._start_oauth(provider)
            try:
                try:
                    await asyncio.to_thread(webbrowser.open, started['auth_url'])
                except Exception:
                    logger.warning('Could not open the OAuth browser')
                result = await self._wait_oauth(started['session_id'], provider, 300)
                if result.get('success'):
                    label, prefix = ('Codex', 'codex/') if provider == 'codex' else ('Gemini', 'gemini-cli/')
                    result = {**result, 'message': f'{label} OAuth login successful. You can now use {prefix} models.'}
                return result
            finally:
                await self._close_oauth_session(started['session_id'])
        return await self._run_oauth(login)

    def _oauth_status_sync(self) -> dict:
        """Get OAuth authentication status for all supported providers.

        Returns:
            Dict with provider statuses including authentication state and account info.
        """
        from pantheon.utils.oauth import CodexOAuthManager, GeminiCliOAuthManager
        from pantheon.utils.oauth.codex import CODEX_CLI_AUTH
        from pantheon.utils.oauth.gemini import GEMINI_CLI_AUTH

        codex = CodexOAuthManager()
        # Actually verify the token works (auto_refresh=True will try to refresh if expired)
        access_token = codex.get_access_token(auto_refresh=True)
        codex_authenticated = access_token is not None
        codex_account_id = codex.get_account_id() if codex_authenticated else None
        cli_available = CODEX_CLI_AUTH.exists()
        gemini = GeminiCliOAuthManager()
        gemini_access = gemini.get_access_token(refresh_if_needed=True)
        gemini_authenticated = gemini_access is not None
        gemini_cli_available = GEMINI_CLI_AUTH.exists()
        gemini_project_id = gemini.get_project_id()
        gemini_runtime_ready = gemini_authenticated and bool(gemini_project_id)
        gemini_runtime_error = None
        if gemini_authenticated and not gemini_runtime_ready:
            gemini_runtime_error = (
                "Gemini CLI OAuth is signed in, but no Code Assist project is available yet. "
                "Pantheon will try to resolve one automatically at runtime; if it still fails, "
                "set GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_PROJECT_ID."
            )

        return {
            "providers": {
                "codex": {
                    "authenticated": codex_authenticated,
                    "account_id": codex_account_id,
                    "description": "OpenAI Codex (ChatGPT backend-api, free with ChatGPT Plus)",
                    "supports_browser_login": True,
                    "supports_import": cli_available,
                },
                "gemini": {
                    "authenticated": gemini_authenticated,
                    "email": gemini.get_email() if gemini_authenticated else None,
                    "project_id": gemini_project_id if gemini_authenticated else None,
                    "runtime_ready": gemini_runtime_ready,
                    "runtime_error": gemini_runtime_error,
                    "description": "Gemini CLI OAuth (Gemini CLI auth with Code Assist project resolution)",
                    "supports_browser_login": True,
                    "supports_import": gemini_cli_available,
                },
            },
        }


    def _oauth_import_sync(self, provider: str = "codex") -> dict:
        """Import OAuth tokens from native CLI tools.

        For Codex: imports from ~/.codex/auth.json (Codex CLI).
        For Gemini: imports from ~/.gemini/oauth_creds.json (Gemini CLI).

        Args:
            provider: OAuth provider name ('codex' or 'gemini')

        Returns:
            Dict with success status.
        """
        try:
            if provider == "codex":
                from pantheon.utils.oauth import CodexOAuthManager

                mgr = CodexOAuthManager()
                result = mgr.import_from_codex_cli()
                success_payload = {
                    "success": True,
                    "provider": "codex",
                    "account_id": mgr.get_account_id(),
                    "message": "Imported Codex CLI tokens successfully.",
                }
                error_payload = {
                    "success": False,
                    "error": "No Codex CLI auth found (~/.codex/auth.json). Install Codex CLI or use browser login.",
                }
            elif provider == "gemini":
                from pantheon.utils.oauth import GeminiCliOAuthManager

                mgr = GeminiCliOAuthManager()
                result = mgr.import_from_gemini_cli()
                success_payload = {
                    "success": True,
                    "provider": "gemini",
                    "email": mgr.get_email(),
                    "project_id": mgr.get_project_id(),
                    "runtime_ready": bool(mgr.get_project_id()),
                    "message": "Imported Gemini CLI auth successfully.",
                }
                error_payload = {
                    "success": False,
                    "error": "No Gemini CLI auth found (~/.gemini/oauth_creds.json). Install Gemini CLI or use browser login.",
                }
            else:
                return {"success": False, "error": f"Unsupported OAuth provider: {provider}"}

            if result:
                from pantheon.utils.model_selector import reset_model_selector
                reset_model_selector()
                return success_payload
            else:
                return error_payload
        except Exception as e:
            logger.error(f"OAuth import failed: {e}")
            return {"success": False, "error": str(e)}
