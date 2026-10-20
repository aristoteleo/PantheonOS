"""A tool call still running after the foreground time moves to the background;
background_task, the way to wait for such work, never does."""

import asyncio
import json

import pytest

from pantheon import agent as agent_module
from pantheon.agent import Agent
from pantheon.agent_tools import execute_tool_calls
from pantheonos.toolset import ToolSet, tool


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


class Disk(ToolSet):
    """A file and two commands that read it, with a log of what overlapped."""

    def __init__(self):
        super().__init__(name="file_manager")
        self.files, self.log, self.running = {}, [], 0

    async def _work(self, label):
        self.running += 1
        self.log.append((label, "start", self.running))
        await asyncio.sleep(.05)
        self.running -= 1
        self.log.append((label, "end", self.running))

    @tool
    async def write_file(self, file_path: str, content: str) -> dict:
        """Write a file.

        Args:
            file_path: Path.
            content: Text.
        """
        await self._work("write")
        self.files[file_path] = content
        return {"success": True}

    @tool
    async def run_command(self, command: str) -> str:
        """Run a command.

        Args:
            command: The command.
        """
        await self._work(command)
        return self.files.get("fib.py", "No such file or directory")


@pytest.mark.asyncio
async def test_a_turn_writes_a_file_before_the_calls_after_it_and_runs_the_rest_together():
    agent = Agent(name="a", instructions="", model="gpt-4o-mini")
    disk = Disk()
    await agent.toolset(disk)
    messages = await execute_tool_calls(agent, [
        call("file_manager__write_file", {"file_path": "fib.py", "content": "print(1)"}, "c1"),
        call("file_manager__run_command", {"command": "python3 fib.py"}, "c2"),
        call("file_manager__run_command", {"command": "cat fib.py"}, "c3"),
    ], {}, timeout=60, time_delta=.01)
    # The script was written before it ran, as the model listed them.
    assert [m["tool_call_id"] for m in messages] == ["c1", "c2", "c3"]
    assert all("print(1)" in str(m["content"]) for m in messages[1:])
    assert disk.log[:2] == [("write", "start", 1), ("write", "end", 0)]
    # The two commands still ran at the same time.
    assert max(running for _, _, running in disk.log) == 2


class Printing(ToolSet):
    """A command that prints a line, then runs until it is stopped."""

    def __init__(self):
        super().__init__(name="shell")
        self.printed = asyncio.Event()

    @tool
    async def run_loop(self, command: str, context_variables: dict = None) -> str:
        """Run a command for a long time.

        Args:
            command: The command.
        """
        (context_variables or {}).get("_report_output", lambda line: None)("1")
        self.printed.set()
        await asyncio.sleep(3600)
        return "never"


@pytest.mark.asyncio
async def test_a_stop_keeps_every_call_of_the_turn_with_what_it_did():
    from pantheon.agent_tools import NOT_RUN, ToolCallsStopped

    agent = Agent(name="a", instructions="", model="gpt-4o-mini")
    disk, printing = Disk(), Printing()
    await agent.toolset(disk)
    await agent.toolset(printing)
    stop = asyncio.Event()

    async def stop_once_printed():
        await printing.printed.wait()
        await asyncio.sleep(.1)
        stop.set()
    asyncio.create_task(stop_once_printed())
    with pytest.raises(ToolCallsStopped) as stopped:
        await execute_tool_calls(agent, [
            call("shell__run_loop", {"command": "for i in $(seq 1 40); do echo $i; sleep 1; done"}, "c1"),
            call("file_manager__run_command", {"command": "ls"}, "c2"),
            call("file_manager__write_file", {"file_path": "a.txt", "content": "x"}, "c3"),
        ], {}, timeout=60, time_delta=.02, check_stop=lambda *_: stop.is_set())
    loop, done, later = stopped.value.messages
    assert [m["tool_call_id"] for m in (loop, done, later)] == ["c1", "c2", "c3"]
    # The running call says it was stopped, after how long, and what it printed.
    assert loop["content"].startswith("Stopped by the user after 0 s")
    assert loop["content"].endswith("Output before the stop:\n1")
    # The call that finished keeps its result; the one after the stop never ran.
    assert "No such file" in str(done["content"])
    assert later["content"] == NOT_RUN and disk.files == {}


@pytest.mark.asyncio
async def test_a_stop_before_the_calls_start_runs_none_of_them():
    from pantheon.agent_tools import NOT_RUN, ToolCallsStopped

    agent = Agent(name="a", instructions="", model="gpt-4o-mini")
    disk = Disk()
    await agent.toolset(disk)
    with pytest.raises(ToolCallsStopped) as stopped:
        await execute_tool_calls(agent, [call("file_manager__write_file", {"file_path": "a", "content": "x"})],
                                 {}, timeout=60, check_stop=lambda *_: True)
    assert [m["content"] for m in stopped.value.messages] == [NOT_RUN] and disk.log == []
