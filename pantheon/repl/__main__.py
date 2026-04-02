"""
REPL entry point - Launch Pantheon REPL from command line.

Usage:
    python -m pantheon.repl                              # Start with defaults
    python -m pantheon.repl --template team.md           # Use specific template
    python -m pantheon.repl --memory-dir ./chats         # Custom directory
    python -m pantheon.repl start --template team.md     # Explicit start command
"""

import asyncio
import os
import sys
import logging
import warnings
from pathlib import Path

# Warning filters and litellm config are already set in pantheon/__init__.py
# which runs before this __main__.py

import fire
from dotenv import load_dotenv

# Capture original working directory BEFORE any changes
_ORIGINAL_CWD = os.getcwd()

# Load environment variables from .env file
load_dotenv()
# Also load global API keys from ~/.pantheon/.env and ~/.env (legacy fallback)
load_dotenv(os.path.join(os.path.expanduser("~"), ".pantheon", ".env"), override=False)
load_dotenv(os.path.join(os.path.expanduser("~"), ".env"), override=False)

# Enable UTF-8 mode on Windows for fancy Unicode characters
if sys.platform == "win32":
    try:
        # Set console to UTF-8 mode
        os.system("chcp 65001 > nul 2>&1")
        # Also set environment variable for Python
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # Silently ignore if it fails


def start(
    template: str = None,
    memory_dir: str = None,
    workspace: str = None,
    chat_id: str = None,
    resume: str | bool = False,
    log_level: str = None,
    quiet: bool = None,
    resync: bool = False,
    i: str = None,
):
    """Start Pantheon REPL.

    Args:
        template: Path to team template markdown file.
        memory_dir: Directory for chat persistence. (default from settings: .pantheon)
        workspace: Workspace directory for Endpoint.
        chat_id: Resume specific chat by ID.
        resume: Resume a previous chat. Use --resume to resume the last chat,
                or --resume=<id> / --resume=<number> to resume a specific one.
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL). Default: CRITICAL.
        quiet: Disable all logging. Use --quiet to enable. (default: False)
        resync: Force resync templates by deleting skills/agents/teams directories. (default: False)
        i: Initial input message to send immediately after startup.
    """
    # Load settings for defaults (CLI > Settings > code defaults)
    from pantheon.settings import get_settings
    import shutil

    settings = get_settings()

    # Resync: delete template directories to force re-copy from package defaults
    if resync:
        dirs_to_clean = [
            settings.skills_dir,
            settings.agents_dir,
            settings.teams_dir,
            settings.prompts_dir,
        ]
        for dir_path in dirs_to_clean:
            if dir_path.exists():
                shutil.rmtree(dir_path)
                print(f"🗑️  Cleaned: {dir_path}")
        print("✅ Template directories cleaned. Will resync from package defaults.")

    # Apply defaults: CLI > Settings > code defaults
    memory_dir = memory_dir or settings.get(
        "chatroom.memory_dir", str(settings.memory_dir)
    )
    quiet = quiet if quiet is not None else settings.get("repl.quiet", False)
    log_level = log_level or settings.get("repl.log_level", "CRITICAL")

    # Handle --resume: resolve to a chat_id before starting
    if resume is not False and resume is not None:
        from datetime import datetime as dt
        from pantheon.internal.memory import MemoryManager
        mm = MemoryManager(Path(memory_dir))
        ids = mm.list_memories()
        if not ids:
            print("No chat sessions found.")
            return
        # Build chat list sorted by last activity (newest first)
        chats = []
        for mid in ids:
            mem = mm.get_memory(mid)
            chats.append({
                "id": mid,
                "name": mem.name,
                "last_activity_date": mem.extra_data.get("last_activity_date"),
            })
        chats.sort(
            key=lambda x: dt.fromisoformat(x["last_activity_date"])
            if x["last_activity_date"] else dt.min,
            reverse=True,
        )
        if resume is True:
            # --resume without value: pick the most recent chat
            chat_id = chats[0]["id"]
        else:
            # --resume=<value>: match by index number, ID prefix, or name prefix
            resume_str = str(resume)
            found = None
            if resume_str.isdigit():
                idx = int(resume_str)
                if 1 <= idx <= len(chats):
                    found = chats[idx - 1]
            if not found:
                for chat in chats:
                    if chat.get("id", "").startswith(resume_str) or \
                       chat.get("name", "").lower().startswith(resume_str.lower()):
                        found = chat
                        break
            if not found:
                print(f"Chat not found: {resume}")
                return
            chat_id = found["id"]

    # Check for API keys and run setup wizard if none found
    from .setup_wizard import check_and_run_setup

    check_and_run_setup()

    asyncio.run(
        _start_async(
            template=template,
            memory_dir=memory_dir,
            workspace=workspace,
            chat_id=chat_id,
            log_level=log_level,
            quiet=quiet,
            initial_input=i,
        )
    )


async def _update_litellm_cost_map():
    """Background task to update litellm model cost map.

    Delegates to the shared utility in pantheon.utils.llm.
    """
    from pantheon.utils.llm import update_litellm_cost_map
    await update_litellm_cost_map()


async def _start_async(
    template: str = None,
    memory_dir: str = None,
    workspace: str = None,
    chat_id: str = None,
    log_level: str = "CRITICAL",
    quiet: bool = False,
    initial_input: str = None,
):
    """Async implementation of start."""
    from pantheon.settings import get_settings

    # Ensure memory_dir has a default
    if memory_dir is None:
        memory_dir = str(get_settings().memory_dir)
    # Import modules first (this triggers utils/log.py which sets up default logging)
    from .core import Repl
    from pantheon.chatroom import ChatRoom
    from pantheon.utils.log import disable_all, set_level

    # Setup logging AFTER imports (to override utils/log.py defaults)
    if quiet and log_level is None:
        # Completely disable all logging
        disable_all()
        # Also silence traitlets which might be used by jupyter_client/ipykernel components
        logging.getLogger("traitlets").setLevel(logging.ERROR)
    elif log_level is not None:
        # Use specified log level
        set_level(log_level)
    else:
        # Default to WARNING level
        set_level("WARNING")

    # Suppress FastMCP and Uvicorn logs unless explicitly debugging
    # Must be set BEFORE ChatRoom/Endpoint initialization starts MCP servers
    if log_level != "DEBUG":
        # FastMCP uses its own global settings for logging
        # Set via environment variable to avoid importing fastmcp here
        # For external libs, we default to WARNING to avoid INFO noise,
        # but respect user's choice if they want to be stricter (ERROR, CRITICAL)
        ext_level = "WARNING"
        if log_level in ("ERROR", "CRITICAL"):
            ext_level = log_level

        os.environ.setdefault("FASTMCP_LOG_LEVEL", ext_level)

        # Also set Python logging for uvicorn and MCP SDK
        logging.getLogger("FastMCP").setLevel(ext_level)
        logging.getLogger("uvicorn").setLevel(ext_level)
        logging.getLogger("uvicorn.access").setLevel(ext_level)
        # Suppress MCP SDK "Processing request of type" INFO logs
        logging.getLogger("mcp").setLevel(ext_level)
        logging.getLogger("mcp.server").setLevel(ext_level)
        logging.getLogger("mcp.server.lowlevel.server").setLevel(ext_level)

    # Use original CWD as workspace if not specified
    # This ensures file operations work relative to user's launch directory
    if workspace is None:
        workspace = _ORIGINAL_CWD

    if template:
        # Load team from template file
        template_path = Path(template)
        if not template_path.exists():
            print(f"Error: Template file not found: {template_path}")
            sys.exit(1)

        # Create ChatRoom (will read learning config from settings internally)
        chatroom = ChatRoom(
            endpoint=None,
            memory_dir=memory_dir,
            workspace_path=workspace,
            enable_nats_streaming=False,
            learning_config=get_settings().get_learning_config(),
            enable_auto_chat_name=True,
        )

        # Setup ChatRoom (including auto-created Endpoint)
        await chatroom.run_setup()

        # Parse template and create team
        from pantheon.factory import get_template_manager

        template_manager = get_template_manager()
        template_content = template_path.read_text(encoding="utf-8")
        team_config = template_manager.parse_template_content(
            template_content, file_path=template_path.resolve()
        )

        # Create chat with template
        result = await chatroom.create_chat("repl-session")
        chat_id = result["chat_id"]
        await chatroom.setup_team_for_chat(
            chat_id, team_config.to_dict(), save_to_memory=False
        )

        repl = Repl(
            chatroom=chatroom,
            chat_id=chat_id,
        )
    else:
        # Default: auto-create everything with workspace set to original CWD
        # ChatRoom will read learning config from settings internally
        chatroom = ChatRoom(
            endpoint=None,
            memory_dir=memory_dir,
            workspace_path=workspace,
            enable_nats_streaming=False,
            learning_config=get_settings().get_learning_config(),
            enable_auto_chat_name=True,
        )
        # Note: run_setup() is called in repl.run() AFTER UI display
        repl = Repl(
            chatroom=chatroom,
            chat_id=chat_id,
        )

    # Suppress CancelledError traceback from uvicorn during REPL shutdown
    # This is a benign error that occurs when uvicorn's lifespan is cancelled
    if log_level != "DEBUG":

        class _SuppressCancelledErrorFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                # Check exc_info
                if record.exc_info and isinstance(
                    record.exc_info[1], asyncio.CancelledError
                ):
                    return False
                # Check message content (traceback may be formatted as message)
                msg = (
                    record.getMessage()
                    if hasattr(record, "getMessage")
                    else str(getattr(record, "msg", ""))
                )
                if "CancelledError" in msg:
                    return False
                return True

        logging.getLogger("uvicorn.error").addFilter(_SuppressCancelledErrorFilter())

    # Disable logging unless explicitly set to DEBUG
    disable_logging = quiet and log_level != "DEBUG"

    # Start background task to update litellm cost map (non-blocking)
    asyncio.create_task(_update_litellm_cost_map())

    await repl.run(message=initial_input, disable_logging=disable_logging, log_level=log_level)


if __name__ == "__main__":
    # Support two call styles:
    # 1. python -m pantheon.repl start --template xxx
    # 2. python -m pantheon.repl --template xxx (implicit start)
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1].startswith("-")):
        sys.argv.insert(1, "start")
    fire.Fire(
        {
            "start": start,
        },
        name="pantheon-repl",
    )
