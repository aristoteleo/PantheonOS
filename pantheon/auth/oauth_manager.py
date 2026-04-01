"""
OAuth Types and Protocols for PantheonOS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Optional


@dataclass
class OAuthTokens:
    """Generic OAuth tokens."""
    id_token: str
    access_token: str
    refresh_token: str
    account_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> OAuthTokens:
        return cls(
            id_token=data["id_token"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            account_id=data.get("account_id"),
            organization_id=data.get("organization_id"),
            project_id=data.get("project_id"),
        )

    def to_dict(self) -> dict:
        return {
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "account_id": self.account_id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
        }


@dataclass
class OAuthStatus:
    """Generic OAuth status."""
    authenticated: bool
    email: str = ""
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    token_expires_at: Optional[float] = None
    provider: str = ""


@dataclass
class AuthRecord:
    """Generic OAuth auth record."""
    provider: str
    tokens: OAuthTokens
    last_refresh: str
    email: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> AuthRecord:
        return cls(
            provider=data.get("provider", "unknown"),
            tokens=OAuthTokens.from_dict(data["tokens"]),
            last_refresh=data.get("last_refresh", ""),
            email=data.get("email"),
        )

    def to_dict(self) -> dict:
        result = {
            "provider": self.provider,
            "tokens": self.tokens.to_dict(),
            "last_refresh": self.last_refresh,
        }
        if self.email:
            result["email"] = self.email
        return result


class OAuthProvider(Protocol):
    """Protocol for OAuth providers."""

    @property
    def name(self) -> str:
        """Provider name."""
        ...

    @property
    def display_name(self) -> str:
        """Display name for UI."""
        ...

    def login(
        self,
        *,
        open_browser: bool = True,
        timeout_seconds: int = 300,
    ) -> bool:
        """Initiate OAuth login flow."""
        ...

    def get_status(self) -> OAuthStatus:
        """Get current OAuth status."""
        ...

    def logout(self) -> None:
        """Clear OAuth credentials."""
        ...

    def ensure_access_token(self, refresh_if_needed: bool = True) -> Optional[str]:
        """Get a valid access token."""
        ...


class OAuthManager:
    """Manages multiple OAuth providers."""

    def __init__(self):
        self._providers: dict[str, OAuthProvider] = {}
        self._default_provider: str = "openai"

    def register(self, provider: OAuthProvider) -> None:
        """Register an OAuth provider."""
        self._providers[provider.name] = provider

    def set_default(self, provider_name: str) -> None:
        """Set the default provider."""
        if provider_name not in self._providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        self._default_provider = provider_name

    @property
    def default_provider(self) -> str:
        """Get the default provider name."""
        return self._default_provider

    def list_providers(self) -> list[str]:
        """List all registered provider names."""
        return list(self._providers.keys())

    def get_provider(self, name: Optional[str] = None) -> OAuthProvider:
        """Get a provider by name, or the default provider."""
        provider_name = name or self._default_provider
        if provider_name not in self._providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        return self._providers[provider_name]

    def login(
        self,
        provider: Optional[str] = None,
        *,
        open_browser: bool = True,
        timeout_seconds: int = 300,
    ) -> bool:
        """Login with a specific provider."""
        p = self.get_provider(provider)
        return p.login(open_browser=open_browser, timeout_seconds=timeout_seconds)

    def get_status(self, provider: Optional[str] = None) -> OAuthStatus:
        """Get status from a specific provider."""
        p = self.get_provider(provider)
        status = p.get_status()
        status.provider = p.name
        return status

    def logout(self, provider: Optional[str] = None) -> None:
        """Logout from a specific provider."""
        p = self.get_provider(provider)
        p.logout()

    def ensure_access_token(
        self,
        provider: Optional[str] = None,
        refresh_if_needed: bool = True,
    ) -> Optional[str]:
        """Get a valid access token from a specific provider."""
        p = self.get_provider(provider)
        return p.ensure_access_token(refresh_if_needed=refresh_if_needed)


_oauth_manager: Optional[OAuthManager] = None


def get_oauth_manager() -> OAuthManager:
    """Get the OAuth manager singleton."""
    global _oauth_manager
    if _oauth_manager is None:
        _oauth_manager = OAuthManager()
        from pantheon.auth.openai_provider import OpenAIOAuthProvider
        _oauth_manager.register(OpenAIOAuthProvider())
    return _oauth_manager


def reset_oauth_manager() -> None:
    """Reset the OAuth manager singleton."""
    global _oauth_manager
    _oauth_manager = None


def get_oauth_token(provider: str = "openai", refresh_if_needed: bool = True) -> Optional[str]:
    """Get a valid OAuth access token for the specified provider.
    
    This is a convenience function for other modules to get OAuth tokens.
    
    Args:
        provider: The OAuth provider name (default: "openai")
        refresh_if_needed: Whether to refresh the token if expired
        
    Returns:
        The access token string, or None if not available
    """
    try:
        manager = get_oauth_manager()
        return manager.ensure_access_token(provider, refresh_if_needed)
    except Exception:
        return None


def is_oauth_available(provider: str = "openai") -> bool:
    """Check if OAuth is available for the specified provider.
    
    Args:
        provider: The OAuth provider name (default: "openai")
        
    Returns:
        True if OAuth tokens are available, False otherwise
    """
    try:
        from pathlib import Path
        
        manager = get_oauth_manager()
        oauth_provider = manager.get_provider(provider)
        
        # Check if auth file exists
        if hasattr(oauth_provider, 'auth_path') and oauth_provider.auth_path.exists():
            return True
        return False
    except Exception:
        return False