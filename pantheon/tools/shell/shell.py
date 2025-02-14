import uuid

from ._shell import AsyncShell
from ...remote.toolset import ToolSet, tool
from ...utils.log import logger


class ShellToolSet(ToolSet):
    def __init__(
            self,
            name: str,
            worker_params: dict | None = None,
            ):
        super().__init__(name, worker_params)
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

    @tool
    async def run_command_in_shell(self, command: str, shell_id: str, timeout: int = 10):
        """Run a command in a shell.

        Args:
            command: The command to run.
            shell_id: The id of the shell to run the command in.
            timeout: The timeout for the command to run.

        Returns:
            The output of the command.
        """
        shell = self.shells[shell_id]
        output, finished = await shell.run_command(command, timeout=timeout)
        if not finished:
            output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_shell_output to get the remaining output of the shell."
        return output

    @tool
    async def get_shell_output(self, shell_id: str, timeout: int = 10) -> str:
        """Get the output of a shell. Don't use this function unless you need to get the remaining output of an interrupted command.

        Args:
            shell_id: The id of the shell to get the output from.
            timeout: The timeout for the output to be returned.
        """
        shell = self.shells[shell_id]
        output, finished = await shell.read_until_marker(timeout=timeout)
        if not finished:
            output += "\n[Warning] The execution of the command was interrupted because of the timeout. "
            output += "You can try to run get_shell_output to get the remaining output of the shell."
        return output

    @tool
    async def run_command(self, command: str, timeout: int = 10, __client_id__: str | None = None):
        """Run shell command and get the output.

        Args:
            command: The command to run.
            timeout: The timeout for the command to run.
        """
        initial_output = ""
        if __client_id__ is not None:
            shell_id = self.clientid_to_shellid.get(__client_id__)

            if (shell_id is None) or (shell_id not in self.shells):
                res = await self.new_shell()
                shell_id = res["shell_id"]
                initial_output = res["initial_output"]
                self.clientid_to_shellid[__client_id__] = shell_id
        else:
            res = await self.new_shell()
            shell_id = res["shell_id"]
            initial_output = res["initial_output"]
        output = await self.run_command_in_shell(command, shell_id, timeout=timeout)
        if __client_id__ is None:
            await self.close_shell(shell_id)
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
