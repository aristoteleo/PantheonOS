from ..base import CommandHandler


class RCommandHandler(CommandHandler):
    def match_command(self, command: str) -> bool:
        return command.startswith(">")

    async def handle_command(self, command: str):
        if command.startswith(">"):
            r_code = command[1:].strip()  # Remove the ! prefix
            if r_code:
                await self._execute_direct_r(r_code)
        # TODO: Add support for restarting the R interpreter

    async def _execute_direct_r(self, code: str):
        """Execute R code directly using the R toolset"""
        try:
            # Use the r_toolset attached to the agent
            if hasattr(self.agent, '_r_toolset') and self.agent._r_toolset:
                r_toolset = self.agent._r_toolset
            else:
                # Fallback: try to find the run_r_code function
                if hasattr(self.agent, 'functions'):
                    r_func = self.agent.functions.get('run_r_code')
                    if r_func:
                        # Get client_id for shared interpreter
                        client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
                        
                        result = await r_func(code=code, context_variables={"client_id": client_id})
                        if result and isinstance(result, dict):
                            # Extract and display relevant parts
                            if result.get('stdout'):
                                self.console.print(result['stdout'])
                            if result.get('stderr'):
                                self.console.print(f"[red]{result['stderr']}[/red]")
                            if result.get('result') is not None:
                                self.console.print(str(result['result']))
                        elif result:
                            self.console.print(result)
                        self.console.print()
                        return
                
                self.console.print("[red]R interpreter not available. Please ensure it's loaded.[/red]")
                return
            
            # Display execution info
            if len(code) > 100:
                # For multi-line code, show first line
                first_line = code.split('\n')[0][:50]
                self.console.print(f"[dim]Executing R: {first_line}...[/dim]")
            else:
                self.console.print(f"[dim]Executing R: {code}[/dim]")
            
            # Execute the R code using the toolset directly with Agent's memory.id as client_id
            # This ensures the same R interpreter instance is used as the Agent
            client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
            result = await r_toolset.run_r_code(
                code=code,
                context_variables={"client_id": client_id}
            )
            
            # Process and display the result
            if result:
                if isinstance(result, dict):
                    # Handle structured result from the toolset
                    stdout = result.get('stdout', '').strip()
                    stderr = result.get('stderr', '').strip()
                    exec_result = result.get('result')
                    
                    # Display output in a clean way
                    if stdout:
                        self.console.print(stdout)
                    if stderr:
                        self.console.print(f"[red]{stderr}[/red]")
                    if exec_result is not None and str(exec_result).strip() and str(exec_result) != "None":
                        self.console.print(str(exec_result))
                    
                    # If nothing was printed, show a subtle success indicator
                    if not stdout and not stderr and (exec_result is None or str(exec_result) == "None"):
                        self.console.print("[dim]✓ Code executed successfully[/dim]")
                else:
                    # Handle plain text result
                    self.console.print(result)
            
        except Exception as e:
            self.console.print(f"[red]Error executing R code: {str(e)}[/red]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
        
        self.console.print()  # Add spacing
