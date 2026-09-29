"""A hard-bounded Python runner: one self-contained script per call, in a fresh process.

PythonInterpreterToolSet is a stateful session that inherits the host's environment and, on a tool
timeout, is adopted into the background rather than stopped. For running code an LLM wrote to try an
idea out -- a prototype timed against a budget -- the opposite is wanted: a fresh process per call, a
wall-clock limit that kills the whole process group, a memory ceiling, a scrubbed environment (no
credentials) and a private temporary directory. That is this toolset.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
from typing import Any, Dict, Optional, Sequence

from pantheon.toolset import ToolSet, tool

_KEEP_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
             "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


def _tail(text: str, n: int) -> str:
    return text if len(text) <= n else "[... " + str(len(text) - n) + " characters cut ...]\n" + text[-n:]


class SandboxPythonToolSet(ToolSet):
    """Run a self-contained Python script in a fresh process with hard limits."""

    def __init__(self, name: str, python: Optional[str] = None, default_timeout_s: int = 120,
                 max_timeout_s: int = 300, memory_mb: int = 24000, output_chars: int = 6000,
                 keep_env: Sequence[str] = _KEEP_ENV, **kwargs):
        super().__init__(name, **kwargs)
        self.python = python or sys.executable
        self.default_timeout_s = int(default_timeout_s)
        self.max_timeout_s = int(max_timeout_s)
        self.memory_mb = int(memory_mb)
        self.output_chars = int(output_chars)
        self.keep_env = tuple(keep_env)
        self.log: list = []

    def _limits(self):          # runs in the child between fork and exec
        os.setsid()
        try:
            import resource
            cap = self.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except Exception:  # noqa: BLE001 -- not supported on this platform (macOS): no memory cap
            pass

    @tool
    async def run_python_script(self, code: str, timeout_s: int = 120) -> Dict[str, Any]:
        """
        Run a self-contained Python script in a fresh process and return its output. numpy, scipy and
        scikit-learn are available. Nothing is kept between calls (write the whole script each time);
        the process is killed when the time limit is reached. Print what you want to see.

        Args:
            code: The complete Python script
            timeout_s: Wall-clock limit in seconds (default 120, at most 300)

        Returns:
            dict with exit_code, wall_seconds, timed_out, stdout and stderr (tails)
        """
        limit = max(1, min(int(timeout_s or self.default_timeout_s), self.max_timeout_s))
        tmp = tempfile.mkdtemp(prefix="pantheon-sandbox-")
        path = os.path.join(tmp, "script.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        env = {k: os.environ[k] for k in self.keep_env if k in os.environ}
        env.update({"HOME": tmp, "TMPDIR": tmp, "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg"})
        t0 = time.time()
        timed_out = False
        try:
            proc = await asyncio.create_subprocess_exec(
                self.python, path, cwd=tmp, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, preexec_fn=self._limits)
            # the pipes are drained by their own tasks, so output printed before a kill is kept
            # (a cancelled communicate() discards what it had buffered)
            chunks: Dict[str, list] = {"out": [], "err": []}

            async def _drain(stream, key):
                while True:
                    b = await stream.read(65536)
                    if not b:
                        return
                    chunks[key].append(b)
            readers = [asyncio.create_task(_drain(proc.stdout, "out")),
                       asyncio.create_task(_drain(proc.stderr, "err"))]
            try:
                await asyncio.wait_for(proc.wait(), timeout=limit)
            except asyncio.TimeoutError:
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001 -- already gone
                    proc.kill()
                await proc.wait()
            try:
                await asyncio.wait_for(asyncio.gather(*readers), timeout=10)
            except asyncio.TimeoutError:
                for t in readers:
                    t.cancel()
            out, err = b"".join(chunks["out"]), b"".join(chunks["err"])
            result = {"exit_code": proc.returncode, "wall_seconds": round(time.time() - t0, 2),
                      "timed_out": timed_out,
                      "stdout": _tail(out.decode("utf-8", "replace"), self.output_chars),
                      "stderr": _tail(err.decode("utf-8", "replace"), self.output_chars // 2)}
        except Exception as e:  # noqa: BLE001
            result = {"exit_code": None, "wall_seconds": round(time.time() - t0, 2),
                      "timed_out": False, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.log.append({"wall_seconds": result["wall_seconds"], "exit_code": result["exit_code"],
                         "timed_out": result["timed_out"], "chars": len(code)})
        return result


__all__ = ["SandboxPythonToolSet"]
