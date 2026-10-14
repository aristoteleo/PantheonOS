"""Core tool exposure: a few tools in the model's tool list, every other App
tool on demand (docs/agent-core.md).

A core-mode Agent declares only CORE_TOOLS to the model, plus two built-ins:
``describe_app`` (read an App's tool schemas) and ``call_app_tool`` (call one).
The tool list never changes during a conversation, so the cached prompt prefix
survives however many Apps the Agent can reach.

History keeps every App call in its expanded form (``integrated_notebook__
notebook_execute`` with its own arguments), exactly as a directly declared tool
would be recorded: the UI cards, result truncation and task tracking key on that
name. Only the model's view folds those calls back into ``call_app_tool``, the
form the model itself used. Folding is deterministic, so the prefix stays
cacheable.
"""
from __future__ import annotations

import json
from typing import Any

#: Tools a core-mode Agent always declares. Everything else an Agent can reach
#: (its other providers' tools, its own non-core functions) is on demand.
CORE_TOOLS = frozenset({
    "file_manager__read_file",
    "file_manager__write_file",
    "file_manager__update_file",
    "shell__run_command",
    "call_agent",
    "background_task",
    "describe_app",
    "call_app_tool",
})

#: The "App" an Agent's own non-core functions are listed under.
AGENT_APP = "agent"

_DESCRIBE = {
    "type": "function",
    "function": {
        "name": "describe_app",
        "description": (
            "Read the tools of one App: each tool's description and JSON parameters. "
            "Call this before call_app_tool for an App you have not used in this conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {"app": {"type": "string", "description": "An App name from call_app_tool's list."}},
            "required": ["app"],
            "additionalProperties": False,
        },
    },
}


def call_app_tool_schema(index: dict[str, list[str]], descriptions: dict[str, str] | None = None) -> dict:
    """The call_app_tool declaration, listing every App and its tool names
    (with a one-line description where an App has one)."""
    descriptions = descriptions or {}
    lines = "\n".join(
        f"- {app}" + (f" ({descriptions[app]})" if descriptions.get(app) else "") + f": {', '.join(tools)}"
        for app, tools in sorted(index.items()) if tools)
    return {
        "type": "function",
        "function": {
            "name": "call_app_tool",
            "description": (
                "Call a tool of an App that is not in your tool list. Read its parameters with "
                "describe_app first. Put `\"_background\": true` in args to run a long call in the "
                "background (track it with background_task).\n\nApps and their tools:\n" + lines
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {"type": "string", "description": "App name, e.g. web."},
                    "tool": {"type": "string", "description": "Tool name within that App."},
                    "args": {"type": "object", "description": "The tool's arguments, as describe_app lists them."},
                },
                "required": ["app", "tool", "args"],
                "additionalProperties": False,
            },
        },
    }


def describe_app_schema() -> dict:
    return json.loads(json.dumps(_DESCRIBE))


def split_name(full_name: str) -> tuple[str, str]:
    """(app, tool) for a recorded tool name; the Agent's own functions are AGENT_APP."""
    if "__" in full_name:
        app, tool = full_name.split("__", 1)
        return app, tool
    return AGENT_APP, full_name


def _arguments(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_unparsed_arguments": raw}


def fold_for_model(messages: list[dict], visible: frozenset[str] | set[str]) -> list[dict]:
    """The model's view: calls to tools it does not declare become call_app_tool.

    Returns new message dicts where anything changes; the history is untouched.
    """
    folded: set[str] = set()
    out: list[dict] = []
    for message in messages:
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        if calls:
            changed, new_calls = False, []
            for call in calls:
                function = call.get("function") or {}
                name = function.get("name") or ""
                if name and name not in visible:
                    app, tool = split_name(name)
                    arguments = json.dumps({"app": app, "tool": tool, "args": _arguments(function.get("arguments"))},
                                           ensure_ascii=False)
                    call = {**call, "function": {**function, "name": "call_app_tool", "arguments": arguments}}
                    if call.get("id"):
                        folded.add(call["id"])
                    changed = True
                new_calls.append(call)
            if changed:
                message = {**message, "tool_calls": new_calls}
        elif message.get("role") == "tool" and message.get("tool_call_id") in folded:
            message = {**message, "name": "call_app_tool"}
            if "tool_name" in message:
                message["tool_name"] = "call_app_tool"
        out.append(message)
    return out


def expand_call(function: dict, resolve) -> bool:
    """Rewrite one call_app_tool function call in place to the tool it names.

    ``resolve(app, tool)`` returns the recorded full name, or None when the App
    or tool does not exist; such a call is left as call_app_tool, which answers
    the model with what does exist. A top-level ``_background`` moves into args.
    """
    if function.get("name") != "call_app_tool":
        return False
    payload = _arguments(function.get("arguments"))
    if not isinstance(payload, dict):
        return False
    app, tool, args = payload.get("app"), payload.get("tool"), payload.get("args", {})
    if isinstance(args, str):
        args = _arguments(args)
    if not isinstance(app, str) or not isinstance(tool, str) or not isinstance(args, dict):
        return False
    full_name = resolve(app, tool)
    if full_name is None:
        return False
    if payload.get("_background") and "_background" not in args:
        args = {**args, "_background": True}
    function["name"] = full_name
    function["arguments"] = json.dumps(args, ensure_ascii=False)
    return True
