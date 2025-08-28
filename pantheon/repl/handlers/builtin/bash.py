from ..base import CommandHandler


class BashCommandHandler(CommandHandler):
    def match_command(self, command: str) -> bool:
        return command.startswith("!")

    async def handle_command(self, command: str):
        if command.startswith("!"):
            bash_code = command[1:].strip()  # Remove the ! prefix
            if bash_code:
                await self._execute_direct_bash(bash_code)
        # TODO: Add support for restarting the bash interpreter

    async def _execute_direct_bash(self, command: str):
        """Execute bash command directly without LLM analysis"""
        # TODO: Use the shell toolset attached to the agent
        import subprocess
        import shlex
        
        try:
            self.console.print(f"[dim]Executing:[/dim] {command}")
            
            # Use subprocess to execute the command
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                universal_newlines=True
            )
            
            # Get output
            stdout, stderr = process.communicate()
            
            # Print output
            if stdout:
                self.console.print(stdout.strip())
            if stderr:
                self.console.print(f"[red]{stderr.strip()}[/red]")
            
            # Show return code if non-zero
            if process.returncode != 0:
                self.console.print(f"[yellow]Exit code: {process.returncode}[/yellow]")
                
        except Exception as e:
            self.console.print(f"[red]Error executing command: {str(e)}[/red]")
        
        self.console.print()  # Add spacing