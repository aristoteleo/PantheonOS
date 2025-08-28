from ..base import CommandHandler


class PythonCommandHandler(CommandHandler):
    def match_command(self, command: str) -> bool:
        return command.startswith("%") or command.lower() in ["/restart-python"]

    async def handle_command(self, command: str):
        if command.startswith("%"):
            python_code = command[1:].strip()  # Remove the % prefix
            if python_code:
                await self._execute_direct_python(python_code)
        elif command.lower() in ["/restart-python"]:
            await self._restart_python_interpreter()

    async def _execute_direct_python(self, code: str):
        """Execute Python code directly using the Python toolset"""
        try:
            # Use the python_toolset attached to the agent
            if hasattr(self.agent, '_python_toolset') and self.agent._python_toolset:
                python_toolset = self.agent._python_toolset
            else:
                # Fallback: try to find the run_python_code function
                if hasattr(self.agent, 'functions'):
                    python_func = self.agent.functions.get('run_python_code')
                    if python_func:
                        # Get client_id for shared interpreter
                        client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
                        
                        result = await python_func(code=code, context_variables={"client_id": client_id})
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
                
                self.console.print("[red]Python interpreter not available. Please ensure it's loaded.[/red]")
                return
            
            # Display execution info
            if len(code) > 100:
                # For multi-line code, show first line
                first_line = code.split('\n')[0][:50]
                self.console.print(f"[dim]Executing Python: {first_line}...[/dim]")
            else:
                self.console.print(f"[dim]Executing Python: {code}[/dim]")
            
            # Execute the Python code using the toolset directly with Agent's memory.id as client_id
            # This ensures the same Python interpreter instance is used as the Agent
            client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
            result = await python_toolset.run_python_code(
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
                    
                    # Check for interpreter restart or crash
                    if result.get("interpreter_restarted"):
                        restart_reason = result.get("restart_reason", "Unknown reason")
                        self.console.print(f"[yellow]⚠️  Python interpreter was automatically restarted due to: {restart_reason}[/yellow]")
                        self.console.print(f"[dim]All previous variables and imports have been lost. You may need to re-import libraries.[/dim]\n")
                    
                    if result.get("interpreter_crashed"):
                        self.console.print(f"[red]💥 Python interpreter crashed and could not be restarted automatically.[/red]")
                        self.console.print(f"[dim]Use [bold]/restart[/bold] command to manually reset the Python environment.[/dim]\n")
                        return
                    
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
            self.console.print(f"[red]Error executing Python code: {str(e)}[/red]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")
        
        self.console.print()  # Add spacing

    async def _restart_python_interpreter(self):
        """Restart the Python interpreter"""
        try:
            self.console.print("\n[yellow]⚡ Restarting Python interpreter...[/yellow]")
            
            # Get the python toolset
            python_toolset = None
            if hasattr(self.agent, '_python_toolset') and self.agent._python_toolset:
                python_toolset = self.agent._python_toolset
            else:
                self.console.print("[red]Python interpreter not available.[/red]")
                return
            
            # Get client_id
            client_id = self.agent.memory.id if hasattr(self.agent, 'memory') and hasattr(self.agent.memory, 'id') else "default"
            
            # Force restart by cleaning up old interpreter and creating new one
            old_interpreter_id = python_toolset.clientid_to_interpreterid.get(client_id)
            
            if old_interpreter_id and old_interpreter_id in python_toolset.interpreters:
                # Clean up old interpreter
                try:
                    await python_toolset.delete_interpreter(old_interpreter_id)
                    self.console.print("[dim]Old interpreter cleaned up[/dim]")
                except Exception as e:
                    self.console.print(f"[dim]Warning: Failed to clean up old interpreter: {e}[/dim]")
                finally:
                    # Remove from tracking even if cleanup failed
                    if old_interpreter_id in python_toolset.interpreters:
                        del python_toolset.interpreters[old_interpreter_id]
                    if old_interpreter_id in python_toolset.jobs:
                        del python_toolset.jobs[old_interpreter_id]
            
            # Create new interpreter
            new_interpreter_id = await python_toolset.new_interpreter()
            python_toolset.clientid_to_interpreterid[client_id] = new_interpreter_id
            
            self.console.print("[green]✓ Python interpreter restarted successfully![/green]")
            self.console.print("[dim]All variables and imports have been cleared.[/dim]\n")
            
        except Exception as e:
            self.console.print(f"[red]Failed to restart Python interpreter: {e}[/red]")
            import traceback
            self.console.print(f"[dim]{traceback.format_exc()}[/dim]")