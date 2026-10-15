"""Core tool exposure: four tools in the model's tool list (docs/pantheon-cli.md).

A core-mode Agent declares read_file, write_file, update_file and run_command.
Everything else is the ``pantheon`` command, run with run_command: Apps, the
desktop and browser, other nodes, sub-agents, background commands, task
tracking and skills. The command goes through this Agent
(pantheon/chatroom/command_gateway.py), which still runs its built-ins and
App tools as before. The tool list never changes, so the cached prompt prefix
survives however many Apps the Agent can reach.

History keeps every call in its recorded form (``integrated_notebook__
notebook_execute`` with its own arguments; ``task__task_boundary``): the UI
cards, result truncation and task tracking key on that name. The model's view
shows a call to a tool it does not declare as the ``pantheon`` command that
does the same, so an older conversation reads as this Agent works now.
Folding is deterministic, so the prefix stays cacheable.
"""
from __future__ import annotations

import json
import shlex
from typing import Any

CALL_APP = "pantheonos_call_app"
#: The name an App call had before pantheonos_call_app; older histories hold it.
LEGACY_CALL_APP = "call_app_tool"
PANTHEONOS_TOOLS = ("pantheonos_list_apps", "pantheonos_describe_app", CALL_APP, "pantheonos_start_app",
                    "pantheonos_stop_app", "pantheonos_manage_app", "pantheonos_develop_app")

#: App tools declared directly, because nearly every turn uses them.
DIRECT_APP_TOOLS = frozenset({
    "file_manager__read_file",
    "file_manager__write_file",
    "file_manager__update_file",
    "shell__run_command",
})

#: Built-in capabilities that are toolsets of the Agent, not Apps: never in
#: the App list, never filtered.
BUILTIN_PROVIDERS = frozenset({"task", "skills"})

#: Tools a core-mode Agent declares; the rest is the pantheon command.
CORE_TOOLS = DIRECT_APP_TOOLS

#: Where an undeclared own function of the Agent (in an older history) is
#: folded to.
AGENT_APP = "agent"

#: One line for each deployment App in the App list.
DEPLOYMENT_DESCRIPTIONS = {
    "file_manager": "Files on the workspace: read, write, search, images",
    "shell": "Commands on the workspace",
    "integrated_notebook": "Jupyter notebooks on the workspace",
    "web": "Web search and pages",
    "evolution": "Evolutionary optimisation of programs",
    "desktop": "The user's desktop: windows, the browser, screenshots",
    "fleet": "The user's computers (nodes), transfers between them, HPC clusters",
    "model_services": "Models served on the user's Fleet",
}


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


RUN_COMMAND = "shell__run_command"
_MANAGE = {"set_version": "set-version", "set_node": "set-node", "set_policy": "policy"}
_SKILL_ACTIONS = {"search_store": "search", "browse_store": "browse"}


def _flag(args: Any) -> str:
    """Arguments as the command's --args JSON (none: nothing)."""
    if not args:
        return ""
    return " --args " + shlex.quote(json.dumps(args, ensure_ascii=False, separators=(",", ":"), default=str))


def as_command(name: str, args: Any) -> str:
    """The pantheon command that does what a call to the tool ``name`` did."""
    a = {k: v for k, v in args.items() if k != "_background"} if isinstance(args, dict) else {}
    q = lambda value: shlex.quote(str(value or ""))
    pop = lambda *keys: [a.pop(k, None) for k in keys]
    if name in (CALL_APP, LEGACY_CALL_APP):
        app, tool, inner = pop("app", "tool", "args")
        return f"pantheon apps call {q(app)} {q(tool)}" + _flag(inner if isinstance(inner, dict) else a)
    if name == "pantheonos_list_apps":
        return "pantheon apps list" + (f" --query {q(a['query'])}" if a.get("query") else "")
    if name == "pantheonos_describe_app":
        return f"pantheon apps describe {q(a.get('app'))}"
    if name in ("pantheonos_start_app", "pantheonos_stop_app"):
        app, node, version, window, job = pop("app", "node", "version", "window", "job")
        verb = "start" if name.endswith("start_app") else "stop"
        return (f"pantheon apps {verb} {q(app)}" + (f" --node {q(node)}" if node else "")
                + (f" --version {q(version)}" if version else "") + (" --window" if window else "")
                + (f" --job {shlex.quote(json.dumps(job, ensure_ascii=False))}" if job else ""))
    if name == "pantheonos_manage_app":
        action, app, inner = pop("action", "app", "args")
        return (f"pantheon apps {_MANAGE.get(str(action), action)}" + (f" {q(app)}" if app else "")
                + _flag(inner))
    if name == "pantheonos_develop_app":
        action, app, inner = pop("action", "app", "args")
        return f"pantheon dev {q(app)} {q(str(action or '').replace('_', '-'))}" + _flag(inner)
    if name == "call_agent":
        return f"pantheon agent run {q(a.get('agent_name'))} {q(a.get('instruction'))}"
    if name == "background_task":
        action, task_id = pop("action", "task_id")
        return f"pantheon jobs {action or 'list'}" + (f" {q(task_id)}" if task_id else "")
    if name == "task__task_boundary":
        return "pantheon task update" + _flag(a)
    if name == "task__notify_user":
        wait = a.pop("blocked_on_user", False)
        return "pantheon task notify" + (" --wait" if wait else "") + _flag(a)
    if name == "task__register_output":
        return f"pantheon task output {q(a.pop('path', ''))}" + _flag(a)
    if name == "task__list_outputs":
        return "pantheon task outputs"
    if name == "skills__skill_view":
        name_, file = pop("name", "file_path")
        return f"pantheon skills view {q(name_)}" + (f" {q(file)}" if file else "")
    if name == "skills__skill_manage":
        action, skill = pop("action", "name")
        if action in _SKILL_ACTIONS:
            options = dict(a.pop("options", None) or {})
            query = options.pop("query", "") or skill
            return f"pantheon skills {_SKILL_ACTIONS[action]}" + (f" {q(query)}" if query else "") + _flag(options)
        return f"pantheon skills {action}" + (f" {q(skill)}" if skill else "") + _flag(a)
    if name.startswith(("desktop__browser_", "desktop__desktop_")):
        group, action = name.split("__", 1)[1].split("_", 1)
        return f"pantheon {group} {action.replace('_', '-')}" + _flag(a)
    if name == "fleet__fleet_list_nodes":
        return "pantheon nodes list"
    if name == "fleet__fleet_node_info":
        return f"pantheon nodes info {q(a.get('node_id'))}"
    if name == "fleet__run_on_node":
        kind = a.get("kind") or "shell"
        return f"pantheon run --node {q(a.get('node_id'))}" + (f" --kind {kind}" if kind != "shell" else "") + f" -- {q(a.get('code'))}"
    if name == "fleet__transfer":
        source = "%s:%s" % (a.get("src_node", ""), a.get("src_path", ""))
        target = "%s:%s" % (a.get("dst_node", ""), a.get("dst_path", ""))
        return f"pantheon transfer {q(source)} {q(target)}"
    app, tool = split_name(name)
    return f"pantheon apps call {q(app)} {q(tool)}" + _flag(a)


def fold_for_model(messages: list[dict], visible: frozenset[str] | set[str]) -> list[dict]:
    """The model's view: a call to a tool it does not declare becomes the
    run_command of the pantheon command that does the same.

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
                    command = as_command(name, _arguments(function.get("arguments")))
                    arguments = json.dumps({"command": command}, ensure_ascii=False)
                    call = {**call, "function": {**function, "name": RUN_COMMAND, "arguments": arguments}}
                    if call.get("id"):
                        folded.add(call["id"])
                    changed = True
                new_calls.append(call)
            if changed:
                message = {**message, "tool_calls": new_calls}
        elif message.get("role") == "tool" and message.get("tool_call_id") in folded:
            message = {**message, "name": RUN_COMMAND}
            if "tool_name" in message:
                message["tool_name"] = RUN_COMMAND
        out.append(message)
    return out


def expand_call(function: dict, resolve) -> bool:
    """Rewrite one pantheonos_call_app function call in place to the tool it names.

    ``resolve(app, tool)`` returns the recorded full name, or None when the App
    or tool does not exist; such a call is left as pantheonos_call_app, which
    answers the model with what does exist. A top-level ``_background`` moves
    into args.
    """
    if function.get("name") not in (CALL_APP, LEGACY_CALL_APP):
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
