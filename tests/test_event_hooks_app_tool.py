"""A streamed call_app_tool is shown as the tool it names, with its own args."""
import pytest

from pantheon.chatroom.event_hooks import ChatEventHooks


class Recorder(ChatEventHooks):
    def __init__(self):
        self.events = []

    async def publish(self, chat_id, message_type, data):
        self.events.append((message_type, data.get("tool_name"), data.get("delta")))


def call(arguments, name=None):
    function = {"arguments": arguments, **({"name": name} if name else {})}
    return {"tool_calls": [{"function": function}], "message_id": "m1"}


@pytest.mark.asyncio
async def test_call_app_tool_streams_under_the_named_tool():
    hooks = Recorder()
    chunk, _ = hooks.create_hooks("chat")
    await chunk(call('{"app": "web", "to', "call_app_tool"))
    for part in ('ol": "duckduckgo_search", "ar', 'gs": {"query": "ce', 'lls"}}'):
        await chunk(call(part))
    await chunk(call('{"command": "ls"}', "shell__run_command"))
    names = [name for _, name, _ in hooks.events]
    assert names == ["web__duckduckgo_search", "web__duckduckgo_search", "shell__run_command"]
    streamed = "".join(delta for _, name, delta in hooks.events if name == "web__duckduckgo_search")
    assert streamed.startswith('{"query": "cells"}')
    # An Agent function is shown under its own name.
    hooks.events.clear()
    await chunk(call('{"app": "agent", "tool": "list_agents", "args": {}}', "call_app_tool"))
    assert hooks.events[0][1] == "list_agents"
