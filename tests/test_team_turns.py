"""Team Chat commands of a Pantheon Agent turn (docs/apps/team-chat.md,
"Pantheon Agent turns: Team Chat tools"): `pantheon team` requests reach
Team Chat only through the conversation's running Team Chat turn, which
opened it, and only while that turn runs."""
import asyncio
from types import SimpleNamespace

import pytest

from pantheon.chatroom import command_gateway
from pantheon.chatroom.command_gateway import CommandGateway, LiveCall, TeamTurns

from test_command_gateway import agent_with_gateway


def live(chat_id="c1", lease="", call="t1"):
    return LiveCall(SimpleNamespace(name="leader"), chat_id, call, None, team_lease=lease)


POST = {"command": "post", "args": ["#general"], "opts": {}, "files": [], "text": "hello\n"}


async def serve_one(turns, chat_id, lease, code=0, text="posted"):
    """The turn's process: takes the next request and answers it."""
    requests = await turns.next(chat_id, lease, wait=5)
    for r in requests:
        turns.answer(chat_id, lease, r["id"], code, text)
    return requests


@pytest.mark.asyncio
async def test_a_request_of_the_turns_conversation_goes_to_the_turn_and_back():
    turns = TeamTurns()
    lease = turns.open("c1")
    server = asyncio.create_task(serve_one(turns, "c1", lease))
    out = await turns.request("c1", live(lease=lease), POST)
    assert out == {"code": 0, "output": "posted"}
    (handed,) = await server
    assert {k: handed[k] for k in ("command", "args", "text")} == {"command": "post", "args": ["#general"],
                                                                   "text": "hello\n"}


@pytest.mark.asyncio
async def test_a_plain_chat_or_another_conversation_is_refused():
    turns = TeamTurns()
    # No Team Chat turn in this conversation at all (a plain Agent chat).
    with pytest.raises(PermissionError, match="running Team Chat turn started"):
        await turns.request("plain", live("plain"), POST)
    lease = turns.open("c1")
    # Another conversation of the owner's Agent, while c1's turn runs.
    with pytest.raises(PermissionError, match="running Team Chat turn started"):
        await turns.request("other", live("other", lease=lease), POST)
    # A command of c1's conversation that started before the turn (no lease),
    # or under an earlier turn's lease.
    with pytest.raises(PermissionError, match="not part of the running Team Chat turn"):
        await turns.request("c1", live(lease=""), POST)
    old = lease
    lease = turns.open("c1")
    with pytest.raises(PermissionError, match="not part of the running Team Chat turn"):
        await turns.request("c1", live(lease=old), POST)
    # The command's call ended (a background command later).
    with pytest.raises(PermissionError, match="not part of the running Team Chat turn"):
        await turns.request("c1", None, POST)
    assert await turns.next("c1", lease) == []


@pytest.mark.asyncio
async def test_after_the_turn_ends_nothing_is_answered():
    turns = TeamTurns()
    lease = turns.open("c1")
    waiting = asyncio.create_task(turns.request("c1", live(lease=lease), POST))
    await asyncio.sleep(0.05)
    turns.close("c1", lease)
    assert await waiting == {"code": 1, "output": command_gateway.TEAM_ENDED}
    with pytest.raises(PermissionError, match="running Team Chat turn started"):
        await turns.request("c1", live(lease=lease), POST)
    with pytest.raises(PermissionError):
        await turns.next("c1", lease)


@pytest.mark.asyncio
async def test_a_turn_nobody_polls_ends(monkeypatch):
    turns = TeamTurns()
    lease = turns.open("c1")
    monkeypatch.setattr(command_gateway, "TEAM_LEASE_IDLE", 0)
    with pytest.raises(PermissionError, match="running Team Chat turn started"):
        await turns.request("c1", live(lease=lease), POST)


@pytest.mark.asyncio
async def test_a_request_is_handed_out_and_answered_once():
    turns = TeamTurns()
    lease = turns.open("c1")
    waiting = asyncio.create_task(turns.request("c1", live(lease=lease), POST))
    (r,) = await turns.next("c1", lease, wait=5)
    assert await turns.next("c1", lease) == []  # handed out once
    turns.answer("c1", lease, r["id"], 0, "ok")
    assert (await waiting)["output"] == "ok"
    with pytest.raises(ValueError, match="answered already"):
        turns.answer("c1", lease, r["id"], 0, "again")
    with pytest.raises(ValueError, match="No request"):
        turns.answer("c1", lease, "made-up", 0, "x")
    with pytest.raises(PermissionError):
        turns.answer("c1", "not-the-lease", r["id"], 0, "x")


@pytest.mark.asyncio
async def test_words_are_checked_before_the_turn_sees_them():
    turns = TeamTurns()
    lease = turns.open("c1")
    for bad in ({"command": "Post"}, {"command": "post", "args": ["#g\nfile: /etc/passwd"]},
                {"command": "post", "opts": {"file": "/x"}}, {"command": "post", "files": ["rel.txt"]},
                {"command": "post", "opts": {"in": "a\nb"}}):
        with pytest.raises(ValueError):
            await turns.request("c1", live(lease=lease), bad)
    assert await turns.next("c1", lease) == []


@pytest.mark.asyncio
async def test_a_run_command_records_its_turns_lease_and_pantheon_team_goes_through_it():
    agent, shell, gateway = await agent_with_gateway()
    # Before any turn: the command has no lease, and is refused.
    await agent.call_tool("shell__run_command", {"command": "pantheon team inbox"}, {"chat_id": "c1"}, "t0")
    token = shell.seen[-1]["PANTHEON_TOKEN"]
    with pytest.raises(PermissionError):
        await gateway.handle(token, {"op": "team", "params": {"command": "inbox"}})
    lease = gateway.team.open("c1")

    seen = {}


    async def run_command(command: str, env: dict | None = None) -> dict:
        server = asyncio.create_task(serve_one(gateway.team, "c1", lease, 0, "Messages in #general: 3"))
        seen["out"] = await gateway.handle(env["PANTHEON_TOKEN"], {"op": "team", "params": {
            "command": "read", "args": ["#general"]}})
        await server
        return {"success": True, "output": "ok"}
    gateway._live.clear()
    env = await gateway.open(agent, {"chat_id": "c1"}, "t1")
    assert gateway._live["t1"].team_lease == lease
    await run_command("pantheon team read '#general'", env)
    gateway.close("t1")
    assert seen["out"] == {"code": 0, "output": "Messages in #general: 3"}
    # The same token after its command ended: refused.
    with pytest.raises(PermissionError, match="not part of the running Team Chat turn"):
        await gateway.handle(env["PANTHEON_TOKEN"], {"op": "team", "params": {"command": "inbox"}})


def test_the_agent_app_has_the_turn_methods_but_does_not_offer_them_to_other_apps():
    from pantheon.chatroom.native import NativeAgentApplication
    for name in ("team_turn_open", "team_turn_next", "team_turn_answer", "team_turn_close"):
        assert callable(getattr(NativeAgentApplication, name))
    from pantheon.chatroom.package import source_manifest
    manifest = source_manifest("0.0.1")
    provided = {t["name"] for t in manifest["provides"]["tools"]}
    assert not provided & {"team_turn_open", "team_turn_next", "team_turn_answer", "team_turn_close"}
