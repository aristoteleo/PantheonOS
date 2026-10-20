"""Core tool exposure: four tools, everything else the pantheon command;
history in the recorded form the UI renders, the model's view as commands
(docs/os/pantheon-cli.md)."""

import json

import pytest

from pantheon.agent import Agent
from pantheon.agent_core_tools import CORE_TOOLS, fold_for_model
from pantheon.agent_pantheonos import AppFilter
from pantheon.team.pantheon import PantheonTeam
from pantheonos.toolset import ToolSet, tool


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
async def test_core_agent_declares_four_tools_and_reaches_the_rest_through_pantheon():
    agent = await core_agent()
    from pantheonos.appkit.builtin.task import TaskToolSet
    await agent.toolset(TaskToolSet())
    tools = {t["function"]["name"]: t["function"] for t in await agent.get_tools_for_llm()}
    # Of the four, this agent has read_file; no pantheonos_*, task or background tools.
    assert set(tools) == {"file_manager__read_file"}
    assert "node" in tools["file_manager__read_file"]["parameters"]["properties"]
    # The pantheon command still carries App requests out with these functions.
    described = await agent.call_tool("pantheonos_describe_app", {"app": "web"})
    assert described["source"] == "deployment"
    assert [t["name"] for t in described["tools"]] == ["duckduckgo_search"]
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


def test_the_model_sees_older_calls_as_the_pantheon_commands_that_do_them():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            call("file_manager__read_file", {"file_path": "/a"}, "keep"),
            call("web__duckduckgo_search", {"query": "x"}, "fold"),
            call("call_agent", {"agent_name": "Researcher", "instruction": "find it"}, "sub"),
            call("task__task_boundary", {"task_name": "Survey", "mode": "PLANNING"}, "task"),
            call("call_app_tool", {"app": "web", "tool": "nope", "args": {}}, "legacy"),
        ]},
        {"role": "tool", "tool_call_id": "keep", "name": "file_manager__read_file", "content": "a"},
        {"role": "tool", "tool_call_id": "fold", "name": "web__duckduckgo_search", "tool_name": "web__duckduckgo_search", "content": "[]"},
        {"role": "tool", "tool_call_id": "sub", "name": "call_agent", "content": "found"},
        {"role": "tool", "tool_call_id": "task", "name": "task__task_boundary", "content": "{}"},
        {"role": "tool", "tool_call_id": "legacy", "name": "call_app_tool", "content": ""},
    ]
    view = fold_for_model(history, CORE_TOOLS)
    calls = view[1]["tool_calls"]
    assert calls[0]["function"]["name"] == "file_manager__read_file"
    commands = [json.loads(c["function"]["arguments"])["command"] for c in calls[1:]]
    assert {c["function"]["name"] for c in calls[1:]} == {"shell__run_command"}
    assert commands == [
        """pantheon apps call web duckduckgo_search --args '{"query":"x"}'""",
        "pantheon agent run Researcher 'find it'",
        """pantheon task update --args '{"task_name":"Survey","mode":"PLANNING"}'""",
        "pantheon apps call web nope",
    ]
    assert [m.get("name") for m in view[2:]] == ["file_manager__read_file"] + ["shell__run_command"] * 4
    assert view[3]["tool_name"] == "shell__run_command"
    # History is untouched, and folding twice gives byte-identical requests (cache).
    assert history[1]["tool_calls"][1]["function"]["name"] == "web__duckduckgo_search"
    assert json.dumps(fold_for_model(history, CORE_TOOLS)) == json.dumps(view)


def test_every_folded_command_is_one_the_pantheon_command_parses():
    import shlex
    from pantheon.agent_core_tools import as_command
    samples = {
        "pantheonos_start_app": {"app": "game-of-life", "node": "mac", "window": True},
        "pantheonos_manage_app": {"action": "set_version", "app": "web", "args": {"version": "1.2.0"}},
        "pantheonos_develop_app": {"action": "read_file", "app": "demo", "args": {"path": "app.json"}},
        "background_task": {"action": "wait", "task_id": "bg_1", "timeout": 30},
        "task__notify_user": {"message": "Pick one: 'a' or \"b\"", "blocked_on_user": True},
        "task__register_output": {"path": "/w/report.md", "title": "Report"},
        "skills__skill_view": {"name": "omics/qc", "file_path": "references/x.md"},
        "skills__skill_manage": {"action": "search_store", "options": {"query": "single cell"}},
        "desktop__browser_open": {"url": "https://example.com"},
        "fleet__run_on_node": {"node_id": "mac", "code": "ls ~ | wc -l"},
        "fleet__transfer": {"src_node": "a", "src_path": "/x y", "dst_node": "b", "dst_path": "/z"},
    }
    expected = {
        "pantheonos_start_app": ["pantheon", "apps", "start", "game-of-life", "--node", "mac", "--window"],
        "background_task": ["pantheon", "jobs", "wait", "bg_1"],
        "skills__skill_view": ["pantheon", "skills", "view", "omics/qc", "references/x.md"],
        "fleet__run_on_node": ["pantheon", "run", "--node", "mac", "--", "ls ~ | wc -l"],
        "fleet__transfer": ["pantheon", "transfer", "a:/x y", "b:/z"],
    }
    for name, args in samples.items():
        words = shlex.split(as_command(name, args))
        assert words[0] == "pantheon", name
        if name in expected:
            assert words == expected[name], (name, words)
        if "--args" in words:
            json.loads(words[words.index("--args") + 1])
    notify = shlex.split(as_command("task__notify_user", samples["task__notify_user"]))
    assert notify[:4] == ["pantheon", "task", "notify", "--wait"]
    assert json.loads(notify[-1])["message"] == samples["task__notify_user"]["message"]


@pytest.mark.asyncio
async def test_agent_mode_team_starts_sub_agents_with_the_pantheon_command():
    leader = await core_agent()
    worker = Agent(name="Worker", instructions="Sub", model="gpt-4o-mini", description="General-purpose sub-agent.")
    researcher = Agent(name="Researcher", instructions="Sub", model="gpt-4o-mini", description="Information gathering.\nMore.")
    team = PantheonTeam([leader, worker, researcher], mode="agent")
    await team.async_setup()
    assert "call_agent" in leader.functions
    assert not any(name.startswith(("transfer_to", "list_agents")) for name in leader.functions)
    assert "call_agent" not in worker.functions and "call_agent" not in researcher.functions
    tools = {t["function"]["name"] for t in await leader.get_tools_for_llm()}
    assert "call_agent" not in tools  # `pantheon agent run`
    assert "`pantheon agent run <profile> <task>`" in leader.instructions
    assert "- Worker: General-purpose sub-agent." in leader.instructions
    assert "- Researcher: Information gathering. More." in leader.instructions


class Shell(ToolSet):
    def __init__(self):
        super().__init__(name="shell")

    @tool
    async def run_command(self, command: str) -> dict:
        """Run a command.

        Args:
            command: The command.
        """
        return {"output": command}


@pytest.mark.asyncio
async def test_every_core_agent_has_run_command_whatever_its_apps_filter():
    # A researcher allowed only web and files had no run_command, so no
    # pantheon command: it answered "no web tool" without searching.
    agent = await core_agent()
    await agent.toolset(Shell())
    agent.app_filter = AppFilter(allow=["web"])
    assert {t["function"]["name"] for t in await agent.get_tools_for_llm()} == {"shell__run_command"}
    # An agent that declares every tool keeps the Shell out unless allowed.
    legacy = Agent(name="old", instructions="Test", model="gpt-4o-mini")
    await legacy.toolset(Shell())
    await legacy.toolset(Web())
    legacy.app_filter = AppFilter(allow=["web"])
    assert not any(t["function"]["name"].startswith("shell__") for t in await legacy.get_tools_for_llm())
