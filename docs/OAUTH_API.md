# OpenAI OAuth 2.0 API Reference

## Module: `pantheon.auth.openai_oauth_manager`

Complete API reference for OpenAI OAuth 2.0 authentication in PantheonOS.

## Classes

### `OpenAIOAuthManager`

Main class for managing OpenAI OAuth 2.0 authentication.

```python
class OpenAIOAuthManager:
    """Pantheon's wrapper for OpenAI OAuth 2.0 authentication."""
```

#### Constructor

```python
def __init__(self, auth_path: Optional[Path] = None) -> None:
    """
    Initialize OpenAI OAuth Manager.

    Args:
        auth_path: Path to store OAuth tokens.
                   Defaults to ~/.pantheon/oauth.json

    Example:
        # Use default location
        manager = OpenAIOAuthManager()

        # Use custom location
        from pathlib import Path
        manager = OpenAIOAuthManager(auth_path=Path("/var/pantheon/oauth.json"))
    """
```

#### Methods

##### `async get_access_token()`

Retrieve a valid OpenAI access token.

```python
async def get_access_token(self, refresh_if_needed: bool = True) -> Optional[str]:
    """
    Get a valid OpenAI access token.

    This method will:
    1. Try to use existing token if valid
    2. Refresh if expired and refresh_token available
    3. Import from Codex CLI if available
    4. Return None if no token available

    Args:
        refresh_if_needed (bool): Whether to refresh expired tokens automatically.
                                  Default: True

    Returns:
        str: Valid access token string
        None: If no token is available

    Raises:
        No exceptions. Returns None on all errors.

    Examples:
        import asyncio
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        async def main():
            manager = get_oauth_manager()
            token = await manager.get_access_token()

            if token:
                print(f"Token obtained: {token[:20]}...")
                # Use token with OpenAI API
            else:
                print("Authentication required")

        asyncio.run(main())

    Notes:
        - Tokens are automatically refreshed when < 5 minutes to expiry
        - Codex CLI credentials are imported if OAuth token missing
        - All errors are caught and None is returned
        - Token refresh happens in background without blocking
    """
```

##### `async get_org_context()`

Get user's organization context from JWT claims.

```python
async def get_org_context(self) -> Dict[str, str]:
    """
    Get user's organization context from JWT claims.

    Returns:
        Dict with keys:
            - organization_id: User's OpenAI organization ID
            - project_id: User's OpenAI project ID
            - chatgpt_account_id: User's ChatGPT account ID (if available)

    Returns empty dict if:
        - No id_token available
        - JWT parsing fails
        - Token not authenticated

    Examples:
        import asyncio
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        async def main():
            manager = get_oauth_manager()
            context = await manager.get_org_context()

            if 'organization_id' in context:
                print(f"Org: {context['organization_id']}")
                print(f"Project: {context['project_id']}")
            else:
                print("Organization context not available")

        asyncio.run(main())

    Notes:
        - Requires valid OAuth token
        - JWT parsing is cached for performance
        - Returns empty dict on any error (no exceptions raised)
        - Information comes from JWT claims, not API calls
    """
```

##### `async login()`

Initiate OpenAI OAuth login flow.

```python
async def login(
    self,
    workspace_id: Optional[str] = None,
    open_browser: bool = True
) -> bool:
    """
    Initiate OpenAI OAuth login flow.

    Opens a browser window for user to authorize and returns automatically
    when authorization is complete.

    Args:
        workspace_id (Optional[str]): Optional OpenAI workspace ID to
                                      restrict login to
        open_browser (bool): Whether to automatically open browser
                            (default: True)

    Returns:
        bool: True if login successful, False if error occurred

    Examples:
        import asyncio
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        async def main():
            manager = get_oauth_manager()

            # Simple login
            if await manager.login():
                print("Successfully authenticated!")
            else:
                print("Authentication failed")

            # Login to specific workspace
            if await manager.login(workspace_id="ws-12345"):
                print("Logged in to workspace ws-12345")

        asyncio.run(main())

    Notes:
        - Browser opens automatically unless open_browser=False
        - Runs in thread pool to avoid blocking event loop
        - User interacts with OpenAI in browser to authorize
        - Returns when authorization complete or error occurs
        - No authorization code returned (handled internally)
        - Tokens automatically saved to auth_path
    """
```

##### `async get_status()`

Get current OAuth status and user information.

```python
async def get_status(self) -> Dict[str, Any]:
    """
    Get current OAuth status and user information.

    Returns:
        Dict with keys:
            - authenticated (bool): Is user authenticated
            - email (str): User's email address
            - organization_id (str): User's OpenAI organization ID
            - project_id (str): User's OpenAI project ID
            - token_expires_at (str): ISO format timestamp when token expires

    Returns:
        {"authenticated": False} if error occurs

    Examples:
        import asyncio
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        async def main():
            manager = get_oauth_manager()
            status = await manager.get_status()

            if status['authenticated']:
                print(f"User: {status['email']}")
                print(f"Org: {status['organization_id']}")
                print(f"Expires: {status['token_expires_at']}")
            else:
                print("Not authenticated. Run: /oauth login")

        asyncio.run(main())

    Notes:
        - Checks token validity without forcing refresh
        - If token is expired but refreshable, status shows new expiry
        - Organization/project info comes from JWT claims
        - Safe to call frequently (minimal network overhead)
    """
```

##### `async import_codex_credentials()`

Try to import credentials from Codex CLI.

```python
async def import_codex_credentials(self) -> bool:
    """
    Try to import credentials from Codex CLI.

    If user has already authenticated with Codex CLI, this will import
    those credentials and optionally refresh them.

    Returns:
        bool: True if import successful, False if:
              - Codex CLI not installed
              - No Codex credentials found
              - Import error occurred

    Examples:
        import asyncio
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        async def main():
            manager = get_oauth_manager()

            if await manager.import_codex_credentials():
                print("Codex CLI credentials imported!")
            else:
                print("No Codex CLI credentials found")
                print("Run: /oauth login")

        asyncio.run(main())

    Notes:
        - Automatically called during get_access_token()
        - Only works if Codex CLI is installed
        - Credentials are refreshed if needed
        - Does not require browser interaction
        - Fallback when OAuth token missing
    """
```

##### `async clear_token()`

Clear stored OAuth token (logout).

```python
async def clear_token(self) -> bool:
    """
    Clear stored OAuth token (logout).

    Deletes the OAuth token file and resets cached manager instance.
    User must re-authenticate to use OpenAI.

    Returns:
        bool: True if cleared successfully or no token to clear
              False if filesystem error occurred

    Examples:
        import asyncio
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        async def main():
            manager = get_oauth_manager()

            if await manager.clear_token():
                print("Logged out successfully")
            else:
                print("Error clearing token")

        asyncio.run(main())

    Notes:
        - Deletes file at auth_path
        - Resets cached OmicVerse manager
        - Returns True even if file doesn't exist
        - Token becomes immediately invalid on OpenAI servers
        - User can re-authenticate using login()
    """
```

##### `reset()`

Reset the manager instance (clears cached OmicVerse manager).

```python
def reset(self) -> None:
    """
    Reset the manager instance.

    Clears the cached OmicVerse manager. Useful for cleanup after
    logout or credential refresh.

    Examples:
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        manager = get_oauth_manager()
        manager.reset()
        # Next call to get_access_token() will reinitialize manager

    Notes:
        - Useful for testing and cleanup
        - Does NOT delete token file (use clear_token() for that)
        - Called automatically after clear_token()
        - Safe to call multiple times
    """
```

#### Properties

##### `auth_path`

Location where OAuth tokens are stored.

```python
auth_path: Path

# Example:
manager = OpenAIOAuthManager()
print(manager.auth_path)  # ~/.pantheon/oauth.json
```

## Functions

### `get_oauth_manager()`

Get or create the OpenAI OAuth manager singleton.

```python
def get_oauth_manager(auth_path: Optional[Path] = None) -> OpenAIOAuthManager:
    """
    Get or create the OpenAI OAuth manager singleton.

    Uses double-checked locking pattern to ensure thread-safe singleton
    creation. Multiple calls return the same instance.

    Args:
        auth_path (Optional[Path]): Custom path for OAuth token storage.
                                    Only used on first call.

    Returns:
        OpenAIOAuthManager: Singleton instance

    Examples:
        from pantheon.auth.openai_oauth_manager import get_oauth_manager

        # Get singleton
        manager1 = get_oauth_manager()
        manager2 = get_oauth_manager()

        assert manager1 is manager2  # Same instance

        # Custom path (only on first call)
        from pathlib import Path
        manager = get_oauth_manager(auth_path=Path("/custom/path.json"))

    Notes:
        - Thread-safe with double-checked locking
        - First call with auth_path sets path for all future calls
        - Subsequent auth_path arguments are ignored
        - Use reset_oauth_manager() to change path (testing only)
    """
```

### `reset_oauth_manager()`

Reset the OAuth manager singleton (for testing).

```python
def reset_oauth_manager() -> None:
    """
    Reset the OAuth manager singleton.

    Clears the cached singleton instance, allowing a fresh instance to be
    created on the next call to get_oauth_manager(). For testing only.

    Examples:
        from pantheon.auth.openai_oauth_manager import get_oauth_manager, reset_oauth_manager

        # Get singleton
        manager1 = get_oauth_manager()

        # Reset singleton
        reset_oauth_manager()

        # Get new singleton
        manager2 = get_oauth_manager()

        assert manager1 is not manager2  # Different instances

    Notes:
        - For testing and development only
        - Never use in production code
        - Clears the global _oauth_manager variable
        - Called automatically between unit tests
    """
```

## Exceptions

### `OpenAIOAuthError`

Raised by OmicVerse when OAuth operations fail.

```python
# Imported from omicverse.jarvis.openai_oauth
from pantheon.auth.openai_oauth_manager import OpenAIOAuthError

try:
    await manager.login()
except OpenAIOAuthError as e:
    print(f"OAuth error: {e}")
```

## Usage Patterns

### Pattern 1: Check Authentication Status

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def check_auth():
    manager = get_oauth_manager()
    status = await manager.get_status()

    if status['authenticated']:
        return True
    else:
        return await manager.login()

success = asyncio.run(check_auth())
```

### Pattern 2: Get Token for API Calls

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager
import openai

async def use_openai():
    manager = get_oauth_manager()
    token = await manager.get_access_token()

    if not token:
        print("Not authenticated")
        return

    # Use token with OpenAI API
    openai.api_key = token
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello!"}]
    )
    print(response.choices[0].text)

asyncio.run(use_openai())
```

### Pattern 3: Setup Fallback Chain

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def get_auth_token():
    """Get token using fallback chain."""
    manager = get_oauth_manager()

    # 1. Try existing token
    token = await manager.get_access_token(refresh_if_needed=True)
    if token:
        return token

    # 2. Try importing Codex credentials
    if await manager.import_codex_credentials():
        token = await manager.get_access_token()
        if token:
            return token

    # 3. Require browser login
    if await manager.login():
        return await manager.get_access_token()

    # 4. All fallbacks failed
    return None
```

### Pattern 4: Monitor Authentication

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def monitor_auth():
    """Continuously monitor authentication status."""
    manager = get_oauth_manager()

    while True:
        status = await manager.get_status()

        if not status['authenticated']:
            print("Not authenticated. Trying login...")
            if not await manager.login():
                print("Login failed. Waiting 60 seconds...")
                await asyncio.sleep(60)
                continue

        expires = status.get('token_expires_at')
        print(f"Authenticated as {status['email']}, expires {expires}")

        # Check again in 5 minutes
        await asyncio.sleep(300)
```

### Pattern 5: Custom Logout on Exit

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def main():
    try:
        manager = get_oauth_manager()

        # Use OAuth for something
        token = await manager.get_access_token()
        # ... do work ...

    finally:
        # Always logout on exit
        if await manager.clear_token():
            print("Logged out")
```

## Thread Safety

All methods are thread-safe:

```python
import threading
from pantheon.auth.openai_oauth_manager import get_oauth_manager

def thread_func():
    manager = get_oauth_manager()
    # All threads get same singleton instance
    print(f"Manager: {id(manager)}")

threads = [threading.Thread(target=thread_func) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

# Output: All threads print same manager ID
```

## Async Safety

All async methods are concurrent-safe:

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager

async def main():
    manager = get_oauth_manager()

    # Multiple concurrent calls are safe
    results = await asyncio.gather(
        manager.get_access_token(),
        manager.get_org_context(),
        manager.get_status(),
    )
    print(f"Results: {results}")

asyncio.run(main())
```

## Error Handling

Recommended error handling pattern:

```python
import asyncio
from pantheon.auth.openai_oauth_manager import get_oauth_manager, OpenAIOAuthError

async def safe_login():
    try:
        manager = get_oauth_manager()
        if await manager.login():
            print("Login successful")
            return True
    except OpenAIOAuthError as e:
        print(f"OAuth error (expected): {e}")
        # OAuth-specific error, user feedback
    except Exception as e:
        print(f"Unexpected error: {e}")
        # System error, log and continue

    return False

asyncio.run(safe_login())
```

## Performance Notes

- Token refresh: ~100-200ms
- Status check: ~50-100ms
- Concurrent access: Safe with no contention
- Memory usage: ~2-5MB per manager instance
- Network: Minimal (only on token refresh or login)

## See Also

- [User Guide](./OAUTH_USER_GUIDE.md)
- [Admin Guide](./OAUTH_ADMIN_GUIDE.md)
- [OmicVerse Documentation](https://github.com/Jintao-Huang/OmicVerse)
- [OpenAI OAuth Documentation](https://platform.openai.com/docs/guides/oauth)
