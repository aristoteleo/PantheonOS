"""
OAuth support for LLM providers.

Currently supports:
- Codex (OpenAI ChatGPT backend-api) via browser-based OAuth 2.0 + PKCE
- Gemini CLI OAuth via Google OAuth 2.0 / imported ~/.gemini credentials
"""

from .codex import CodexOAuthManager, CodexOAuthError
from .gemini import GeminiCliOAuthManager, GeminiCliOAuthError

__all__ = [
    "CodexOAuthManager",
    "CodexOAuthError",
    "GeminiCliOAuthManager",
    "GeminiCliOAuthError",
]
