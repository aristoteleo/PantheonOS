"""Core tool exposure: a fixed small tool list, every App through the
pantheonos_* tools, history in the expanded form the UI renders
(docs/agent-core.md, docs/pantheonos-app-interface.md)."""

import json

import pytest

from pantheon.agent import Agent
from pantheon.agent_core_tools import CORE_TOOLS, PANTHEONOS_TOOLS, expand_call, fold_for_model
from pantheon.agent_pantheonos import AppFilter
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
    assert set(tools) == {"file_manager__read_file", "background_task", *PANTHEONOS_TOOLS}
    index = tools["pantheonos_call_app"]["description"]
    assert "- file_manager (Files on the workspace: read, write, search, images): grep" in index
    assert "- web (Web search and pages): duckduckgo_search" in index
    assert "read_file" not in index.split("Apps and their tools:")[1]  # declared directly, not on demand

    described = await agent.call_tool("pantheonos_describe_app", {"app": "web"})
    assert described["source"] == "deployment"
    assert [t["name"] for t in described["tools"]] == ["duckduckgo_search"]
    assert "query" in described["tools"][0]["parameters"]["properties"]
    assert "apps" in await agent.call_tool("pantheonos_describe_app", {"app": "nope"})
    listed = await agent.call_tool("pantheonos_list_apps", {})
    assert [a["app"] for a in listed["apps"]] == ["file_manager", "web"]
    assert listed["apps"][1]["running"].startswith("always")
    assert [a["app"] for a in (await agent.call_tool("pantheonos_list_apps", {"query": "pages"}))["apps"]] == ["web"]


@pytest.mark.asyncio
async def test_pantheonos_call_app_is_recorded_as_the_named_tool_and_runs_it():
    agent = await core_agent()
    message = {"role": "assistant", "content": "", "tool_calls": [
        call("pantheonos_call_app", {"app": "web", "tool": "duckduckgo_search", "args": {"query": "cells"}, "_background": False}),
        call("pantheonos_call_app", {"app": "web", "tool": "missing", "args": {}}, "c2"),
        call("call_app_tool", {"app": "web", "tool": "duckduckgo_search", "args": {"query": "old"}}, "c3"),
    ]}
    await agent._expand_app_tool_calls(message)
    first, second, legacy = message["tool_calls"]
    # The UI and result handling see the real tool and its own arguments.
    assert first["function"]["name"] == "web__duckduckgo_search" == legacy["function"]["name"]
    assert json.loads(first["function"]["arguments"]) == {"query": "cells"}
    assert await agent.call_tool(first["function"]["name"], json.loads(first["function"]["arguments"])) == [{"title": "hit for cells"}]
    # An unknown tool stays a pantheonos_call_app call, which answers with what exists.
    assert second["function"]["name"] == "pantheonos_call_app"
    answer = await agent.call_tool("pantheonos_call_app", json.loads(second["function"]["arguments"]))
    assert answer["tools"] == ["duckduckgo_search"]


@pytest.mark.asyncio
async def test_deployment_apps_are_neither_stopped_nor_managed_by_the_agent():
    agent = await core_agent()
    assert "deployment" in (await agent.call_tool("pantheonos_stop_app", {"app": "web"}))["error"]
    assert (await agent.call_tool("pantheonos_start_app", {"app": "web"}))["running"] is True
    assert "deployment" in (await agent.call_tool("pantheonos_manage_app", {"action": "uninstall", "app": "web"}))["error"]


@pytest.mark.asyncio
async def test_the_apps_filter_hides_and_refuses_what_it_excludes():
    agent = await core_agent()
    agent.app_filter = AppFilter(deny=["web"])
    tools = {t["function"]["name"]: t["function"] for t in await agent.get_tools_for_llm()}
    assert "- web" not in tools["pantheonos_call_app"]["description"]
    assert "Unknown App" in (await agent.call_tool("pantheonos_describe_app", {"app": "web"}))["error"]
    message = {"role": "assistant", "content": "", "tool_calls": [
        call("pantheonos_call_app", {"app": "web", "tool": "duckduckgo_search", "args": {"query": "x"}})]}
    await agent._expand_app_tool_calls(message)
    assert message["tool_calls"][0]["function"]["name"] == "pantheonos_call_app"
    # A directly declared App tool goes with its App.
    agent.app_filter = AppFilter(allow=["web"])
    tools = {t["function"]["name"] for t in await agent.get_tools_for_llm()}
    assert "file_manager__read_file" not in tools
    assert [a["app"] for a in (await agent.call_tool("pantheonos_list_apps", {}))["apps"]] == ["web"]


def test_an_apps_field_is_checked_and_names_match_with_either_separator():
    assert AppFilter.check({"allow": ["web", "game-of-life", "web"]}) == {"allow": ["game-of-life", "web"]}
    for bad in ({"only": []}, {"allow": "web"}, {"deny": ["../x"]}, []):
        with pytest.raises(ValueError):
            AppFilter.check(bad)
    flt = AppFilter.from_config({"allow": ["game-of-life"], "deny": []})
    assert flt.allows("game_of_life") and not flt.allows("web")
    assert AppFilter.from_config(None) is None and AppFilter.from_config({}).allows("anything")


def test_built_in_tools_are_declared_with_short_descriptions_not_listed_as_apps():
    from pantheon.agent_core_tools import BUILTIN_DESCRIPTIONS, BUILTIN_PROVIDERS
    assert {"task__task_boundary", "task__notify_user", "skills__skill_view", "skills__skill_manage"} <= CORE_TOOLS
    assert {name.split("__")[0] for name in BUILTIN_DESCRIPTIONS} == BUILTIN_PROVIDERS
    assert all(len(text) < 1100 for text in BUILTIN_DESCRIPTIONS.values())


@pytest.mark.asyncio
async def test_built_in_tools_are_declared_compactly():
    from pantheon.apps.builtin.task import TaskToolSet
    agent = await core_agent()
    await agent.toolset(TaskToolSet())
    tools = {t["function"]["name"]: t["function"] for t in await agent.get_tools_for_llm()}
    notify = tools["task__notify_user"]
    assert notify["description"].startswith("Message the user during a task")
    assert "_background" not in notify["parameters"]["properties"]
    assert not any("description" in spec for spec in notify["parameters"]["properties"].values())
    assert "_background" not in notify["parameters"].get("required", [])
    assert len(json.dumps(notify)) < 1600


def test_the_model_sees_its_own_pantheonos_call_app_form_again():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            call("file_manager__read_file", {"file_path": "/a"}, "keep"),
            call("web__duckduckgo_search", {"query": "x"}, "fold"),
            call("list_agents", {}, "own"),
            call("call_app_tool", {"app": "web", "tool": "nope", "args": {}}, "legacy"),
        ]},
        {"role": "tool", "tool_call_id": "keep", "name": "file_manager__read_file", "content": "a"},
        {"role": "tool", "tool_call_id": "fold", "name": "web__duckduckgo_search", "tool_name": "web__duckduckgo_search", "content": "[]"},
        {"role": "tool", "tool_call_id": "own", "name": "list_agents", "content": ""},
        {"role": "tool", "tool_call_id": "legacy", "name": "call_app_tool", "content": ""},
    ]
    view = fold_for_model(history, CORE_TOOLS)
    calls = view[1]["tool_calls"]
    assert calls[0]["function"]["name"] == "file_manager__read_file"
    assert calls[1]["function"]["name"] == "pantheonos_call_app"
    assert json.loads(calls[1]["function"]["arguments"]) == {"app": "web", "tool": "duckduckgo_search", "args": {"query": "x"}}
    assert json.loads(calls[2]["function"]["arguments"])["app"] == "agent"
    assert calls[3]["function"] == {"name": "pantheonos_call_app",
                                    "arguments": history[1]["tool_calls"][3]["function"]["arguments"]}
    assert [m.get("name") for m in view[2:]] == ["file_manager__read_file"] + ["pantheonos_call_app"] * 3
    assert view[3]["tool_name"] == "pantheonos_call_app"
    # History is untouched, and folding twice gives byte-identical requests (cache).
    assert history[1]["tool_calls"][1]["function"]["name"] == "web__duckduckgo_search"
    assert json.dumps(fold_for_model(history, CORE_TOOLS)) == json.dumps(view)
    # Expanding the folded call yields the recorded call again.
    function = dict(calls[1]["function"])
    assert expand_call(function, lambda app, tool: f"{app}__{tool}")
    assert function == history[1]["tool_calls"][1]["function"]


@pytest.mark.asyncio
async def test_agent_mode_team_delegates_with_one_call():
    leader = await core_agent()
    worker = Agent(name="Worker", instructions="Sub", model="gpt-4o-mini", description="General-purpose sub-agent.")
    researcher = Agent(name="Researcher", instructions="Sub", model="gpt-4o-mini", description="Information gathering.\nMore.")
    team = PantheonTeam([leader, worker, researcher], mode="agent")
    await team.async_setup()
    assert "call_agent" in leader.functions
    assert not any(name.startswith(("transfer_to", "list_agents")) for name in leader.functions)
    assert "call_agent" not in worker.functions and "call_agent" not in researcher.functions
    tools = {t["function"]["name"]: t["function"] for t in await leader.get_tools_for_llm()}
    assert "call_agent" in tools
    assert "- Worker: General-purpose sub-agent." in tools["call_agent"]["description"]
    assert "- Researcher: Information gathering. More." in tools["call_agent"]["description"]
