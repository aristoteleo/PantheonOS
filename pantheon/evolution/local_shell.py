"""A minimal in-process shell for evolution's worker agents.

Evolution mutates code inside its own worker process/sandbox and only ever
needed "run a command in this worktree". The shell App is a runner builtin
now (no Python class to embed), and placing a fleet instance per mutation
worker would be machinery for machinery's sake — a subprocess is the whole
requirement.
"""

from __future__ import annotations

import asyncio
import os

from pantheon.toolset import ToolSet, tool
from .lifetime import EvolutionCleanupError, join_cleanup, reap_process


class LocalShellToolSet(ToolSet):
    """Run commands in one working directory, in-process."""

    def __init__(self, name: str, workdir: str | None = None, **kwargs):
        super().__init__(name, **kwargs)
        self.workdir = workdir
        self._calls = set()
        self._closing = None

    async def cleanup(self):
        if self._closing is None:
            async def finish():
                tasks = tuple(self._calls)
                for task in tasks:
                    if not task.cancelling():
                        task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                errors = [result for result in results if isinstance(result, BaseException)
                          and not isinstance(result, asyncio.CancelledError)]
                if errors:
                    raise EvolutionCleanupError(errors) from errors[0]
            self._closing = asyncio.create_task(finish())
        await join_cleanup(self._closing)

    @tool
    async def run_command(self, command: str, timeout: int | None = None) -> dict:
        """Run a shell command in the worktree and return its output.

        Args:
            command: The command to run.
            timeout: Optional timeout in seconds.
        """
        if self._closing is not None:
            raise RuntimeError('Evolution shell is closed')
        task = asyncio.current_task()
        self._calls.add(task)
        spawn = asyncio.create_task(asyncio.create_subprocess_shell(
            command,
            cwd=self.workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == 'posix',
        ))
        communication = None
        try:
            proc = await asyncio.shield(spawn)
            communication = asyncio.create_task(proc.communicate())
            try:
                out, _ = await asyncio.wait_for(asyncio.shield(communication), timeout=timeout)
                status = 'completed'
            except asyncio.TimeoutError:
                status = 'timeout'
        finally:
            try:
                await join_cleanup(asyncio.create_task(reap_process(spawn, communication)))
            finally:
                self._calls.discard(task)
        if status == 'timeout':
            out, _ = communication.result()
        return {"success": True, "status": status,
                "output": (out or b"").decode(errors="replace")}
