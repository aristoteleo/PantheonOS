"""Unified Pantheon CLI entry point.

Usage:
    pantheon cli [OPTIONS]       Start Pantheon CLI (REPL)
    pantheon ui [OPTIONS]        Start Pantheon UI (Chatroom)
"""

import warnings

# Suppress DeprecationWarnings before any third-party imports (fastapi, starlette, etc.)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

import asyncio
import os
import sys

import fire
from dotenv import load_dotenv

# Load .env files: cwd/.env > ~/.pantheon/.env > ~/.env (legacy fallback)
load_dotenv()
load_dotenv(
    os.path.join(os.path.expanduser("~"), ".pantheon", ".env"), override=False
)
load_dotenv(
    os.path.join(os.path.expanduser("~"), ".env"), override=False
)

# Windows UTF-8 setup
if sys.platform == "win32":
    try:
        os.system("chcp 65001 > nul 2>&1")
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def setup():
    """Launch the setup wizard to configure LLM provider API keys."""
    from pantheon.repl.setup_wizard import run_setup_wizard

    run_setup_wizard(standalone=True)


def update_templates():
    """Compare and selectively update .pantheon/ templates from factory defaults."""
    from rich.console import Console
    from prompt_toolkit import prompt as pt_prompt
    from pantheon.factory.template_manager import get_template_manager

    console = Console()
    tm = get_template_manager()
    items = tm.get_updatable_templates()

    if not items:
        console.print("[green]All templates are up to date.[/green]")
        return

    # Display list
    console.print(f"\n[bold]Found {len(items)} template(s) that can be updated:[/bold]\n")

    STATUS_STYLE = {
        "new": "[cyan]new[/cyan]",
        "updated": "[green]updated[/green]",
        "modified": "[yellow]modified (user-edited)[/yellow]",
    }

    for i, item in enumerate(items, 1):
        style = STATUS_STYLE[item["status"]]
        console.print(f"  [cyan][{i}][/cyan] {item['category']}/{item['rel_path']}  {style}")

    console.print(f"\n  [cyan][a][/cyan] Select all")
    console.print()

    try:
        selection = pt_prompt("Select templates to update (comma-separated, e.g. 1,3,5 or a): ")
    except (EOFError, KeyboardInterrupt):
        console.print("\nCancelled.")
        return

    # Parse selection
    selection = selection.strip().lower()
    if selection == "a":
        selected = items
    else:
        selected = []
        for part in selection.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(items):
                    selected.append(items[idx - 1])

    if not selected:
        console.print("[yellow]No templates selected.[/yellow]")
        return

    # Confirm if any modified files
    modified = [s for s in selected if s["status"] == "modified"]
    if modified:
        console.print(f"\n[yellow]Warning: {len(modified)} file(s) have user modifications that will be overwritten:[/yellow]")
        for item in modified:
            console.print(f"  - {item['category']}/{item['rel_path']}")
        try:
            confirm = pt_prompt("Continue? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            confirm = "n"
        if confirm.strip().lower() != "y":
            console.print("Cancelled.")
            return

    # Execute update
    tm.force_update_templates(selected)
    console.print(f"\n[green]Updated {len(selected)} template(s).[/green]")


def main():
    # Skip auto-setup if user explicitly requested "pantheon setup"
    if len(sys.argv) < 2 or sys.argv[1] != "setup":
        from pantheon.repl.setup_wizard import check_and_run_setup

        check_and_run_setup()

    # Ensure an event loop exists for Fire + async functions (Python 3.10+)
    # Python Fire internally calls asyncio.get_event_loop() when handling async functions,
    # which raises RuntimeError in Python 3.12+ if no loop exists.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Import REAL functions — Fire reads their signatures for --help
    from pantheon.repl.__main__ import start as cli
    from pantheon.chatroom.start import start_services as ui
    from pantheon.store.cli import StoreCLI
    fire.Fire(
        {
            "cli": cli,
            "ui": ui,
            "setup": setup,
            "update-templates": update_templates,
            "store": StoreCLI,
        },
        name="pantheon",
    )


if __name__ == "__main__":
    main()
