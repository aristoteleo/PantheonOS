from rich.prompt import Prompt
from pantheon.repl.handlers.base import CommandHandler
from pantheonos.utils.misc import run_func

class RevertCommandHandler(CommandHandler):
    """Handler for the /revert command."""

    def match_command(self, command: str) -> bool:
        return command.lower().startswith("/revert")

    async def handle_command(self, command: str) -> str | None:
        """Handle the /revert command."""
        
        args = command[7:].strip()
        # Read-only: reverting messages, no need to fix (revert_to_message handles cleanup)
        memory = await run_func(self.parent._chatroom.memory_manager.get_memory, self.parent._chat_id)

        # Mode 1: Revert by index directly
        if args:
            try:
                target_index = int(args)
                
                # Check message content before reverting
                reverted_content = None
                if 0 <= target_index < len(memory._messages):
                    msg = memory._messages[target_index]
                    if msg.get("role") == "user":
                        reverted_content = str(msg.get("content", ""))
                
                await run_func(memory.revert_to_message, target_index)
                self.console.print(f"[green]Reverted to state before message index {target_index}.[/green]")
                self.parent._add_to_history(f"Reverted to state before message index {target_index}")
                
                # Populate input buffer if available
                if reverted_content and self.parent.prompt_app:
                    self.parent.prompt_app.set_input_text(reverted_content)
                    
                return None
            except ValueError:
                self.console.print("[red]Invalid index provided. Usage: /revert <index> or just /revert[/red]")
                return None
        
        # Mode 2: Interactive selection
        user_turns = memory.get_user_turns()
        
        if not user_turns:
            self.console.print("[yellow]No user messages found to revert to.[/yellow]")
            return None
            
        # Display table of user turns
        self.console.print("[bold]Conversation History (User Turns)[/bold]")
        
        # Show last 10 turns by default
        display_turns = user_turns[-10:]
        
        for idx, msg in display_turns:
            content = str(msg.get("content", ""))
            # Truncate long content
            if len(content) > 80:
                content = content[:77] + "..."
            
            timestamp = msg.get("timestamp", "-")
            from datetime import datetime
            if isinstance(timestamp, (int, float)):
                ts_str = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
            else:
                ts_str = str(timestamp)
                
            self.console.print(f"  [cyan]{idx:<4}[/cyan] [dim]{ts_str}[/dim] {content}")
        self.console.print(
            "[yellow]WARNING: Reverting only affects conversation memory. It does NOT revert file changes or other external states.[/yellow]"
        )
        self.console.print(
            "[dim]Note: Reverting will delete the selected message and EVERYTHING after it.[/dim]"
        )
        
        self.console.print(
            "[dim]To proceed, run: /revert <index>[/dim]"
        )
        
        return None
