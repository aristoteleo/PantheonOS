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

## Next

* Per-chat default node: core tools run on the chat's node (default: the
  workspace node), switchable in the UI and per call with `node`; results carry
  `node_id`; the model learns the node, OS, shell dialect and directory from an
  appended environment note. Needs the allocator to bind Files/Shell on a named
  node.
* One compaction mechanism instead of three; retries only in Model Services.
* An append-only session tree for fork, revert and compaction.
