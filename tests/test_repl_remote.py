"""The terminal chat as a client of the Agent App (docs/os/pantheon-cli.md)."""
import asyncio
import base64
import gzip
import io
import json
import tarfile

import pytest

from pantheon.repl.remote import RemoteChatRoom, RemoteMemory


def fragments(event: dict, sequence: int, size: int = 40):
    raw = json.dumps(event)
    parts = [raw[i:i + size] for i in range(0, len(raw), size)]
    return [{"sequence": sequence + i, "event_id": f"e{sequence}", "part": i, "total": len(parts),
             "json_fragment": part} for i, part in enumerate(parts)]


class FakeRPC:
    def __init__(self, pages, *, hold=False):
        self.pages, self.calls, self.hold = list(pages), [], hold

    async def call(self, method, args=None, timeout=120):
        self.calls.append((method, args))
        if method == "get_agent_event_cursor":
            return {"cursor": {"epoch": "a", "sequence": 0}}
        if method == "read_agent_events":
            if self.pages:
                return self.pages.pop(0)
            return {"events": [], "cursor": args["cursor"], "has_more": False}
        if method == "chat":
            if self.hold:
                await asyncio.sleep(3600)
            return {"success": True}
        if method == "get_chat_messages":
            return {"messages": [{"id": "u1", "role": "user", "content": "hi"},
                                 {"id": "a1", "role": "assistant", "content": "Hello there"}]}
        return {"success": True}

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_a_turn_streams_from_the_event_stream_and_ends_at_chat_finished():
    chunk = {"type": "chunk", "data": {"chat_id": "c", "chunk": {"content": "Hello there, " * 5}}}
    step = {"type": "step", "data": {"chat_id": "c", "step_message": {"id": "a1", "role": "assistant", "content": "Hello there"}}}
    done = {"type": "chat_finished", "data": {"chat_id": "c"}}
    rpc = FakeRPC([
        {"events": fragments(chunk, 1)[:2], "cursor": {"epoch": "a", "sequence": 2}, "has_more": True},
        {"events": fragments(chunk, 1)[2:] + fragments(step, 20), "cursor": {"epoch": "a", "sequence": 30}, "has_more": False},
        {"events": fragments(done, 40), "cursor": {"epoch": "a", "sequence": 41}, "has_more": False},
    ])
    room = RemoteChatRoom(rpc)
    chunks, steps = [], []
    result = await room.chat("c", [{"role": "user", "content": "hi"}],
                             process_chunk=chunks.append, process_step_message=steps.append)
    assert result == {"success": True}
    assert chunks == [chunk["data"]["chunk"]] and steps == [step["data"]["step_message"]]
    # The history is the App's after the turn.
    memory = room.memory_manager.get_memory("c")
    assert [m["id"] for m in memory.get_messages(None, for_llm=False)] == ["u1", "a1"]
    assert memory.get_user_turns() == [(0, memory._messages[0])]
    reads = [args["cursor"]["sequence"] for method, args in rpc.calls if method == "read_agent_events"]
    assert reads[:3] == [0, 2, 30]


@pytest.mark.asyncio
async def test_interrupting_the_terminal_stops_the_agents_turn():
    rpc = FakeRPC([], hold=True)
    room = RemoteChatRoom(rpc)
    task = asyncio.create_task(room.chat("c", [{"role": "user", "content": "go"}]))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("stop_chat", {"chat_id": "c"}) in rpc.calls


@pytest.mark.asyncio
async def test_revert_goes_to_the_app_by_message_id():
    rpc = FakeRPC([])
    room = RemoteChatRoom(rpc)
    memory = RemoteMemory(room, "c", messages=[{"id": "u1", "role": "user"}, {"id": "a1", "role": "assistant"}])
    room._memories["c"] = memory
    await memory.revert_to_message(1)
    assert ("revert_to_message", {"chat_id": "c", "message_id": "a1"}) in rpc.calls


def test_the_terminal_frontend_is_the_release_package_read_in_chunks(tmp_path, monkeypatch):
    from pantheon.chatroom import terminal_frontend
    root = tmp_path / "agent"
    vendor = root / "backend" / "_vendor" / "pantheon"
    (vendor / "repl").mkdir(parents=True)
    (vendor / "__init__.py").write_text("")
    (vendor / "repl" / "remote.py").write_text("print('hi')")
    (vendor / "repl" / "__pycache__").mkdir()
    (vendor / "repl" / "__pycache__" / "remote.cpython-312.pyc").write_bytes(b"x")
    for name in ("terminal.py", "requirements.txt", "app.json"):
        (root / name).write_text(name)
    (root / "frontend").mkdir()
    (root / "frontend" / "index.js").write_text("big")
    monkeypatch.setattr(terminal_frontend, "package_root", lambda: root)
    terminal_frontend.bundle.cache_clear()
    monkeypatch.setattr(terminal_frontend, "CHUNK", 64)
    data, offset = b"", 0
    while True:
        part = terminal_frontend.chunk(offset)
        data += base64.b64decode(part["data"])
        offset += len(base64.b64decode(part["data"]))
        if offset >= part["size"]:
            break
    import hashlib
    assert hashlib.sha256(data).hexdigest() == part["sha256"]
    names = tarfile.open(fileobj=io.BytesIO(gzip.decompress(data))).getnames()
    assert {"terminal.py", "requirements.txt", "app.json", "backend/_vendor/pantheon/repl/remote.py"} <= set(names)
    assert not any("__pycache__" in n or n.startswith("frontend") for n in names)
    terminal_frontend.bundle.cache_clear()


@pytest.mark.asyncio
async def test_a_terminal_on_another_node_puts_its_conversations_there():
    rpc = FakeRPC([])
    async def call(method, args=None, timeout=120):
        rpc.calls.append((method, args))
        return {"chat_id": "c1"} if method == "create_chat" else {"success": True}
    rpc.call = call
    room = RemoteChatRoom(rpc)
    room.work_node, room.work_dir = "mac", "/Users/me/proj"
    assert (await room.create_chat("repl-session"))["chat_id"] == "c1"
    assert ("set_chat_node", {"chat_id": "c1", "node_id": "mac", "workdir": "/Users/me/proj"}) in rpc.calls
    # Without a node, conversations stay where the Agent puts them.
    rpc.calls.clear()
    room.work_node = ""
    await room.create_chat("repl-session")
    assert [m for m, _ in rpc.calls] == ["create_chat"]
