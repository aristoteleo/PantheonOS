"""A program that calls exit() must not take the host process down.

With the in-process interpreter, SystemExit slipped past `except Exception` and propagated
through the agent into the evolution run (seen on the AlphaEvolve-suite batch A,
autocorr_first/hypbandit_s0). Code now runs on a Jupyter kernel, which contains it; this
pins that down: sys.exit() is reported as an ordinary failure, exit() is harmless, and the
session keeps its state.
"""
import pytest

from pantheon.toolsets.python.python_interpreter import PythonInterpreterToolSet


@pytest.mark.asyncio
async def test_exit_in_code_is_an_ordinary_error_and_the_session_survives():
    toolset = PythonInterpreterToolSet("python_interpreter_sysexit")
    sid = (await toolset.new_interpreter())["interpreter_id"]
    try:
        r = await toolset.run_python_code("kept = 41", interpreter_id=sid)
        assert r["success"]
        r = await toolset.run_python_code("import sys\nsys.exit(3)", interpreter_id=sid)
        assert not r["success"] and "SystemExit" in r["stderr"]
        await toolset.run_python_code("exit()", interpreter_id=sid)
        r = await toolset.run_python_code("kept + 1", interpreter_id=sid)
        assert r["success"] and r["result"] == "42", "namespace must survive an exit() call"
    finally:
        await toolset.delete_interpreter(sid)
