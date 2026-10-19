"""The terminal chat as a client of an Agent App (docs/pantheon-cli.md).

``pantheon agent chat`` runs this module for the Agent the owner runs, with
``PANTHEON_AGENT_RPC`` (a loopback URL the pantheon command serves; it holds
the Agent App's grant) and ``PANTHEON_AGENT_RPC_TOKEN``. The REPL
(pantheon/repl/core.py) runs on :class:`RemoteChatRoom`: every chat-room call
it makes is an RPC of the Agent App, and its live output is the App's event
stream. Nothing of the Agent runs here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any, Callable

#: How often the event stream is read while a reply streams.
POLL_SECONDS = 0.25
_ALL = object()


class AgentRPC:
    """One Agent App method call, through the pantheon command's loopback."""

    def __init__(self, url: str, token: str):
        import httpx
        self.url, self._headers = url, {"Authorization": f"Bearer {token}"}
        self._client = httpx.AsyncClient(timeout=None)

    async def call(self, method: str, args: dict | None = None, timeout: float = 120) -> Any:
        response = await self._client.post(self.url, headers=self._headers, timeout=timeout + 30,
                                           json={"method": method, "args": args or {}, "timeout_s": int(timeout)})
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError(f"{method}: HTTP {response.status_code}") from None
        if response.status_code != 200 or not body.get("success"):
            raise RuntimeError(f"{method}: {body.get('error') or response.status_code}")
        return body.get("result")

    async def close(self) -> None:
        await self._client.aclose()


class RemoteMemory:
    """A conversation's history as the Agent App last returned it."""

    def __init__(self, room: "RemoteChatRoom", chat_id: str, name: str = "", messages: list | None = None):
        self._room, self.id, self.name = room, chat_id, name
        self._messages: list[dict] = list(messages or [])
        self.extra_data: dict = {}

    def get_messages(self, execution_context_id=_ALL, for_llm: bool = True) -> list[dict]:
        if execution_context_id is _ALL:
            return list(self._messages)
        return [m for m in self._messages if m.get("execution_context_id") == execution_context_id]

    def get_user_turns(self) -> list[tuple[int, dict]]:
        return [(i, m) for i, m in enumerate(self._messages) if m.get("role") == "user"]

    async def revert_to_message(self, index: int) -> None:
        message_id = self._messages[index].get("id") if 0 <= index < len(self._messages) else None
        if not message_id:
            raise ValueError("No such message")
        result = await self._room.rpc.call("revert_to_message", {"chat_id": self.id, "message_id": message_id})
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(result.get("message") or "Could not revert")
        await self._room.refresh(self.id)

    def delete_metadata(self, key: str) -> None:
        self.extra_data.pop(key, None)

    def save(self, *_args, **_kwargs) -> None:
        """The Agent App keeps the conversation."""


class _MemoryManager:
    def __init__(self, room: "RemoteChatRoom"):
        self._room = room

    def get_memory(self, chat_id: str, *_args, **_kwargs) -> RemoteMemory:
        memories = self._room._memories
        if chat_id not in memories:
            memories[chat_id] = RemoteMemory(self._room, chat_id)
        return memories[chat_id]


class RemoteAgent:
    """An agent of the conversation's team, as the Agent App describes it."""

    def __init__(self, info: dict):
        self.name = info.get("name") or "Agent"
        self.models = list(info.get("models") or ([info["model"]] if info.get("model") else []))
        self.icon = info.get("icon") or ""
        self.instructions = info.get("instructions") or ""
        self.description = info.get("description") or ""
        self.functions = {name: None for name in info.get("tools") or []}
        self.providers: dict = {}

    async def get_tools_for_llm(self) -> list:
        return []


class RemoteTeam:
    def __init__(self, agents: list[dict], active: str | None = None):
        self.agents = {a.name: a for a in (RemoteAgent(info) for info in agents)}
        self.team_agents = list(self.agents.values())
        self._active = active

    def get_active_agent(self, memory=None):
        return self.agents.get(self._active) or next(iter(self.agents.values()), None)


class RemoteChatRoom:
    """What the REPL calls on a chat room, as RPCs of the Agent App."""

    remote = True
    _endpoint = None

    def __init__(self, rpc: AgentRPC):
        self.rpc = rpc
        self._memories: dict[str, RemoteMemory] = {}
        self.memory_manager = _MemoryManager(self)
        self.chat_teams: dict = {}
        self.threads: dict = {}

    # ---- conversations ----

    async def run_setup(self) -> None:
        """The Agent App is already running."""

    def _settings(self):
        from pantheonos.settings import get_settings
        return get_settings()

    async def list_chats(self, project_name: str | None = None, **_kwargs) -> dict:
        return await self.rpc.call("list_chats", {"project_name": project_name} if project_name else {})

    async def create_chat(self, chat_name: str | None = None, **kwargs) -> dict:
        args = {k: v for k, v in kwargs.items() if v is not None}
        if chat_name:
            args["chat_name"] = chat_name
        return await self.rpc.call("create_chat", args)

    async def delete_chat(self, chat_id: str) -> dict:
        self._memories.pop(chat_id, None)
        return await self.rpc.call("delete_chat", {"chat_id": chat_id})

    async def refresh(self, chat_id: str) -> RemoteMemory:
        """Reload a conversation's history from the Agent App."""
        result = await self.rpc.call("get_chat_messages", {"chat_id": chat_id}, timeout=120)
        messages = result.get("messages", []) if isinstance(result, dict) else (result or [])
        memory = self.memory_manager.get_memory(chat_id)
        memory._messages = [m for m in messages if isinstance(m, dict)]
        if isinstance(result, dict) and result.get("name"):
            memory.name = result["name"]
        return memory

    async def get_team_for_chat(self, chat_id: str, *_args, **_kwargs) -> RemoteTeam:
        agents = await self.rpc.call("get_agents", {"chat_id": chat_id})
        active = None
        try:
            current = await self.rpc.call("get_active_agent", {"chat_id": chat_id})
            active = current.get("agent") if isinstance(current, dict) else None
        except RuntimeError:
            pass
        await self.refresh(chat_id)
        return RemoteTeam(agents.get("agents", []) if isinstance(agents, dict) else [], active)

    async def _passthrough(self, method: str, **args) -> Any:
        return await self.rpc.call(method, {k: v for k, v in args.items() if v is not None})

    async def setup_team_for_chat(self, chat_id: str, template_obj: dict) -> dict:
        return await self._passthrough("setup_team_for_chat", chat_id=chat_id, template_obj=template_obj)

    async def set_active_agent(self, chat_id: str, agent_name: str) -> dict:
        return await self._passthrough("set_active_agent", chat_id=chat_id, agent_name=agent_name)

    async def set_agent_model(self, chat_id: str, agent_name: str, model: str, **kwargs) -> dict:
        return await self._passthrough("set_agent_model", chat_id=chat_id, agent_name=agent_name, model=model, **kwargs)

    async def list_available_models(self) -> dict:
        return await self.rpc.call("list_available_models", {})

    async def get_chat_template(self, chat_id: str) -> dict:
        return await self.rpc.call("get_chat_template", {"chat_id": chat_id})

    async def validate_template(self, template: dict) -> dict:
        return await self.rpc.call("validate_template", {"template": template})

    async def list_template_files(self, file_type: str = "teams", view: str = "files") -> dict:
        return await self.rpc.call("list_template_files", {"file_type": file_type, "view": view})

    async def read_template_file(self, file_path: str, **kwargs) -> dict:
        return await self._passthrough("read_template_file", file_path=file_path, **kwargs)

    async def compress_chat(self, chat_id: str) -> dict:
        return await self.rpc.call("compress_chat", {"chat_id": chat_id}, timeout=600)

    async def stop_chat(self, chat_id: str) -> dict:
        return await self.rpc.call("stop_chat", {"chat_id": chat_id})

    async def cleanup(self) -> None:
        await self.rpc.close()

    # ---- a turn ----

    async def chat(self, chat_id: str, message: list[dict], context_variables: dict | None = None,
                   process_chunk: Callable | None = None, process_step_message: Callable | None = None) -> dict:
        """Send a message and stream the reply from the App's event stream.

        The call that runs the turn may outlast one request; the turn's end
        is its chat_finished event."""
        from pantheonos.utils.misc import run_func
        position = await self.rpc.call("get_agent_event_cursor", {"chat_id": chat_id})
        cursor = position.get("cursor") if isinstance(position, dict) else None
        args = {"chat_id": chat_id, "message": message}
        if context_variables:
            args["context_variables"] = context_variables
        turn = asyncio.create_task(self.rpc.call("chat", args, timeout=600))
        pending: dict | None = None
        status = None
        try:
            while True:
                page = await self.rpc.call("read_agent_events", {"chat_id": chat_id, "cursor": cursor, "limit": 128})
                if page.get("reset_required"):
                    # Events were dropped: the history has the turn so far.
                    cursor, pending = None, None
                    position = await self.rpc.call("get_agent_event_cursor", {"chat_id": chat_id})
                    cursor = position.get("cursor")
                for item in page.get("events", []):
                    if pending is None or pending["id"] != item["event_id"]:
                        pending = {"id": item["event_id"], "total": item["total"], "parts": []}
                    pending["parts"].append(item["json_fragment"])
                    if len(pending["parts"]) < pending["total"]:
                        continue
                    event = json.loads("".join(pending["parts"]))
                    pending = None
                    data = event.get("data") or {}
                    kind = event.get("type")
                    if kind == "chunk" and process_chunk is not None:
                        await run_func(process_chunk, data.get("chunk") or {})
                    elif kind == "step" and process_step_message is not None:
                        await run_func(process_step_message, data.get("step_message") or {})
                    elif kind == "chat_finished":
                        status = data.get("status") or "finished"
                cursor = page.get("cursor", cursor)
                if status is not None:
                    break
                if turn.done() and turn.exception() is not None and not _timed_out(turn.exception()):
                    raise turn.exception()
                if not page.get("has_more"):
                    await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            # The REPL was interrupted: stop the Agent's turn too.
            await asyncio.shield(self._stop_quietly(chat_id))
            turn.cancel()
            raise
        result: dict = {"success": True}
        if turn.done() and not turn.cancelled() and turn.exception() is None and isinstance(turn.result(), dict):
            result = turn.result()
        elif not turn.done():
            turn.cancel()  # the turn ended; only its long request is still open
        await self.refresh(chat_id)
        return result

    async def _stop_quietly(self, chat_id: str) -> None:
        try:
            await self.stop_chat(chat_id)
        except Exception:  # noqa: BLE001 - the run may have just ended
            pass


def select_resume(chats: list[dict], selection) -> str:
    """The conversation to continue: the latest, or one by number, id or name
    (as `pantheon cli --resume` chose)."""
    from datetime import datetime

    def activity(chat):
        try:
            return datetime.fromisoformat(chat.get("last_activity_date")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return float("-inf")
    rows = sorted(chats, key=activity, reverse=True)
    if not rows:
        raise SystemExit("No conversations to continue")
    if selection is True:
        return rows[0]["id"]
    value = str(selection)
    if value.isdigit() and 1 <= int(value) <= len(rows):
        return rows[int(value) - 1]["id"]
    for row in rows:
        if row["id"].startswith(value) or str(row.get("name") or "").lower().startswith(value.lower()):
            return row["id"]
    raise SystemExit("No such conversation")


def _timed_out(error: BaseException) -> bool:
    return "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower()


async def run(url: str, token: str, args: argparse.Namespace) -> None:
    from pantheonos.utils.log import set_level
    from .core import Repl
    set_level("CRITICAL")
    room = RemoteChatRoom(AgentRPC(url, token))
    chat_id = args.chat
    if args.resume is not None and chat_id is None:
        chats = await room.list_chats()
        chat_id = select_resume(chats.get("chats", []), args.resume)
    repl = Repl(chatroom=room, chat_id=chat_id)
    repl.resume_command = "pantheon agent chat --resume"
    await repl.run(message=args.message, once=args.message is not None, disable_logging=True,
                   log_level="CRITICAL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pantheon agent chat",
                                     description="Chat with your Pantheon Agent in the terminal.")
    parser.add_argument("-i", "--message", help="Send one message, print the answer and exit.")
    parser.add_argument("--chat", help="Continue this conversation (its id).")
    parser.add_argument("--resume", nargs="?", const=True,
                        help="Continue the latest conversation, or one by number, id or name.")
    args = parser.parse_args(argv)
    url, token = os.environ.get("PANTHEON_AGENT_RPC"), os.environ.get("PANTHEON_AGENT_RPC_TOKEN")
    if not url or not token:
        parser.error("run this with `pantheon agent chat`")
    asyncio.run(run(url, token, args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
