import time
import asyncio
from executor.engine import Engine, LocalJob

from pantheon.toolset import ToolSet, tool
from pantheon.endpoint import ToolsetProxy


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
        # Use ToolsetProxy instead of remote_toolset
        proxy = ToolsetProxy.from_toolset(toolset.service_id)
        await agent.toolset(proxy)

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
        # Use ToolsetProxy instead of remote_toolset
        proxy = ToolsetProxy.from_toolset(toolset.service_id)
        await agent.toolset(proxy)

        resp = await agent.run("Call function `print_hello`")
        assert "TimeoutError" in resp.details.messages[1]["content"]

        await job.cancel()
        await engine.wait_async()
