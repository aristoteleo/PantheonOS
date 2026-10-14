# One command: `pantheon` (2026-10-14)

PantheonOS has one command-line program, `pantheon`, a single Go binary. It is
the client of PantheonOS for people and for any agent: it finds, calls, starts,
stops, manages and develops Apps, drives the desktop and its browser, and
reaches the owner's nodes. It also runs the node itself, local mode, and the
Pantheon agent.

An agent therefore needs no PantheonOS-specific tools. Any agent with a shell
uses the PantheonOS ecosystem through `pantheon`. The Pantheon agent itself
keeps four tools: read, write, edit, and run a command.

This replaces the seven `pantheonos_*` tools
(docs/pantheonos-app-interface.md), which stay on staging until this lands.
Their server side stays. It moves behind the command, unchanged:

* the Desktop's `agent_app_*` methods;
* the `apps` filter of templates;
* the node's collection of superseded installations.

## Why one command

* **A fixed, small tool list.** The model's tool list no longer grows with the
  Apps. The prompt prefix is the same for every conversation, so it stays
  cached.
* **Any agent.** An agent that only has a shell can use every App, node and
  window, exactly as the Pantheon agent does.
* **Discoverable.** `pantheon --help` and `pantheon <group> --help` are the
  documentation. Models already know how to use a CLI.
* **One install.** The user installs one binary. It brings the Python runtime,
  the Apps and their dependencies itself (Installation, below).

## Commands

```
Apps
  pantheon apps list [--running] [--json]
  pantheon apps describe <app>
  pantheon apps call <app> <tool> [--args JSON | --args-file F | -]   (- reads stdin)
  pantheon apps start <app> [--node N] [--version V] [--window] [--job JSON]
  pantheon apps stop <app> [--node N]
  pantheon apps search <query> | install <repository> <version> | uninstall <app>
  pantheon apps versions <app> | set-version <app> <version> | set-node <app> <node>
  pantheon apps policy <app> on_demand|resident

App development
  pantheon dev <app> status | files | read | write | diff | branch | switch | merge
                     | commit | test | history | tag | build | publish | ...

Desktop and browser
  pantheon desktop windows | open | read | update | set | act | call | inspect
  pantheon desktop screenshot [--window W] [--out FILE]   (prints the image path)
  pantheon browser open | goto | read | click | type | scroll | screenshot | close

Nodes
  pantheon nodes list | info <node>
  pantheon run --node N -- <command>
  pantheon transfer <src-node>:<path> <dst-node>:<path>
  pantheon node up [...]          (the node daemon, formerly `fleet up`)

The Pantheon agent
  pantheon agent chat             (formerly `pantheon cli`)
  pantheon agent run <profile> <task> [--detach]   (a sub-agent, below)
  pantheon task start | update | output | notify   (task tracking, below)
  pantheon skills list | view | search | adopt | publish

Local PantheonOS and account
  pantheon up | down | status     (local mode: controller, broker and node)
  pantheon ui                     (local mode plus the web interface)
  pantheon login | logout | whoami
  pantheon setup | templates update | templates sync
```

Every command takes `--json` and prints one JSON document. Without it, the
output is short text for people. Errors exit non-zero and say what to do.

Code and other long arguments are passed with `--args-file` or on stdin
(`- <<'EOF'`), never through shell quoting.

A screenshot is written to a file. The command prints its path; the agent
looks at it with its read tool.

## Identity and authority

Who the command acts for decides what it may do.

* **A person or an outside agent** on their own machine signs in once with
  `pantheon login`. The command keeps one credential, a platform key
  (`pbk_…`, `~/.pantheon/cli/credentials.json`, readable by the user only). It
  lasts until the owner revokes it (Platform API Keys), and acts with the
  owner's authority:
  * the Hub accepts it for App grants (`/api/fleet/apps/workload-connect`);
  * the controller accepts it for the deployment's status
    (`/deployments/general-team`);
  * nodes, runs and transfers go through the Fleet App, so no other Hub
    endpoint is needed.

  There are three ways to get the key:
  * `pantheon login`: the browser opens the Hub's sign-in page for the
    command (`#/cli-login`). On Allow it creates a key named for the machine
    and returns it to the command's loopback address in the URL fragment. The
    command's callback page posts it back on its own origin, with the
    command's random state; any other post is refused.
  * `pantheon login --key pbk_…`, or `--key-stdin`: a key the owner created;
  * `pantheon login --username U`: the command signs in once with the
    password and creates a key named for the machine (`--no-browser` asks for
    either a key or the password).

  `PANTHEON_KEY` and `PANTHEON_HUB_URL` override the saved login, for scripts.
  A grant for an App instance is cached until shortly before it expires
  (`~/.pantheon/cli/grants.json`).
* **A command the Pantheon agent runs** acts for that conversation only. The
  agent gives every command it runs a short-lived token in the environment
  (`PANTHEON_TOKEN`, `PANTHEON_ENDPOINT`). The token is bound to:
  * the Agent App instance;
  * the conversation;
  * the agent (leader or sub-agent);
  * the tool call that runs the command.

  The command sends its requests to that Agent App's command gateway (below).
  The Agent App carries them out with its own grants, under the agent's `apps`
  filter. Code that runs in the sandbox (a script, a package) gets nothing
  more. The owner's credentials never enter the sandbox.

`run_command` gets an `env` argument for this. The Agent fills it in; the model
never sees it.

The command reaches the gateway through the node it runs on, using the
node's direct App transport (`fleet app-dial`). On a machine without a node,
it uses the Hub's App gateway over HTTPS.

## The command gateway (Agent App)

The Agent App serves the command's requests for its conversations.

* It checks the token.
* It applies the agent's `apps` filter.
* It forwards each request:
  * a deployment App: through that App's grant;
  * a library App, a window or App management: through the Desktop's
    `agent_app_*` methods;
  * nodes: through the Fleet App.
* It records each App call as a structured event in the conversation: the App,
  the tool, the arguments and the result, attached to the `run_command` call
  that made it. The UI renders the same cards it renders today (notebook
  execution, file changes, App calls) under that command, instead of only the
  command's output.

People and outside agents reach the same operations without a conversation.
Their requests go to the same services with the owner's authority. There are
no events, since there is no conversation.

## The Pantheon agent: four tools

* `read_file`, which also reads images;
* `write_file`;
* `update_file`;
* `run_command`.

Each takes the optional `node` argument, as today (docs/agent-core.md).

Everything else is either a file or a command:

| Was | Now |
|---|---|
| App tools, `pantheonos_*` | `pantheon apps …`, `pantheon dev …` |
| Desktop and browser tools | `pantheon desktop …`, `pantheon browser …` |
| Fleet tools | `pantheon nodes …`, `pantheon run`, `pantheon transfer` |
| `call_agent` | `pantheon agent run` |
| `background_task` | `run_command` in the background (it already runs long commands in the background) |
| Task tools | `pantheon task …` |
| Skills | files: read with `read_file`; the index stays in the prompt; `pantheon skills` for the Store |
| Memory | files, unchanged |

The system prompt says that `pantheon` is how Apps, the desktop and other
nodes are reached, and that `pantheon --help` lists the commands. Long
procedures stay in skills (app-development, desktop).

## Sub-agents are processes

A sub-agent is a separate agent process: the same agent program with a fresh
context. It returns only its final answer. Several run in parallel when the
leader starts several commands.

### Running one

`pantheon agent run <profile> <task>`, run by the leader:

1. The gateway checks that the token belongs to the leader of the
   conversation.
2. The Agent App starts a child agent process: the same package, its own
   interpreter process. It gives the child:
   * the profile (prompt and `apps` filter);
   * the task;
   * a child identity, `execution_context_id`, built as today:
     `<root>|d<depth>|<profile>|<rand>`;
   * `parent_tool_call_id`: the leader's `run_command` call;
   * the `chain_path`.
3. The child talks to the Agent App over its stdin and stdout. The protocol:
   * the child sends its model requests, its tool calls and its step messages;
   * the Agent App sends back the model responses, the tool results, and a
     stop when the run is stopped.

   The Agent App keeps the model access and the grants, so a child holds no
   credentials. The child's own commands get child tokens, so its App calls
   are attributed to the child.
4. The child's final answer is the command's output. The leader reads it like
   any command output.

### What the user sees

The Agent App is the single writer of the conversation's event log and memory
(`AgentEventStore`). It relays the child's step messages into both, as
in-process sub-agents do today:

* with `execution_context_id`, `parent_tool_call_id` and `_metadata.chain_path`;
* into the parent chat's memory, so the steps survive reloading the history
  (`open_agent_history` reads all contexts);
* to the UI as `step_message` events.

The UI's sub-agent card no longer keys on the tool name `call_agent`. It
appears for any tool call that has messages with its id as
`parent_tool_call_id`, so a `run_command` that ran `pantheon agent run` shows
the sub-agent's progress and result as `call_agent` does now. As today, a
sub-agent shows its completed steps, not its streamed text.

### Stopping and failure

* Stopping the leader's run stops its children: the Agent App sends them a
  stop, then ends the processes.
* A child process that dies ends its command with an error, and the leader
  sees it.
* `--detach` returns at once with a run id. `pantheon agent run --wait <id>`
  collects the answer later.

## The terminal chat is a frontend of the Agent App

The Agent App has two frontends, shipped and versioned with it: the web
window, and the terminal chat. `pantheon agent chat` opens the terminal one, as
the desktop opens the web one.

* **A pure client.** The terminal chat runs on the user's machine and talks to
  the Agent App over the same RPC as the web window:
  * conversations: `list_chats`, `create_chat`, `chat`, `stop`;
  * history and events: `get_chat_messages` and the event cursor;
  * agents, models and templates.

  It no longer runs the Agent in its own process, so it reaches the local
  Agent and the cloud Agent alike. Today's REPL becomes this client. Its
  direct uses of the ChatRoom, the team's Agent objects and the memory store
  are replaced by those calls, and its live output comes from the event
  stream.
* **Declared by the App.** The manifest names the terminal frontend and its
  own small requirements (`entry.terminal`, e.g. prompt_toolkit and rich).
  These are not the backend's.
* **Fetched for the running revision.** `pantheon agent chat`:
  1. finds the owner's Agent: local mode, or the cloud deployment once signed
     in;
  2. fetches the terminal frontend of the exact revision that runs, verified
     against the release set;
  3. builds its environment with the managed Python, cached per revision;
  4. runs it with the connection (endpoint and token) in its environment.

  Upgrading the Agent upgrades its terminal chat, as with the web window.
* **One-shot.** `pantheon agent chat -i "<message>"` sends one message,
  prints the answer and exits. This is today's `pantheon cli -i`, now against
  any of the owner's Agents. Scripts and outside agents use it.

Any App may declare a terminal frontend the same way;
`pantheon apps open <app> --terminal` runs it. `pantheon agent chat` is the
Agent's.

## Task tracking and messages to the user

`pantheon task start|update` (task_boundary), `pantheon task output <path>`
(register_output) and `pantheon task notify` (notify_user) go to the gateway.
It calls the conversation's task toolset, which renders in the Task UI as
today.

`pantheon task notify --wait` (blocked_on_user) marks the run to stop after
the current command. The Agent's loop ends the turn there and waits for the
user, as notify_user does now.

## Installation

The user installs one binary: `pantheon` for their OS and architecture, from
the release page or the install script. It replaces `fleet`. Everything else
is brought by the binary, pinned and verified (sha256 in the release set):

* **Python.** The binary installs a managed interpreter with uv: a pinned uv
  binary and a pinned Python 3.12 build, under `~/.pantheon/runtime`.
  * The node runs App install hooks with this interpreter first on `PATH`, so
    an App's environment no longer depends on the machine's `python3`.
  * App environments are still built by the App's own install hook
    (`portable_runtime/install.py`).
* **Local mode.** `pantheon up` runs the controller, the message broker and the
  node. They are Go and are linked into the same binary; no separate bundle
  binaries are needed. The node installs the Pantheon agent and the other Apps
  of the release set, as on a cloud node.
* **The agent's chat.** `pantheon agent chat` runs the Agent App's terminal
  frontend (above) for the local or, signed in, the cloud Agent.

Nothing is installed with `pip` by hand. Packages come from the release set,
built from source; nothing is taken from an unpinned index.

The Python package keeps its own entry point under the name `pantheon-agent`,
for developing the Agent's backend. Users never need it.

## Migration

| Today | Then |
|---|---|
| `fleet up`, `fleet credentials`, `fleet capture` | `pantheon node up`, `pantheon node credentials`, `pantheon node capture`. `fleet` stays as an alias of `pantheon node` for nodes already installed, the macOS app and auto-update. |
| `pantheon cli` | `pantheon agent chat`; `pantheon cli` stays as an alias for a while |
| `pantheon ui`, `pantheon setup`, `pantheon update-templates`, `pantheon sync-templates` | `pantheon ui`, `pantheon setup`, `pantheon templates update`, `pantheon templates sync` |
| `pantheon store` | `pantheon apps search/install`, `pantheon skills search/adopt/publish` |
| `pantheon local*` | `pantheon up` and `pantheon setup` |
| Release assets `fleet-<os>-<arch>` | `pantheon-<os>-<arch>`; the updater fetches the new name |

## Phases

1. **The command.** The `pantheon` binary (the fleet binary under a second
   name; `fleet` stays) with `login`, `apps`, `dev`, `desktop`, `browser`,
   `nodes`, `run` and `transfer` (`fleet/internal/pantheoncli`). It uses the
   owner's authority. It is usable at once by outside agents and people. The
   App gateway now waits 660 s for an App's reply, past the 600 s an App RPC
   may run (it cut every call at 120 s).
2. **The gateway.** Conversation tokens through `run_command`'s `env`;
   structured events and the UI's cards for commands; `pantheon task`.
3. **Four tools.** The Pantheon agent declares read, write, update and
   run_command. The prompt and the skills move to the command. The
   `pantheonos_*` tools are removed.
4. **Sub-agents as processes.** `pantheon agent run`, the child protocol, the
   event relay, and the UI's sub-agent card keyed on child messages.
   `call_agent` is removed.
5. **The terminal chat as a frontend.** The REPL becomes an RPC client,
   declared as the Agent App's `entry.terminal`; `pantheon agent chat` fetches
   and runs it.
6. **One install.** Managed Python, local mode in the binary, the new release
   assets and install scripts.

Each phase ships to staging on its own.
