"""A conversation's working node: core tools on another Fleet node go through
the Fleet App (docs/agent-core.md)."""

import json

import pytest

from pantheon.agent import Agent
from pantheon.agent_nodes import describe_node, route, summarize, with_note
from pantheon.toolset import ToolSet, tool


class Files(ToolSet):
    def __init__(self):
        super().__init__(name="file_manager")
        self.calls = []

    @tool
    async def read_file(self, file_path: str) -> dict:
        """Read a file.

        Args:
            file_path: Absolute path.
        """
        self.calls.append(file_path)
        return {"success": True, "content": "local"}


class FakeFleet:
    """The Fleet App as the Agent's provider sees it."""

    def __init__(self):
        self.files = {"/Users/me/notes.txt": "alpha\nbeta\nbeta\n"}
        self.calls = []

    async def list_tools(self):
        return []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "fleet_list_nodes":
            return {"success": True, "nodes": [
                {"node_id": "ws", "name": "workspace", "os": "linux", "shell": "posix", "status": "online",
                 "caps": ["fs:workspace", "proc"], "file_roots": []},
                {"node_id": "mac", "name": "Mac", "os": "darwin", "shell": "posix", "status": "online",
                 "caps": ["fs:local", "proc"], "file_roots": ["/Users/me"]},
                {"node_id": "pod", "name": "brain", "os": "linux", "shell": "posix", "status": "online",
                 "caps": [], "file_roots": []},
                {"node_id": "off", "name": "Laptop", "os": "windows", "shell": "powershell", "status": "offline",
                 "caps": ["fs:local", "proc"], "file_roots": ["C:/Users/me"]}]}
        if name == "node_call":
            method, a = args["method"], args["args"]
            if method == "read_file":
                if a["file_path"] not in self.files:
                    return {"success": False, "error": "not found", "node_id": args["node_id"]}
                return {"success": True, "content": self.files[a["file_path"]], "truncated": False, "node_id": args["node_id"]}
            if method == "write_file":
                self.files[a["file_path"]] = a["content"]
                return {"success": True, "node_id": args["node_id"]}
        if name == "run_on_node":
            return {"success": True, "exit_code": 0, "stdout": "Darwin\n", "stderr": ""}
        raise AssertionError(name)


async def agent_on(default="ws"):
    agent = Agent(name="leader", instructions="Test", model="gpt-4o-mini")
    await agent.toolset(Files())
    agent.providers["file_manager"].node_id = default
    agent.providers["fleet"] = FakeFleet()
    agent.enable_core_tools()
    return agent


@pytest.mark.asyncio
async def test_core_node_tools_take_an_optional_node():
    agent = await agent_on()
    tools = {t["function"]["name"]: t["function"] for t in await agent.get_tools_for_llm()}
    assert "node" in tools["file_manager__read_file"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_own_node_runs_as_usual_and_another_goes_through_fleet():
    agent = await agent_on()
    files = agent.providers["file_manager"].toolset
    # Own node (explicitly or by default): the Agent's own Files App.
    assert await agent.call_tool("file_manager__read_file", {"file_path": "/a", "node": "ws"}) == {"success": True, "content": "local"}
    assert await agent.call_tool("file_manager__read_file", {"file_path": "/b"}) == {"success": True, "content": "local"}
    # Named rather than given by id, it is still the Agent's own node.
    assert await agent.call_tool("file_manager__read_file", {"file_path": "/c", "node": "workspace"}) == {
        "success": True, "content": "local"}
    assert files.calls == ["/a", "/b", "/c"]
    # The conversation's node is another one: its file service, with node_id.
    read = await agent.call_tool("file_manager__read_file", {"file_path": "/Users/me/notes.txt"},
                                 context_variables={"chat_node": "mac"})
    assert read["content"].startswith("alpha") and read["node_id"] == "mac"
    fleet = agent.providers["fleet"]
    assert ("node_call", {"node_id": "mac", "method": "read_file", "args": {"file_path": "/Users/me/notes.txt"}}) in fleet.calls
    # A command there runs in the node's shared folder, output bounded.
    ran = await agent.call_tool("shell__run_command", {"command": "uname", "node": "Mac"})
    assert ran == {"success": True, "output": "Darwin\n", "exit_code": 0, "status": "completed", "node_id": "mac"}
    run = [a for n, a in fleet.calls if n == "run_on_node"][-1]
    assert run["cwd"] == "/Users/me" and run["max_output"] > 0


@pytest.mark.asyncio
async def test_update_on_another_node_replaces_exactly_once():
    agent = await agent_on()
    fleet = agent.providers["fleet"]
    edit = lambda **a: route(agent, "file_manager__update_file", {"file_path": "/Users/me/notes.txt", **a}, "mac")
    assert "occurs 2 times" in (await edit(old_string="beta", new_string="gamma"))["error"]
    assert "not found" in (await edit(old_string="delta", new_string="x"))["error"]
    done = await edit(old_string="alpha", new_string="ALPHA")
    assert done["success"] and done["replacements"] == 1
    assert fleet.files["/Users/me/notes.txt"] == "ALPHA\nbeta\nbeta\n"
    # Limited to a line range, or all occurrences.
    assert (await edit(old_string="beta", new_string="B", start_line=3, end_line=3))["success"]
    assert fleet.files["/Users/me/notes.txt"] == "ALPHA\nbeta\nB\n"
    assert (await edit(old_string="ALPHA\nbeta", new_string="x", replace_all=True))["replacements"] == 1


@pytest.mark.asyncio
async def test_unusable_nodes_answer_the_model():
    agent = await agent_on()
    assert "offline" in (await route(agent, "file_manager__read_file", {"file_path": "x"}, "off"))["error"]
    assert "not one of your nodes" in (await route(agent, "file_manager__read_file", {"file_path": "x"}, "nope"))["error"]
    assert "does not allow running" in (await route(agent, "shell__run_command", {"command": "ls"}, "pod"))["error"]
    del agent.providers["fleet"]
    assert "cannot reach" in (await route(agent, "file_manager__read_file", {"file_path": "x"}, "mac"))["error"]


def test_environment_note_reaches_only_the_model():
    note = describe_node({"node_id": "mac", "name": "Mac", "os": "darwin", "shell": "posix", "file_roots": ["/Users/me"]},
                         default=False)
    assert "Mac (mac): darwin, posix shell" in note and "/Users/me" in note and "fresh shell" in note
    message = [{"role": "user", "content": "hi"}]
    out = with_note(message, note)
    assert out[0]["content"] == "hi" and out[0]["_llm_content"].startswith("<environment>")
    assert message == [{"role": "user", "content": "hi"}]
    listed = summarize({"node_id": "pod", "caps": [], "file_roots": []}, "ws")
    assert listed["files"] is False and listed["commands"] is False
    assert summarize({"node_id": "ws"}, "ws")["default"] is True
    json.dumps(listed)


@pytest.mark.asyncio
async def test_a_sub_agent_works_on_the_conversations_node_through_the_leaders_fleet():
    from pantheon.agent_nodes import note_for
    from pantheon.team import PantheonTeam

    leader = await agent_on()
    worker = Agent(name="Worker", instructions="Sub", model="gpt-4o-mini", description="General-purpose sub-agent.")
    await worker.toolset(Files())
    worker.providers["file_manager"].node_id = "ws"
    worker.enable_core_tools()
    team = PantheonTeam([leader, worker], mode="agent")
    await team.async_setup()
    assert team.use_summary is False  # a sub-agent starts from its instruction alone
    fleet = leader.providers["fleet"]
    assert worker.node_fleet is fleet and "fleet" not in worker.providers
    read = await worker.call_tool("file_manager__read_file", {"file_path": "/Users/me/notes.txt"},
                                  context_variables={"chat_node": "mac"})
    assert read["content"].startswith("alpha") and read["node_id"] == "mac"
    note = await note_for(worker, "mac")
    assert "Mac (mac)" in note and "/Users/me" in note
    assert await note_for(worker, "ws") is None


@pytest.mark.asyncio
async def test_the_node_switcher_does_not_offer_the_agents_own_node():
    from types import SimpleNamespace
    from pantheon.chatroom.runtime import AgentRuntime
    from pantheon.internal.memory import Memory

    leader = await agent_on()
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._environment = SimpleNamespace(host_node="pod")
    memory = Memory("chat")

    async def team_for(chat_id):
        return SimpleNamespace(team_agents=[leader])
    runtime.get_team_for_chat = team_for
    runtime.memory_manager = SimpleNamespace(get_memory=lambda chat_id: memory)
    reply = await runtime.get_chat_node("chat")
    assert reply["success"] and [n["node_id"] for n in reply["nodes"]] == ["ws", "mac", "off"]
