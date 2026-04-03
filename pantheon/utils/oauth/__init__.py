"""
OAuth support for LLM providers.

Currently supports:
- Codex (OpenAI ChatGPT backend-api) via browser-based OAuth 2.0 + PKCE
"""

from .codex import CodexOAuthManager, CodexOAuthError

__all__ = ["CodexOAuthManager", "CodexOAuthError"]
