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
against their current releases. Gemini CLI follows its documented format and
the tool names of its source; its parser has fixtures but no live tool stream
behind it (see `os/codingagents/testdata/README.md`). Headless, Gemini CLI
refuses a folder it does not trust yet, so its turns run with
`GEMINI_CLI_TRUST_WORKSPACE=true` (that run only, and only when the user's
environment for it does not set the variable; a terminal launch still asks).


The first entry is the user's own **Pantheon Agent**: the terminal chat of the
node's own `pantheon` command (`pantheon agent chat`, resumed with
`--chat ID`). Detection takes the node's command (`PANTHEON_CLI_BIN`) rather
than whatever `pantheon` is on PATH, so the chat matches the node. It signs in
with the node's key, so it counts as signed in only while agents on that node
may use PantheonOS. It has no conversation mode and no hooks.
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
when it cannot tell it says nothing. A sign-in that exists may still not serve: an
agent counts as not signed in (`signed_in: false`, with `sign_in_note`
saying why) after a turn on the node failed for its sign-in or its account,
or when the provider's answer to the quota read refuses the account (Gemini
CLI's Code Assist lists it only among `ineligibleTiers`). That holds until a
turn succeeds, a quota read finds the account served, or its sign-in files
change; the service keeps it in memory, so a restart forgets it.

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
| `agents_detect(refresh)` | The detected agents, with which modes each supports, and `canvas` for each with a headless mode (below). |
| `agent_terminal_command(agent, resume, auto)` | The argv for a terminal launch. |
| `agent_send(agent, cwd, prompt, conversation_id, model, auto)` | Starts a turn. A new conversation when `conversation_id` is empty. A conversation keeps its agent: a send naming another fails ("This conversation runs Codex; start a new one to use Gemini CLI"). |
| `agent_stop(conversation_id)` | Interrupts the running turn. |
| `agent_conversations(agent, cwd)` | Conversations on this node, newest first. |
| `agent_conversation(conversation_id, after)` | One conversation's events, for replay. |
| `agent_delete(conversation_id)` | Removes a conversation's record. The agent's own session stays. |
| `agents_skills(cwd, agent)` | One agent's skills (`agent` defaults to `claude`), from where that agent loads them, with the node's settings for it (its command and environment). Claude Code: the user's, the project's (not again in the home folder), the skills and commands of the plugins enabled there (`source: plugin`, named `<plugin>:<name>`), the skills synced from the signed-in account (`skills/synced/<organization>_<account>` under its config folder, the ids from `.claude.json`; `source: account`, named `anthropic-skills:<name>`), then those that come with Claude Code (dataviz, code-review, …; `source: builtin`, no `path`): they live inside its executable, so they are the plain names of the `skill_listing` record at the start of Claude Code's newest session in `cwd` (`projects/<cwd as a folder name>/*.jsonl`, the home folder's when `cwd` has none), with the descriptions listed there; none whose `skillOverrides` is `off`, and none that come with it under `disableBundledSkills` or `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS`. Codex: what its app-server's `skills/list` says for `cwd` (`source` `user`, `project`, `system`, `admin` or `plugin`), or, when Codex cannot be asked, `.codex/skills` and `.agents/skills` of the project, `$CODEX_HOME/skills` (`.system` inside it as `system`; `CODEX_HOME` as Codex runs with it) and `~/.agents/skills`. Gemini CLI, in its order, a later one replacing an earlier one of the same name: its built-in skills (`bundle/builtin` next to the CLI, `system`), those of the extensions enabled for `cwd` (`~/.gemini/extensions`, a linked one where it points, `extension-enablement.json`; `source: extension`, `plugin` the extension), `~/.gemini/skills`, `~/.agents/skills`, then the project's `.gemini/skills` and `.agents/skills`; only skills whose front matter has a name and a description, none of those in its settings' `skills.disabled`, and none at all when `skills.enabled` is false (its home is `GEMINI_CLI_HOME` when set). Cursor Agent: `skills` under `.cursor`, `.claude`, `.codex`, `.grok` and `.agents`, in the project, then in the home directory, then its built-in `~/.cursor/skills-cursor` (`system`); its plugins' skills are not listed (which plugins are on is the account's to say). Another agent has none. |
| `agents_repo_info(path)` | A folder's commit (hash, short, subject), branch and local branches. |
| `agents_folder_tree(path, dir)` | A folder's subfolders with recursive file counts (no `.git`, `node_modules`, hidden folders; bounded at 100k files / 2 s). |
| `agents_reference_put(cwd, upload_id, name, offset, data, final)` | Uploads a reference image in ≤ 48 KB chunks into `<cwd>/.pantheon/references` (PNG, JPEG, WebP, GIF; ≤ 20 MB and 40 M pixels). |
| `agents_reference_list(cwd)` / `agents_reference_thumb(cwd, path)` | The uploaded images; a ≤ 40 KB JPEG preview of one. |
| `agents_file_read(conversation_id, path, offset, length)` | A ≤ 48 KB piece (base64, with `size` and `mime`) of a file the conversation worked with: inside its folder, or one its log names (the path, or the name and its folder). The node's file service reaches only shared folders; this reaches what the agent read or made, for the canvas's previews. |

A turn is one process: the agent's headless command with the prompt on stdin
(as an argument for agents that do not read stdin: Gemini CLI's joined to its
flag, `--prompt=<text>`, Cursor Agent's last, after `--`, so a prompt that
starts with a dash is never read as an option), resuming the agent's own
session id from the previous turn. Its stdout is parsed line by line into
events, appended to the conversation's log, and published on
`pantheon.stream.coding_<conversation_id>`. stderr is kept for the error shown
when a turn fails.

Conversations live under `~/.pantheon/coding-agents/` on the node: an index
and one event log per conversation. A conversation records the agent, the
directory, a title (the first prompt), the agent's session id and the time of
its last turn. A turn running when the service stops (or dies) ends with a
`done` event saying it was interrupted, and the conversation is marked
`interrupted`; the next message resumes the agent's session.

### Canvas turns

A turn may carry the Agent Canvas options of `agent_send`: `effort`,
`subagents` (`[{name, description, prompt, write, model}]`), `focus`
(folders), `files` (pinned files), `research`, `images` and `skills` (pinned
skills, by name). They are checked the same way for every agent (folders and
files exist, images are absolute paths on the node) and then said in the
agent's own terms, as far as its capabilities go; an option the agent cannot
take is left out of its turn, never half-applied. The conversation keeps the
latest as `canvas`, as the agent took it (an option it cannot take left out;
research on where it cannot be switched off).

`agents_detect` reports each headless agent's capabilities as `canvas`:

```json
{"effort": ["low", "medium", "high", "xhigh"], "research": true, "images": true,
 "skills": true, "roles": false, "subagents": false, "models": null}
```

`effort` is the levels it takes (null: none); `research`, whether web
research can be switched off; `images`, whether reference images reach it;
`skills`, whether it has skills (`agents_skills`); `roles`, whether sub-agent
roles can be defined for a turn; `subagents`, whether its own sub-agents show
in its events; `models`, a short list to offer besides the default (null:
only the default).

| | Claude Code | Codex | Gemini CLI | Cursor Agent |
|---|---|---|---|---|
| effort | `--effort` (low…max) | `-c model_reasoning_effort=…` (max runs as xhigh) | — | — |
| research off | `--disallowedTools WebSearch,WebFetch` | `-c web_search="disabled"`; the shell in the `workspace-write` sandbox with its network off (`--sandbox workspace-write` before `resume`, `-c sandbox_workspace_write.network_access=false`; full auto then `-c approval_policy="never"` instead of `--dangerously-bypass-approvals-and-sandbox`); and told not to reach the network | — | — |
| focus, files outside the folder | `--add-dir` | `--add-dir` (before `resume`) | `--include-directories` (a folder with a comma is refused: the CLI splits the list there) | `--add-dir` |
| pinned files | listed after the prompt | same | same | same |
| pinned skills | — (typed as `/name`) | listed after the prompt with their `SKILL.md` | same | same |
| images | listed after the prompt, read with Read | `-i <path>` each, and listed | — | listed after the prompt |
| the turn's settings (focus, roles, research) | `--append-system-prompt` | before the prompt (`exec resume` keeps a thread's first developer instructions and ignores new ones) | before the prompt | before the prompt |
| roles (`subagents`) | `--agents`; read-only roles get Read, Grep, Glob and, with research on, WebSearch and WebFetch | — | — | — |
| models | opus, sonnet, haiku | — | — | — |
| skills | yes | yes | yes | yes |
| own sub-agents as nodes | yes | no | no | no |

Each turn says its settings again (a resumed session remembers earlier
ones), including research and roles switched back off. What is not in the
table was not possible or could not be confirmed on the agent's current
release: Gemini CLI's `--policy` (to deny its web tools) and `@path` images
need a signed-in run to confirm; Cursor Agent has no switch for its web
search; Codex's sub-agents do not show in `exec --json`
(`os/codingagents/testdata/README.md`).

Research off removes the agent's web tools and tells it not to use the web.
On Codex it also runs the turn in Codex's own sandbox with the network off:
the folder and the `--add-dir` folders stay writable, and a `curl` or a
Python `urllib` call in its shell fails to resolve the host. Elsewhere it does
not firewall the turn: Claude Code's Bash could still reach the network.

Pinned skills (`skills`, for agents other than Claude Code) are found among
the agent's own skills for the folder, as `agents_skills` lists them, and
named after the prompt: "Skills I pinned for this turn (read each SKILL.md
first and follow it):" then `- <name>: <SKILL.md path>`. A name the agent
has no skill of fails the send ("moon is not a Cursor Agent skill on this
node"). Each pinned skill gets a `skill` tool event (done, `path` its
`SKILL.md`) after the `user` event. Skills a Claude Code prompt starts with
(`/name …`) are expanded by Claude Code without a tool call, so the service
adds a `Skill` tool event (done) for each after the `user` event.

### Events

Every agent's stream is mapped to one shape:

```json
{"seq": 12, "type": "tool", "id": "toolu_…", "name": "Bash",
 "input": "cat a.txt", "status": "done", "output": "hello"}
```

| Type | Fields |
|---|---|
| `user` | `text` — the prompt that started the turn; `images` and `branch` for a canvas turn |
| `session` | `session_id`, `model` |
| `text` | `text`, `delta` when the agent streams pieces |
| `thinking` | `text`, `delta` |
| `tool` | `id`, `name`, `input` (a one-line summary: for agents other than Claude Code, what a call looks for before where, e.g. a search's pattern before its folder, else its first short text argument), `kind`, `path`, `status` (`running`, `done`, `error`), `output` (capped at 4 KB) |
| `done` | `error` when the turn failed; `duration_ms`; `input_tokens` (cached included), `cached_tokens`, `output_tokens`; `context_tokens` and `context_window` where the agent reports them; `cost_usd` (Claude Code: the price at API rates, not a charge on a subscription) |
| `limits` | `limits`: the account's quota windows (`five_hour`, `seven_day`, …), each `used` (0–1) and `resets_at` |

`kind` says what a tool call does, in the same words for every agent:
`read`, `write`, `shell`, `search` (the web), `fetch` (a web page), `skill`,
`agent` (a sub-agent launch: Claude Code's Agent, Cursor Agent's task, Gemini
CLI's `invoke_agent` and `codebase_investigator`) or `other` (a search
through the code among others); `path` is the file a `read` or `write` is about, as the agent gave
it. Both come with the event that names the tool (a result that carries only
the id has neither). Commands stay `shell`, also when they read files. A skill
shows as `skill` with its name as `input`, whichever way the agent opens it:
Claude Code's Skill tool, Gemini CLI's `activate_skill`, Codex reading its
`SKILL.md` through the shell, Cursor Agent reading it with its read tool. A
read counts as a skill only for a `…/skills/<name>/SKILL.md` path (or
`skills/.system/<name>`, Codex's own); in a shell line, only a plain read
(`cat`, `head`, `tail`, `nl`, `less`, `bat`, `sed -n` without `-i`) without an
output redirection, in any command of a `;`, `&&`, `||` or `|` list, with
quotes read as the shell reads them. A Codex file change is one `write` event
per file: the first has the item's id, the others `#2`, `#3`, … after it. A
Codex web search has its item's id (its line also carries the search call's,
under the same key) and is known for a page opened only when it completes: it
starts as `search` and ends as `fetch` then.

Claude Code's sub-agents: every event from inside one has `parent`, the id of
the tool call that launched it. The launch (tool `Agent`, `Task` in older
versions) has `subagent` (its type), `input` (the description) and `text` (the
task). Progress reports update its `activity`; it turns `done` or `error` when
the sub-agent ends (`stopped` when the turn was stopped), with the final report
as `output` (up to 64 KB). Background sub-agents end by notification, not by the
launch's tool result.

Lines a parser does not recognise are dropped. A quota (`limits`) is kept
with the conversation and with the agent, so the agent list shows the last
one reported on that node. A turn without a `done` from
the agent gets one from the service when the process exits. A failed turn's
error is one line that names the agent, without stack frames, dumped objects
or the JSON of an API error (a URL in the message is kept): "Gemini CLI
could not sign in: This client is no longer supported … https://…" when the
output says the sign-in or the account failed (or the agent is known to have
no sign-in here), else "Codex exited (exit status 1): <the line that says
what failed>". An error the agent reports itself in one line stays as it
wrote it. Other errors name the agent too: "Codex is not installed on this
node", "Could not start Codex: …".

## Terminal launch

`pty_open` takes an optional `command`. The PTY runs it through the user's
shell as `$SHELL -ilc '<command>; exec $SHELL -i'`, so the agent sees the same
environment as in a terminal, and the window drops to a shell when the agent
exits instead of closing. The command is built by the Coding Agents service
from the catalog: the detected path, the full-auto flag and a resume id. A
launch without a folder starts in the user's home directory.

The agent runs as a child of the launch's subshell, not in its place, so the
subshell outlives it and posts its exit status as the `Exit` hook (below)
before the window drops to the shell. Whatever ended the agent (the user
quit it, it crashed, it updated itself), the service knows, and the desktop
offers it back in the same tab (Coding Agents App, below). The subshell
ignores Ctrl-C while it waits (`trap : INT`); the agent gets it as usual.

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

## Agents using PantheonOS

An agent can use PantheonOS itself through the `pantheon` command: the
user's desktop and its windows, Apps (notebooks, model services and others),
the other Fleet nodes and their files, models, and the Pantheon Agent.

- **Key.** The user turns this on per node in Coding Agents. The desktop
  makes a platform key named `Coding agents · <node>` and hands it to the
  node's service (`agents_access`), which keeps it in its settings file
  (mode 600). Settings → API keys lists it; turning access off, or revoking
  it there, ends it. A platform key only reaches workload routes (deployment
  Apps, model services), not keys or the account.
- **Environment.** Every turn and terminal launch gets `PANTHEON_KEY`,
  `PANTHEON_HUB_URL`, `PANTHEON_CLIENT=coding-agent/<id>` (sent by the CLI as
  `X-Pantheon-Client`), `PANTHEON_CODING_AGENT`, the conversation or terminal
  id and the node id. The node's own `pantheon` comes first on PATH.
- **Note.** A short note says what the command is for and to run
  `pantheon --help`: Claude Code gets it through `--append-system-prompt`,
  Codex through `-c developer_instructions=…` (which holds from a thread's
  first turn); Gemini CLI and Cursor Agent
  get it before the first prompt of a conversation. Nothing is written into
  the agents' own configuration or the project.

A terminal launch never carries the key in its command line: the line reads
the variables from a file only the user can read, removes it, and runs the
agent in a subshell, so the shell left when the agent exits has none of them.

## Updates

An agent that offers to update itself in its own interface can end the
session doing it: Codex asks on start, runs its installer and exits ("Please
restart Codex"). A terminal launch therefore passes the catalog's
`QuietArgs`, before the user's extra arguments (which may turn it back on):

| Agent | At launch | How it updates | `update` |
|---|---|---|---|
| Codex | `-c check_for_update_on_startup=false` | asks on start, installs, exits | `codex update` |
| Claude Code | nothing | in the background; "Restart to apply", the session goes on | `claude update` |
| Gemini CLI | nothing | in the background (`general.enableAutoUpdate`); the new version runs next time | — |
| Cursor Agent | nothing | in the background into its `versions` folder; the new version runs next time | `cursor-agent update` |

`agents_detect` gives each agent that has an update command (`UpdateArgs`)
as `update`, its detected path first. The desktop runs it in a Terminal
window when the user asks (the Agents page). An agent already running keeps
its version until it is started again; an exited tab resumes on the new one,
since a launch starts the detected path, which an update replaces in place.

## Settings

Per node, `agents_settings` / `agents_configure` keep, for each agent: hidden,
a replacement command (an absolute path or a name on PATH), extra arguments,
environment variables, a default model (passed with the agent's model flag)
and whether full auto is the default. Custom agents are a name and a command
line; they open in a terminal. Hidden agents leave the node's report. For a
known agent that is missing, detection returns the command that installs it,
which the desktop runs in a Terminal window on that node.

## Terminal sessions

A terminal launch is a terminal session of the service, reported on the
`pantheon.stream.coding_terminals` stream and by `agent_terminals`. Its state
— starting, ready, working, waiting (for a permission or an answer), ended —
comes from hooks given to that launch only:

- Claude Code: `--settings <file>` with hooks for its session, prompt, tool,
  permission, notification and stop events, and, when the user has no status
  line of their own, a status line that forwards the account's limits at
  most every 15 s.
- Codex: `-c notify=…`, which reports each finished turn.
- Pantheon Agent: the terminal chat posts `UserPromptSubmit` (with the chat
  id and its prompt) and `Stop` around each turn when the launch gave it the
  hook address.
- Every agent: the launch line posts `Exit` with `{"exit_code": N}` once the
  agent's process is gone. The session is then `ended` for good, with
  `exited: true` and `exit_code` (`detail` says a status other than 0); a
  hook that lands later (Codex's notify runs apart from Codex) changes no
  state, though the session id it carries is kept.

The first prompt a hook carries (Claude Code's `prompt`, Codex's
`input-messages`, the Pantheon Agent's `title`) titles the session. Each hook posts the event to a listener of the service on 127.0.0.1 with a
token from the launch. The desktop shows the state and notifies the user
when an agent waits for them.

## Quota

- Claude Code: the limits in its stream (`rate_limit_event`) during a turn,
  and in its status line in a terminal.
- Codex: `codex app-server` answers `account/rateLimits/read` (windows, plan);
  the service asks at most every five minutes. Codex reads its own sign-in.

The agent list shows the last quota with its time.


Cursor Agent and Gemini CLI report no quota of their own, so the service
reads it from the provider with the sign-in the CLI keeps, read-only:

- **Cursor**: the token cursor-agent keeps in the macOS Keychain (or its
  `auth.json`), sent as the cursor.com dashboard's session cookie to
  `/api/usage-summary`: the plan's share used this billing cycle (`monthly`,
  reset at the cycle's end) and the membership as the plan.
- **Gemini**: Gemini CLI's `~/.gemini/oauth_creds.json` while its access
  token is valid (the CLI renews it as it runs; the service never refreshes
  or writes it): Code Assist `loadCodeAssist` for the project and tier, then
  `retrieveUserQuota`; the model closest to its limit is shown (`daily`). An
  account with no Code Assist tier has no quota.

When a provider will not say, `agents_detect` carries why as `quota_note`.
## Sessions

`agent_sessions` lists the sessions the agents keep themselves, including
those started outside PantheonOS: Claude Code's session files under
`~/.claude/projects` (only each file's head and tail are read: first prompt,
folder, branch, title) and Codex's threads through `codex app-server`
(`thread/list`). The Pantheon Agent's chats live with the Agent, so the ones
started in a terminal here are listed from what their hooks reported (chat
id, folder, first prompt), kept with the desktop's known sessions. A session opens in a terminal with its resume argument, or
continues as a new conversation (`agent_send` with `session_id`).

## Where it shows

- **Coding Agents App.** One computer at a time. The sidebar lists the
  projects the user keeps for that computer (settings `projects`, folders on
  the node, set with `agents_configure`; a session belongs to the longest
  project path that holds its folder). Adding a project needs no typed path:
  `agents_browse` lists a folder's folders (git repositories marked) to pick
  from, and `agents_create_project` makes a new folder there, optionally
  running `git init`, and adds it. A node whose service keeps no list shows
  every folder with sessions as a project.

  Each project has workspaces, shown as cards under it: the project's own
  folder, and git worktrees made for it (`agents_workspace_create` runs `git
  worktree add` on a new branch, or an existing one of that name, into
  `<parent>/<project>-worktrees/<name>`; `agents_workspace_remove` takes one
  off the list and, when asked, runs `git worktree remove`, which git refuses
  while it has changes not committed; the branch stays). Workspaces are kept
  in the project's settings entry, and `agents_detect` lists them with the
  branch each has checked out. A card stays until the user removes it.

  A workspace holds tabs, each a real terminal on that computer: an agent
  running its own interface (resumed with its session id when it has one),
  or a plain shell in the workspace's folder (on a Mac, the user's login
  shell). The tab bar shows the current workspace's tabs after its own page,
  which starts an agent (with full auto or not) or a terminal, lists the
  workspace's sessions, makes another workspace, and offers agents the
  computer could install; `+` starts the same things. A card lists its open
  tabs (with their hook state) and then its latest past sessions: the agents' own sessions
  started, resumed or imported here, Pantheon Agent chats, and conversations
  started by the Pantheon Agent or the `pantheon` command. Closing a tab ends
  its terminal; its session stays listed under the project, to resume. An
  agent that exits in its tab (quit, crashed, updated itself) leaves its
  shell: the tab says so above it and offers to resume the session there (the
  one its hooks reported once a prompt began it, else the one the tab opened
  with), or, before any session began, to start the agent again; the shell it
  left ends. Its sidebar row and tab show it ended meanwhile. The Agents page
  shows each agent's version and, for one with an `update` command, runs it
  in a Terminal window. Tabs
  keep their pty, so switching tabs or projects or reloading the desktop
  rejoins it; closing the window ends them. Sessions in folders outside the
  projects are left out and their folders offered to add; sessions started
  outside PantheonOS wait on the Import page. A conversation whose turn is
  still running opens in a read-only view that streams its events until it
  finishes. The status bar shows each agent's quota windows and reset times,
  and whether the agents may use PantheonOS. An agent in a background tab
  raises a notice when it needs the user or finishes.
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
- Gemini CLI asks to trust a folder in a terminal; its conversations trust
  the folder for their own runs only.
- Subscription limits, sign-in and billing belong to the agent and the user's
  own account.
