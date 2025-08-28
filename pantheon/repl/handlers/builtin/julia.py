from ..base import CommandHandler


class JuliaCommandHandler(CommandHandler):
    def match_command(self, command: str) -> bool:
        return command.startswith("]")

    async def handle_command(self, command: str):
        if command.startswith("]"):
            julia_code = command[1:].strip()  # Remove the ! prefix
            if julia_code:
                await self._execute_direct_julia(julia_code)

    async def _execute_direct_julia(self, code: str):
        """Execute Julia code directly using the Julia toolset"""
        try:
            # Use the julia_toolset attached to the agent
            if hasattr(self.agent, '_julia_toolset') and self.agent._julia_toolset:
                julia_toolset = self.agent._julia_toolset
            else:
                # Fallback: try to find the run_julia_code function
                if hasattr(self.agent, 'functions'):
                    julia_func = self.agent.functions.get('run_julia_code')
                    if julia_func:
                        # Get client_id for shared interpreter
                        client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
                        
                        result = await julia_func(code=code, context_variables={"client_id": client_id})
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
                
                self.console.print("[red]Julia interpreter not available. Please ensure it's loaded.[/red]")
                return
            
            # Display execution info
            if len(code) > 100:
                # For multi-line code, show first line
                first_line = code.split('\n')[0][:50]
                self.console.print(f"[dim]Executing Julia: {first_line}...[/dim]")
            else:
                self.console.print(f"[dim]Executing Julia: {code}[/dim]")
            
            # Execute the Julia code using the toolset directly with Agent's memory.id as client_id
            # This ensures the same Julia interpreter instance is used as the Agent
            client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
            result = await julia_toolset.run_julia_code(
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
            self.console.print(f"[red]Error executing Julia code: {str(e)}[/red]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
        
        self.console.print()  # Add spacing