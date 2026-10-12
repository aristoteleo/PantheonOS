"""Core tool exposure: a fixed small tool list, every other App tool on demand,
history in the expanded form the UI renders (docs/agent-core.md)."""

import json

import pytest

from pantheon.agent import Agent
from pantheon.agent_core_tools import CORE_TOOLS, expand_call, fold_for_model
from pantheon.team.pantheon import PantheonTeam
from pantheon.toolset import ToolSet, tool


class Files(ToolSet):
    def __init__(self):
        super().__init__(name="file_manager")

    @tool
    async def read_file(self, file_path: str) -> str:
        """Read a file.

        Args:
            file_path: Absolute path.
        """
        return "contents of " + file_path

    @tool
    async def grep(self, pattern: str) -> list:
        """Search files.

        Args:
            pattern: Regular expression.
        """
        return [pattern]


class Web(ToolSet):
    def __init__(self):
        super().__init__(name="web")

    @tool
    async def duckduckgo_search(self, query: str) -> list:
        """Search the web.

        Args:
            query: Search terms.
        """
        return [{"title": "hit for " + query}]


async def core_agent(name="leader"):
    agent = Agent(name=name, instructions="Test", model="gpt-4o-mini")
    await agent.toolset(Files())
    await agent.toolset(Web())
    agent.enable_core_tools()
    return agent


def call(name, arguments, call_id="c1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


@pytest.mark.asyncio
async def test_core_agent_declares_only_core_tools_and_indexes_the_rest():
    agent = await core_agent()
    tools = {t["function"]["name"]: t["function"] for t in await agent.get_tools_for_llm()}
    assert set(tools) == {"file_manager__read_file", "background_task", "describe_app", "call_app_tool"}
    index = tools["call_app_tool"]["description"]
    assert "- file_manager: grep" in index and "- web: duckduckgo_search" in index
    assert "read_file" not in index.split("Apps and their tools:")[1]  # declared directly, not on demand

    described = await agent.call_tool("describe_app", {"app": "web"})
    assert [t["name"] for t in described["tools"]] == ["duckduckgo_search"]
    assert "query" in described["tools"][0]["parameters"]["properties"]
    assert "apps" in await agent.call_tool("describe_app", {"app": "nope"})


@pytest.mark.asyncio
async def test_call_app_tool_is_recorded_as_the_named_tool_and_runs_it():
    agent = await core_agent()
    message = {"role": "assistant", "content": "", "tool_calls": [
        call("call_app_tool", {"app": "web", "tool": "duckduckgo_search", "args": {"query": "cells"}, "_background": False}),
        call("call_app_tool", {"app": "web", "tool": "missing", "args": {}}, "c2"),
    ]}
    await agent._expand_app_tool_calls(message)
    first, second = message["tool_calls"]
    # The UI and result handling see the real tool and its own arguments.
    assert first["function"]["name"] == "web__duckduckgo_search"
    assert json.loads(first["function"]["arguments"]) == {"query": "cells"}
    assert await agent.call_tool(first["function"]["name"], json.loads(first["function"]["arguments"])) == [{"title": "hit for cells"}]
    # An unknown tool stays a call_app_tool call, which answers with what exists.
    assert second["function"]["name"] == "call_app_tool"
    answer = await agent.call_tool("call_app_tool", json.loads(second["function"]["arguments"]))
    assert answer["tools"] == ["duckduckgo_search"]


def test_the_model_sees_its_own_call_app_tool_form_again():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            call("file_manager__read_file", {"file_path": "/a"}, "keep"),
            call("web__duckduckgo_search", {"query": "x"}, "fold"),
            call("list_agents", {}, "own"),
        ]},
        {"role": "tool", "tool_call_id": "keep", "name": "file_manager__read_file", "content": "a"},
        {"role": "tool", "tool_call_id": "fold", "name": "web__duckduckgo_search", "tool_name": "web__duckduckgo_search", "content": "[]"},
        {"role": "tool", "tool_call_id": "own", "name": "list_agents", "content": ""},
    ]
    view = fold_for_model(history, CORE_TOOLS)
    calls = view[1]["tool_calls"]
    assert calls[0]["function"]["name"] == "file_manager__read_file"
    assert calls[1]["function"]["name"] == "call_app_tool"
    assert json.loads(calls[1]["function"]["arguments"]) == {"app": "web", "tool": "duckduckgo_search", "args": {"query": "x"}}
    assert json.loads(calls[2]["function"]["arguments"])["app"] == "agent"
    assert [m.get("name") for m in view[2:]] == ["file_manager__read_file", "call_app_tool", "call_app_tool"]
    assert view[3]["tool_name"] == "call_app_tool"
    # History is untouched, and folding twice gives byte-identical requests (cache).
    assert history[1]["tool_calls"][1]["function"]["name"] == "web__duckduckgo_search"
    assert json.dumps(fold_for_model(history, CORE_TOOLS)) == json.dumps(view)

    # Expanding the folded call yields the recorded call again.
    function = dict(calls[1]["function"])
    assert expand_call(function, lambda app, tool: f"{app}__{tool}")
    assert function == history[1]["tool_calls"][1]["function"]


@pytest.mark.asyncio
async def test_agent_mode_team_delegates_with_one_call_and_no_transfer():
    leader = await core_agent()
    worker = Agent(name="Worker", instructions="Sub", model="gpt-4o-mini", description="General-purpose sub-agent.")
    researcher = Agent(name="Researcher", instructions="Sub", model="gpt-4o-mini", description="Information gathering.\nMore.")
    team = PantheonTeam([leader, worker, researcher], mode="agent", allow_transfer=True)
    await team.async_setup()
    assert "call_agent" in leader.functions
    assert not any(name.startswith(("transfer_to", "list_agents")) for name in leader.functions)
    assert "call_agent" not in worker.functions and "call_agent" not in researcher.functions
    tools = {t["function"]["name"]: t["function"] for t in await leader.get_tools_for_llm()}
    assert "call_agent" in tools
    assert "- Worker: General-purpose sub-agent." in tools["call_agent"]["description"]
    assert "- Researcher: Information gathering. More." in tools["call_agent"]["description"]
