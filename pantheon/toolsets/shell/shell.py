import uuid

from ._shell import AsyncShell
from ...toolset import ToolSet, tool
from ...utils.log import logger


class ShellToolSet(ToolSet):
    """Shell toolset for running shell commands.

    Args:
        name: The name of the toolset.
        worker_params: The parameters for the worker.
        **kwargs: Additional keyword arguments.
    """
    def __init__(
            self,
            name: str,
            worker_params: dict | None = None,
            **kwargs,
            ):
        super().__init__(name, worker_params, **kwargs)
        self.clientid_to_shellid = {}
        self.shells = {}

    @tool
    async def new_shell(self) -> dict:
        """Create a new shell and return its id.
        You can use `run_command_in_shell` to run command in the shell,
        by providing the shell id. """
        shell = AsyncShell()
        initial_output = await shell.start()
        shell_id = str(uuid.uuid4())
        self.shells[shell_id] = shell
        
        # Show shell creation status
        logger.info(f"[dim]New shell created: {shell_id[:8]}[/dim]")
        
        return {
            "shell_id": shell_id,
            "initial_output": initial_output,
        }

    @tool
    async def close_shell(self, shell_id: str):
        """Close a shell.

        Args:
            shell_id: The id of the shell to close.
        """
        shell = self.shells[shell_id]
        await shell.close()
        del self.shells[shell_id]
        
        # Show shell closure status
        logger.info(f"[dim]Shell closed: {shell_id[:8]}[/dim]")

    @tool
    async def run_command_in_shell(self, command: str, shell_id: str, timeout: int | None = None):
        """Run a command in a shell.

        Args:
            command: The command to run.
            shell_id: The id of the shell to run the command in.
            timeout: The timeout for the command to run. Use None for no timeout (long-running commands).

        Returns:
            The output of the command.
        """
        shell = self.shells[shell_id]
        output, finished = await shell.run_command(command, timeout=timeout)
        if timeout is not None and not finished:
            timeout_msg = f"Command timed out after {timeout}s - you can try get_shell_output for remaining output"
            logger.info(f"[yellow]⚠️ {timeout_msg}[/yellow]")
            output += f"\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_shell_output to get the remaining output of the shell."
        return output

    @tool
    async def get_shell_output(self, shell_id: str, timeout: int | None = None) -> str:
        """Get the output of a shell. Don't use this function unless you need to get the remaining output of an interrupted command.

        Args:
            shell_id: The id of the shell to get the output from.
            timeout: The timeout for the output to be returned. Use None for no timeout.
        """
        shell = self.shells[shell_id]
        output, finished = await shell.read_until_marker(timeout=timeout)
        if timeout is not None and not finished:
            timeout_msg = f"Reading shell output timed out after {timeout}s"
            logger.info(f"[yellow]⚠️ {timeout_msg}[/yellow]")
            output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_shell_output to get the remaining output of the shell."
        return output

    def _is_shell_alive(self, shell_id: str) -> bool:
        """Check if a shell is still alive and responsive."""
        if shell_id not in self.shells:
            return False
        
        shell = self.shells[shell_id]
        if not shell.process:
            return False
        
        # Check if process is still running
        if shell.process.returncode is not None:
            return False
        
        return True
    
    async def _restart_shell(self, client_id: str) -> str:
        """Restart a shell for a given client_id."""
        old_shell_id = self.clientid_to_shellid.get(client_id)
        
        # Clean up old shell if it exists
        if old_shell_id and old_shell_id in self.shells:
            try:
                await self.close_shell(old_shell_id)
            except Exception:
                pass  # Ignore cleanup errors
            finally:
                if old_shell_id in self.shells:
                    del self.shells[old_shell_id]
        
        # Create new shell
        logger.warning(f"Shell crashed (client_id: {client_id}), restarting...")
        res = await self.new_shell()
        new_shell_id = res["shell_id"]
        self.clientid_to_shellid[client_id] = new_shell_id
        logger.info(f"Shell restarted (client_id: {client_id})")
        
        return new_shell_id

    @tool
    async def run_command(
        self,
        command: str,
        timeout: int | None = None,
        context_variables: dict | None = None,
    ):
        """Run shell command and get the output.

        Args:
            command: The command to run.
            timeout: The timeout for the command to run. Use None for no timeout (long-running commands).
        """
        #context_variables: The context variables to use.
        #    "client_id" is used to identify the shell.
        #    If not provided will use the default client id.
        #    For each client id, a new shell will be created.
        #    NOTE: This context_variables is not visible to the agent.
        if context_variables is None:
            context_variables = {}
        client_id = context_variables.get("client_id")
        if client_id is None:
            client_id = "default"
            logger.warning("No client id provided, using default client id.")

        initial_output = ""
        shell_id = self.clientid_to_shellid.get(client_id)

        # Check if we need to create a new shell
        if (shell_id is None) or (shell_id not in self.shells):
            res = await self.new_shell()
            shell_id = res["shell_id"]
            initial_output = res["initial_output"]
            self.clientid_to_shellid[client_id] = shell_id
        
        # Check if shell is still alive before running command
        if not self._is_shell_alive(shell_id):
            shell_id = await self._restart_shell(client_id)
            initial_output = ""  # New shell will have its own initial output
        
        # Try to run command with automatic recovery on failure
        try:
            output = await self.run_command_in_shell(command, shell_id, timeout=timeout)
        except Exception as e:
            # Handle shell crashes and other failures
            error_msg = str(e).lower()
            if ("broken" in error_msg or "closed" in error_msg or 
                "process" in error_msg or "pipe" in error_msg or
                shell_id not in self.shells or not self._is_shell_alive(shell_id)):
                
                # Shell crashed, restart and retry
                logger.warning(f"Shell command failed: {e}")
                shell_id = await self._restart_shell(client_id)
                
                try:
                    output = await self.run_command_in_shell(command, shell_id, timeout=timeout)
                    logger.info("Command execution successful after shell restart")
                except Exception as retry_error:
                    logger.error(f"Command execution failed even after shell restart: {retry_error}")
                    raise retry_error
            else:
                # Re-raise non-shell-related exceptions
                raise e
        
        if initial_output:
            output = initial_output + "\n" + output
        return output

    async def run_setup(self):
        """Setup the toolset before running it."""
        logger.warning(
            "This ToolSet is not secure, it can be used to execute arbitrary code."
            " Please be careful when using it."
            " Highly recommend using it in a controlled environment like a docker container."
        )
