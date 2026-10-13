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

## Next

* One compaction mechanism instead of three; retries only in Model Services.
* An append-only session tree for fork, revert and compaction.
