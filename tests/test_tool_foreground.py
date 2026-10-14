"""A tool call still running after the foreground time moves to the background;
background_task, the way to wait for such work, never does."""

import asyncio
import json

import pytest

from pantheon import agent as agent_module
from pantheon.agent import Agent
from pantheon.agent_tools import execute_tool_calls
from pantheon.toolset import ToolSet, tool


class Slow(ToolSet):
    def __init__(self):
        super().__init__(name="slow")
        self.release = asyncio.Event()

    @tool
    async def crawl(self, url: str) -> str:
        """Fetch a page slowly.

        Args:
            url: Address.
        """
        await self.release.wait()
        return "page " + url


def call(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def test_tool_calls_run_in_the_foreground_for_a_minute():
    assert Agent(name="a", instructions="", model="gpt-4o-mini").tool_timeout == 60
    assert Agent(name="a", instructions="", model="gpt-4o-mini", tool_timeout=5).tool_timeout == 5


@pytest.mark.asyncio
async def test_a_long_call_moves_to_the_background_and_finishes_there(monkeypatch):
    monkeypatch.setattr(agent_module, "TOOL_FOREGROUND_SECONDS", .2)
    agent = Agent(name="a", instructions="", model="gpt-4o-mini")
    slow = Slow()
    await agent.toolset(slow)
    [message] = await execute_tool_calls(agent, [call("slow__crawl", {"url": "x"})], {},
                                         timeout=agent.tool_timeout, time_delta=.05)
    assert "moved to background" in message["content"]
    [task] = agent._bg_manager.list_tasks()
    assert task.status == "running"
    slow.release.set()
    await asyncio.wait_for(task.asyncio_task, 2)
    assert task.status == "completed" and task.result == "page x"


@pytest.mark.asyncio
async def test_waiting_on_background_work_is_not_itself_backgrounded():
    agent = Agent(name="a", instructions="", model="gpt-4o-mini", tool_timeout=0)
    slow = Slow()
    await agent.toolset(slow)
    task = agent._bg_manager.start("slow__crawl", "bg-call", {}, slow.crawl(url="y"))
    asyncio.get_running_loop().call_later(.3, slow.release.set)
    [message] = await execute_tool_calls(
        agent, [call("background_task", {"action": "wait", "task_id": task.task_id, "timeout": 5})], {},
        timeout=agent.tool_timeout, time_delta=.05)
    assert "moved to background" not in message["content"]
    assert "completed" in message["content"]
    assert len(agent._bg_manager.list_tasks()) == 1
