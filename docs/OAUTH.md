# OAuth Guide

## Overview

- Pantheon has a generic OAuth provider registry
- OAuth credentials are stored per provider
- Providers can expose provider-specific behavior on top of the shared flow

Today, Pantheon only ships one concrete OAuth provider: `openai`.

## Why OAuth?

- Browser-based account authentication
- Automatic token refresh
- Codex CLI credential import
- Account status inspection in Pantheon

## Current Providers

Use `/oauth list` to see registered providers.

At the moment, the built-in provider set is:

- `openai`

Future providers can be added through the same `OAuthManager` registry without changing the `/oauth` command surface.

## Important Limitation For OpenAI

Pantheon's OAuth support manages OpenAI account credentials only. It does not treat the
resulting OAuth access token as a substitute for `OPENAI_API_KEY` when calling the OpenAI API.

The current exception is Pantheon's dedicated Codex transport: models whose name contains
`codex` can be routed through the ChatGPT/Codex backend using OAuth credentials when available.

You can trigger this path explicitly with a Codex-prefixed model name such as:

```bash
/model codex/gpt-5.4
```

To call OpenAI models through the standard OpenAI API path, you still need one of:

- `OPENAI_API_KEY`
- `LLM_API_KEY` with a compatible base URL
- `CUSTOM_OPENAI_API_BASE` plus `CUSTOM_OPENAI_API_KEY`

## Integration Risk

Pantheon's OpenAI OAuth integration reuses the Codex CLI OAuth client identity.
This is not a public third-party OAuth app registration flow.

Implications:

- OpenAI can revoke, restrict, or change this integration path at any time
- A working setup today may break without a Pantheon code change
- This path should be treated as best-effort, not as a long-term stable contract

For maintainers:

- Do not assume the current client ID / originator values are durable
- Prefer isolating Codex-specific OAuth behavior from standard OpenAI API auth
- Be prepared to disable or replace this path if OpenAI changes upstream behavior

## Quick Start

```bash
pantheon
/oauth list
/oauth login openai
# Browser opens - log in and authorize
```

## REPL Commands

| Command | Description |
|---------|-------------|
| `/oauth list` | List registered OAuth providers |
| `/oauth login [provider]` | Start provider login flow |
| `/oauth status [provider]` | Check authentication status |
| `/oauth logout [provider]` | Clear provider credentials |
| `/oauth explain [model]` | Explain which auth method a model will use |
| `/oauth prefs [provider]` | Show provider auth preferences |

If no provider is supplied, Pantheon uses `auth.default_oauth_provider`.

## API Reference

### `get_oauth_manager() -> OAuthManager`

Get the singleton provider registry for OAuth-capable providers.

### Generic Concepts

- `OAuthManager`: provider registry, default-provider selection, shared command entry point
- `OAuthProvider`: protocol for `login()`, `get_status()`, `logout()`, `ensure_access_token()`
- `OAuthStatus`: normalized status payload used by the REPL and setup wizard

### `OpenAIOAuthProvider`

| Method | Returns | Description |
|--------|---------|-------------|
| `login()` | `bool` | Start OAuth flow |
| `ensure_access_token()` | `str\|None` | Get a valid access token |
| `ensure_access_token_with_codex_fallback()` | `str\|None` | Get token, importing Codex CLI auth if needed |
| `build_codex_auth_context()` | `dict\|None` | Build ChatGPT/Codex backend auth context |
| `get_status()` | `OAuthStatus` | Current auth status |
| `logout()` | `None` | Revoke tokens and remove local auth file |

### Example

```python
from pantheon.auth.oauth_manager import get_oauth_manager

mgr = get_oauth_manager()
provider = mgr.get_provider("openai")
token = provider.ensure_access_token()
if token:
    status = provider.get_status()
    print(f"Logged in as: {status.email}")
```

## Configuration

Pantheon now uses a provider-aware auth layout:

```json
{
  "auth": {
    "default_oauth_provider": "openai",
    "providers": {
      "openai": {
        "mode": "auto",
        "enable_api_key": true,
        "enable_oauth": true
      }
    }
  }
}
```

Backward compatibility:

- Existing `auth.openai` settings are still read
- New writes should target `auth.providers.openai`

```python
# Custom token location
from pathlib import Path
from pantheon.auth.openai_provider import OpenAIOAuthProvider

provider = OpenAIOAuthProvider(auth_path=Path("/custom/path.json"))
```

```bash
# OpenAI API model calls still require an API key
export OPENAI_API_KEY="sk-..."
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `No module named 'requests'` | `pip install requests` |
| `No module named 'jwt'` | `pip install pyjwt` |
| `No module named 'cryptography'` | `pip install cryptography` |
| Browser didn't open | Set default browser in OS settings |
| Token expired | Run `/oauth login` to re-authenticate |
| Can't import Codex | Use browser login instead |

## Security

- Provider auth files use the pattern `~/.pantheon/oauth_<provider>.json`
- OpenAI tokens are stored at `~/.pantheon/oauth_openai.json`
- Tokens auto-refresh when ~5 min from expiry
- JWT claims used for email / org / project context are signature-verified before use
- OAuth callback requests are checked against `Origin` / `Referer` when headers are present
- Use `/oauth logout` on shared systems

## See Also

- [OpenAI OAuth Docs](https://platform.openai.com/docs/guides/oauth)
- [PKCE RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)
