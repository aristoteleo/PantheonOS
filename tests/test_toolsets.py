import sys

from pantheon.toolset import tool, ToolSet, run_toolsets
from pantheon.remote import connect_remote
from pantheon.toolsets.web import WebToolSet
from pantheon.toolsets.python.python_interpreter import PythonInterpreterToolSet
from pantheon.toolsets.r.r_interpreter import RInterpreterToolSet
from pantheon.toolsets.julia.julia_interpreter import JuliaInterpreterToolSet
from pantheon.toolsets.shell import ShellToolSet
from executor.engine import Engine, ProcessJob

import pytest


async def test_remote_toolset():
    class MyToolSet(ToolSet):
        @tool(job_type="thread")
        def my_tool(self):
            return "Hello, world!"

    my_toolset = MyToolSet("my_toolset")
    assert len(my_toolset..functions) == 1

    toolset = MyToolSet("my_toolset")
    with Engine() as engine:
        job = ProcessJob(toolset.run)
        engine.submit(job)
        await job.wait_until_status("running")
        s = await connect_remote(toolset.service_id)
        resp = await s.invoke("my_tool")
        assert resp == "Hello, world!"
        await job.cancel()


async def test_web_toolset():
    toolset = WebToolSet("web_browse")

    async def start_toolset():
        await toolset.run()

    with Engine() as engine:
        job = ProcessJob(start_toolset)
        engine.submit(job)
        await job.wait_until_status("running")
        s = await connect_remote(toolset.service_id)
        try:
            await s.invoke("duckduckgo_search", {"query": "Hello, world!"})
        except Exception as e:
            print(e)
        finally:
            await job.cancel()
            await engine.wait_async()


async def test_python_interpreter_toolset():
    toolset = PythonInterpreterToolSet("python_interpreter")

    async def start_toolset():
        await toolset.run()

    with Engine() as engine:
        job = ProcessJob(start_toolset)
        await engine.submit_async(job)
        await job.wait_until_status("running")
        s = await connect_remote(toolset.service_id)
        with pytest.raises(Exception):
            resp = await s.invoke("run_python_code", {"code": "xxxxx"})
        resp = await s.invoke(
            "run_python_code", {"code": "res = 1 + 1", "result_var_name": "res"}
        )
        assert resp["result"] == 2
        resp = await s.invoke("run_python_code", {"code": "", "result_var_name": "res"})
        assert resp["result"] == 2
        s = await connect_remote(toolset.service_id)
        resp = await s.invoke(
            "run_python_code", {"code": "res = 1 + 1", "result_var_name": "res"}
        )
        assert resp["result"] == 2
        await job.cancel()
        await engine.wait_async()


async def test_r_toolset():
    toolset = RInterpreterToolSet("r_interpreter")

    async with run_toolsets([toolset]):
        s = await connect_remote(toolset.service_id)
        await s.invoke("run_r_code", {"code": "a <- 1 + 1"})
        resp = await s.invoke("run_r_code", {"code": "a"})
        print(resp)
        assert resp["stdout"].strip() == "[1] 2"


async def test_julia_toolset():
    toolset = JuliaInterpreterToolSet("julia_interpreter")

    async with run_toolsets([toolset]):
        s = await connect_remote(toolset.service_id)

        # Test 1: Simple calculation
        resp = await s.invoke("run_julia_code", {"code": "2 + 3"})
        assert resp["stdout"].strip() == "5"

        # Test 2: Variable assignment and retrieval (using client_id for persistence)
        context = {"client_id": "test_client"}
        resp = await s.invoke(
            "run_julia_code",
            {"code": "x = 10; y = 20; x + y", "context_variables": context},
        )
        assert "30" in resp["stdout"]

        # Test 3: Multi-line function definition
        code = """
function multiply(a, b)
    return a * b
end
multiply(4, 5)
"""
        resp = await s.invoke("run_julia_code", {"code": code})
        assert "20" in resp["stdout"]

        # Test 4: Array operations
        resp = await s.invoke("run_julia_code", {"code": "[1, 2, 3, 4, 5]"})
        assert "Vector" in resp["stdout"] or "[1, 2, 3, 4, 5]" in resp["stdout"]

        # Test 5: Error handling
        resp = await s.invoke("run_julia_code", {"code": "undefined_variable"})
        assert "Error" in resp["stdout"] or "ERROR" in resp["stdout"]


async def test_julia_interpreter_management():
    """Test direct interpreter management for Julia"""
    toolset = JuliaInterpreterToolSet("julia_interpreter_mgmt")

    async with run_toolsets([toolset]):
        s = await connect_remote(toolset.service_id)

        # Create a new interpreter
        resp = await s.invoke("new_interpreter", {})
        interpreter_id = resp["interpreter_id"]
        assert interpreter_id is not None

        # Run code in the specific interpreter
        output = await s.invoke(
            "run_code_in_interpreter",
            {"code": "a = 100; b = 200; a + b", "interpreter_id": interpreter_id},
        )
        assert "300" in output

        # Variables should persist in the same interpreter
        output = await s.invoke(
            "run_code_in_interpreter",
            {
                "code": 'c = a * b; println("Result: ", c)',
                "interpreter_id": interpreter_id,
            },
        )
        assert "20000" in output

        # Test multiline code
        code = """
for i in 1:3
    println("Count: ", i)
end
"""
        output = await s.invoke(
            "run_code_in_interpreter", {"code": code, "interpreter_id": interpreter_id}
        )
        assert "Count: 1" in output
        assert "Count: 2" in output
        assert "Count: 3" in output

        # Delete the interpreter
        await s.invoke("delete_interpreter", {"interpreter_id": interpreter_id})


async def test_shell_toolset():
    toolset = ShellToolSet("shell")

    async with run_toolsets([toolset]):
        s = await connect_remote(toolset.service_id)
        if sys.platform.startswith("win"):
            command = "dir"
        else:
            command = "ls"
        await s.invoke("run_command", {"command": command})
        resp = await s.invoke("run_command", {"command": "echo 'Hello, world!'"})
        assert "Hello, world!" in resp
