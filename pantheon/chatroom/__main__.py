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
    if provider not in {"codex", "gemini"}:
        print(f"Unsupported OAuth provider: {provider}")
        print("Supported providers: codex, gemini")
        return

    if provider == "codex":
        from pantheon.utils.oauth import CodexOAuthError, CodexOAuthManager

        mgr = CodexOAuthManager()
        label = "Codex OAuth"
        success_lines = [
            f"  Account ID: {mgr.get_account_id()}",
            "  You can now use codex/ models (e.g., codex/gpt-5.4-mini)",
        ]
        import_prompt = "Importing from Codex CLI (~/.codex/auth.json)..."
        import_fail_lines = [
            "Import failed. Make sure Codex CLI is installed and authenticated.",
            "  Install: npx @anthropic-ai/codex",
            "  Or use: pantheon-chatroom oauth login",
        ]
    else:
        from pantheon.utils.oauth import GeminiCliOAuthError as CodexOAuthError
        from pantheon.utils.oauth import GeminiCliOAuthManager as CodexOAuthManager

        mgr = CodexOAuthManager()
        label = "Gemini OAuth"
        success_lines = [
            f"  Email: {mgr.get_email()}",
            f"  Project ID: {mgr.get_project_id()}",
            "  You can now use gemini-cli/ models (e.g., gemini-cli/gemini-2.5-flash)",
        ]
        import_prompt = "Importing from Gemini CLI (~/.gemini/oauth_creds.json)..."
        import_fail_lines = [
            "Import failed. Make sure Gemini CLI is installed and authenticated.",
            "  Or use: pantheon-chatroom oauth login --provider gemini",
        ]

    if action == "status":
        if mgr.is_authenticated():
            print(f"{label}: authenticated")
            if provider == "codex":
                print(f"  Account ID: {mgr.get_account_id()}")
            else:
                print(f"  Email: {mgr.get_email()}")
                print(f"  Project ID: {mgr.get_project_id()}")
                if not mgr.get_project_id():
                    print("  Runtime ready: no")
                    print("  Gemini CLI auth is present, but no Code Assist project has been resolved yet.")
                    print("  Pantheon will try to resolve one automatically on first use.")
                else:
                    print("  Runtime ready: yes")
            print(f"  Auth file: {mgr.auth_file}")
            if provider == "codex":
                print("  Use model prefix: codex/gpt-5.4-mini, codex/gpt-5, etc.")
            else:
                print("  Use model prefix: gemini-cli/gemini-2.5-flash, gemini-cli/gemini-2.5-pro, etc.")
        else:
            print(f"{label}: not authenticated")
            print(f"  Run: pantheon-chatroom oauth login --provider {provider}")
            print(f"  Or:  pantheon-chatroom oauth import --provider {provider}")

    elif action == "login":
        print(f"Starting {label} login...")
        if provider == "codex":
            print("A browser window will open. Please log in with your OpenAI account.")
        else:
            print("A browser window will open. Please log in with your Google account.")
        try:
            mgr.login(open_browser=True, timeout_seconds=300)
            print(f"\nLogin successful!")
            for line in success_lines:
                print(line)
        except CodexOAuthError as e:
            print(f"\nLogin failed: {e}")
        except KeyboardInterrupt:
            print("\nLogin cancelled.")

    elif action == "import":
        print(import_prompt)
        result = mgr.import_from_codex_cli() if provider == "codex" else mgr.import_from_gemini_cli()
        if result:
            print(f"Import successful!")
            for line in success_lines[:-1]:
                print(line)
        else:
            for line in import_fail_lines:
                print(line)

    elif action == "logout":
        if mgr.auth_file.exists():
            mgr.auth_file.unlink()
            print(f"{label} tokens removed.")
        else:
            print(f"No {label} tokens found.")

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
