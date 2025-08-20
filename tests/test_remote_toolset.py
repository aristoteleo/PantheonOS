import time
import asyncio
from executor.engine import Engine, LocalJob

from pantheon.toolsets.utils.toolset import ToolSet, tool


async def test_agent_call_remote_toolset():
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
        await asyncio.sleep(2)
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


async def test_agent_call_remote_toolset_with_timeout():
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
        assert "TimeoutError" in resp.details.messages[1]["content"]

        await job.cancel()
        await engine.wait_async()
