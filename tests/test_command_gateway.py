"""The pantheon command, run by an agent, goes through that agent
(docs/pantheon-cli.md)."""
import json
import time
from types import SimpleNamespace

import pytest

from pantheon.agent import Agent
from pantheon.chatroom import command_gateway
from pantheon.chatroom.command_gateway import CommandGateway
from pantheon.toolset import ToolSet, tool


class Shell(ToolSet):
    def __init__(self):
        super().__init__(name="shell")
        self.seen = []

    @tool
    async def run_command(self, command: str, env: dict | None = None) -> dict:
        """Run a command.

        Args:
            command: The command.
            env: Set by the Agent.
        """
        self.seen.append(env)
        return {"success": True, "output": "ok"}


class Web(ToolSet):
    def __init__(self):
        super().__init__(name="web")

    @tool
    async def search(self, query: str) -> list:
        """Search.

        Args:
            query: Terms.
        """
        return ["hit " + query]


class Grant:
    async def _current(self):
        credential = SimpleNamespace(endpoint="https://agent.apps.example/rpc", key="k" * 64)
        return SimpleNamespace(_client=SimpleNamespace(credential=credential))


async def agent_with_gateway():
    agent = Agent(name="leader", instructions="Test", model="gpt-4o-mini")
    shell = Shell()
    await agent.toolset(shell)
    await agent.toolset(Web())
    agent.enable_core_tools()
    team = SimpleNamespace(agents=[agent])

    async def team_for_chat(chat_id):
        return team
    gateway = CommandGateway(lambda: Grant(), team_for_chat)
    agent.command_access = gateway
    return agent, shell, gateway


def test_a_token_is_bound_and_cannot_be_forged_or_outlive_its_time(monkeypatch):
    gateway = CommandGateway(lambda: None, None)
    token = gateway.mint(chat_id="c1", agent="leader", tool_call_id="t1")
    assert gateway.verify(token) == {"chat": "c1", "agent": "leader", "call": "t1", "exp": pytest.approx(time.time() + command_gateway.TOKEN_SECONDS, abs=5)}
    body, signature = token.split(".")
    with pytest.raises(PermissionError):
        gateway.verify(body + "x." + signature)
    with pytest.raises(PermissionError):
        CommandGateway(lambda: None, None).verify(token)  # another Agent run's secret
    monkeypatch.setattr(command_gateway, "TOKEN_SECONDS", -1)
    with pytest.raises(PermissionError):
        gateway.verify(gateway.mint(chat_id="c1", agent="leader", tool_call_id="t1"))


@pytest.mark.asyncio
async def test_run_command_gets_the_access_only_while_it_runs():
    agent, shell, gateway = await agent_with_gateway()
    result = await agent.call_tool("shell__run_command", {"command": "pantheon apps list"},
                                   {"chat_id": "c1"}, "t1")
    assert result == {"success": True, "output": "ok"}
    env = shell.seen[-1]
    assert set(env) == {"PANTHEON_ENDPOINT", "PANTHEON_GRANT", "PANTHEON_TOKEN"}
    assert gateway.verify(env["PANTHEON_TOKEN"])["call"] == "t1" and not gateway._live
    # The model often names the conversation's own node explicitly.
    await agent.call_tool("shell__run_command", {"command": "pantheon nodes list", "node": ""}, {"chat_id": "c1"}, "t3")
    assert shell.seen[-1] and gateway.verify(shell.seen[-1]["PANTHEON_TOKEN"])["call"] == "t3"
    # The model never sees env.
    tools = {t["function"]["name"]: t["function"] for t in await agent.get_tools_for_llm()}
    assert "env" not in tools["shell__run_command"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_a_request_is_carried_out_as_the_agent_and_shown_under_the_command():
    agent, shell, gateway = await agent_with_gateway()
    steps = []

    async def show(message):
        steps.append(message)
    token = gateway.mint(chat_id="c1", agent="leader", tool_call_id="t1")
    gateway._live["t1"] = command_gateway.LiveCall(agent, "c1", "t1", SimpleNamespace(process_step_message=show))
    listed = await gateway.handle(token, {"op": "apps.list", "params": {}})
    assert [a["app"] for a in listed["apps"]] == ["shell", "web"]
    called = await gateway.handle(token, {"op": "apps.call", "params": {"app": "web", "tool": "search",
                                                                        "args": {"query": "cells"}}})
    assert called == ["hit cells"]
    call, result = steps
    assert call["parent_tool_call_id"] == result["parent_tool_call_id"] == "t1"
    assert call["tool_calls"][0]["function"]["name"] == "web__search"
    assert result["tool_call_id"] == call["tool_calls"][0]["id"] and "hit cells" in result["content"]
    # The agent's apps filter holds for its commands too.
    from pantheon.agent_pantheonos import AppFilter
    agent.app_filter = AppFilter(deny=["web"])
    denied = await gateway.handle(token, {"op": "apps.call", "params": {"app": "web", "tool": "search", "args": {}}})
    assert "Unknown App" in denied["error"]


class AskingShell(ToolSet):
    """A shell whose command ran `pantheon task notify --wait`."""

    def __init__(self, gateway):
        super().__init__(name="shell")
        self.gateway = gateway

    @tool
    async def run_command(self, command: str, env: dict | None = None) -> dict:
        """Run a command.

        Args:
            command: The command.
            env: Set by the Agent.
        """
        self.gateway._live[self.gateway.verify(env["PANTHEON_TOKEN"])["call"]].interrupt = True
        return {"success": True, "output": "asked"}


@pytest.mark.asyncio
async def test_a_command_that_asks_the_user_stops_the_run():
    agent = Agent(name="leader", instructions="Test", model="gpt-4o-mini")
    gateway = CommandGateway(lambda: Grant(), None)
    await agent.toolset(AskingShell(gateway))
    agent.enable_core_tools()
    agent.command_access = gateway
    result = await agent.call_tool("shell__run_command", {"command": "pantheon task notify hi --wait"},
                                   {"chat_id": "c1"}, "t2")
    assert result["interrupt"] is True and not gateway._live


def test_the_agent_declares_pantheon_command_for_its_commands():
    from pantheon.chatroom.package import source_manifest
    manifest = source_manifest("0.0.1")
    tools = {t["name"]: t for t in manifest["provides"]["tools"]}
    assert tools["pantheon_command"].get("hidden") is True
    assert {"name": "pantheon-command", "version": 1, "tools": ["pantheon_command"]} in manifest["provides"]["interfaces"]


def test_a_profile_of_service_methods_only_is_a_valid_binding():
    # The Agent's own pantheon_command profile has no model tools. A launch
    # that refused it left the Agent unable to start.
    from pantheon.factory.dependency_provisioner import DependencyInstanceProvisioner

    class Capability:
        async def bind(self, **kwargs):
            raise AssertionError('validation allocates nothing')
    command = {"name": "pantheon_command", "description": "x", "parameters": {
        "type": "object", "properties": {"token": {"type": "string"}, "request": {"type": "object"}},
        "required": ["token", "request"], "additionalProperties": False}}
    provisioner = DependencyInstanceProvisioner(Capability(), consumer={
        "node_id": "n", "instance_id": "a", "revision": "a" * 64, "generation": 1},
        profiles={"toolsets": {"pantheon": {"alias": "agent", "functions": [], "service_functions": [command]}},
                  "mcp_servers": {}})
    assert provisioner.toolset_names == () and provisioner.command_grant() is not None


@pytest.mark.asyncio
async def test_every_member_of_a_real_team_gets_the_command_access():
    # PantheonTeam.agents is a dict: iterating it gave names, and every chat
    # failed with "'str' object has no attribute 'command_access'".
    from pantheon.chatroom.command_gateway import members
    from pantheon.team.pantheon import PantheonTeam
    leader = Agent(name="Leader", instructions="x", model="gpt-4o-mini")
    worker = Agent(name="Worker", instructions="x", model="gpt-4o-mini", description="Sub")
    team = PantheonTeam([leader, worker], mode="agent")
    assert {a.name for a in members(team)} == {"Leader", "Worker"}
    gateway = CommandGateway(lambda: Grant(), None)

    async def team_for_chat(chat_id):
        return team
    gateway._team_for_chat = team_for_chat
    token = gateway.mint(chat_id="c1", agent="Worker", tool_call_id="t9")
    assert (await gateway._agent(gateway.verify(token))) is worker


@pytest.mark.asyncio
async def test_a_command_is_told_why_the_agent_cannot_give_it_access():
    class Refused:
        async def _current(self):
            raise RuntimeError("Dependency allocation unavailable")
    agent = Agent(name="leader", instructions="Test", model="gpt-4o-mini")
    gateway = CommandGateway(lambda: Refused(), None)
    env = await gateway.open(agent, {"chat_id": "c1"}, "t1")
    assert list(env) == ["PANTHEON_UNAVAILABLE"] and "allocation unavailable" in env["PANTHEON_UNAVAILABLE"]
    assert not gateway._live
