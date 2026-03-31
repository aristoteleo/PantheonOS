# OpenAI OAuth 2.0 Guide

## Why OAuth?

- No API key stored locally
- Browser-based authentication
- Automatic token refresh
- Codex CLI credential import

## Quick Start

```bash
pantheon
# Select "OpenAI (OAuth)" from menu
# Browser opens - log in and authorize
# Done!
```

## REPL Commands

| Command | Description |
|---------|-------------|
| `/oauth status` | Check authentication |
| `/oauth login` | Initiate login |
| `/oauth logout` | Clear credentials |

## Installation

No additional dependencies required! The OAuth implementation is built into PantheonOS using standard libraries and minimal dependencies (requests, pyjwt, cryptography).

```bash
# These dependencies are already included in PantheonOS
pip install requests pyjwt cryptography
```

## API Reference

### `get_oauth_manager(auth_path?: Path) -> OpenAIOAuthManager`

Get singleton OAuth manager.

### `OpenAIOAuthManager` Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `get_access_token()` | `str\|None` | Get valid token |
| `get_org_context()` | `dict` | Org/project from JWT |
| `login()` | `bool` | Start OAuth flow |
| `get_status()` | `dict` | Auth status info |
| `import_codex_credentials()` | `bool` | Import Codex creds |
| `clear_token()` | `bool` | Logout |

### Example

```python
from pantheon.auth.openai_oauth_manager import get_oauth_manager
import asyncio

async def main():
    mgr = get_oauth_manager()
    token = await mgr.get_access_token()
    if token:
        status = await mgr.get_status()
        print(f"Logged in as: {status['email']}")

asyncio.run(main())
```

## Configuration

```python
# Custom token location
manager = get_oauth_manager(auth_path=Path("/custom/path.json"))
```

```bash
# Environment: API key takes precedence over OAuth
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

- Tokens stored at `~/.pantheon/oauth.json`
- Tokens auto-refresh when ~5 min from expiry
- Use `/oauth logout` on shared systems

## See Also

- [OpenAI OAuth Docs](https://platform.openai.com/docs/guides/oauth)
- [PKCE RFC 7636](https://datatracker.ietf.org/doc/html/rfc7636)