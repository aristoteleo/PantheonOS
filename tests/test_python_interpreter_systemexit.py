"""A program that calls exit() must not take the host process down.

SystemExit is a BaseException, so it slipped past the interpreter loop's `except Exception`,
closed the generator, and propagated through the agent into the evolution run (seen on the
AlphaEvolve-suite batch A, autocorr_first/hypbandit_s0). It is now reported like any other
error, and the interpreter keeps its state.
"""
import pytest

from pantheon.toolsets.python.python_interpreter import PythonInterpreterError, PythonInterpreterToolSet


@pytest.mark.asyncio
async def test_exit_in_code_is_an_ordinary_error_and_the_interpreter_survives():
    toolset = PythonInterpreterToolSet("python_interpreter_sysexit")
    try:
        r = await toolset.run_python_code("kept = 41", result_var_name="kept")
        assert r["result"] == 41
        with pytest.raises(PythonInterpreterError, match="SystemExit"):
            await toolset.run_python_code("import sys\nsys.exit(3)")
        with pytest.raises(PythonInterpreterError, match="SystemExit"):
            await toolset.run_python_code("exit()")
        r = await toolset.run_python_code("res = kept + 1", result_var_name="res")
        assert r["result"] == 42, "namespace must survive an exit() call"
    finally:
        for pid in list(toolset.interpreters):
            await toolset.delete_interpreter(pid)
