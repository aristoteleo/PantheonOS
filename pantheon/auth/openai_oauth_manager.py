"""
OpenAI OAuth 2.0 Authentication Manager for Pantheon.

This module provides OAuth support for OpenAI Codex and other OpenAI services.
It wraps OmicVerse's OpenAIOAuthManager to provide a Pantheon-specific interface.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from pantheon.utils.log import logger

try:
    from omicverse.jarvis.openai_oauth import (
        OpenAIOAuthManager,
        OpenAIOAuthError,
        jwt_org_context,
        token_expired,
    )
except ImportError:
    raise ImportError(
        "OmicVerse is required for OAuth support. "
        "Install it with: pip install 'omicverse>=1.6.2'"
    )


class OpenAIOAuthManager:
    """
    Pantheon's wrapper for OpenAI OAuth 2.0 authentication.

    This class provides OAuth support for OpenAI services including Codex,
    with automatic token refresh and Codex CLI credential import.

    Features:
    - PKCE-based OAuth 2.0 flow (RFC 7636)
    - Automatic token refresh
    - Codex CLI credential import
    - Organization and project context support
    - Thread-safe token management
    """

    def __init__(self, auth_path: Optional[Path] = None) -> None:
        """
        Initialize OpenAI OAuth Manager.

        Args:
            auth_path: Path to store OAuth tokens. Defaults to ~/.pantheon/oauth.json
        """
        self.auth_path = auth_path or Path.home() / ".pantheon" / "oauth.json"
        self._omicverse_manager = None
        self._lock = asyncio.Lock()

    def _get_manager(self) -> Any:
        """Lazily initialize OmicVerse manager."""
        if self._omicverse_manager is None:
            from omicverse.jarvis.openai_oauth import OpenAIOAuthManager as OmicverseOpenAIOAuthManager
            self._omicverse_manager = OmicverseOpenAIOAuthManager(self.auth_path)
        return self._omicverse_manager

    async def get_access_token(self, refresh_if_needed: bool = True) -> Optional[str]:
        """
        Get a valid OpenAI access token.

        This method will:
        1. Try to use existing token if valid
        2. Refresh if expired and refresh_token available
        3. Import from Codex CLI if available
        4. Return None if no token available

        Args:
            refresh_if_needed: Whether to refresh expired tokens automatically

        Returns:
            Valid access token string, or None if not available
        """
        async with self._lock:
            try:
                manager = self._get_manager()
                token = manager.ensure_access_token_with_codex_fallback(
                    refresh_if_needed=refresh_if_needed,
                    import_codex_if_missing=True
                )
                if token:
                    logger.debug("OpenAI OAuth token retrieved successfully")
                return token
            except OpenAIOAuthError as e:
                logger.warning(f"OpenAI OAuth token retrieval failed: {e}")
                return None
            except Exception as e:
                logger.error(f"Unexpected error in OAuth token retrieval: {e}")
                return None

    async def get_org_context(self) -> Dict[str, str]:
        """
        Get user's organization context from JWT claims.

        Returns:
            Dictionary with keys:
            - organization_id: User's OpenAI organization ID
            - project_id: User's OpenAI project ID
            - chatgpt_account_id: User's ChatGPT account ID (if available)
        """
        async with self._lock:
            try:
                manager = self._get_manager()
                auth = manager.load()
                id_token = auth.get("tokens", {}).get("id_token", "")

                if not id_token:
                    logger.debug("No id_token available, cannot extract org context")
                    return {}

                context = jwt_org_context(id_token)
                logger.debug(f"Organization context: {context}")
                return context
            except Exception as e:
                logger.warning(f"Failed to extract org context: {e}")
                return {}

    async def login(self, workspace_id: Optional[str] = None, open_browser: bool = True) -> bool:
        """
        Initiate OpenAI OAuth login flow.

        This opens a browser window for user to authorize and returns automatically
        when authorization is complete.

        Args:
            workspace_id: Optional OpenAI workspace ID to restrict login to
            open_browser: Whether to automatically open browser (default: True)

        Returns:
            True if login successful, False otherwise
        """
        async with self._lock:
            try:
                manager = self._get_manager()
                # Run sync login() in thread pool to avoid blocking event loop
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: manager.login(workspace_id=workspace_id, open_browser=open_browser)
                )
                logger.info("OpenAI OAuth login successful")
                return True
            except OpenAIOAuthError as e:
                logger.error(f"OpenAI OAuth login failed: {e}")
                return False
            except Exception as e:
                logger.error(f"Unexpected error during OAuth login: {e}")
                return False

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current OAuth status and user information.

        Returns:
            Dictionary with keys:
            - authenticated: Boolean indicating if token is valid
            - email: User's email (if available)
            - organization_id: User's OpenAI organization ID
            - project_id: User's OpenAI project ID
            - token_expires_at: ISO format timestamp when token expires
        """
        try:
            token = await self.get_access_token(refresh_if_needed=False)
            org_context = await self.get_org_context()

            manager = self._get_manager()
            auth = manager.load()
            tokens = auth.get("tokens", {})
            expires_at = tokens.get("expires_at")

            return {
                "authenticated": bool(token),
                "email": tokens.get("email", ""),
                "organization_id": org_context.get("organization_id", ""),
                "project_id": org_context.get("project_id", ""),
                "token_expires_at": expires_at,
            }
        except Exception as e:
            logger.error(f"Failed to get OAuth status: {e}")
            return {"authenticated": False}

    async def import_codex_credentials(self) -> bool:
        """
        Try to import credentials from Codex CLI.

        If user has already authenticated with Codex CLI, this will import
        those credentials and optionally refresh them.

        Returns:
            True if import successful, False otherwise
        """
        try:
            manager = self._get_manager()
            result = manager.import_codex_auth()
            if result:
                logger.info("Successfully imported Codex CLI credentials")
                return True
            else:
                logger.debug("No Codex CLI credentials found to import")
                return False
        except Exception as e:
            logger.debug(f"Failed to import Codex credentials: {e}")
            return False

    async def clear_token(self) -> bool:
        """
        Clear stored OAuth token (logout).

        Returns:
            True if cleared successfully (or no token to clear)
        """
        try:
            if self.auth_path.exists():
                self.auth_path.unlink()
                self._omicverse_manager = None  # Reset cached manager instance
                logger.info("OpenAI OAuth token cleared and manager reset")
            else:
                logger.debug("No OAuth token file to clear")
            return True
        except Exception as e:
            logger.error(f"Failed to clear OAuth token: {e}")
            return False

    def reset(self) -> None:
        """Reset the manager instance (clears cached OmicVerse manager).

        Useful for cleanup after logout or credential refresh.
        """
        self._omicverse_manager = None
        logger.debug("OpenAI OAuth manager instance reset")


# Singleton instance for use across Pantheon
_oauth_manager: Optional[OpenAIOAuthManager] = None
_oauth_manager_lock = threading.Lock()


def get_oauth_manager(auth_path: Optional[Path] = None) -> OpenAIOAuthManager:
    """Get or create the OpenAI OAuth manager singleton.

    Uses double-checked locking pattern to ensure thread-safe singleton creation.
    """
    global _oauth_manager
    if _oauth_manager is None:
        with _oauth_manager_lock:
            if _oauth_manager is None:  # Double-check pattern
                _oauth_manager = OpenAIOAuthManager(auth_path)
    return _oauth_manager


def reset_oauth_manager() -> None:
    """Reset the OAuth manager singleton (for testing).

    This clears the cached singleton instance, allowing a fresh instance
    to be created on the next call to get_oauth_manager().
    """
    global _oauth_manager
    _oauth_manager = None
    logger.debug("OAuth manager singleton reset")
