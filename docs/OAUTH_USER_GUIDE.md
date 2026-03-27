# OpenAI OAuth 2.0 Authentication Guide

## Overview

PantheonOS now supports OpenAI OAuth 2.0 authentication as an alternative to API key-based authentication. This guide explains how to set up, use, and manage OAuth authentication in PantheonOS.

## Why Use OAuth?

- **No API Key Exposure**: Your API key is never stored locally. Authentication is done via secure OAuth flow.
- **Browser-Based Authentication**: Uses your OpenAI web account for a familiar, secure login experience.
- **Automatic Token Refresh**: Tokens are automatically refreshed when they expire.
- **Organization Context**: Automatically detects your OpenAI organization and project information.
- **Codex CLI Integration**: Can import existing credentials from Codex CLI if available.

## Quick Start

### 1. Initial Setup

When you start PantheonOS for the first time without API key authentication:

```bash
pantheon  # Start PantheonOS
```

The Setup Wizard will appear. Select **"OpenAI (OAuth)"** from the provider menu:

```
Select your AI provider:
1. OpenAI (API Key)
2. OpenAI (OAuth)
3. Anthropic (Claude)
4. Google (Gemini)
...

Choose provider [1-4]: 2
```

### 2. Browser Login

After selecting OAuth:

1. A browser window will automatically open
2. Log in with your OpenAI account (or sign up if needed)
3. You'll see a request for authorization
4. Click **"Authorize"** to grant PantheonOS access
5. You'll be redirected with a success message
6. PantheonOS will automatically store your credentials

### 3. Start Using

Once authenticated, you can immediately start using PantheonOS with OpenAI models:

```
$ pantheon
> /model normal
Resolving models...
Available models: gpt-4-turbo, gpt-4, gpt-3.5-turbo
> /select gpt-4
Selected: gpt-4
> Hello, how are you?
```

## REPL Commands

### Check Authentication Status

```
> /oauth status
OAuth Status:
  Authenticated: Yes
  Email: user@example.com
  Organization: org-123abc
  Project: proj-xyz789
  Token Expires: 2025-04-30T12:00:00Z
```

### Re-authenticate (Login Again)

If your token expires or you want to switch accounts:

```
> /oauth login
[Browser opens for OpenAI login]
```

### Logout (Clear OAuth Token)

To remove stored credentials and log out:

```
> /oauth logout
OAuth token cleared. You will need to authenticate again.
```

### View Help

```
> /oauth
OAuth Status:
  Authenticated: Yes
  ...
```

## Migrating from API Key to OAuth

### Step 1: Verify Current Setup

Check if you're currently using API key authentication:

```bash
echo $OPENAI_API_KEY
```

### Step 2: Clear API Key (Optional)

If you want to switch from API key to OAuth:

```bash
# Linux/macOS
unset OPENAI_API_KEY

# Windows PowerShell
Remove-Item Env:OPENAI_API_KEY

# Windows cmd.exe
set OPENAI_API_KEY=
```

### Step 3: Start PantheonOS

```bash
pantheon
```

PantheonOS will detect that no API key is set and offer OAuth as an option in Setup Wizard.

### Step 4: Authenticate with OAuth

Follow the "Quick Start" section above to authenticate via OAuth.

## Using OAuth with Codex CLI

If you already have Codex CLI credentials on your system, PantheonOS can import them:

1. Start the OAuth login flow as normal
2. During the first token request, PantheonOS will check for existing Codex credentials
3. If found, they will be automatically imported and refreshed
4. You won't need to log in again unless the token expires

This provides seamless migration from Codex CLI to PantheonOS.

## Troubleshooting

### "Browser didn't open automatically"

**Solution**:
- Manual login is supported in future versions
- Check that your default browser is set correctly in system settings
- Try restarting PantheonOS

### "OAuth token expired"

**Symptoms**:
- Error: `OpenAI OAuth token retrieval failed`
- Models are unavailable

**Solution**:
```
> /oauth login
[Re-authenticate with browser]
```

Tokens are automatically refreshed internally when they approach expiration (5 minutes before actual expiry).

### "No organization/project information"

**Causes**:
- Your OpenAI account doesn't have organization/project information set up
- This doesn't prevent authentication, just limits context information

**Solution**:
- Visit https://platform.openai.com/account/organization/ to set up organization
- Visit https://platform.openai.com/account/projects/ to set up projects

### "Can't import Codex credentials"

**Causes**:
- Codex CLI not installed on your system
- Codex credentials are too old or corrupted

**Solution**:
- Use the OAuth browser login instead
- Reinstall Codex CLI if needed: `pip install openai-codex`

### "OAuth token file not found"

**Location**: `~/.pantheon/oauth.json`

**Solution**:
- Delete this file to force re-authentication: `rm ~/.pantheon/oauth.json`
- Then run PantheonOS and authenticate again

### "Multiple accounts / switching accounts"

**Current Limitation**: PantheonOS stores one OAuth token at a time.

**Workaround**:
1. Log out: `> /oauth logout`
2. Clear OAuth file: `rm ~/.pantheon/oauth.json`
3. Log in with new account: `> /oauth login`

(Future: Multi-account support planned)

## Advanced Usage

### Programmatic OAuth Access

If you're using PantheonOS as a Python library:

```python
from pantheon.auth.openai_oauth_manager import get_oauth_manager
import asyncio

async def main():
    oauth_mgr = get_oauth_manager()

    # Get access token
    token = await oauth_mgr.get_access_token()
    print(f"Token: {token}")

    # Get organization context
    context = await oauth_mgr.get_org_context()
    print(f"Organization: {context.get('organization_id')}")
    print(f"Project: {context.get('project_id')}")

    # Get full status
    status = await oauth_mgr.get_status()
    print(f"Authenticated: {status['authenticated']}")
    print(f"Email: {status['email']}")

asyncio.run(main())
```

### Custom OAuth Token Location

```python
from pathlib import Path
from pantheon.auth.openai_oauth_manager import get_oauth_manager

# Store OAuth token in custom location
custom_path = Path("/tmp/my_pantheon_oauth.json")
oauth_mgr = get_oauth_manager(auth_path=custom_path)

# Now authentication will use custom location
```

### Environment Variable Coexistence

OAuth and API key authentication can coexist:

```bash
# You can set both
export OPENAI_API_KEY="sk-..."

# PantheonOS will detect both and let you choose during Setup Wizard
pantheon
```

## Security Considerations

### Token Storage

- OAuth tokens are stored in `~/.pantheon/oauth.json`
- File permissions: readable only by your user (mode 0600)
- Tokens are **never** logged or displayed
- Always use HTTPS for OAuth communication

### Token Lifecycle

- **Expiration**: OAuth tokens expire after a period (typically 30 days)
- **Automatic Refresh**: Tokens are silently refreshed when approaching expiration
- **Manual Refresh**: Can force refresh by calling `/oauth login` again

### Logout / Revocation

- `/oauth logout` immediately removes the token file
- Token becomes invalid on OpenAI servers
- Browser session is also cleared

### Best Practices

1. **Don't Share OAuth Files**: Never share `~/.pantheon/oauth.json`
2. **Logout on Shared Systems**: Always `/oauth logout` when done
3. **Regular Logout**: If not using PantheonOS frequently, log out for security
4. **Check Status**: Use `/oauth status` to verify you're logged in as the right account
5. **Monitor Email**: OpenAI sends notification emails for new OAuth applications

## FAQ

**Q: What data does PantheonOS request from OpenAI?**
A: PantheonOS requests access to:
- Email address
- Organization ID
- Project ID
- Read access to model lists

**Q: Can I revoke PantheonOS OAuth access?**
A: Yes, visit https://platform.openai.com/account/connected-apps and disconnect PantheonOS

**Q: Is OAuth more secure than API key?**
A: OAuth has advantages:
- No long-lived secrets stored locally
- Revocable at any time
- Works with OpenAI 2FA if enabled
- Automatic token refresh

**Q: What if I lose my OpenAI account access?**
A: OAuth tokens will become invalid. You'll need to re-authenticate with the recovered account.

**Q: Can I use OAuth with proxy / VPN?**
A: Yes, if your browser can access openai.com, OAuth will work. The browser automatically handles proxy settings.

**Q: How often are tokens refreshed?**
A: Tokens are checked every 5 minutes. If within 5 minutes of expiration, automatic refresh is attempted.

## Getting Help

- **OAuth Issues**: Check `/oauth status` to see current state
- **REPL Help**: Type `/?` for available commands
- **Documentation**: See `docs/OAUTH_ADMIN_GUIDE.md` for administrative setup
- **API Reference**: See `docs/OAUTH_API.md` for programmatic use

## See Also

- [OpenAI OAuth Documentation](https://platform.openai.com/docs/guides/oauth)
- [PKCE Security Standard](https://tools.ietf.org/html/rfc7636)
- [PantheonOS Setup Guide](./SETUP.md)
- [API Key Authentication Guide](./API_KEY_GUIDE.md)
