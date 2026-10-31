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
(docs/agent/pantheonos-app-interface.md), which stay on staging until this lands.
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
  pantheon agent run --profile FILE.md | --team NAME|FILE.md | --resume RUN_ID <task>
  pantheon agent runs | wait <run_id> | stop <run_id>
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

How the variables are made and delivered:

* `PANTHEON_ENDPOINT` and `PANTHEON_GRANT` are a grant to the Agent App's own
  `pantheon_command` method and nothing else. The allocator issues it to the
  Agent App like any dependency grant (the `pantheon` profile:
  provider and consumer are both the Agent, interface `pantheon-command@1`),
  and renews it while the Agent runs. A grant leaked from the sandbox reaches
  only that method, which still needs a valid token.
* `PANTHEON_TOKEN` is signed by the Agent App with a per-process key and names
  the conversation, the agent and the tool call. It expires after six hours
  and with the Agent process.
* `run_command` has an `env` argument that the Agent fills in and the model
  never sees (it is removed from the model's schema). The shell writes the
  variables as `export` lines into the session's input, so no process
  listing shows them, and unsets them when the command ends. The result
  shows only the command the model gave.
* A command on another of the owner's nodes (`run_command` with `node`) gets
  them as its task's environment (`run_on_node` `env`), set in the task's
  process rather than its command line. A delegated node (HPC) is not given
  them: it runs commands through a batch script.
* A node installed only as `fleet` puts a `pantheon` linked to its own binary
  at the end of its commands' `PATH`.

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

Each takes the optional `node` argument, as today (docs/agent/agent-core.md).

Everything else is either a file or a command:

| Was | Now |
|---|---|
| App tools, `pantheonos_*` | `pantheon apps …`, `pantheon dev …` |
| Desktop and browser tools | `pantheon desktop …`, `pantheon browser …` |
| Fleet tools | `pantheon nodes …`, `pantheon run`, `pantheon transfer` |
| `call_agent` | `pantheon agent run` |
| `background_task` | `run_command` in the background (`_background`, or past a minute); `pantheon jobs` follows it |
| Task tools | `pantheon task …` |
| Skills | `pantheon skills view` (they live with the Agent, not on the workspace); the index stays in the prompt; `pantheon skills search/adopt/…` for the Store |
| Memory | files, unchanged |

A call to any other tool in an older conversation is shown to the model as
the `run_command` of the `pantheon` command that does the same
(`as_command` in `apps/agent/pantheon/agent_core_tools.py`); the history keeps the call
as recorded, which the UI renders.

The system prompt says that `pantheon` is how Apps, the desktop and other
nodes are reached, and that `pantheon --help` lists the commands. Long
procedures stay in skills (app-development, desktop).

## Sub-agents are processes

A sub-agent is a separate agent process: the same agent program with a fresh
context. It returns only its final answer. Several run in parallel when the
leader starts several commands.

`pantheon agent run` starts it: the gateway's `agent.start` runs the
leader's `call_agent` with the command's call as the parent, and that runs the
sub-agent's loop in its own process (`apps/agent/pantheon/team/subagent_child.py`, driven
by `apps/agent/pantheon/team/subagent_process.py`). The command asks `agent.wait` until
the answer is there, so no request outlasts the App gateway. A sub-agent's
tool calls stay in the foreground: its own commands cannot follow its
background work.

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
   * the child sends its model requests, its tool calls, its compactions and
     its step messages (one JSON object per line: `call`, `step`, then `done`
     or `failed`);
   * the Agent App sends `start`, then a `reply` to each call, a `steer` with
     the messages the user sends this sub-agent (the Team view; read at its
     next turn, and the ones it ends without reading come back in `done` as
     `unread`), and a `stop` when the run or this sub-agent is stopped (it
     ends the process if the child does not).

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
* `--detach` returns at once with a run id. `pantheon agent wait <id>`
  collects the answer later.

### Which agent, and continuing one

`pantheon agent run` takes what to run as:

* `<profile>` or `--profile NAME`: a profile of the caller's team (the
  conversation's; a sub-team's leader starts its own team's members).
* `--profile FILE.md`: an agent file. The command reads it where it runs and
  sends its text; the Agent builds it as a team of one, in the caller's mode.
* `--team NAME|FILE.md`: a team (a template by name, or a team file, sent with
  the agent files it names by path, read relative to it). Its leader runs, and
  starts its members as the conversation's leader starts its own;
  `--profile MEMBER` runs one member instead.

A value ending in `.md` or holding a path separator is a file. A team or agent
defined this way is built for the conversation beside its own team
(`ChatRoom.sub_team`): its members get identities of their own, so they never
take the conversation's members' instances, and no App its starter may not
use (`AppFilter.within`). The definition is kept with the run
(`sub_agent_sources` in the conversation's metadata), so the run can be
continued after its files change or the Agent restarts.

A run's id is its execution context with `.` for `|`
(`<root>.d1.<profile>.<rand>`): every run prints it, `pantheon agent runs`
lists the conversation's runs (id, profile, status, task), and
`pantheon agent run --resume <id> <message>` continues one, as the user's
message in the Team view does: a working run reads it at its next turn, a
finished one works on with it (the lead gets no note of its own message).

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
* **On a node.** `pantheon agent chat --node <node> [--workdir <dir>]` puts
  the conversations it opens or continues on that node (`set_chat_node`), so
  the Agent's files and commands are there rather than on the workspace. The
  Coding Agents App starts the Pantheon Agent this way, with the computer and
  folder it was started from.
* **One-shot.** `pantheon agent chat -i "<message>"` sends one message,
  prints the answer and exits. This is today's `pantheon cli -i`, now against
  any of the owner's Agents. Scripts and outside agents use it.

### One turn as JSON lines

`pantheon agent chat --json [--chat ID] [--node N [--workdir DIR]] [--model M]
[-- MESSAGE]` runs one turn without the terminal frontend: the command itself
calls the Agent App (`create_chat` with the model, or `set_agent_model` on a
continued chat's first agent; `set_chat_node`; the event cursor; `chat`) and
turns its event stream into one JSON object per line. Nothing is fetched or
installed first. The message is the words after `--`, after `-i`, or stdin.
The Coding Agents service runs it for the Agent Canvas
(`docs/apps/coding-agents.md`).

```
{"type":"chat","chat_id":"…"}                          first: the conversation
{"type":"text","text":"…"}                             the Agent's text
{"type":"tool_call","id":"…","name":"file_manager__read_file","args":{…}}
{"type":"tool_result","id":"…","name":"…","output":"…","is_error":true}
{"type":"done","chat_id":"…","status":"finished|error|stopped","error":"…",
 "model":"…","usage":{"input_tokens":…,"cached_tokens":…,"output_tokens":…,
 "context_tokens":…,"context_window":…}}
```

A call made inside another (an App tool a `pantheon` command ran, a
sub-agent's steps under `pantheon agent run`) has `parent`, the outer call's
id, and `context`, its execution context. A tool result's output is capped at
16 KB. `usage` adds up the main agent's model requests. Text streams as whole
steps, not as pieces. The turn ends at the Agent's `chat_finished`; a turn
longer than the call that runs it (ten minutes) is followed on the stream, and
the conversation is asked whether it still runs when the stream stays quiet.
SIGINT or SIGTERM stops the Agent's turn (`stop_chat`) and ends with
`stopped`. A failure before the turn starts (not signed in, no Agent, a node
that is not the user's) is one line `{"type":"error","error":"…"}` and exit
status 1.

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
  node. They are Go and are linked into the same binary (`pantheon node
  controller`, `pantheon node broker`, `pantheon node up`); no separate bundle
  binaries are needed. The node installs the Pantheon agent and the other Apps
  of the release set, as on a cloud node.
  * The owner that composes the deployment and supervises the three is the
    product bundle's own Python: `owner/` holds the pantheon package it was
    built from (`pantheonos.local`) and the owner's hash-pinned requirements
    (`os/python/pantheonos/local/owner-requirements.lock`, about twenty packages). It runs
    in the managed interpreter, in an environment kept per requirements
    digest. The same holds for `pantheon local`, `local-setup`,
    `local-update` and `local-release` when they name a bundle or launch.
  * `pantheon up` runs the launch it keeps in `~/.pantheon/local` (or the one
    given) in the foreground; Ctrl-C stops the Apps. `pantheon down` asks that
    owner to stop them; `pantheon status` asks the profile's controller.
  * The first `pantheon up` sets one up. It reads the published manifest
    (GitHub release `pantheon-local-latest`, asset `pantheon-local.json`:
    version, versioned release tag, default Store and each platform's bundle
    with its sha256), downloads this machine's bundle once into
    `~/.pantheon/bundles/<version>`, and asks for the workspace folder and a
    model: Ollama or LM Studio found on this machine (with their models), or
    an OpenAI-compatible API (the key is read without echo). The product's
    own `local-setup` saves the answers.
  * The node there, like every machine node, runs its Apps and their install
    hooks with the managed interpreter first on `PATH`.
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
| Release tags `fleet-v<version>` | `pantheon-v<version>`. Nodes released after `fleet-v0.5.1-staging.2` update from either; releases keep the `fleet-v` tag until no older node remains, since those accept only it. |
| The Agent release's `fleet-app-transport` | `pantheon-app-transport`; the model client finds either, so releases built before the rename keep working |

What keeps its name, because changing it would cut off what is already running:

* wire names: NATS subjects (`fleet.<id>.…`), the node KV bucket, NATS
  account and user names, libp2p protocol ids, the `X-Fleet-*` headers, the
  Hub's `/api/fleet/*` routes, `fleet.json` App manifests and the `fleet` App;
* saved state: a node's state directory and credentials file (a renamed path
  is a new node), the controller's state directory;
* the macOS app's bundle id (`org.pantheonos.fleet`) and its `fleet`
  executable: macOS keys the user's Files and screen-recording grants to it;
* public addresses: the `fleet-latest` install alias and the controller host
  names, which installed nodes have saved.

## Phases

1. **The command.** The `pantheon` binary (the fleet binary under a second
   name; `fleet` stays) with `login`, `apps`, `dev`, `desktop`, `browser`,
   `nodes`, `run` and `transfer` (`os/internal/pantheoncli`). It uses the
   owner's authority. It is usable at once by outside agents and people. The
   App gateway now waits 660 s for an App's reply, past the 600 s an App RPC
   may run (it cut every call at 120 s).
2. **The gateway.** Conversation tokens through `run_command`'s `env`;
   structured events and the UI's cards for commands; `pantheon task`
   (`apps/agent/pantheon/chatroom/command_gateway.py`, the CLI's agent mode in
   `os/internal/pantheoncli/agent.go`). An App call a command makes is
   recorded as child messages of the `run_command` call (execution context
   `<chat>|cli|<call>`); the UI lists them under the command.

Nodes keep their own credentials from the commands run on them
(`os/childenv`): shell sessions, terminals, tasks and supervised processes
get no Fleet key, broker credential or platform credential, and the Hub no
longer puts the platform's Modal credentials in a sandbox.
3. **Four tools.** The Pantheon agent declares read, write, update and
   run_command (`CORE_TOOLS`). The prompt, the profiles and the skills move to
   the command (`pantheon agent`, `pantheon skills`, `pantheon jobs`,
   `pantheon task outputs`). The `pantheonos_*` tools are no longer declared;
   the gateway still carries requests out with them. The UI renders task and
   notification cards and sub-agent cards from a command's child messages.
4. **Sub-agents as processes.** `pantheon agent run` starts the sub-agent's
   loop in a child process; the Agent App answers its model, tool and
   compaction calls and relays its steps (child messages of the command's
   call, shown as the sub-agent card under it). `call_agent` is no longer
   declared to the model.
5. **The terminal chat as a frontend.** Done for the cloud Agent. The
   package's `terminal.py` (`entry.terminal`) runs the REPL on
   `apps/agent/pantheon/repl/remote.py`: each chat-room call is an App RPC, live output
   is the event stream (`read_agent_events`), and an interrupt stops the
   turn. The Agent App serves the frontend of its own revision
   (`agent_terminal_frontend`, 512 KiB chunks with its digest).
   `pantheon agent chat [-i MESSAGE] [--chat ID] [--resume [N]]` keeps it per
   revision under `~/.pantheon/runtime/terminals/<revision>`, builds its
   environment from the package's hash-pinned requirements, and runs it
   against a loopback this command serves with the owner's grant (a per-run
   token; the frontend holds no grant). For now the frontend is the whole
   Agent package and its requirements, not a smaller set. Still to do: local
   mode (after `pantheon up`) and `pantheon apps open --terminal` for other
   Apps.
6. **One install.** Done in code:
   * The managed Python (`os/internal/pyruntime`): a pinned uv with its
     published sha256 and the Python 3.12 it installs, under
     `~/.pantheon/runtime`. The terminal chat and local mode use it, and a
     machine node installs it once at start and puts it first on its Apps'
     `PATH`, so their install hooks build environments with it
     (`--system-python` keeps the machine's python3; Slurm job nodes use
     that, their home being shared storage).
   * The controller (`os/internal/controller`) and the NATS server
     (`os/internal/broker`, nats-server v2.14.7) linked into the binary.
   * `pantheon up | down | status` (`os/internal/localmode`) over the
     bundle's own owner, which takes this binary for all three
     (`PANTHEON_FLEET_BINARY`); bundles need no executables. Checked on a
     Mac with a darwin-arm64 bundle and a local Ollama model: first start
     165 s with all thirteen Apps ready (their installs included), a restart
     plus one Agent reply 75 s, `pantheon down` 12 s with nothing left
     running.
   * Releases publish `pantheon-<os>-<arch>` with `fleet-<os>-<arch>` copies;
     the updater prefers the new name. The installers put `pantheon` on
     `PATH` with `fleet` as an alias and start `pantheon node up`.
     `pantheon node X` and `fleet [node] X` are the same command.

   * The first start: the published bundle downloaded and checked, and the
     workspace and model asked in the terminal.

   Published as staging prereleases: the Fleet release `fleet-v0.5.1-staging.2`
   (staging nodes follow their controller's latest tag; `fleet-latest` and
   production are unchanged) and the local bundles `pantheon-local-v0.9.75`
   for macOS and Linux on arm64 and amd64. Still to do: `pantheon agent chat`
   in local mode (today `pantheon up --agent agent [-i MESSAGE]` runs the
   owner's terminal client) and Windows.

Each phase ships to staging on its own.
