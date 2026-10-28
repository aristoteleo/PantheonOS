# Coding agents on Fleet nodes

People often have other coding agents installed on their machines: Claude Code,
Codex, Gemini CLI, Cursor Agent and others. PantheonOS finds them on every
Fleet node, lists them in the desktop, and runs them in two ways:

- **Terminal.** The agent's own interactive interface in a Terminal window on
  the node. It behaves exactly as it does in a local terminal.
- **Conversation.** The agent's headless mode, with its output shown as a
  conversation in the Coding Agents App. The Pantheon Agent drives the same
  path to hand a task to one of them.

Nothing is installed or configured on the node. An agent signs in, bills and
keeps its sessions the way it always does; PantheonOS starts the binary the
user already has.

## Catalog

`os/codingagents` holds one entry per agent: an id, a label, the command names
that identify it, and how to start it. Detection uses every entry; terminal
and conversation launches use the fields an entry fills in.

| Id | Command | Terminal | Conversation | Full-auto flag |
|---|---|---|---|---|
| `claude` | `claude` | yes, resume | `-p --output-format stream-json` | `--dangerously-skip-permissions` |
| `codex` | `codex` | yes, resume | `exec --json` | `--dangerously-bypass-approvals-and-sandbox` |
| `gemini` | `gemini` | yes, resume | `-p -o stream-json` | `--yolo` |
| `cursor` | `cursor-agent` | yes, resume | `-p --output-format stream-json` | `--force` |
| `opencode`, `copilot`, `aider`, `goose`, `amp`, `qwen`, `kimi`, `crush`, `droid`, `cline`, `auggie`, `kiro` | their CLI | yes | no | where the CLI has one |

Conversation mode needs a stable machine-readable stream and a way to resume a
session from a later process. Claude Code, Codex and Cursor Agent were checked
against their current releases. Gemini CLI follows its documented format; its
parser has a fixture but no live run behind it.

## Detection

A node finds an agent by looking for its command, without running it:

1. **The login shell's PATH.** A node started by launchd, systemd or a
   container entrypoint has a short PATH. The node runs `$SHELL -ilc` once,
   prints PATH between two markers and puts that order first. The probe has a
   10 second limit and is cached for five minutes.
2. **Usual install directories**, for CLIs set up by a version manager the
   login shell does not load: nvm (default alias first), volta, asdf, fnm,
   mise, `~/.local/bin`, `~/.npm-global/bin`, `~/.claude/local`, pnpm, yarn,
   bun, Homebrew, `/usr/local/bin`, nix, snap.

A hit must be a regular file with the executable bit. Detection reads no
credentials. The Coding Agents service adds each agent's version
(`--version`, cached until the binary changes) and whether a sign-in exists:
a credential file or API key variable, the Keychain item's presence for Claude
Code on macOS, `cursor-agent status` for Cursor. It never reads a credential;
when it cannot tell it says nothing.

The node reports what it found as `capability.coding_agents`
(`[{id, label, path}]`). It detects again every five minutes, so an agent
installed later appears without restarting the node. The Coding Agents service
also has a refresh call that detects at once; the next heartbeat carries the
result.

## The `coding-agents` service

A Go builtin on each node, started on demand the same way the node's Files and
PTY services are (`node_service` with `service: "coding_agent"`). It needs the
`proc` capability. Methods:

| Method | Does |
|---|---|
| `agents_detect(refresh)` | The detected agents, with which modes each supports. |
| `agent_terminal_command(agent, resume, auto)` | The argv for a terminal launch. |
| `agent_send(agent, cwd, prompt, conversation_id, model, auto)` | Starts a turn. A new conversation when `conversation_id` is empty. |
| `agent_stop(conversation_id)` | Interrupts the running turn. |
| `agent_conversations(agent, cwd)` | Conversations on this node, newest first. |
| `agent_conversation(conversation_id, after)` | One conversation's events, for replay. |
| `agent_delete(conversation_id)` | Removes a conversation's record. The agent's own session stays. |

A turn is one process: the agent's headless command with the prompt on stdin
(as an argument for agents that do not read stdin), resuming the agent's own
session id from the previous turn. Its stdout is parsed line by line into
events, appended to the conversation's log, and published on
`pantheon.stream.coding_<conversation_id>`. stderr is kept for the error shown
when a turn fails.

Conversations live under `~/.pantheon/coding-agents/` on the node: an index
and one event log per conversation. A conversation records the agent, the
directory, a title (the first prompt), the agent's session id and the time of
its last turn.

### Events

Every agent's stream is mapped to one shape:

```json
{"seq": 12, "type": "tool", "id": "toolu_…", "name": "Bash",
 "input": "cat a.txt", "status": "done", "output": "hello"}
```

| Type | Fields |
|---|---|
| `user` | `text` — the prompt that started the turn |
| `session` | `session_id`, `model` |
| `text` | `text`, `delta` when the agent streams pieces |
| `thinking` | `text`, `delta` |
| `tool` | `id`, `name`, `input` (a one-line summary), `status` (`running`, `done`, `error`), `output` (capped at 4 KB) |
| `done` | `error` when the turn failed; `duration_ms`; `input_tokens` (cached included), `cached_tokens`, `output_tokens`; `context_tokens` and `context_window` where the agent reports them; `cost_usd` (Claude Code: the price at API rates, not a charge on a subscription) |
| `limits` | `limits`: the account's quota windows (`five_hour`, `seven_day`, …), each `used` (0–1) and `resets_at` |

Lines a parser does not recognise are dropped. A quota (`limits`) is kept
with the conversation and with the agent, so the agent list shows the last
one reported on that node. A turn without a `done` from
the agent gets one from the service when the process exits, carrying the exit
status and the tail of stderr.

## Terminal launch

`pty_open` takes an optional `command`. The PTY runs it through the user's
shell as `$SHELL -ilc '<command>; exec $SHELL -i'`, so the agent sees the same
environment as in a terminal, and the window drops to a shell when the agent
exits instead of closing. The command is built by the Coding Agents service
from the catalog: the detected path, the full-auto flag and a resume id. A
launch without a folder starts in the user's home directory.

An agent runs with the workspace environment minus the markers of another
agent session (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, …): a node started from
a terminal inside such a session would otherwise pass them on, and the agent
would treat itself as that session's child. Configuration the user sets, such
as `CLAUDE_CONFIG_DIR`, is kept.

## Permissions

Both modes start agents in full-auto mode by default: the agent edits files
and runs commands without asking. The Coding Agents App has a per-launch switch
that turns it off. In a terminal the agent then asks in its own interface; in a
conversation a turn that needs approval stops with the agent's denial.

Claude Code refuses its full-auto flag as root. A node running as root is
normally a disposable workspace container, so the service sets `IS_SANDBOX=1`
for Claude there.

## Where it shows

- **Coding Agents App.** Agents grouped by node; per agent, its conversations
  and buttons to start a conversation or open a terminal in a chosen
  directory. A conversation view streams events and has a composer, Stop, and
  "Continue in terminal", which resumes the same session interactively.
- **Fleet.** Each node row lists the agents found on it; a name opens a
  terminal with that agent, and the node menu opens Coding Agents there.
- **Terminal.** The new-session menu offers the agents found on the selected
  node.

## Pantheon Agent

The Fleet App's `node_call` accepts `service: "coding_agent"` with the
`agents_detect`, `agent_send`, `agent_conversation` and `agent_conversations`
methods. `pantheon coding-agent` wraps them:

```
pantheon coding-agent list [--node N]
pantheon coding-agent run --node N --agent claude --cwd DIR [--conversation ID] PROMPT
pantheon coding-agent show ID --node N
```

`run` waits for the turn and prints the agent's final message and the
conversation id; a later `run --conversation ID` continues it. The Agent learns
about the command from its CLI reference. Its runs appear in the Coding Agents
App like any other conversation.

## Limits

- Windows nodes are detected but have no terminal launch (the PTY backend is
  Unix only). Conversations run there.
- Agents that ask to trust a folder before their first run in it (Gemini CLI)
  may need one terminal run in that folder first.
- Subscription limits, sign-in and billing belong to the agent and the user's
  own account.
