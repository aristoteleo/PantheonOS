"""Core tool exposure: a few tools in the model's tool list, every other App
tool on demand (docs/agent-core.md, docs/pantheonos-app-interface.md).

A core-mode Agent declares CORE_TOOLS: the Files and Shell tools every turn
uses, its built-ins (sub-agents, background tasks, task tracking, skills) and
the seven ``pantheonos_*`` tools through which it reaches every App. The tool
list never changes during a conversation, so the cached prompt prefix survives
however many Apps the Agent can reach.

History keeps every App call in its expanded form (``integrated_notebook__
notebook_execute`` with its own arguments), exactly as a directly declared tool
would be recorded: the UI cards, result truncation and task tracking key on that
name. Only the model's view folds those calls back into ``pantheonos_call_app``,
the form the model itself used. Folding is deterministic, so the prefix stays
cacheable.
"""
from __future__ import annotations

import json
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
#: the App list, never filtered. Their declared tools, with the short
#: description a core-mode Agent shows for each.
BUILTIN_PROVIDERS = frozenset({"task", "skills"})
BUILTIN_DESCRIPTIONS = {
    "task__task_boundary": (
        "Start or update the current task, which shows the user what you are doing in a multi-step "
        "task. Call it when you begin and when the status changes, with the same task_name to update; "
        "\"%SAME%\" reuses a previous value. task_name: human-readable. mode: PLANNING, EXECUTION or "
        "VERIFICATION (or RESEARCH, ANALYSIS, INTERPRETATION). task_summary: what is done so far, past "
        "tense, 1-2 lines. task_status: what you do next. predicted_task_size: expected tool calls. "
        "output_dir: the workspace folder for this task's outputs, declared when planning; the user "
        "sees it fill."
    ),
    "task__notify_user": (
        "Message the user during a task: progress, a finished result, or a question. Never call it in "
        "parallel with other tools. blocked_on_user: true only when the user must approve, review or "
        "decide before you continue (the run stops and waits); false to inform, including the final "
        "result. message: concise GitHub markdown; never put questions here. paths_to_review: absolute "
        "paths of files to show. questions: [] or interactive questions, each {question, header (at "
        "most 12 chars), input_type: single_choice | multiple_choice | text_input, options: [{label, "
        "description, value?}], placeholder?, required?}; questions make the run wait for answers. "
        "confidence_score: 0.0-1.0, lower for gaps, unverified assumptions, risk or ambiguity; "
        "confidence_justification: why."
    ),
    "task__register_output": (
        "Register a deliverable so the user finds it in the Output panel; files produced by running "
        "code appear nowhere else. Prefer registering the folder of related outputs. path: an existing "
        "file or folder (workspace-relative or absolute). title, description: short. kind: report, "
        "figure, data, table, dir or other. node_id: the Fleet node that has the file, if not the "
        "workspace."
    ),
    "task__list_outputs": "The deliverables registered in this conversation, with the task of each.",
    "skills__skill_view": None,
    "skills__skill_manage": None,
}
BUILTIN_TOOLS = frozenset(BUILTIN_DESCRIPTIONS)

#: Tools a core-mode Agent always declares. Everything else an Agent can reach
#: is an App tool, called with pantheonos_call_app.
CORE_TOOLS = DIRECT_APP_TOOLS | BUILTIN_TOOLS | frozenset({"call_agent", "background_task", *PANTHEONOS_TOOLS})

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


def _function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


_APP = {"type": "string", "description": "An App name, as pantheonos_list_apps shows it."}
_NODE = {"type": "string", "description": "A Fleet node id or name. Omit for the App's default node."}


def pantheonos_schemas(index: dict[str, list[str]], descriptions: dict[str, str] | None = None) -> list[dict]:
    """The seven pantheonos_* declarations; pantheonos_call_app lists every App
    and its tool names (with a one-line description where an App has one)."""
    descriptions = descriptions or {}
    lines = "\n".join(
        f"- {app}" + (f" ({descriptions[app]})" if descriptions.get(app) else "") + f": {', '.join(tools)}"
        for app, tools in sorted(index.items()) if tools)
    return [
        _function("pantheonos_list_apps", (
            "List the Apps you can use, read live: each App's description, what it offers (backend "
            "tools, window actions, a window), whether and where it runs, its policy, and its tool and "
            "action names."), {"query": {"type": "string", "description": "Only Apps whose name or "
                                         "description contains this."}}, []),
        _function("pantheonos_describe_app", (
            "Read one App in full: its tools' and window actions' descriptions and JSON parameters, its "
            "window, version, development branches and where it runs. Call it before "
            "pantheonos_call_app for an App you have not used in this conversation."),
            {"app": _APP}, ["app"]),
        _function(CALL_APP, (
            "Call a tool or window action of an App. A backend tool starts the App if it is not "
            "running; a window action runs in the App's window on the user's desktop, opening one if "
            "needed. Put `\"_background\": true` in args to run a long call in the background (track "
            "it with background_task).\n\nApps and their tools:\n" + lines),
            {"app": _APP, "tool": {"type": "string", "description": "A tool or action of that App."},
             "args": {"type": "object", "description": "Its arguments, as pantheonos_describe_app lists them."}},
            ["app", "tool", "args"]),
        _function("pantheonos_start_app", (
            "Start an App explicitly; calling it starts it anyway. Use it to run a version (a "
            "development version to try), on a chosen node, as an HPC job, or to open its window. "
            "Later calls in this conversation go to the instance started here. Deployment Apps "
            "(Files, Shell, ...) are always running."),
            {"app": _APP, "node": _NODE,
             "version": {"type": "string", "description": "A tag, branch:<name>, commit or latest."},
             "window": {"type": "boolean", "description": "Open a window of it on the user's desktop."},
             "job": {"type": "object", "description": (
                 "Run as a bounded HPC job: {cluster_id, name, partition, cpus, mem_gb, minutes} and "
                 "optional gpus, account, qos; node is the cluster's login node.")}},
            ["app"]),
        _function("pantheonos_stop_app", (
            "Stop a library App's backend you are done with; an idle App also stops on its own ten "
            "minutes after your last call. The user starts and stops deployment Apps in Fleet."),
            {"app": _APP, "node": {"type": "string", "description": "Stop it only on this node."}}, ["app"]),
        _function("pantheonos_manage_app", (
            "Manage the user's Apps. Actions and their args: search {query} and inspect {repository} "
            "the Store; install {repository, version}; uninstall (to the Trash); trash (list it); "
            "restore {archive_id}; versions; set_version {version: a tag, branch:<name> or latest, the "
            "version launches use}; nodes (where it can run); set_node {node; empty for automatic}; "
            "set_policy {policy: on_demand (started when used, stopped when idle) or resident (kept "
            "running)}."),
            {"action": {"type": "string", "enum": ["search", "inspect", "install", "uninstall", "trash",
                                                    "restore", "versions", "set_version", "nodes",
                                                    "set_node", "set_policy"]},
             "app": {"type": "string", "description": "The App, for actions on one App."},
             "args": {"type": "object", "description": "The action's arguments."}},
            ["action"]),
        _function("pantheonos_develop_app", (
            "Develop an App's code in its Git repository; read the skill app-development first. "
            "Actions and their args: create {message: display name}; fork {version, name} (a private "
            "copy to edit); status; files {branch?}; read {path, branch?}; write {files: {path: "
            "content}, expected_commit?}; diff; branch {branch}; switch {branch}; merge {branch}; commit "
            "{message}; test {command: argv}; history; tag {version}; build and build_status {node, "
            "version?}; prepare; publish {name, expected_commit}; fetch; pull {expected_commit}; "
            "community; contributions {inbox?}; submit {repository, version, target_repository, "
            "expected_commit, expected_base, title, description}; inspect_contribution, prepare_merge "
            "{version}, checkout_review {expected_commit}, review {decision, expected_commit, "
            "description}, merge_contribution {expected_commit}, close_contribution: each with "
            "{request_id}. Any action takes repository to pick one of several copies."),
            {"action": {"type": "string", "enum": [
                "create", "fork", "status", "files", "read", "write", "diff", "branch", "switch", "merge",
                "commit", "test", "history", "tag", "build", "build_status", "prepare", "publish", "fetch",
                "pull", "community", "contributions", "submit", "inspect_contribution", "prepare_merge",
                "checkout_review", "review", "merge_contribution", "close_contribution"]},
             "app": {"type": "string", "description": "The App id (a new one for create)."},
             "args": {"type": "object", "description": "The action's arguments."}},
            ["action"]),
    ]


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
    """The model's view: calls to tools it does not declare become
    pantheonos_call_app (an older history's call_app_tool is renamed).

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
                if name == LEGACY_CALL_APP:
                    call = {**call, "function": {**function, "name": CALL_APP}}
                    if call.get("id"):
                        folded.add(call["id"])
                    changed = True
                elif name and name not in visible:
                    app, tool = split_name(name)
                    arguments = json.dumps({"app": app, "tool": tool, "args": _arguments(function.get("arguments"))},
                                           ensure_ascii=False)
                    call = {**call, "function": {**function, "name": CALL_APP, "arguments": arguments}}
                    if call.get("id"):
                        folded.add(call["id"])
                    changed = True
                new_calls.append(call)
            if changed:
                message = {**message, "tool_calls": new_calls}
        elif message.get("role") == "tool" and message.get("tool_call_id") in folded:
            message = {**message, "name": CALL_APP}
            if "tool_name" in message:
                message["tool_name"] = CALL_APP
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
