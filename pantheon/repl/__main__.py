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
from pathlib import Path

import fire
from dotenv import load_dotenv

# Capture original working directory BEFORE any changes
_ORIGINAL_CWD = os.getcwd()

# Load environment variables from .env file
load_dotenv()

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
    log_level: str = None,
    quiet: bool = None,
):
    """Start Pantheon REPL.

    Args:
        template: Path to team template markdown file.
        memory_dir: Directory for chat persistence. (default from settings: .pantheon)
        workspace: Workspace directory for Endpoint.
        chat_id: Resume specific chat by ID.
        log_level: Log level (DEBUG, INFO, WARNING, ERROR). Default: ERROR.
        quiet: Disable all logging. Use --quiet to enable. (default: False)
    """
    # Load settings for defaults (CLI > Settings > code defaults)
    from pantheon.settings import get_settings
    settings = get_settings()

    # Apply defaults: CLI > Settings > code defaults
    memory_dir = memory_dir or settings.get("chatroom.memory_dir", str(settings.memory_dir))
    quiet = quiet if quiet is not None else settings.get("repl.quiet", False)
    log_level = log_level or settings.get("repl.log_level", "ERROR")
    
    asyncio.run(
        _start_async(
            template=template,
            memory_dir=memory_dir,
            workspace=workspace,
            chat_id=chat_id,
            log_level=log_level,
            quiet=quiet,
        )
    )


async def _start_async(
    template: str = None,
    memory_dir: str = None,
    workspace: str = None,
    chat_id: str = None,
    log_level: str = "ERROR",
    quiet: bool = False,
):
    """Async implementation of start."""
    # Ensure memory_dir has a default
    if memory_dir is None:
        from pantheon.settings import get_settings
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

        # Create ChatRoom
        chatroom = ChatRoom(
            endpoint=None,
            memory_dir=memory_dir,
            workspace_path=workspace,
            enable_nats_streaming=False,
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
        await chatroom.setup_team_for_chat(chat_id, team_config.to_dict())

        repl = Repl(
            chatroom=chatroom,
            chat_id=chat_id,
        )
    else:
        # Default: auto-create everything with workspace set to original CWD
        chatroom = ChatRoom(
            endpoint=None,
            memory_dir=memory_dir,
            workspace_path=workspace,
            enable_nats_streaming=False,
        )
        repl = Repl(
            chatroom=chatroom,
            chat_id=chat_id,
        )

    # Suppress FastMCP and Uvicorn logs unless explicitly debugging
    # specific libraries that use standard logging
    if log_level != "DEBUG":
        # FastMCP uses a custom logger globally configured on import
        # We must suppress "FastMCP" (capitalized) not "fastmcp"
        logging.getLogger("FastMCP").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Disable logging unless explicitly set to DEBUG
    disable_logging = quiet and log_level != "DEBUG"
    await repl.run(disable_logging=disable_logging)


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
