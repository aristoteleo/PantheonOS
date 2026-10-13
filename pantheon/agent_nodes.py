"""A conversation's working node (docs/agent-core.md).

A core-tool Agent's file tools and run_command act on one Fleet node: the
conversation's node, by default the node its Files and Shell Apps run on (the
workspace). The conversation can switch to another of the owner's nodes, and
one call can name a node with ``node``. On another node the tools go through
the Fleet App: files through that node's file service (``node_call``),
commands through ``run_on_node`` (no shell session: each command starts fresh).
"""
from __future__ import annotations

import time
from typing import Any

#: Core tools that act on a node; they gain the optional ``node`` argument.
NODE_TOOLS = frozenset({
    "file_manager__read_file",
    "file_manager__write_file",
    "file_manager__update_file",
    "shell__run_command",
})

NODE_PARAM = {
    "type": "string",
    "description": "Fleet node id to act on. Empty: this conversation's node.",
}

#: Largest text a node's file service reads or writes in one call.
_MAX_TEXT = 256 * 1024
_RUN_OUTPUT = 30_000
_NODES_TTL = 30.0


def add_node_param(tool: dict) -> None:
    """Give one declared core tool the optional ``node`` argument."""
    parameters = tool["function"].setdefault("parameters", {"type": "object", "properties": {}})
    parameters.setdefault("properties", {})["node"] = dict(NODE_PARAM)
    required = parameters.get("required")
    # Strict schemas list every property as required; the empty string is the
    # conversation's node.
    if isinstance(required, list) and parameters.get("additionalProperties") is False and "node" not in required:
        required.append("node")


async def default_node(agent) -> str | None:
    """The node the Agent's own Files App runs on (the workspace)."""
    files = agent.providers.get("file_manager")
    bound = getattr(files, "bound_node", None)
    if bound is None:
        return getattr(files, "node_id", None)
    try:
        return await bound()
    except Exception:
        return None


def fleet_of(agent):
    """The Fleet App an Agent reaches other nodes through: its own, or (a
    sub-agent) the one it was lent by the agent that starts it."""
    return agent.providers.get("fleet") or getattr(agent, "node_fleet", None)


async def list_nodes(agent) -> list[dict]:
    """The owner's nodes (fleet_list_nodes), briefly cached on the Agent."""
    cached = getattr(agent, "_fleet_nodes_cache", None)
    if cached and time.monotonic() - cached[0] < _NODES_TTL:
        return cached[1]
    fleet = fleet_of(agent)
    if fleet is None:
        return []
    try:
        reply = await fleet.call_tool("fleet_list_nodes", {})
    except Exception:
        return []
    nodes = reply.get("nodes") if isinstance(reply, dict) else None
    nodes = [n for n in nodes or [] if isinstance(n, dict) and n.get("node_id")]
    agent._fleet_nodes_cache = (time.monotonic(), nodes)
    return nodes


def describe_node(node: dict, *, default: bool, workdir: str | None = None) -> str:
    """The environment note the model reads when its node is set or changes."""
    name = node.get("name") or node.get("node_id")
    os_name = node.get("os") or "unknown OS"
    shell = node.get("shell") or "posix"
    lines = [f"Your files and commands are on node {name} ({node.get('node_id')}): {os_name}, {shell} shell."]
    if default:
        if workdir:
            lines.append(f"Working directory: {workdir}.")
    else:
        roots = node.get("file_roots") or []
        if roots:
            lines.append("Files you can reach there: " + ", ".join(str(r) for r in roots) + ".")
        lines.append("Each run_command there starts a fresh shell: cd and variables do not carry over.")
    lines.append("Pass node to a file tool or run_command to act on another node once; "
                 "fleet_list_nodes (App fleet) lists the nodes.")
    return "<environment>\n" + "\n".join(lines) + "\n</environment>"


def _node(nodes: list[dict], node_id: str) -> dict | None:
    return next((n for n in nodes if n.get("node_id") == node_id or n.get("name") == node_id), None)


async def route(agent, name: str, args: dict, node_id: str) -> Any:
    """Run a core node tool on node_id, or return None to run it as usual
    (node_id is the Agent's own node)."""
    default = await default_node(agent)
    if not node_id or node_id == default:
        return None
    fleet = fleet_of(agent)
    if fleet is None:
        return {"success": False, "error": f"This agent cannot reach node {node_id}; it works on its own node only."}
    node = _node(await list_nodes(agent), node_id)
    if node is None:
        return {"success": False, "error": f"Node {node_id} is not one of your nodes. fleet_list_nodes lists them."}
    node_id = node["node_id"]
    if node.get("status") == "offline":
        return {"success": False, "error": f"Node {node.get('name') or node_id} is offline.", "node_id": node_id}
    if name == "shell__run_command":
        return await _run(fleet, node, args)
    return await _files(fleet, node, name.split("__", 1)[1], args)


async def _call_files(fleet, node_id: str, method: str, args: dict) -> dict:
    reply = await fleet.call_tool("node_call", {"node_id": node_id, "method": method, "args": args})
    return reply if isinstance(reply, dict) else {"success": False, "error": "Invalid reply", "node_id": node_id}


async def _files(fleet, node: dict, tool: str, args: dict) -> dict:
    node_id = node["node_id"]
    if tool == "read_file":
        keep = {k: args[k] for k in ("file_path", "start_line", "end_line", "max_chars") if args.get(k) not in (None, "")}
        return await _call_files(fleet, node_id, "read_file", keep)
    if tool == "write_file":
        keep = {k: args[k] for k in ("file_path", "content", "overwrite", "append") if k in args}
        if len((keep.get("content") or "").encode()) > _MAX_TEXT:
            return {"success": False, "error": "Content exceeds 256 KiB; write it in parts with append.", "node_id": node_id}
        return await _call_files(fleet, node_id, "write_file", keep)
    if tool == "update_file":
        return await _update(fleet, node_id, args)
    return {"success": False, "error": f"{tool} is not available on other nodes", "node_id": node_id}


async def _update(fleet, node_id: str, args: dict) -> dict:
    """update_file on a node's file service: read, replace exactly, write."""
    path, old, new = args.get("file_path"), args.get("old_string"), args.get("new_string")
    if not isinstance(path, str) or not isinstance(old, str) or not old or not isinstance(new, str):
        return {"success": False, "error": "Give file_path, old_string (non-empty) and new_string.", "node_id": node_id}
    read = await _call_files(fleet, node_id, "read_file", {"file_path": path, "max_chars": _MAX_TEXT})
    if read.get("success") is False or "content" not in read:
        return read
    if read.get("truncated"):
        return {"success": False, "error": "The file is larger than 256 KiB; edit it with run_command.", "node_id": node_id}
    text = read["content"]
    start, end = args.get("start_line"), args.get("end_line")
    head, region, tail = "", text, ""
    if start or end:
        lines = text.split("\n")
        first = max(int(start or 1), 1) - 1
        last = min(int(end or len(lines)), len(lines))
        head = "\n".join(lines[:first]) + ("\n" if first else "")
        region = "\n".join(lines[first:last])
        tail = ("\n" if last < len(lines) else "") + "\n".join(lines[last:])
    count = region.count(old)
    if count == 0:
        return {"success": False, "error": "old_string was not found in the file.", "node_id": node_id}
    if count > 1 and not args.get("replace_all"):
        return {"success": False, "error": f"old_string occurs {count} times; add context or set replace_all.",
                "node_id": node_id}
    region = region.replace(old, new) if args.get("replace_all") else region.replace(old, new, 1)
    updated = head + region + tail
    if len(updated.encode()) > _MAX_TEXT:
        return {"success": False, "error": "The edited file would exceed 256 KiB; edit it with run_command.", "node_id": node_id}
    written = await _call_files(fleet, node_id, "write_file", {"file_path": path, "content": updated, "overwrite": True})
    if written.get("success") is False:
        return written
    return {"success": True, "file_path": path, "replacements": count if args.get("replace_all") else 1, "node_id": node_id}


async def _run(fleet, node: dict, args: dict) -> dict:
    node_id = node["node_id"]
    caps = node.get("caps")
    if caps is not None and "proc" not in caps:  # older controllers list no caps
        return {"success": False, "error": f"Node {node.get('name') or node_id} does not allow running programs.",
                "node_id": node_id}
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"success": False, "error": "Give a command.", "node_id": node_id}
    roots = node.get("file_roots") or []
    reply = await fleet.call_tool("run_on_node", {
        "node_id": node_id, "code": command, "kind": "shell",
        "timeout": int(args.get("timeout") or 120), "cwd": str(roots[0]) if roots else "",
        "max_output": int(args.get("max_output") or _RUN_OUTPUT)})
    if not isinstance(reply, dict):
        return {"success": False, "error": "Invalid reply", "node_id": node_id}
    output = reply.get("stdout") or ""
    if reply.get("stderr"):
        output += ("\n" if output else "") + reply["stderr"]
    if reply.get("error") and not output:
        output = str(reply["error"])
    return {"success": bool(reply.get("success")), "output": output, "exit_code": reply.get("exit_code"),
            "status": "completed", "node_id": node_id}


async def note_for(agent, node_id: str) -> str | None:
    """The environment note for node_id, unless it is the Agent's own node."""
    default = await default_node(agent)
    if not node_id or node_id == default:
        return None
    node = next((n for n in await list_nodes(agent) if node_id in (n.get("node_id"), n.get("name"))), None)
    return describe_node(node, default=False) if node is not None else None


def with_note(message: list[dict], note: str) -> list[dict]:
    """The user message with the note added to what only the model reads
    (``_llm_content``); the conversation shows the message unchanged."""
    result = list(message)
    for i in range(len(result) - 1, -1, -1):
        msg = result[i]
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        msg = dict(msg)
        content = msg.get("_llm_content") or msg.get("content", "")
        if isinstance(content, list):
            msg["_llm_content"] = [{"type": "text", "text": note}, *content]
        else:
            msg["_llm_content"] = note + "\n\n" + str(content)
        result[i] = msg
        break
    return result


def summarize(node: dict, default: str | None) -> dict:
    """What the UI shows to choose a conversation's node."""
    caps = node.get("caps") or []
    return {"node_id": node.get("node_id"), "name": node.get("name") or node.get("node_id"),
            "os": node.get("os"), "shell": node.get("shell"), "status": node.get("status"),
            "default": node.get("node_id") == default,
            # Another node's files go through its file service (shared
            # folders); its commands need process execution.
            "files": node.get("node_id") == default or bool(node.get("file_roots")) or "fs:local" in caps,
            "commands": node.get("node_id") == default or "proc" in caps}
