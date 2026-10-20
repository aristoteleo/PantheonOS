"""A sub-agent's own process (docs/os/pantheon-cli.md, "Sub-agents are processes").

The same Agent loop as an in-process sub-agent, in a process of its own: a
sub-agent that grows or fails takes only itself down, and the Agent App's
event loop does not carry its turns. It holds no credentials. Everything that
needs the model or a tool is asked of the Agent App over stdin and stdout,
one JSON object per line:

* from the Agent App: ``start`` (the profile, the task, the run's settings),
  ``reply`` (the answer to one ``call``), ``steer`` (messages the user sent
  this sub-agent, read at its next turn) and ``stop``;
* to the Agent App: ``call`` (``complete``: a model call; ``tool``: a tool
  call; ``compact``: a compaction record), ``step`` (a step message, which the
  Agent App relays to the conversation) and finally ``done`` or ``failed``,
  with the ``unread`` messages it ended without reading.

Run as ``python -m pantheon.team.subagent_child``; logs go to stderr.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


class Inbox:
    """Messages the user sent this sub-agent while it runs: the loop reads it
    as the chatroom's queue (the Agent's ``_steer_queue``)."""

    def __init__(self):
        self._messages: list[dict] = []

    def put(self, messages: list[dict]) -> None:
        self._messages.extend(m for m in messages if isinstance(m, dict))

    def drain(self) -> list[dict]:
        messages, self._messages = self._messages, []
        return messages

    def __len__(self) -> int:
        return len(self._messages)


class Link:
    """The protocol over this process's stdin and stdout."""

    def __init__(self, reader: asyncio.StreamReader, write=None):
        self._reader = reader
        self._write = write or self._stdout
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self.stopped = False
        self.inbox = Inbox()
        self.started: asyncio.Future = asyncio.get_running_loop().create_future()

    @staticmethod
    def _stdout(data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def send(self, message: dict) -> None:
        self._write((json.dumps(message, ensure_ascii=False, default=str) + "\n").encode())

    async def call(self, method: str, params: dict) -> Any:
        if self.stopped:
            raise RuntimeError("The sub-agent was stopped")
        self._next += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next] = future
        self.send({"type": "call", "id": self._next, "method": method, "params": params})
        reply = await future
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error") or "The Agent App could not answer"))
        return reply.get("result")

    async def pump(self) -> None:
        """Read the Agent App's messages until it closes stdin."""
        while True:
            line = await self._reader.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "start" and not self.started.done():
                self.started.set_result(message)
            elif kind == "reply":
                future = self._pending.pop(message.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(message)
            elif kind == "steer":
                self.inbox.put(message.get("messages") or [])
            elif kind == "stop":
                self.stopped = True
        self.stopped = True
        for future in self._pending.values():
            if not future.done():
                future.set_result({"ok": False, "error": "The Agent App closed the sub-agent"})
        if not self.started.done():
            self.started.set_exception(RuntimeError("No start message"))


def child_agent(link: Link, start: dict):
    """The sub-agent: its loop runs here; model, tools and compaction are the
    Agent App's."""
    from pantheon.agent import Agent

    class ChildAgent(Agent):
        async def _acompletion_with_models(self, history, tool_use=True, response_format=None,
                                           process_chunk=None, model=None, context_variables=None):
            return await link.call("complete", {"history": history, "tool_use": tool_use, "model": model})

        async def call_tool(self, prefixed_name, args, context_variables=None, tool_call_id=None):
            if prefixed_name == "file_manager__read_file":
                # Large results this process saved are read here.
                from pantheon.agent_tools import read_tool_output
                saved = read_tool_output(self, args)
                if saved is not None:
                    return saved
            return await link.call("tool", {"name": prefixed_name, "args": args, "tool_call_id": tool_call_id})

        async def _compact_history(self, history, model, *, force=False):
            return await link.call("compact", {"history": history, "model": model, "force": force})

    models = [m for m in start.get("models") or [] if isinstance(m, str)]
    agent = ChildAgent(name=start["name"], instructions=start.get("instructions") or "",
                       model=models[0] if models else None, description=start.get("description") or "")
    if models:
        agent.models = models
    # Images are expanded by the Agent App, which reads them, per model call.
    agent._image_resolver = object()
    agent.foreground_only = True
    agent._steer_queue = link.inbox
    return agent


async def main() -> int:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=256 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    link = Link(reader)
    pump = asyncio.create_task(link.pump())
    try:
        start = await link.started
    except RuntimeError:
        return 2
    try:
        from pantheon.internal.memory import Memory
        agent = child_agent(link, start)

        async def step(message: dict) -> None:
            link.send({"type": "step", "message": message})
        response = await agent.run(
            start["task"],
            memory=Memory(name=f"{start['name']}-{start.get('execution_context_id') or 'child'}"),
            use_memory=False,
            update_memory=True,
            process_step_message=step,
            check_stop=lambda *_: link.stopped,
            execution_context_id=start.get("execution_context_id"),
            context_variables=start.get("context_variables") or {},
            max_turns=start.get("max_turns") or float("inf"),
            model=start.get("model"),
        )
        link.send({"type": "done", "content": response.content if response else "",
                   "unread": link.inbox.drain()})
        return 0
    except BaseException as exc:  # noqa: BLE001 - reported, then the process ends
        link.send({"type": "failed", "error": f"{type(exc).__name__}: {exc}"[:2000],
                   "unread": link.inbox.drain()})
        return 1
    finally:
        pump.cancel()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
