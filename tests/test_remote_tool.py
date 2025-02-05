import sys
import time

from pydantic import BaseModel
from magique.client import MagiqueError
from pantheon.remote import tool, ToolSet, connect_remote
from pantheon.tools.web_browse import WebBrowseToolSet
from pantheon.tools.python.python_interpreter import PythonInterpreterToolSet, PythonInterpreterError
from pantheon.tools.r import RInterpreterToolSet
from pantheon.tools.shell import ShellToolSet
from pantheon.remote import run_toolsets
from executor.engine import Engine, LocalJob, ProcessJob

import pytest


def test_remote_toolset():

    class MyToolSet(ToolSet):
        @tool(job_type="thread")
        def my_tool(self):
            return "Hello, world!"

    my_toolset = MyToolSet("my_toolset")
    assert len(my_toolset.worker.functions) == 1
    

async def test_web_browse_toolset():
    toolset = WebBrowseToolSet("web_browse")

    async def start_toolset():
        await toolset.run()

    with Engine() as engine:
        job = ProcessJob(start_toolset)
        engine.submit(job)
        await job.wait_until_status("running")
        s = await connect_remote(toolset.service_id)
        try:
            res = await s.invoke("duckduckgo_search", {"query": "Hello, world!"})
            assert len(res) > 0
            await job.cancel()
            await engine.wait_async()
        except MagiqueError as e:
            print(e)


async def test_agent_call_toolset():
    from pantheon.agent import Agent

    a = False

    class MyToolSet(ToolSet):
        @tool
        def print_hello(self):
            """Print hello"""
            nonlocal a
            a = True
            return "Hello, world!"

    toolset = MyToolSet("my_toolset")

    async def start_toolset():
        await toolset.run()

    with Engine() as engine:
        job = LocalJob(start_toolset)
        await engine.submit_async(job)
        await job.wait_until_status("running")

        agent = Agent(
            "test",
            "You are an asistant, help me test my code",
        )
        await agent.remote_toolset(toolset.service_id)

        resp = await agent.run("Call function `print_hello`")
        print(resp.content)
        assert a

        await job.cancel()
        await engine.wait_async()


async def test_agent_call_toolset_with_timeout():
    from pantheon.agent import Agent

    class MyToolSet(ToolSet):
        @tool
        def print_hello(self):
            """Print hello"""
            time.sleep(10)
            return "Hello, world!"

    toolset = MyToolSet("my_toolset")

    async def start_toolset():
        await toolset.run()

    with Engine() as engine:
        job = LocalJob(start_toolset)
        await engine.submit_async(job)
        await job.wait_until_status("running")

        agent = Agent(
            "test",
            "You are an asistant, help me test my code",
            tool_timeout=1,
        )
        await agent.remote_toolset(toolset.service_id)

        resp = await agent.run("Call function `print_hello`")
        assert "TimeoutError" in resp.details.messages[1]['content']

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
        with pytest.raises(MagiqueError):
            resp = await s.invoke("run_code", {"code": "xxxxx"})
        resp = await s.invoke("run_code", {"code": "res = 1 + 1", "result_var_name": "res"})
        assert resp["result"] == 2
        resp = await s.invoke("run_code", {"code": "", "result_var_name": "res"})
        assert resp["result"] == 2
        await job.cancel()
        await engine.wait_async()


async def test_r_toolset():
    toolset = RInterpreterToolSet("r_interpreter")

    async with run_toolsets([toolset]):
        s = await connect_remote(toolset.service_id)
        await s.invoke("run_code", {"code": "a <- 1 + 1"})
        resp = await s.invoke("run_code", {"code": "a"})
        assert resp.strip() == "[1] 2"


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
