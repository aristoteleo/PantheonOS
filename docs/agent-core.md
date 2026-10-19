# A small Agent core (2026-10-13)

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

### Talking to a sub-agent

Each sub-agent run keeps its messages in the conversation under its execution
context (`<root>|d<depth>|<profile>|<rand>`), so the UI's Team view lists every
run, working or done, and the user can address one: `chat(chat_id, message,
sub_agent=<context>)`.

* A running sub-agent reads the message at its next turn, like a message to the
  conversation does for the lead: each run has its own queue in the chatroom's
  `SteerQueue` (`open_run`). One the lead waits on (in process) also reads the
  messages sent to the conversation, as every sub-agent did before. One a
  `pantheon agent run` command started runs in its own process while the lead
  may work on: it reads only its own messages, which the Agent App forwards
  over the child's stdin (docs/pantheon-cli.md).
* `stop_sub_agent(chat_id, sub_agent)` stops that run only; the lead gets
  "The user stopped this sub-agent before it finished." and its work so far.
  Each call of the run the stop cut short gets the same sentence as its
  result, so the run's history shows when it ended; a run that ends any
  other way (the conversation stopped, a failure) closes its unfinished calls
  with "This sub-agent's run ended before this call finished."
* A finished sub-agent continues (`PantheonTeam.continue_sub_agent`): it reads
  its own earlier messages and the new one, and its new messages keep its
  context. While the lead works this runs beside it, in the same thread; the
  thread ends when both have.
* The user can start a run of a profile themselves: `chat(chat_id, task,
  sub_agent=<new context '<root>|d1|<profile>|<id>'>, profile=<profile>)`. It
  runs as a finished run continues (beside the lead while the lead works),
  reading only its task.
* The user can close a run (`close_sub_agent(chat_id, sub_agent)`): one that
  runs is stopped, and the Team view keeps closed runs out of sight
  (`closed_sub_agents` in the conversation's metadata, `get_sub_agents`).
  `closed=False`, or a message to it, opens it again.
* The lead never reads a sub-agent's messages, so at its next turn it gets a
  note of what the user asked each sub-agent directly, or started one with,
  and what it answered (`sub_agent_notes` in the conversation's metadata,
  added to the user message's `_llm_content`).

## Core tools (`pantheon/agent_core_tools.py`)

Every agent in agent mode declares only:

* `file_manager__read_file`, `file_manager__write_file`,
  `file_manager__update_file`, `shell__run_command` (when its `apps` filter
  allows Files and Shell);
* its built-ins: `call_agent`, `background_task`, the task tools
  (`task_boundary`, `register_output`, `notify_user`, `list_outputs`) and the
  skill tools (`skill_view`, `skill_manage`), with short descriptions;
* the seven `pantheonos_*` tools, through which it reaches every App
  (docs/pantheonos-app-interface.md).

The `pantheonos_call_app` description lists every App and its tool names;
`pantheonos_describe_app(app)` returns that App's schemas. The tool list never
changes within a conversation, so the cached prefix holds.

History records an App call in its expanded form, e.g.
`integrated_notebook__notebook_execute` with that tool's own arguments, exactly
as a directly declared tool. UI cards, result truncation, task tracking and
file-change tracking key on that name and keep working unchanged. Only the
model's view (`fold_for_model`, applied in `Agent._acompletion` after the token
optimization view) turns those calls back into
`pantheonos_call_app(app, tool, args)`, deterministically. A `call_app_tool`
call in an older history folds the same way.

## Prompt

The leader prompt is ~550 words (`teams/default.md`). In agent mode the memory
and skills plugins append short versions (`MEMORY_GUIDANCE_CORE`,
`SKILLS_GUIDANCE_CORE` plus the skill index). The task plugin's tools are
declared with short descriptions; it injects no workflow instructions or
per-turn reminders.

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
  tool messages. A turn's calls run at the same time, but not across a file
  change (`FILE_CHANGES`: write, update, patch, move, delete…): it starts once
  the calls listed before it are done, and the calls after it once it is, so a
  script written and run in one turn runs after it is written;
* `agent.py`: the Agent itself (tools, providers, `run`).

Extensions attach to the loop through `agent.hooks` (`AgentHooks`): `context`
(messages added to one model call, e.g. task reminders, budgets), `before_tool`
/ `after_tool` (gate or decorate one call), `after_tools` (observe a turn).
Team plugins keep their run-level hooks (`on_run_start`, `on_run_end`).

The Agent does not retry a failed model call; the model layer decides. Model
Services never sends a submitted inference again (a route picks a healthy,
loaded candidate before submitting), so a Fleet model's failure is final. A
direct provider call is sent again after an overloaded, rate-limited or
unreachable reply while nothing has been streamed (`retry_transient` in
`pantheon/utils/model_request.py`; never for a 4xx); a direct model that still
fails moves to the next model of the Agent's list.

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

## The conversation is an append-only tree (`pantheon/internal/memory/tree.py`)

A chat's `.jsonl` log is never rewritten. Each message record has `id` and
`parent_id`; appending makes it a child of the head. A head record
(`{"_head": id}`) moves the head; a record with an existing `id` is a new
version of that message. The conversation is the path from the first message
to the head (`Memory._messages`); the log is `Memory._records`.

* Revert moves the head to the parent of a message. The messages after it
  stay as an earlier branch; a new message there starts a second branch.
* `switch_branch(chat_id, message_id)` continues on the branch containing a
  message (at its latest message). History replies mark each message with
  alternatives with `_branch` (index, count, siblings); the UI shows ‹ i/n ›
  on such a user message.
* Compaction appends its record on the path (see Context).
* A stop's cleanup moves the head back past empty steps and writes a new
  version of a message whose tool calls never ran.
* Fork copies the path up to a message into a new chat (`forked_from`).
* Logs written as a list read as one branch: a line without `parent_id`
  follows the line before it.
* An export holds the current branch as a list.
