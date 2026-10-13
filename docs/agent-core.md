# A small Agent core (2026-10-12)

A small loop, a few tools, a short prompt, everything else loaded when
needed. Measured before this work on
staging: a one-word reply cost ~74.5k prompt tokens (a ~9.3k-word system prompt
plus ~100 tool schemas).

## Agent mode (`mode: agent` in a team template)

The default General Team is now one agent. The first template agent is the only
one the user talks to; the other members are sub-agent profiles it starts with
one `call_agent` call (fresh context, only the final answer returns, several in
one turn run in parallel). No transfer, no `list_agents`. A profile is an agent
file: name, description, toolsets and a short prompt (`agents/worker.md`,
`researcher.md`, `scientific_illustrator.md`).

## Core tools (`pantheon/agent_core_tools.py`)

Every agent in agent mode declares only:
`file_manager__read_file`, `file_manager__write_file`, `file_manager__update_file`,
`shell__run_command`, `call_agent`, `background_task`, `describe_app`,
`call_app_tool`. The `call_app_tool` description lists every App and its tool
names; `describe_app(app)` returns that App's schemas. The tool list never
changes within a conversation, so the cached prefix holds.

History records an App call in its expanded form, e.g.
`integrated_notebook__notebook_execute` with that tool's own arguments, exactly
as a directly declared tool. UI cards, result truncation, task tracking and
file-change tracking key on that name and keep working unchanged. Only the
model's view (`fold_for_model`, applied in `Agent._acompletion` after the token
optimization view) turns those calls back into `call_app_tool(app, tool, args)`,
deterministically.

## Prompt

The leader prompt is ~550 words (`teams/default.md`). In agent mode the memory
and skills plugins append short versions (`MEMORY_GUIDANCE_CORE`,
`SKILLS_GUIDANCE_CORE` plus the skill index). The task plugin keeps its tools
on demand (`register_output`, `notify_user`, `task_boundary`) but injects no
workflow instructions or per-turn reminders.

## Prompt cache

App-mode model refs (`fleet-route://…`, `fleet-model://…`) resolve to a model
only in `ModelServices.complete`, which now places the cache breakpoints
(Anthropic, Qwen, GLM); the conversation breakpoint includes tool results.

## A conversation's working node (`pantheon/agent_nodes.py`)

The core file tools and `run_command` act on the conversation's node: by
default the node the Agent's Files and Shell Apps run on (the workspace, from
the Files grant's provider identity). The conversation can switch to another of
the owner's nodes (`set_chat_node`, the composer's node switcher), and one call
can name a node with the optional `node` argument. On another node:

* files go through that node's file service via the Fleet App's `node_call`
  (read_file, write_file; update_file is read, exact replace, write; 256 KiB);
* `run_command` goes through `run_on_node` in the node's first shared folder,
  output bounded to its end. There is no shell session: each command is fresh.

When the node is first known or changes, the model gets an `<environment>` note
(node, OS, shell dialect, folders) in the user message's `_llm_content`; the
conversation shows the message unchanged. Results from another node carry
`node_id`.

## The loop (`pantheon/agent_loop.py`)

A run is a sequence of turns: one model call, then the tool calls it asked for,
until the model answers without tools. Four files hold the core:

* `agent_loop.py`: the turns, stop, and pending messages (user messages queued
  during the run, background-task completions);
* `agent_model.py`: one model call (the model's view, tools, prompt cache) and
  the model chain;
* `agent_tools.py`: one turn's tool calls, foreground or background, and their
  tool messages;
* `agent.py`: the Agent itself (tools, providers, `run`).

Extensions attach to the loop through `agent.hooks` (`AgentHooks`): `context`
(messages added to one model call, e.g. task reminders, budgets), `before_tool`
/ `after_tool` (gate or decorate one call), `after_tools` (observe a turn).
Team plugins keep their run-level hooks (`on_run_start`, `on_run_end`).

The Agent does not retry a failed model call. The model layer does, once
per layer and only while nothing has been streamed: Model Services for Fleet
models, the provider call for direct ones (`retry_transient` in
`pantheon/utils/model_request.py`). A direct model that still fails moves to
the next model of the Agent's list; a Fleet model's failure is final.

## Context (`pantheon/agent_context.py`)

One mechanism keeps a conversation within its model's window:

* A tool result is capped once, when the tool returns: 50 KB
  (`endpoint.max_tool_content_length`) or 2,000 lines. The model reads the
  start and the path of a file with the full output; `read_file` of that path
  is answered on the Agent's own node (the workspace's Files App cannot reach
  it).
* Before every model call, when the prompt (messages, plus the system prompt
  and tool definitions measured from the previous call) would leave less than
  16k tokens of the window, the older part of the conversation is summarized
  (goal, progress, decisions, files, next steps) on the conversation's own
  model. The summary is a compaction record appended to the conversation
  (`role: "compression"`, `first_kept_id`); from then on the model reads the
  summary followed by the recent turns (~20k tokens) verbatim. A later
  compaction updates the previous summary. Nothing is deleted.
* A provider that still rejects the prompt as too long gets one compaction
  and the same turn again.
* Before compacting, plugins' `pre_compression` hooks see the conversation
  (the memory system saves what matters to its daily log).
* The composer's Compress (`compress_chat`) compacts on request.

## Next

* An append-only session tree for fork, revert and compaction.
