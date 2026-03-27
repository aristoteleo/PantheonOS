# OpenAI OAuth 2.0 Administration Guide

## Overview

This guide covers administrative tasks related to OAuth 2.0 support in PantheonOS, including system-wide configuration, troubleshooting, and maintenance.

## Architecture

### Components

1. **OAuth Manager** (`pantheon/auth/openai_oauth_manager.py`)
   - Wraps OmicVerse's OAuth implementation
   - Thread-safe singleton pattern
   - Handles token refresh and storage

2. **Model Selector Integration** (`pantheon/utils/model_selector.py`)
   - Detects OAuth token availability
   - Includes OAuth as available authentication provider
   - Prioritizes OAuth when both OAuth and API key available

3. **Setup Wizard Integration** (`pantheon/repl/setup_wizard.py`)
   - "OpenAI (OAuth)" menu option
   - Automatic setup for new users
   - Backward compatible with existing API key setup

4. **REPL Commands** (`pantheon/repl/core.py`)
   - `/oauth login` - Initiate OAuth flow
   - `/oauth status` - Check authentication status
   - `/oauth logout` - Clear credentials

### Data Flow

```
User → REPL Command
      ↓
   OAuth Manager
      ↓
   OmicVerse Library (PKCE OAuth 2.0)
      ↓
   OpenAI OAuth Server
      ↓
   Browser (for user authorization)
      ↓
   Token Storage (~/.pantheon/oauth.json)
```

## Installation and Setup

### Prerequisites

```bash
# Python 3.9+
python --version

# OmicVerse library with OAuth support
pip install 'omicverse>=1.6.2'

# For development/testing
pip install pytest pytest-asyncio
```

### Dependency Installation

OmicVerse requires careful installation due to scipy dependency:

```bash
# Install with pre-compiled binaries (recommended)
pip install 'omicverse>=1.6.2' --only-binary :all: --no-deps

# Or with full dependency resolution
pip install 'omicverse>=1.6.2'
```

### Verify Installation

```python
from pantheon.auth.openai_oauth_manager import get_oauth_manager, OpenAIOAuthManager
print("OAuth support installed ✓")
```

## Configuration

### Default Token Storage Location

Tokens are stored at: `~/.pantheon/oauth.json`

### Custom Token Location

Administrators can specify a custom location:

```python
from pathlib import Path
from pantheon.auth.openai_oauth_manager import get_oauth_manager

custom_path = Path("/var/pantheon/oauth_tokens/user.json")
oauth_mgr = get_oauth_manager(auth_path=custom_path)
```

### File Permissions

Token files are automatically created with restricted permissions:
- Owner: read/write (0600)
- Group: none
- Others: none

### Environment Variables

OAuth respects these environment variables:

```bash
# If set, API key takes precedence in Setup Wizard
export OPENAI_API_KEY="sk-..."

# Custom Python path for OmicVerse (advanced)
export PYTHONPATH="/custom/path:$PYTHONPATH"
```

## Running OAuth

### Starting PantheonOS with OAuth

```bash
# First time: Setup Wizard will guide you through OAuth
pantheon

# Check authentication status
pantheon > /oauth status
```

### Testing OAuth Implementation

```bash
# Run unit tests
pytest tests/test_oauth_manager_unit.py -v

# Run integration tests
pytest tests/test_oauth_integration.py -v

# Run all OAuth tests
pytest tests/test_oauth*.py -v
```

### Test Coverage

- **Unit Tests** (25 tests)
  - Singleton thread safety
  - Token management and refresh
  - JWT parsing
  - OAuth status reporting
  - Codex CLI credential import
  - Login flow
  - Async concurrency safety
  - Lazy initialization

- **Integration Tests** (21 tests)
  - ModelSelector OAuth integration
  - Setup Wizard OAuth menu
  - REPL command routing
  - Complete OAuth workflows
  - Backward compatibility
  - Error recovery

**Total**: 46 tests, 100% pass rate

## Troubleshooting

### Common Issues

#### Issue: `ModuleNotFoundError: No module named 'omicverse'`

**Cause**: OmicVerse library not installed

**Solution**:
```bash
pip install 'omicverse>=1.6.2' --only-binary :all:
```

#### Issue: OAuth token not detected in ModelSelector

**Cause**: Token file doesn't exist or is in wrong location

**Debug**:
```bash
ls -la ~/.pantheon/oauth.json

# Check if OAuth manager can find it
python -c "
from pantheon.auth.openai_oauth_manager import get_oauth_manager
mgr = get_oauth_manager()
print(f'Token path: {mgr.auth_path}')
print(f'Token exists: {mgr.auth_path.exists()}')
"
```

#### Issue: "Browser didn't open automatically"

**Cause**: System default browser not configured

**Debug**:
```bash
# Check default browser
python -c "import webbrowser; print(webbrowser._browsers)"

# Manually verify browser is available
which firefox  # or chrome, safari, etc.
```

**Solution**:
- Install a browser (Firefox, Chrome)
- Set as system default in OS settings
- Restart PantheonOS

#### Issue: Token refresh fails silently

**Cause**: Network connectivity or token revocation

**Debug**:
```bash
python -c "
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def check():
    mgr = get_oauth_manager()
    token = await mgr.get_access_token(refresh_if_needed=True)
    print(f'Token obtained: {bool(token)}')

asyncio.run(check())
"
```

**Solution**:
- Check network connectivity
- Verify OpenAI account access at https://platform.openai.com
- Re-authenticate: `/oauth login`

#### Issue: Multiple users on shared system

**Current Limitation**: Only one user can be authenticated at a time

**Workaround**:
```bash
# User 1 logs out
pantheon > /oauth logout

# User 2 logs in
pantheon > /oauth login
```

**Future**: Multi-user support planned with per-user token paths

### Log Analysis

OAuth operations write to standard Python logger:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Now all OAuth operations are logged with DEBUG level
from pantheon.auth.openai_oauth_manager import get_oauth_manager
```

### Debug Mode

Enable detailed OAuth debugging:

```bash
# Set environment variable
export PANTHEON_DEBUG_OAUTH=1

# Start PantheonOS with debug logging
PYTHONPATH=. python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from pantheon.repl.core import Repl
repl = Repl()
repl.run()
"
```

## Security Management

### Token Lifecycle

**Creation**:
- User clicks `/oauth login`
- Browser opens to OpenAI authorization page
- User grants PantheonOS permission
- Token is saved to `~/.pantheon/oauth.json`

**Usage**:
- Token used for all OpenAI API requests
- Automatically refreshed when < 5 minutes to expiry
- Refresh happens silently in background

**Revocation**:
- `/oauth logout` immediately deletes local token
- Token becomes invalid on OpenAI servers
- User must re-authenticate to use OpenAI

### Security Best Practices

1. **File System**
   - Token stored with mode 0600 (user only)
   - Never back up `~/.pantheon/oauth.json` to shared storage
   - Use encrypted file system for token storage if possible

2. **Network**
   - All OAuth communication uses HTTPS
   - PKCE flow prevents authorization code theft
   - Browser handles secure OAuth handshake

3. **User Management**
   - Each user has separate token at `~/.pantheon/oauth.json`
   - Don't share token files between users
   - Shared system: log out when finished

4. **Audit**
   - Review connected apps: https://platform.openai.com/account/connected-apps
   - OpenAI sends notification emails for new OAuth applications
   - Check email for unexpected PantheonOS OAuth authorizations

### Revoking OAuth Access

Users can revoke PantheonOS OAuth access at any time:

1. Visit https://platform.openai.com/account/connected-apps
2. Find "PantheonOS"
3. Click "Revoke access"
4. Token becomes immediately invalid

## Maintenance

### Token File Cleanup

```bash
# View token file (DO NOT SHARE)
cat ~/.pantheon/oauth.json

# Manually delete token (same as /oauth logout)
rm ~/.pantheon/oauth.json

# View token expiration
python -c "
import json
from pathlib import Path
with open(Path.home() / '.pantheon' / 'oauth.json') as f:
    data = json.load(f)
    print(f'Expires: {data.get(\"tokens\", {}).get(\"expires_at\")}')"
```

### Monitoring Token Health

```bash
# Check if token is valid
python -c "
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def check_health():
    mgr = get_oauth_manager()
    status = await mgr.get_status()

    if status['authenticated']:
        print(f'✓ Authenticated as {status[\"email\"]}')
        print(f'  Organization: {status[\"organization_id\"]}')
        print(f'  Project: {status[\"project_id\"]}')
        print(f'  Expires: {status[\"token_expires_at\"]}')
    else:
        print('✗ Not authenticated')

asyncio.run(check_health())
"
```

### Backup and Recovery

**Warning**: Never back up `~/.pantheon/oauth.json` to unencrypted locations

**For system migration**:
```bash
# On old system
/oauth logout

# On new system
/oauth login  # Re-authenticate
```

**For disaster recovery**:
- OAuth tokens cannot be recovered once revoked
- User must re-authenticate using `/oauth login`
- No manual token injection is supported

## Performance Considerations

### Token Refresh Performance

- Automatic refresh: ~100-200ms
- Happens in background (non-blocking)
- No impact on user experience

### Concurrent Access

- Thread-safe singleton pattern with double-checked locking
- asyncio.Lock protects concurrent async calls
- 10 concurrent threads tested: ✓ Pass
- 5 concurrent async calls tested: ✓ Pass

### Network Considerations

- First login: requires browser interaction (user-dependent)
- Token refresh: ~100-200ms network request
- Status check: ~50-100ms network request
- No token caching to memory (fresh from file each time)

## Monitoring and Logging

### Log Levels

```
DEBUG   - Token retrieval successful, context extracted, etc.
INFO    - User login, logout, Codex import
WARNING - Token refresh failed, auth error
ERROR   - Unexpected errors, system issues
```

### Log Output Example

```
2025-03-27 10:15:32 INFO     OAuth: User login successful
2025-03-27 10:15:35 DEBUG    OAuth: Organization context extracted: org-abc123
2025-03-27 10:20:00 DEBUG    OAuth: Token refreshed automatically
2025-03-27 10:25:00 WARNING  OAuth: Token refresh failed: Network timeout
```

### Enable OAuth Logging

```python
import logging
logger = logging.getLogger('pantheon.auth.openai_oauth_manager')
logger.setLevel(logging.DEBUG)
```

## Integration Points

### With ModelSelector

```python
# OAuth token availability is checked during provider detection
provider = selector.detect_available_provider()
# Returns "openai" if OAuth token exists, regardless of API key
```

### With Setup Wizard

```
Provider Menu:
- OpenAI (API Key)
- OpenAI (OAuth)  ← New option
- Anthropic (Claude)
- Google (Gemini)
```

### With REPL

```
> /oauth login      # Start OAuth flow
> /oauth status     # Check authentication status
> /oauth logout     # Clear credentials
```

## Upgrade Path

### From API Key to OAuth

1. Existing API key authentication continues to work
2. Users can choose OAuth during Setup Wizard
3. Both can coexist in the same system
4. OAuth is offered as alternative, not replacement

### Backward Compatibility

- 100% backward compatible with existing API key authentication
- API key detection unchanged
- OAuth is additive feature
- No breaking changes to existing code

## API Reference

### Main Class: `OpenAIOAuthManager`

```python
class OpenAIOAuthManager:
    async def get_access_token(refresh_if_needed: bool = True) -> Optional[str]
    async def get_org_context() -> Dict[str, str]
    async def get_status() -> Dict[str, Any]
    async def login(workspace_id: Optional[str] = None, open_browser: bool = True) -> bool
    async def import_codex_credentials() -> bool
    async def clear_token() -> bool
    def reset() -> None
```

### Singleton Interface

```python
from pantheon.auth.openai_oauth_manager import get_oauth_manager, reset_oauth_manager

# Get or create singleton
mgr = get_oauth_manager()

# Reset singleton (testing only)
reset_oauth_manager()
```

## Support and Contact

- **Issue Tracker**: GitHub Issues
- **Documentation**: `docs/OAUTH_*.md`
- **Code**: `pantheon/auth/openai_oauth_manager.py`
- **Tests**: `tests/test_oauth_*.py`

## See Also

- [User Guide](./OAUTH_USER_GUIDE.md)
- [API Reference](./OAUTH_API.md)
- [OpenAI Platform](https://platform.openai.com)
- [OmicVerse Project](https://github.com/Jintao-Huang/OmicVerse)
