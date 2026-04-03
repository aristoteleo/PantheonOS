import asyncio
import os
import sys

import fire
from dotenv import load_dotenv

# Load .env file with override=False to NOT override existing environment variables
# This allows command-line args (like --auto-start-nats) to take precedence
load_dotenv(override=False)
# Also load global API keys from ~/.pantheon/.env and ~/.env (legacy fallback)
load_dotenv(os.path.join(os.path.expanduser("~"), ".pantheon", ".env"), override=False)
load_dotenv(os.path.join(os.path.expanduser("~"), ".env"), override=False)

# Now safe to import other modules
from pantheon.chatroom.start import start_services
from pantheon.repl.setup_wizard import check_and_run_setup


def oauth(action: str = "status", provider: str = "codex"):
    """Manage OAuth authentication for LLM providers.

    Args:
        action: One of 'login', 'import', 'status', 'logout'
        provider: OAuth provider name (default: 'codex')

    Examples:
        pantheon-chatroom oauth login          # Browser-based login
        pantheon-chatroom oauth import         # Import from Codex CLI
        pantheon-chatroom oauth status         # Check auth status
        pantheon-chatroom oauth logout         # Remove stored tokens
    """
    if provider != "codex":
        print(f"Unsupported OAuth provider: {provider}")
        print("Supported providers: codex")
        return

    from pantheon.utils.oauth import CodexOAuthManager, CodexOAuthError
    mgr = CodexOAuthManager()

    if action == "status":
        if mgr.is_authenticated():
            account_id = mgr.get_account_id()
            print(f"Codex OAuth: authenticated")
            print(f"  Account ID: {account_id}")
            print(f"  Auth file: {mgr.auth_file}")
            print(f"  Use model prefix: codex/gpt-5.4-mini, codex/gpt-5, etc.")
        else:
            print(f"Codex OAuth: not authenticated")
            print(f"  Run: pantheon-chatroom oauth login")
            print(f"  Or:  pantheon-chatroom oauth import  (if Codex CLI is installed)")

    elif action == "login":
        print("Starting Codex OAuth login...")
        print("A browser window will open. Please log in with your OpenAI account.")
        try:
            mgr.login(open_browser=True, timeout_seconds=300)
            print(f"\nLogin successful!")
            print(f"  Account ID: {mgr.get_account_id()}")
            print(f"  You can now use codex/ models (e.g., codex/gpt-5.4-mini)")
        except CodexOAuthError as e:
            print(f"\nLogin failed: {e}")
        except KeyboardInterrupt:
            print("\nLogin cancelled.")

    elif action == "import":
        print("Importing from Codex CLI (~/.codex/auth.json)...")
        result = mgr.import_from_codex_cli()
        if result:
            print(f"Import successful!")
            print(f"  Account ID: {mgr.get_account_id()}")
        else:
            print(f"Import failed. Make sure Codex CLI is installed and authenticated.")
            print(f"  Install: npx @anthropic-ai/codex")
            print(f"  Or use: pantheon-chatroom oauth login")

    elif action == "logout":
        if mgr.auth_file.exists():
            mgr.auth_file.unlink()
            print("Codex OAuth tokens removed.")
        else:
            print("No Codex OAuth tokens found.")

    else:
        print(f"Unknown action: {action}")
        print("Actions: login, import, status, logout")


if __name__ == "__main__":
    # Check for API keys and run setup wizard if none found
    check_and_run_setup()
    # prompt_toolkit may close the event loop; ensure one exists for Fire + async
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1].startswith("-")):
        sys.argv.insert(1, "start")
    fire.Fire(
        {"start": start_services, "oauth": oauth},
        name="pantheon-chatroom",
    )
