"""A minimal in-process shell for evolution's worker agents.

Evolution mutates code inside its own worker process/sandbox and only ever
needed "run a command in this worktree". The shell App is a runner builtin
now (no Python class to embed), and placing a fleet instance per mutation
worker would be machinery for machinery's sake — a subprocess is the whole
requirement.
"""

from __future__ import annotations

import asyncio

from pantheon.toolset import ToolSet, tool


class LocalShellToolSet(ToolSet):
    """Run commands in one working directory, in-process."""

    def __init__(self, name: str, workdir: str | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self.workdir = workdir

    @tool
    async def run_command(self, command: str, timeout: int | None = None) -> dict:
        """Run a shell command in the worktree and return its output.

        Args:
            command: The command to run.
            timeout: Optional timeout in seconds.
        """
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=self.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            out, _ = await proc.communicate()
            return {"success": True, "status": "timeout",
                    "output": (out or b"").decode(errors="replace")}
        return {"success": True, "status": "completed",
                "output": (out or b"").decode(errors="replace")}
