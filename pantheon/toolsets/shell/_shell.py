import abc
import asyncio
import sys
import os
import uuid


class AsyncCommandLineInterpreter(abc.ABC):
    def __init__(
            self,
            executable: str,
            args: list[str] | None = None,
            marker: str = "__COMMAND_FINISHED__",
        ):
        self.executable = executable
        self.args = args or []
        self.marker = marker
        self.process = None
        self.encoding = "utf-8"
        self.env = None

    async def start(self) -> str:
        """Starts the command line interpreter process.
        
        Returns:
            The initial output from the command line interpreter.
        """
        self.process = await asyncio.create_subprocess_exec(
            self.executable, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self.env,
        )
        return await self._drain_initial_output()

    async def _drain_initial_output(self):
        """Drains any initial output (banner/prompt) and waits for the process to be ready."""
        await asyncio.sleep(0.2)
        output_lines = []
        while True:
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout=1.0)
            except:
                break
            if not line:
                break
            line = line.decode(self.encoding)
            output_lines.append(line)
        return "".join(output_lines)

    async def read_until_marker(
            self,
            marker: str | None = None,
            timeout: float | None = None,
            ) -> tuple[str, bool]:
        """Reads output from the command line interpreter until a marker is found.
        

        Args:
            marker: The marker to wait for. If None, the default marker will be used.
            timeout: The timeout for the operation. Use None for no timeout.

        Returns:
            A tuple containing the output from the command line interpreter up to the marker
            and a boolean indicating whether the marker was found.
        """
        if marker is None:
            marker = self.marker
        output_lines = []
        loop = asyncio.get_running_loop()
        start_time = loop.time()
        finished = False

        while True:
            if timeout is not None:
                remaining = timeout - (loop.time() - start_time)
                if remaining <= 0:
                    output_lines.append("\n[Warning] Timeout waiting for marker.")
                    break
                try:
                    line = await asyncio.wait_for(self.process.stdout.readline(), timeout=remaining)    
                except:
                    output_lines.append("\n[Warning] Timeout waiting for marker.")
                    break
            else:
                # No timeout - wait indefinitely
                line = await self.process.stdout.readline()
            if not line:
                break
            line = line.decode(self.encoding)
            if self.stop_on_line(line, marker):
                finished = True
                break
            if self.filter_out_line(line, marker):
                continue
            output_lines.append(line)
        return "".join(output_lines), finished

    def stop_on_line(self, line: str, marker: str) -> bool:
        if marker in line:
            return True
        return False

    def filter_out_line(self, line: str, marker: str) -> bool:
        if marker in line:
            return True
        # Filter out heredoc artifacts 
        stripped_line = line.strip()
        
        # Pattern: "DELIMITER < /dev/null" (common heredoc artifact)
        if " < /dev/null" in stripped_line:
            return True
            
        # Pattern: single word lines that are likely heredoc delimiters (PY, EOF, etc)
        # But only if they're uppercase and short
        if (len(stripped_line.split()) == 1 and 
            stripped_line.isupper() and 
            len(stripped_line) <= 10 and
            stripped_line.isalpha()):
            return True
            
        # Don't filter Python code lines - they should be part of the output or errors
        # Remove the overly aggressive filtering
        return False

    # Allow use as an async context manager.
    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


class AsyncShell(AsyncCommandLineInterpreter):
    def __init__(
            self,
            shell: str | None = None,
            shell_args: list[str] | None = None,
            marker: str = "__COMMAND_FINISHED__",
            ):
        self.is_windows = sys.platform.startswith("win")
        # Get shell path from environment or use defaults
        default_shell = self._get_default_shell()
        super().__init__(
            executable=shell or default_shell,
            args=shell_args or (["/Q"] if self.is_windows else []),
            marker=marker,
        )
        self.cmd_separator = " & " if self.is_windows else "; "
        # Adjust environment if needed.
        if not self.is_windows:
            self.env = os.environ.copy()
            self.env["PS1"] = ""
        else:
            self.env = None
    
    def _get_default_shell(self) -> str:
        """Get default shell path with environment variable support"""
        if self.is_windows:
            return "cmd.exe"
        
        # Check environment variables first
        shell_from_env = os.environ.get('SHELL')
        if shell_from_env and os.path.exists(shell_from_env):
            return shell_from_env
            
        # Check common shell paths
        common_shells = ['/bin/bash', '/usr/bin/bash', '/bin/zsh', '/usr/bin/zsh', '/bin/sh']
        for shell_path in common_shells:
            if os.path.exists(shell_path):
                return shell_path
        
        # Fallback to bash
        return "/bin/bash"

    async def run_command(self, command: str, timeout: float | None = None) -> tuple[str, bool]:
        """
        Sends a command to the shell (appending a unique marker) and returns all output
        up to the marker.
        
        Returns:
            A tuple containing (output, finished) where finished indicates if the marker was found.
        """
        marker = f"__COMMAND_END_{uuid.uuid4().hex}__"
        
        # Handle heredoc and multi-line commands properly
        if "<<" in command:
            # For heredoc, the command structure should be complete already
            # Just add the marker command after the heredoc command
            full_command = f"{command}\necho {marker}\n"
        else:
            # For regular commands, append the marker with proper separator
            full_command = f"{command}{self.cmd_separator}echo {marker}\n"
        
        self.process.stdin.write(full_command.encode(self.encoding))
        await self.process.stdin.drain()
        return await self.read_until_marker(marker, timeout)

    async def close(self):
        """Closes the shell process gracefully."""
        if self.process:
            exit_command = "exit\n"
            self.process.stdin.write(exit_command.encode(self.encoding))
            await self.process.stdin.drain()
            await self.process.wait()


# Example usage:
async def main():
    shell = AsyncShell()
    initial_output = await shell.start()
    print("Initial output:")
    print(initial_output)

    output, finished = await shell.run_command("echo Hello, world!")
    print("Output of echo command:")
    print(output)

    # Use "dir" on Windows and "ls -l" on Unix-like systems.
    dir_command = "dir" if sys.platform.startswith("win") else "ls -l"
    output, finished = await shell.run_command(dir_command)
    print("Directory listing:")
    print(output)

    # Run a wrong command.
    wrong_command = "wrong_command"
    output, finished = await shell.run_command(wrong_command)
    print("Output of wrong command:")
    print(output)

    await shell.close()


if __name__ == '__main__':
    asyncio.run(main())
