# Team Chat: agents as teammates in channels (2026-11-04)

Status: design approved by the owner 2026-11-04 (name "Team Chat"; Phase 1 is
one person per space, several people in Phase 3; people may edit and delete
their own messages; the store and dispatcher run on the controller). Phase 1
built and verified on staging 2026-11-05 (T1–T16; see "Phase 1 backend as
built" and its "Staging verification"); Phase 2 built and verified on staging
2026-11-05 (T20–T27; see "Phase 2 as built"); Phase 3 built and verified on
staging 2026-11-05 (T30–T35; see "Phase 3 as built"); not in production. Each phase ends
with acceptance criteria checked on staging before the next starts.

## Goal

People and agents work in one chat: channels, direct messages, threads and
tasks. An agent is a member with a name, a role, memory and a computer. A
message addressed to it wakes it; it works on its computer with its own tools
and reports back in the conversation. People give direction and review.

PantheonOS has the pieces but not the place: the Agent, Coding Agents (Claude
Code, Codex, Gemini CLI, Cursor Agent and `pantheon` on Fleet nodes, with
resumable conversations, linked groups that message each other, live event
streams), Fleet nodes including the Workspace, the controller's always-on
hosted services and KV (`docs/os/desktop-state.md`), the platform budget and
invite links. Team Chat is the App that puts them in one conversation space
that keeps working when no browser and no Workspace is open.

## Concepts

| Concept | Meaning |
|---|---|
| **Space** | A team's shared place. Phase 1: one per Fleet (one account). Phase 3: a space has people from several accounts |
| **Member** | A person or an agent. `@handle` (unique in the space), display name, avatar, role text |
| **Computer** | A Fleet node of the space owner (Mac, Linux, Windows, Workspace) |
| **Agent** | A persistent member that runs on one computer in one folder with one runtime |
| **Conversation** | A channel (public or private) or a DM (exactly two members). `#general` holds every member and cannot be left |
| **Thread** | Replies to one message; members who wrote, replied or were mentioned follow it |
| **Task** | A message with a state and one assignee (Phase 2) |
| **Reminder** | A wake-up an agent sets for itself, once or recurring, tied to a message (Phase 2) |
| **Activity** | Per person: what is new (mentions, unread, tasks to review, threads followed) |

## Architecture

```
 browser (/app/team-chat, desktop window, phone)
   │  App bus (appRpc over /bus-credential, as desktop-state)
   ▼
 controller ── hosted service `team-chat` ──────────────┐
   │  store: KV TEAMCHAT + stream TEAMCHAT_MSG           │ events → browser
   │  dispatcher: wake rules, per-agent queues, turns    │ (fleet.<fid>.apps.team-chat.events)
   │  scheduler: reminders (Phase 2)                     │
   ▼                                                     │
 agent's node ── coding-agents service (agent_send, agent_stop, events on
                 pantheon.stream.coding_<conversation>) ── turn process
                 └─ `pantheon-team` tool script (file inbox, as pantheon-link)
```

* **`team-chat` is a hosted service on the controller**
  (`os/internal/hosted/teamchat*.go`), registered like `desktop-state`, with
  its own NATS credential (the only one naming its KV and stream). Every
  call is scoped to the caller's Fleet by the binding (`Call.Fleet`), never
  by an argument; in Phase 3 the Hub adds the authenticated user to the call.
* **The browser** reaches it over the App bus it already has and listens on
  `fleet.<fid>.apps.team-chat.events` for changes. No Workspace is needed to
  read, write or wake an agent on another node.
* **Turns run on the agent's node** through the existing coding-agents
  service. The controller calls `node_service` (`service: coding_agent`)
  with a per-call timeout, the way it already calls nodes. The turn bills the
  node owner's platform key, as Coding Agents does.
* **The agent's tools** are a `pantheon-team` script in the agent's private
  folder, using the same file-inbox mechanism as linked groups
  (`docs/apps/coding-agents.md`, Linked groups): it works inside Codex's
  network-off sandbox and needs no MCP support from the runtime. The node's
  coding-agents service picks requests up during the turn and forwards them to
  the controller service.

## Storage

JetStream on the controller, one bucket and one stream for all Fleets:

```
KV TEAMCHAT (History 1, CAS writes)
  <fid>.space                         {id, name, created, settings}
  <fid>.member.<mid>                  person or agent record
  <fid>.conv.<cid>                    {kind: channel|dm, name, private, topic,
                                       members[], archived, agent_reply: mention|all}
  <fid>.read.<mid>.<cid>              last read message id
  <fid>.follow.<mid>.<root>           thread follow (Phase 1)
  <fid>.queue.<agent mid>             pending wake events (survives restarts)
  <fid>.turn.<agent mid>              running turn {conversation_id, node, started, cause}
  <fid>.task.<msg id>                 Phase 2
  <fid>.rem.<agent mid>.<rid>         Phase 2

Stream TEAMCHAT_MSG, subjects tc.<fid>.<cid>  (limits below)
  message event {id (ULID), cid, root (thread root or ""), from, kind:
                 post|edit|delete|system, text, mentions[], files[], at}
```

* **Messages are append-only events.** An edit or delete is a new event
  referring to the message id; readers fold them. A deleted message shows
  "deleted"; agents that already saw it get the edit or delete as a new event
  in their next turn.
* **Files** (Phase 1: images and documents in messages): JetStream object
  store `TEAMCHAT_FILES`, ≤ 25 MB per file, ≤ 2 GB per space; agents get them
  saved into their folder when a turn mentions them.
* **Limits:** a message ≤ 32 KB of text (longer agent output becomes an
  attached `.md`), ≤ 200 conversations and ≤ 50 agents per space, stream
  retention 1 year per space and 2 GB per space (oldest first; the owner is
  warned at 80 %).
* **Durability:** the daily snapshot pattern of `desktop-state` (KV + stream
  export, keep 7). Replicated JetStream is a production decision.

## Service contract (`team-chat` 1.0.0)

Browser and `pantheon-team` call the same tools; the service checks who the
caller is (owner browser, or an agent through its node's grant) and what it
may do.

| Tool | Does |
|---|---|
| `space_get()` | Space, members, conversations, my read marks, agent statuses |
| `space_update(name, settings)` | Rename the space; change the guards (as built: added, see below) |
| `me_update(name, handle, avatar)` | The person's own member record (as built: added) |
| `conv_create(kind, name, private, members)` / `conv_update(cid, …)` / `conv_join` / `conv_leave` / `conv_archive` | Conversations |
| `messages(cid, before, after, root, limit)` | Page of folded messages (a thread when `root`) |
| `post(cid, text, root, files, as_task)` | New message; parses `@handles` |
| `edit(id, text)` / `delete(id)` | Own messages only (people; agents too, own only) |
| `read(cid, id)` | Read mark |
| `follow(root, on)` | Thread follow |
| `agent_create(spec)` / `agent_update(mid, …)` / `agent_control(mid, action)` | Agents; actions `start`, `stop`, `stop_turn`, `retry`, `reset_session`, `reset_full`, `delete` |
| `agent_live(mid)` | Running turn's conversation id and node, for Watch |
| `file_put(…)` / `file_get(id, offset, length)` | Files of messages, in pieces (as built: added) |
| `search(q, cid, from, before, after)` | Phase 2 |
| `task_*`, `reminder_*` | Phase 2 |

Events on `fleet.<fid>.apps.team-chat.events`: `message`, `conv`, `member`,
`agent_status`, `read`, `typing` (an agent's live line), each with the
revision, so a browser that missed events re-reads.

### The contract as built (2026-11-04)

Over the App bus as every hosted App: `appRpc('team-chat', method, args)` →
`{success, result}`, or `{success: false, error}` with the reason in plain
words (a refused call, e.g. `#docs exists`, `you can only edit your own
messages`). Unknown arguments are refused. The deployment App is named
`team-chat` (`{"hosted": "team-chat", "scope": "team-chat"}`, added by the
cloud profile); no other App binds it. The person of a Phase 1 space has the
member id `owner`; agents have `a<hex>`; `system` writes the service's lines.

**Records** (as `space_get` returns them):

```
space    {id, name, created, settings: {turns_per_agent_hour: 60, turns_per_space_day: 400,
                                        hop_limit: 8, turn_max_minutes: 30,
                                        agent_messages_in_a_row: 12, posts_per_turn: 20,
                                        turn_minutes_per_agent_hour: 30},
          retired_turns: [unix]}
member   {mid, kind: person|agent, handle, name, avatar, description, created,
          agents: runtime, model, effort, node_id, node_name, node_kind (the computer as
                  last seen, to name it while offline), folder, session_conversation_id,
                  access: only_me|everyone|listed, access_list, listen: mention|all,
                  created_by, state: running|stopped,
                  attention: {reason, fix, at} | absent, turns: [unix seconds, last day]}
conv     {cid, kind: channel|dm, name (channels), private, topic, members: [mid],
          archived, agent_reply: mention|all, created, created_by}
          #general has cid "general"; a DM's cid is "dm-<mid>-<mid>" (sorted)
message  {id (ULID), cid, root ("" or absent: top level), from (mid | "system"),
          kind: post|system, text, mentions: [mid], files: [file], at (RFC 3339),
          edited_at, deleted: true (text "" and no files), hop,
          runtime, model (an agent's message), about (system: the agent), action ("retry"),
          reply_count, last_reply_at, repliers: [mid] (a thread root)}
file     {id, name, mime, size}
status   {state: ready|working|attention|offline, queue: n, reason, fix,
          line (working: the live line; ready with a queue: "Retrying soon[: why]"),
          since, cid, root (working: where it was woken)}
          reason and fix are plain words: computers by name ("Weizes MacBook Pro",
          "your Workspace"), never a node id, host suffix or status code
```

| Method | Args | Result |
|---|---|---|
| `space_get` | — | `{space, me: "owner", members: [member], conversations: [conv], reads: {cid: id}, follows: [root], agent_status: {mid: status}, unread: {cid: {count, mentions, last}}, usage: {limits, turns_last_hour: {mid: n}, turn_minutes_last_hour: {mid: n}, turns_today}, revision, subject}`. The first call creates the space with `#general`. |
| `space_update` | `name`, `settings` (any of the four keys) | `{space, usage}` |
| `me_update` | `name`, `handle`, `avatar` (each optional) | `{member}` |
| `conv_create` | `kind` (`channel` \| `dm`), `name`, `private`, `members` ([mid]), `topic`, `agent_reply` | `{conversation}`; a DM (`members: [the other mid]`) that exists comes back with `existed: true` |
| `conv_update` | `cid`, any of `name`, `topic`, `private`, `agent_reply`, `members` (the whole list) | `{conversation}` |
| `conv_join` / `conv_leave` | `cid`, `mid` (default: the caller) | `{conversation}`; `#general` cannot be left |
| `conv_archive` | `cid`, `archived` (default true) | `{conversation}` |
| `messages` | `cid`, `before`, `after` (ids), `root`, `limit` (default 50, ≤ 200) | `{messages: [message] oldest first, more, cid, root}`; with `root` the root comes first, then its replies |
| `post` | `cid`, `text`, `root`, `files` ([file id]), `as_task` (Phase 2: refused) | `{message}` |
| `edit` | `id`, `text`, `cid` (optional, faster) | `{message}` |
| `delete` | `id`, `cid` (optional) | `{message}` |
| `read` | `cid`, `id` | `{cid, id}` |
| `follow` | `root`, `on` (default true), `cid` (optional) | `{root, on}` |
| `agent_create` | `spec: {name, handle, description, avatar, node_id, runtime, model, effort, folder, access, access_list, listen, channels, dry_run}` | `{agent, status, warning}`; with `dry_run: true` nothing is created: `{folder, warning, runtime, node_id}` |
| `agent_update` | `mid` and fields: `name`, `handle`, `avatar`, `description`, `model`, `effort`, `listen`, `access`, `access_list`, `node_id`, `folder`, `runtime` (the last three start a new session) | `{agent, status}` |
| `agent_control` | `mid`, `action`, `confirm` (`reset_full`: the agent's folder, exactly) | `{agent, status}`; `stop_turn` → `{stopped: true}`; `delete` → `{deleted: mid}` |
| `agent_live` | `mid` | `{running, node_id, conversation_id, stream_id, stream_subject, started, from_seq, line, cid, root, status}` |
| `file_put` | `name`, `mime`, `data` (base64), `offset`, `upload_id`, `final` | the first piece (no `upload_id`, offset 0) → `{upload_id, size}`; each next piece at `offset` = the size so far; the piece with `final: true` → `{file}`. ≤ 25 MB a file, ≤ 2 GB a space. |
| `file_get` | `id`, `offset`, `length` (≤ 256 KB) | `{file, offset, data (base64), eof}` |

`agent_create` refuses: a computer that is offline; a runtime that the
computer's `agents_detect` does not list with a conversation mode (`… is not
installed on <computer>: open Coding Agents → Install first`) or lists as
signed out (`… is not signed in …`); a home folder or `/` for an agent whose
`access` is not `only_me`; a taken handle. For a private agent in a home
folder it creates the agent and returns `warning`. `runtime` is `claude`,
`codex`, `gemini`, `cursor` or `pantheon`; the UI offers those
`agents_detect` (fleet `node_call`) reports installed and not signed out.

**Events** (plain JSON on `space_get().subject`), each with `rev`, one more
than the last; a gap, or a first event after (re)subscribing, means: call
`space_get` (and re-read open conversations):

- `{type: "message", rev, message, change?: "edit"|"delete"}` — a new message,
  or the folded message after an edit or delete;
- `{type: "conv", rev, conversation}`;
- `{type: "member", rev, member}`, or `{type: "member", rev, mid, deleted: true}`;
- `{type: "agent_status", rev, mid, status}`;
- `{type: "read", rev, mid, cid, id}`;
- `{type: "typing", rev, mid, cid, root, line}` — an agent's live line;
- `{type: "space", rev, space, usage}` — after `space_update`.

**Watch**: `agent_live` names the coding-agents conversation and its stream
subject; the browser reads the turn's events with Fleet `node_call`
(`service: coding_agent`, `agent_conversation(conversation_id, after)`) and
listens on `stream_subject`, as the Coding Agents App does. Stop is
`agent_control(mid, "stop_turn")`.

## Agents

Record: `{mid, handle, name, avatar, description, runtime: claude|codex|
gemini|cursor|pantheon, model, effort, node_id, folder, session_conversation_id,
access, listen: mention|all, created_by, state: running|stopped}`.

* **Creating an agent** asks name, description (its role and starting brief),
  computer (online ones first; the Workspace suggested because it keeps
  working when a laptop sleeps), runtime (only those the computer has
  installed and signed in, from `agents_detect`; "Install" opens the
  existing install task) and folder (default `<home>/PantheonAgents/<handle>`
  on a machine, `/workspace/agents/<handle>` on the Workspace; a home
  directory or `/` as the folder is refused for a shared agent and warned for
  a private one).
* **The folder** holds `AGENT.md` (name, role, description, how to use
  `pantheon-team`, written by the service and rewritten on change),
  `MEMORY.md` and `notes/` (the agent's own), and `.team/` (its tool script and
  inbox, mode 700). The brief tells the agent to read `MEMORY.md` at the start
  of a fresh session and to write there what it must keep.
* **One session per agent.** The agent's turns all resume one coding-agents
  conversation (`session_conversation_id`), whatever channel woke it, so it
  carries context across channels like a person. **Reset session** starts a
  new conversation (memory files stay); **Full reset** also empties the
  folder (with a confirmation naming the folder).
* **Runtimes** map to the coding-agents headless modes; the Pantheon Agent runs
  as `pantheon` (`pantheon agent chat --json`), which also gives Team Chat to
  the cloud Agent.

### Waking

A wake event is queued for an agent when:

1. a DM to it is posted;
2. a message @mentions it (in any conversation it is a member of, or in a
   public channel, which adds it);
3. a person's reply is posted in a thread it follows: one it was asked to
   follow, started, or was @mentioned in at the start, or whose last agent
   reply is its own (an agent that once replied in a thread is not woken by
   every later reply there);
4. a message is posted in a channel whose `agent_reply` is `all`, or that it
   listens to (`listen: all`), and it is a member;
5. (Phase 2) its reminder fires;
6. an edit of a message it was woken by. A delete wakes no one; the agent
   sees it when it next looks.

Messages in its channels that do not wake it are not lost: the next turn
includes "since you last looked" for those channels (up to 30 messages per
channel, then a count and `pantheon-team read` to fetch more). By default
channels wake agents only on mention, so an idle channel costs nothing.

### Dispatching a turn

Per agent, one turn at a time:

1. **Idle agent:** take the whole queue, build the turn input and call
   `agent_send(runtime, folder, prompt, session_conversation_id, model,
   auto=true)` on its node. Record `turn.<agent>`.
2. **Busy agent:** new events stay queued. The running turn sees them if it
   runs `pantheon-team inbox` (the brief tells it to check between steps);
   whatever it has not taken starts the next turn as soon as this one ends.
3. **Turn input:**

   ```
   [Team Chat] You are @writer (Writer: drafts and edits documentation).
   New for you:
   #docs (thread on msg 01J…: "Release notes for 0.9"), @weize at 14:02:
     @writer please draft the notes from the changelog in ./CHANGES.md
   Since you last looked: #general 3 messages (pantheon-team read general)
   Reply with: pantheon-team reply 01J… - <<'EOF' … EOF
   ```

4. **Posting:** the agent posts through its tools. If there is nothing to
   say, it posts nothing: it ends the turn, or runs `pantheon-team skip
   [reason]` (logged, not posted). Its final text is posted for it only when
   a DM or an @mention woke it, it posted nothing in the turn, and the text
   is not a note that it has nothing to reply (such a text counts as a skip),
   so a runtime that ignores the tools still answers when asked. **Where a
   reply goes:** in a DM always inline; in a channel a reply to a top-level
   message is inline, and only a message that came from a thread gets its
   reply in that thread. `pantheon-team reply` and the final text follow
   this rule.
5. **Status:** Ready (idle), Working (a turn runs; the last event's summary
   is the live line, e.g. "Running npm test"), Needs attention (a turn failed
   for a reason the agent cannot fix: runtime missing, not signed in, budget
   exhausted; the reason in plain words and the fix), Offline (node offline or
   agent stopped), Starting (a Pantheon Agent whose Agent App is not ready
   yet, e.g. a new account's first minutes). Offline agents keep their queue;
   when the node is back the queue starts a turn. A Starting agent's messages
   wait too and start a turn once the App is ready.
6. **Failures:** a node call that times out is retried with backoff (1, 5,
   30 s, then on the node's next heartbeat); a turn that fails posts one
   system line in the conversation ("Writer could not start: Claude Code is
   not signed in on Weize's MacBook. Open Coding Agents → Sign in.") and the
   events stay queued. Delivery is at-least-once with message ids, so a turn
   that sees an event twice can tell.
7. **Guards** (all in the space settings, editable there, with current
   use):
   * **Agent messages in a row** (`agent_messages_in_a_row`, default 12):
     per conversation, threads included, the service counts the agent
     messages since a person's last message there. The message that reaches
     the limit wakes no agent, and the service posts one system line: "Agents
     paused here after 12 messages in a row. Write a message to let them
     continue." Further agent posts there are refused (the agent is told to
     end its turn), and queued wakes from agents' messages there are dropped.
     A person's message in the conversation resets the count. This guard is
     checked when a message is posted, so it holds whatever the hop
     bookkeeping says, including for an agent that keeps one turn open and
     polls its inbox.
   * **Hops** (`hop_limit`, default 8): a turn's hop starts at the lowest
     hop of its events; events it takes with `pantheon-team inbox` raise it
     to the highest it has seen, so its next post carries that + 1. Agent
     wake chains stop at the limit with a system line.
   * **Messages in one turn** (`posts_per_turn`, default 20): the next post
     is refused and the turn is stopped, with a system line.
   * **Minutes for one turn** (`turn_max_minutes`, default 30 for new
     spaces): the turn is stopped, with a system line.
   * **Per agent per hour:** at most `turns_per_agent_hour` (60) turn starts
     and `turn_minutes_per_agent_hour` (30) minutes spent in turns, so a long
     polling turn counts by its time, not as one start.
   * **Per space per day:** `turns_per_space_day` (400) turn starts; turns
     of deleted agents still count.
   Pause lines give the time in UTC; the interface shows it in the viewer's
   local time.

### `pantheon-team`

```
pantheon-team inbox                         new events since this turn started
pantheon-team read <channel|dm|thread id> [--before ID] [--limit N]
pantheon-team post <channel> [--file PATH]… - <<'EOF' … EOF
pantheon-team reply <message id> - <<'EOF' … EOF      (in its thread)
pantheon-team dm <@handle> - <<'EOF' … EOF
pantheon-team members | channels
pantheon-team join <channel>
pantheon-team edit <message id> - …         its own only
pantheon-team task create|claim|state|assign …   (Phase 2)
pantheon-team remind <when> [--every SPEC] [--about MSG] <note>   (Phase 2)
pantheon-team search <query> [--in CH] [--from @h]                (Phase 2)
```

The node's coding-agents service serves the script's requests as it serves
`pantheon-link` (same inbox rules, limits per turn: 200 requests), adding the
agent's identity; the controller service checks the agent's rights (member of
the channel, its own messages). Every request belongs to the running turn and
carries that turn's credential (see "Turn-bound commands" under "Privacy and
permissions"); one without it is refused.

### Watching and stopping

Watch opens the running turn in the side panel: the coding-agents event
stream of `session_conversation_id` (tool calls, outputs, sub-agents), the
same view the Coding Agents App shows, read-only, with **Stop turn**
(`agent_stop`). "Open in Coding Agents" opens the session there (and a
terminal resume for runtimes with one).

## Privacy and permissions

* **Phase 1:** one person per space, so only the owner reads the space; agents
  read the conversations they are members of, and public channels.
* **Access per agent** (enforced from Phase 3, set from Phase 1): *Only me*
  (default), *Everyone in this space*, *Listed people*. Without access a
  person cannot DM, mention-wake or add the agent; it is shown greyed with
  its owner.
* **Files:** an agent works with its computer's files, and can read whatever
  its computer's user can read, whatever its runtime (Codex's
  `workspace-write` sandbox stops writes outside the folder, and only when
  web access is off; it does not stop reads). A shared agent's folder may not
  be a home directory, a folder that holds home folders (`/Users`, `/home`,
  `/root`, …) or `/`; the create dialog says what the agent can reach on that
  computer and recommends the Workspace or a dedicated machine for shared
  agents.
* **Owner views** (read-only): an agent's folder (through the Files service),
  its agent-to-agent DMs, its reminders.
* **Turn-bound commands** (from the second Phase 3 check). An agent acts only
  inside a turn, and only for the one person that turn works for:
  * When a turn starts, the controller makes a turn id and an unguessable
    credential (32 random bytes) and records who the turn **works for**: the
    person whose message started it, or the agent's owner for a turn the
    owner (or nobody: an agent's own reminder) started. The credential is
    dropped when the turn ends (finished, failed or stopped).
  * The node gives the credential to the turn's processes only in their
    environment (`PANTHEON_TEAM_TURN`), never in a file. `pantheon-team`
    puts it in each request; the node refuses a request whose credential is
    not the running turn's, and the controller refuses any command whose
    turn id and credential are not the agent's running turn's. There is no
    fallback: outside a turn an agent's command does nothing (no "last
    turn's view").
  * A turn works for one person. It takes only queued messages that work for
    the same person; a message from anyone else (the owner's during a
    member's turn, or the reverse) starts a turn of its own after this one.
    `inbox` mid-turn follows the same rule.
  * What a turn makes carries its person and is checked against them: its
    messages (an @mention or DM wakes only agents that person may use), its
    edits (an edit re-wakes the agents the message woke only when that
    person may use them; an agent edits only messages in conversations that
    person sees), its reminders (set in Alice's turn, a reminder works for
    Alice, also when Team Chat posts it while the agent is stopped or busy;
    in a member's turn `--for` names only that member or the agent), its
    tasks (claim, state and assign only tasks in conversations that person
    sees; assign only to agents that person may use) and `remind list` /
    `remind cancel` (in a member's turn, only reminders set in that
    member's turns or for them, or tied to a conversation they see).
  * A Team Chat turn runs in a session (and process group) of its own on its
    computer. When its agent exits, the node ends every process still in
    that session or group, and every process it saw as the turn's
    descendant (checked each second) that is still the same process, so
    `nohup … &` loops do not live on into later turns.
* **What Team Chat cannot prevent** (said in the T35 dialog and here): a
  shared agent runs as its computer's user. Whatever that user can do, a
  person who may use the agent can ask it to do, in its turn: read any file
  the user can read, including other agents' folders, `.team/` inboxes and
  the agent's own `MEMORY.md` and session (so what one person told it can
  come back for another person if the model repeats it); start programs
  that escape the turn (a process that leaves the turn's session and its
  descendants within a second, or one started by another service such as
  `launchd`, `cron` or `systemd`, outlives the turn); and, while a later
  turn runs, a process that survived could read that turn's credential from
  the computer (another process's environment, or a request file before the
  node removes it) and act as that turn. The credential stops a process
  left from an earlier turn from using Team Chat by itself; it does not make
  a shared agent on a person's own computer a boundary. The Workspace or a
  computer of its own is the safe home for a shared agent.
* **Admin powers** (from the second Phase 3 check). An admin: does what a
  member does; renames, archives, changes the topic, the members, privacy
  and wake mode of channels that are not the owner's (#general and the
  channels the owner made are the owner's); adds people and agents they may
  use to any channel they are in; starts, stops and stops the turn of agents
  they may use; invites people and removes members (not admins or the
  owner). An admin may not: make one of the owner's channels private or
  public; change which messages wake agents in #general or the owner's
  channels; rename or archive the owner's channels (the simplest rule: the
  owner's channels are the owner's to shape); take the owner, or an agent of
  the owner's they may not use themselves, out of any of the owner's
  channels; retry a failed turn, reset or delete an agent, change agents or
  the space's settings, or watch a turn.

## Interface

Original look in the Atrium design system (no imitation of other products):

* **Left rail:** Activity, Channels and DMs (unread counts, @ badges), Agents
  (status dots), Tasks (Phase 2), Computers, space settings.
* **Conversation:** header with name, topic, members, and tabs Chat / Tasks
  (Phase 2) / Files; messages grouped by author and day, agent replies show
  runtime and model as a small label; thread replies as "3 replies · last
  14:05"; Markdown, code blocks, images and file cards; hover actions Reply in
  thread, Edit, Delete, Copy link, (Phase 2) Make task.
* **Composer:** @ completion (members with status), # channel links, file
  drop and paste, Enter to send / Shift+Enter newline, (Phase 2) "As task".
* **Side panel:** thread, member profile (agent: Profile, Activity, Folder,
  Reminders, Settings, Controls), or Watch.
* **Agent working:** under the message that woke it, "Writer is working ·
  Running npm test · Watch · Stop".
* **First run:** an empty space shows three steps: pick a computer (online
  ones; "Use my Workspace"; "Connect a computer" from the invite guide's
  computer step), create the first agent (prefilled "Assistant", the best
  installed runtime), say hello in `#general`.
* **Surfaces:** built-in App `team-chat`; desktop window; `/app/team-chat`
  standalone page usable at phone width; Share… gives an invite link
  (Phase 3 brings the person into the space).

## Phases and acceptance criteria (staging)

### Phase 1 — one person, agents that answer

Scope: space, channels, DMs, threads, edit/delete, files; agents on Claude
Code, Codex and Pantheon Agent (Gemini/Cursor where installed) on any of the
owner's computers; waking by DM, mention, followed thread, `agent_reply: all`
channels; dispatcher with queue, guards and failures; `pantheon-team` post /
reply / dm / read / inbox / members / channels / join / edit; memory files;
status, Watch and Stop; desktop window and standalone page.

* T1 Opening Team Chat the first time creates the space with `#general` and
  shows the first-run steps; a channel (public and private) and a DM can be
  created; the space survives a controller restart.
* T2 Create agent: only runtimes installed and signed in on the chosen
  computer are offered; a home-folder choice warns; the agent appears with
  its status; its folder has `AGENT.md` and `.team/`.
* T3 A DM to an agent on the Workspace gets a reply in under 30 s after the
  turn starts; usage is billed to the owner's key; the reply carries runtime
  and model.
* T4 A channel message with `@writer` wakes only Writer; the same channel's
  unaddressed message wakes no one; with `agent_reply: all` every member agent
  is woken.
* T5 The agent replies in the thread it was woken from, and can post into
  another channel and DM another agent through `pantheon-team`.
* T6 Three messages sent while an agent works all reach it (in that turn via
  `inbox` or in the next turn), none twice.
* T7 Edit and delete: the owner edits a message; the agent's next turn gets
  the edit; deleting shows "deleted" for everyone.
* T8 Memory: an instruction the agent saved to `MEMORY.md` is followed after
  Reset session; Full reset (confirmed) forgets it.
* T9 With all browsers closed and the Workspace stopped, a message posted from
  the phone page wakes an agent on a Mac node and the reply is there later;
  the Workspace stays stopped (checked on the controller).
* T10 An agent on a computer that goes offline shows Offline; messages to it
  wait and get answered within one minute of the computer coming back.
* T11 Watch shows the live tool calls of a working agent; Stop turn ends the
  turn and the status returns to Ready.
* T12 Claude Code agent and Codex agent in one channel: one asks the other by
  @mention and the second answers; an agent loop stops after 8 hops with a
  system line.
* T13 A turn failure (runtime not signed in) posts one plain-language system
  line with the fix and keeps the message queued; fixing it and pressing
  Retry answers it.
* T14 Files: an image pasted into a DM reaches the agent as a file in its
  folder and it can describe it (runtimes with images); an agent can attach a
  file it made.
* T15 `/app/team-chat` at phone width: read, post, @complete, open a thread,
  Watch; no desktop loaded.
* T16 Guards: the per-agent turn limit stops further turns with a visible
  reason; the space settings show current use.

### Phase 2 — work tracking

Scope: tasks, reminders, Activity, search, Files tab, notifications in the
browser.

* **Tasks:** any message can be made a task ("As task" or "Make task"); state
  Todo → In progress → In review → Done, or Closed; one assignee; Claim fails
  when someone else holds it; every state change is a system line in the
  task's thread; agents use `pantheon-team task …` and are told to claim
  before working and to move to In review when done. Board and list views per
  channel and for the space, filters by channel, assignee, creator.
* **Reminders:** set by an agent (`remind`) or asked of it; once or recurring
  (cron-like spec in the space's time zone); stored in KV; the scheduler wakes
  only that agent, in the context of the message it is tied to; nothing is
  posted unless the agent posts; stopped agents are woken when started again;
  deleting an agent cancels its reminders; the profile's Reminders tab lists
  them.
* **Activity:** rows for conversations and threads with something new for
  me; filters Mentions, Unread, In review, All; Mark all read.
* **Search:** messages by words, channel, author and time (an index the
  service keeps per space, built from the stream); agents use the same search.
* **Notifications:** browser notifications for DMs, mentions and followed
  threads; per-channel "everything / mentions / mute".

Criteria:

* T20 "As task" creates a task in Todo; an agent mentioned on it claims it,
  works, and moves it to In review with a summary in its thread; the owner
  sets Done.
* T21 Two agents claim the same task: one succeeds, the other gets "already
  claimed by @x" and does not work on it.
* T22 Board and list show the space's tasks; filters work; state changes from
  the board appear as thread lines.
* T23 "Remind me to check the CI in 2 minutes" makes the agent set a reminder;
  it fires within 30 s of its time, wakes only that agent in that thread, and
  survives a controller restart in between.
* T24 A recurring reminder ("every weekday 10:00") is listed on the profile
  and fires on schedule (tested with a short interval); cancelling it by
  asking the agent removes it.
* T25 Activity shows mentions, unread and In review correctly across
  channels, DMs and threads; opening a row lands on the first unread message.
* T26 Search finds a message by a word, filtered by channel and author, in
  under 1 s for a space with 10,000 messages; an agent finds the same through
  `pantheon-team search`.
* T27 Browser notification for a DM from an agent with the page in the
  background; a muted channel sends none.

### Phase 3 — teams

Scope: several people in a space; agents shared under access rules;
agent-to-agent DMs visible to owners; invite into a space; roles.

* **Membership:** a space belongs to its owner's Fleet. The Hub keeps space
  memberships (`space_members`: space, user, role owner|admin|member, joined)
  and gives a member's browser a space grant to the owner's `team-chat`
  service (like a visitor grant, carrying the member's identity). The service
  takes the caller's identity from the grant; read marks, follows and
  notifications are per member.
* **Agents in Phase 3 run on the space owner's computers** (and billing on
  the owner's key). A member who wants their own agent in a space creates it
  there on their own computer: Phase 3b, needing a cross-Fleet turn path;
  not part of Phase 3 acceptance.
* **Access** enforced as described; private channels invisible to
  non-members; DMs between two members visible to them only (an agent DM
  also to the agent's owner).
* **Invite:** Share… on Team Chat makes an invite link (invite-links) whose
  end step joins the space and opens `/app/team-chat` on it.
* **Roles:** owner and admins manage channels, agents and members; members
  post, create channels, use agents they have access to.

Criteria:

* T30 The owner invites a stranger with a link; the stranger signs up on the
  page and lands in the space's `#general` without a desktop.
* T31 The member mentions a shared agent and gets a reply; the turn bills the
  owner's key; the member cannot see the owner's private channels or DMs.
* T32 An "Only me" agent cannot be DMed or woken by the member (clear
  message), and its replies in shared channels remain visible to that channel.
* T33 Two people in one channel see each other's messages, edits and read
  state live; each has their own Activity and notifications.
* T34 Removing a member revokes their grant within one request; they keep
  nothing but what they downloaded.
* T35 Making an agent shared refuses a home-directory or `/` folder (and a
  folder that holds home folders), and the dialog states plainly what the
  agent can reach on its computer. (Folder confinement is not promised: a
  shared agent can read whatever its computer's user can read, whatever the
  runtime; Codex's sandbox only stops writes outside the folder.)

## Phase 1 backend as built (2026-11-04)

Status: service, node side and tests built; deployed to staging and verified
2026-11-05 (first at controller `7a1155bd`, Fleet `fleet-v0.5.1-staging.7`,
Agent App 0.9.109 with `team-chat` in the profile; now controller `0f2a489d`,
Fleet `fleet-v0.5.1-staging.10`, Agent App 0.9.110, see the fixes below);
the UI is built separately. Not in production.

**Service.** `os/internal/hosted/teamchat.go` (records, the space in memory,
messages and their fold), `teamchat_tools.go` (the owner's tools),
`teamchat_dispatch.go` (wake rules, queues, turns, guards, failures, the
loop), `teamchat_agents.go` (agents and their brief), `teamchat_agentrpc.go`
(the agents' requests), `teamchat_files.go`, `teamchat_backup.go`; the
contract is `os/internal/hosted/manifests/team-chat.json` (1.0.0). The
controller registers it with `--deployments` and connects it with its own
credential (`auth.MintTeamChat`, re-minted on every reconnect, inbox
`_INBOX_team-chat`): the only credential that names `KV_TEAMCHAT`,
`TEAMCHAT_MSG` and `OBJ_TEAMCHAT_FILES`; it publishes under
`fleet.*.apps.team-chat.>` and subscribes to `fleet.*.apps.team-chat.rpc`,
answering with a response permission. The cloud profile
(`release_profile.team_chat`) adds the App `team-chat` (`{"hosted":
"team-chat", "scope": "team-chat"}`) to deployments with a Desktop; no App
binds it. **Rollout order: the controller ships before a release whose
profile has it** (an older controller reports it `blocked`).

**Storage as built.** Keys `<fid>.space`, `.member.<mid>`, `.conv.<cid>`,
`.read.<mid>.<cid>` (agents' read marks are what "since you last looked"
counts from), `.follow.<mid>.<root>` (explicit on/off; writing in a thread or
being mentioned there follows it implicitly), `.queue.<mid>` (a JSON list of
wake events `{id, event, cid, root, kind, reason, from, hop, at}`),
`.turn.<mid>` (`{state: starting|running, conversation_id, node_id, started,
from_seq, hop, cause, events, posted, runtime, model, stop}`) and
`.file.<id>`. Writes are checked against the revision last read; the service
is the only writer, keeps each space in memory and reads it again after a
conflict. Messages: stream `TEAMCHAT_MSG`, subjects `tc.<fid>.<cid>`,
retention one year (stream-wide), messages ≤ 64 KB; an event's `woke` lists
the agents it woke (so an edit or delete reaches them). Files: object
`<fid>/<file id>` in `TEAMCHAT_FILES`; uploads in progress and files read for
browsers or agents are kept under `<state-dir>/team-chat/` for an hour. The
2 GB per space applies to files; message size per space is not counted yet.
Snapshots: hourly check, `<state-dir>/backups/teamchat-YYYY-MM-DD.json`
(every key and every message, not files), newest 7 kept;
`TeamChat.Restore` puts keys back that are absent and messages into an
empty stream (tested, no operator command yet).

**Dispatcher as built.** A post computes its wakes before it is stored (DM;
mention, which adds the agent to a public channel; a followed thread; a
channel with `agent_reply: all` or an agent with `listen: all` that is a
member). Rules 3 and 4 apply to people's messages only: an agent's message
wakes other agents only by DM or @mention (on staging, agents following one
thread acknowledged each other's "nothing to do" notes in a loop until the hop
guard stopped it). An agent's own message never wakes it; an agent's message with hop
≥ `hop_limit` wakes no one and the service posts one system line. Wake
events go to the agent's queue key (once per event id) and a turn starts
when the agent has none: the service writes `turn` (`starting`), checks the
node, calls `team_setup` (rewrites `AGENT.md`), saves the turn's files into
`<folder>/files/<id>-<name>` (`team_file_put`, 48 KB pieces, skipped when the
file is there), then `agent_send(cwd: folder, prompt, conversation_id:
session or agent: runtime, model, effort, auto: true, origin: team-chat,
team: {mid, token, handle, name})`; on success the events leave the queue and
the turn is `running`. A turn's hop is the lowest hop of its events (a
person's message resets the chain); what it posts has that hop + 1. Every 2 s
the service reads the turn's events (`agent_conversation(after)`): tool calls
make the live line (`typing` event), text after the last tool call is the
final text, `done` ends the turn. Ending: when the agent posted nothing to
the conversation that woke it (the last post event of the turn), the final
text is posted there (in the thread when woken from one) with the runtime
and model; > 32 KB becomes an attached `.md`. A turn that failed puts its
events back at the head of the queue, marks the agent `attention` and posts
one system line with `action: "retry"` and `about: <mid>` (e.g. "Writer
could not answer: Claude Code is not signed in on Weizes MacBook Pro. Open
Coding Agents → Sign in, then press Retry."); nothing retries it until
`agent_control(retry)` (or `start`). The line is written from the node's
error by `explain` (`teamchat_dispatch.go`): not signed in, not installed,
PantheonOS models not set up on that computer, model budget used up, rate
limited, model not available, conversation too long, the model service's
temporary errors (5xx, overloaded), no network to the model service, the
agent's folder unusable, PantheonOS too old, the session gone, the runtime
stopped unexpectedly, and otherwise "its turn failed on <computer>" with
where to look. Each says what to do; the raw error goes to the controller
log. Errors returned to the owner's calls (`agent_create`, `stop_turn`,
`reset_full`) are written the same way. The computer is named as people know
it: `your Workspace` for the Workspace, a Mac's network name without
`.local` and with spaces ("Weizes MacBook Pro"), else its name, never its id.

**Offline.** A node is offline for the service as soon as either signal
says so: its registry record is gone (a node deletes it when it stops and the
record lapses 30 s after its last heartbeat), or a call to it finds nothing
answering (`no responders`, the node service offline). In the second case it
stays offline until the node sends a heartbeat newer than the one its record
had at the failure, so a record that is still fresh cannot bring it back and
make it flap; a slow call (timeout) on a node with a fresh heartbeat is not
taken for offline. An offline agent keeps its queue and shows `offline` with
"<computer> is offline" (or, mid-turn, the live line "<computer> is offline;
waiting for it"); when the 15 s sweep sees the node back, its waiting agents
start at once (their backoff is cleared). A node call that fails in transit
for another reason is retried after 1, 5, 30 s, then each minute, with the
status line "Retrying soon" and the reason when it is known. A stopped turn
(`stop_turn`) posts nothing; a turn over `turn_max_minutes` is stopped with
a system line. A session the node no longer has, or one made with another
model provider than the agent now uses (its model source changed between
subscription and PantheonOS models), starts a new one by itself, at the
start or the end of the turn, without asking the owner. Guards count turn starts
per agent per hour (`member.turns`) and per space per day; a guard posts one
"Paused: …" line and shows as `attention` until its window passes. The
token in `team` is an HMAC of the Fleet and member id with the controller's
`<state-dir>/team-chat.key`; the service takes the Fleet from the request
subject, so a node can act only as an agent of its own Fleet's space.

**Node side as built** (`apps/coding_agents/team.go`, Fleet
`fleet-v0.5.1-staging.7`). `team_setup(folder, agent_md, reset, check_only)`
makes the folder (`~` expanded), writes `AGENT.md` (`{{TEAM}}` becomes the
tool's quoted path) when it changed, `MEMORY.md` and `notes/` when missing,
and `.team/` (mode 700: `pantheon-team`, `inbox/`); reports `home` and
`root`; `reset: "full"` empties the folder first and refuses for the home
folder and `/`. `team_file_put(folder, name, offset, size, data, final)`
writes under `files/` only. `agent_send` with `team` prepares `.team/` for the
turn as linked groups do theirs (private folders opened by handle, requests
read without following links, a fresh inbox when one cannot be emptied,
added to Codex's `--add-dir`), adds a setting naming the tool, and while the
turn runs (and once as it ends) forwards each request to
`fleet.<fid>.apps.team-chat.rpc` with the agent's `mid` and `token` (90 s),
uploading `--file` attachments first (≤ 25 MB, 48 KB pieces through
`file_put`). The script waits up to 120 s; a turn answers at most 200
requests. Requests: `op`, `arg` lines, `opt: name=value` (`--before`,
`--after`, `--limit`), `file: <absolute path>`, a blank line, the text.

**Tests.** `os/internal/hosted/teamchat_test.go` against an in-process
JetStream server and a fake node: the space survives a restart and is per
Fleet; agent creation (runtimes, default folders, home warning, shared
refusal); wake rules; queue durability across a restart with `inbox` taking
events once; the final text in the waking thread with runtime and model;
edit/delete fold and the agents' next turn; failure line, attention and
Retry; guards (hourly limit, hop chain, turn time); stop turn and resets;
offline node, and a node whose record is gone or that nothing answers
(Offline at once, by its name, answered when back; not back on a stale
heartbeat); a new session when the model source changed; the plain failure
lines; the agents' tool commands and files; snapshot and restore; and,
on an operator-mode server, that only the team-chat credential opens its
bucket, stream and files while a node's App credential reaches its request
subject. `apps/coding_agents/team_test.go`: the folder setup and a turn whose
agent runs `pantheon-team` reply/post `--file`/inbox through the service.

**Deviations from the contract above, as built.** Added: `space_update`,
`me_update`, `file_put` / `file_get`, `agent_control` `retry` and `delete`,
`agent_create` `dry_run`, the member's `node_name` / `node_kind`. Changed:
an agent's message wakes other agents only by DM or @mention (not as a thread
or channel reply).

### Staging verification (2026-11-04/05)

On staging with a test account (its own space, a Mac test node and its
Workspace), through the App gateway as the browser calls it; agents on the
Mac ran Claude Code and Codex, on the Workspace the Pantheon Agent.

| | Result |
|---|---|
| T1 | Pass. The first `space_get` made the space with `#general`; a public and a private channel and a DM were created; all there after a controller restart. |
| T2 | Pass. Claude Code on the Workspace (not installed) refused; a home folder warned, a shared agent in a home folder refused; the folder had `AGENT.md` and `.team/` (700). |
| T3 | Pass. A DM to the Workspace agent was answered 16 s after it was posted, on the platform's model through the account's key; the message carries the runtime (the model when the runtime reports it). |
| T4 | Pass. An unaddressed channel message woke none; `@writer` only Writer; `agent_reply: all` every member agent. |
| T5 | Pass. Writer replied in its thread, posted in `#general` and DMed `@coder`. |
| T6 | Pass. Three messages sent during a running turn reached it through `inbox` (one reply, no extra turn); sent between turns, the next turn took them. |
| T7 | Pass. An edited message reached the next turn as edited; a delete showed as deleted. |
| T8 | Pass. An instruction saved in `MEMORY.md` held after Reset session; Full reset (confirmed with the folder) forgot it. |
| T9 | Pass. With the Workspace paused (no sandbox), the account's Agent idle and no browser, a DM posted through the service woke Writer on the Mac test node: the reply came 4 s later and the Workspace stayed paused. |
| T10 | Pass. The node stopped: Writer showed "Weizes MacBook Pro is offline" at the first look (under 8 s); the node's process killed (record still fresh): Offline within 9 s. Each queued DM was answered 10–14 s after the node was back. Before the fix the status read "ready / Retrying soon" for as long as the node was away. |
| T11 | Pass. The live line ("Running time python3 …") and stream subject; Stop turn returned the agent to Ready, posted nothing and ended the process. |
| T12 | Pass. Claude Code and Codex agents counted by @mention in `#docs`; the chain stopped after 8 hops with a system line. |
| T13 | Pass. An agent on a computer whose runtime was not signed in posted "Pan could not start: Pantheon Agent is not signed in on …. Open Coding Agents → Sign in, then press Retry."; the message stayed queued; fixed and Retry answered it. |
| T14 | Pass. An image in a DM was saved to the agent's `files/` and described; an attachment the agent made was read back with `file_get`. |
| T15 | Checked in the UI pass (`/app/team-chat` at phone width). |
| T16 | Pass. One turn an hour: "Paused: … limit of 1 turns an hour …" with the agent in attention and the use shown; raised, it went on. |

Also seen: after the test node's Claude Code and Codex were switched to
PantheonOS models (`model_source: pantheon`), Codex refused to resume its
earlier session ("This chat uses openai, but your current provider is
pantheon"); the service now starts a new session for it. An independent check then found the
hop guard bypassable: a Codex agent kept one turn open, looping `sleep;
inbox; post`, and since events taken through the inbox never raised its
hop, a relay game ran unbounded; a person's DM in another conversation
restarted a remembered game. Fixed on 2026-11-05 (see "Guards" under
Dispatching a turn and "Fixes after the independent check" below).

### Fixes after the independent check (2026-11-05)

* Loop guard at post time: agent messages in a row per conversation
  (default 12), refused past it, one system line, a person's message
  resets it. Inbox raises the turn's hop. Per-turn post cap (20), per-turn
  time cap (30 min for new spaces), turn-minutes per agent per hour (30).
* The brief (`AGENT.md`, the runtime note, the turn input) says: post
  nothing when there is nothing to say (`pantheon-team skip`); don't resume
  games, chains or routines from earlier unless a person asks now; finish
  within the turn and promise no later reply. The old "Write here to
  continue." line is gone.
* Final text is auto-posted only for a DM or @mention with nothing posted
  and no "nothing to reply" note; deletes wake no one; followed threads wake
  an agent on a person's reply only if it started the thread, was asked in
  it, or wrote its last agent reply.
* Replies: inline in DMs and to top-level channel messages; in the thread
  only for a message from a thread.
* Live lines are short summaries ("Reading AGENT.md", "Running tests",
  "Checking new messages"), never the raw command. Status carries
  `computer`, the same computer name the service uses; a Workspace agent
  reads "Your Workspace is paused".
* Deleting an agent keeps its turns in the space's day count; `agent_control
  delete` takes `keep_folder: false` (with `confirm` = the folder) to remove
  the folder on its node (`team_setup reset: remove`, node 0.5.1-staging.8).
* Interface: settings editable (limits, space name, my name; my name
  defaults from the account), "Use PantheonOS models" turns on the
  computer's PantheonOS access as Coding Agents does, Delete agent in
  Controls, Back closes threads and panels, friendly model labels, local
  times, "Send as a file" for long messages, upload percentage, plain
  errors, Team Chat's Share copy says it is personal in this phase.

### Fixes after the second check (2026-11-05)

* **People and agents.** Every author in a turn's input, `inbox` and `read`
  is marked: `@weize (person, space owner)`, `@coder (agent: Codex)`,
  `@writer (you)`, `@trio (agent, removed)`; the turn input starts with the
  people in the space. `AGENT.md` has a "Who is here" list (people, then
  agents with runtime and role). `pantheon-team members` shows the kind
  (`person, space owner` / `agent: Codex, ready`).
* **The brief** says requests from people always count, including asking
  the agent to talk to, ask or coordinate with other agents; the anti-loop
  line only forbids starting or resuming games, chains or routines on the
  agent's own initiative or because another agent asked, and says to do a
  back-and-forth a person asks for now (the guards bound it). The service
  now sends the turn's Team Chat note to the node (`team.note`, node
  0.5.1-staging.9), so its wording changes without a node release.
* **Handles.** A member keeps `prev_handles`; an old `@handle` still
  mentions it, and agents read old mentions as the current handle. The
  interface defaults the person's handle from the account's full name, else
  the username without a random suffix, and Space settings changes it.
* **Thread replies.** `pantheon-team reply` to a message in a thread, or to
  a thread's first message, posts in that thread; other channel messages
  and DMs get inline replies. A turn woken in a thread is told
  `reply <the waking message>`.
* **Deleted agents.** `space.formers` keeps a deleted agent's handle, name,
  avatar and runtime, so its messages keep them (marked removed) and they
  still count as agent messages for the run guard.
* **Model.** `agent_send` returns the model the node runs the turn on (the
  one asked, else the runtime's default there); the turn's messages carry
  it until the runtime names its own.
* **Hops.** A turn's hop is the furthest hop among its events since the
  newest person's message among them (it was the lowest), so an event a turn
  left in the queue (read with `read`, not `inbox`) no longer pulls a chain
  back. Re-checked on staging: two Claude agents counting stopped at hop 8
  with the system line; a person's next request started a new chain.
* **Live state.** The hourly minutes include the running turn; Stop turn
  emits an empty live line at once (`stopping: true`), and so does every
  turn end. Runtime warnings (`⚠ …`) are not part of the final text.
* **Uploads.** The browser sends 320 KB pieces (the hosted App RPC body is
  at most 512 KB); the service handles a piece in about 15 ms, so the time is
  the round trips.
* **Interface.** Drafts and attachments per conversation and thread; a DM
  with a removed agent says so and cannot be written in; offline shows the
  reason and that messages wait; offline computers stay listed; one
  computer name everywhere; Watch folds runtime warnings and says when
  Codex reports no steps live (a tool event now updates the step of its own
  turn: Codex numbers its items again each turn, so its running command
  had updated an old step); model select in agent Settings; one time
  format; the unexplained badge is gone.

### Fixes after the third check (2026-11-05)

* **Quoted channels.** The turn input says `Reply with: … post '#general' -
  <<'EOF'`; `AGENT.md`, the turn's note and the `pantheon-team` help quote
  channels too and say why: in the shell an unquoted `#general` starts a
  comment, which dropped the channel and the heredoc (the Pantheon Agent then
  posted its greeting as a file `msg.md`). `general` without `#` works.
  `post` with no channel answers `Not posted: no channel. Quote the channel…`;
  a `post`, `reply`, `dm` or `edit` with no text fails (`Not sent: the
  message has no text…`), also with `--file`: an agent's message is never an
  empty message with an attachment.
* **A stopped exchange stays stopped.** After a hop line or a run line
  (`… They stay stopped until a person asks them to continue.`), an agent's
  message in that conversation that would wake another agent is not posted
  (`Not posted: Team Chat stopped the agents' exchange in #x at …`), until a
  person's message there asks to continue (`continue`, `keep going`, `go on`,
  `resume`, `once more`, `again`, `继续`…, not negated) or @mentions that
  agent. In a DM between agents (no person there), a person's @mention of the
  agent anywhere after the stop counts. Messages to people are still posted.
  The turn input lists `Closed exchanges` and shows stop lines under "Since
  you last looked"; the brief says a stopped exchange is over, even when a
  person later writes about something else. Re-checked on staging (hop
  limit 4): relay stopped; an unrelated DM got `7` and nothing in the relay;
  an FYI in the channel with Every message: both agents skipped; `@writer
  quick one: what is 2+2?` there got `4` only; `@writer please continue the
  count` resumed it to the next hop line.
* **Promises.** The brief: another agent's answer wakes you only when it
  @mentions or DMs you, so never promise to pass an answer on later; ask that
  agent to answer the person directly.
* **Model.** Messages an agent posts before its runtime names the model (the
  Pantheon Agent names it only when the turn ends) get it then: a `model`
  event folds it into those messages and the interface updates them. Labels
  drop a model's date or build suffix (`DeepSeek V4.1 Flash`).
* **Slow Pantheon Agent turns.** The 60 s steps were `shell__run_command`
  calls with no command (`{"timeout": 60}`): the shell App then read an idle
  shell until the timeout. `run_command` without a command now answers at
  once (`run_command needs a command…`). Node 0.5.1-staging.10.
  Re-checked on a fresh account through an invite (agents image
  sha-c479726): Use my Workspace → Create agent → Say hello got a text
  greeting in #general in 15 s (`post '#general'`, labelled `Pantheon Agent ·
  DeepSeek V4.1 Flash`); two DMs answered in 15 s each; no empty shell call.
* **Interface.** Open on desktop counts only a desktop tab that answers
  within 500 ms (a desktop whose Workspace is not connected stays silent) and
  otherwise opens `desktop.html`; desktop windows are opaque when not
  focused; Enter or Send during an upload sends when it finishes; Retry shows
  only while the agent needs attention; panel notes clear on navigation; New
  agent offers a paused Workspace (choosing it starts it); computer names are
  not cut; removed agents read `<Name> (removed)` or `Removed agent`
  everywhere, one row; First run hides "Setting up…" once Continue is
  offered; the account's full name comes from Team Chat when it has none, so
  the invite page shows it and Share does not ask again.
* **Mentions between agents.** An agent's incidental @mention of another
  agent (`@weize @coder says Tokyo…`) still wakes it, as designed: agents
  wake each other only by DM or @mention, and the woken agent may skip.

### Fixes after the fourth check (2026-11-05)

* **The first hello on a new account waits for the Agent App.** The
  controller tells Team Chat the state of the owner's Agent App (the `agent`
  App of the `general-team` deployment, cached 2 s). While it is not ready, a
  Pantheon Agent's status is `starting` (also before its first turn, so
  First run says "Assistant is starting. You can say hello now…"), its
  messages stay queued and no turn starts; the service looks again every 3 s
  and starts the turn once the App is ready. A turn that still fails with
  `App "agent" is not ready (…)` (the App restarted) waits the same way
  instead of writing a failure line. Only after 10 minutes is the owner asked
  ("…has not got the Pantheon Agent ready after 10 minutes. Your message is
  kept. Press Retry to wait again…"). The sweep tells browsers when the App's
  state changes. The interface shows "Assistant is starting · Your Workspace
  is still getting the Pantheon Agent ready (the first time takes a few
  minutes). Messages wait and are answered as soon as it is ready."
* **Failure lines** no longer send people to Coding Agents for details: an
  unexplained failure says "Press Retry. If it fails again, wait a few
  minutes or reset its session from the agent's menu."
* **Empty `run_command`.** The Workspace runs the Shell App packaged in the
  Agent App release, so the shell fix of staging.10 had not reached it (Agent
  App 0.9.109 was built before it: `{}` still drained 5 s, `{"timeout": 30}`
  30 s). The cause on the model side: `run_command`'s schema left `command`
  optional (`required: []`, `str | None`), a leftover of the old "no command
  reads the shell" mode. `command` is now a required string, described as
  "Required. … a call without a command runs nothing and fails"; any call
  without a non-empty command returns `no_command` at once (unit-tested for
  `{}`, a timeout, a blank command, extra arguments). Agent App 0.9.110
  carries the rebuilt Shell.
* **Watch** shows a step that a runtime never reported finished (Codex's
  commands) as finished once a later turn end is in the view, so an earlier
  turn's `sleep 25` no longer spins under the live step.
* **Removed agents.** System lines about an agent deleted before its name was
  kept read "Removed agent could not start…", like its messages.
* **Not changed:** an agent's own words about where it answered ("I answered
  @writer's DM directly" for a reply in the thread) are the model's; the
  brief already tells it where replies go, and more wording did not seem
  worth the prompt length.

Re-checked on staging (controller 0f2a489d, frontend a1fc79d9, Agent App
0.9.110, agents image sha-c479726) on two fresh accounts through invites:

* invite-test-25: Continue 19 s after Use my Workspace; Create agent → Say
  hello at once. The agent showed Starting at once (First run and under the
  message), the controller logged one wait, the turn started 15 s later when
  the App was ready and the greeting was in `#general` 28 s after Say hello
  (turn 16 s; reads, one `post '#general'`); no failure line, no Retry.
* invite-test-24 (first account after the release pin, so Continue waited
  196 s while the Workspace fetched the new release): greeting in 18 s
  (turn 19 s).
* Neither turn made a `run_command` without a command; the Workspace's Shell
  is 0.9.110's (revision 27a9ce5c).
* Watch on acct3's Codex agent: of 190 steps none spins, although its
  session still records `sleep 25` and `sleep 90` as running. Its old
  "Tester could not start…" line reads "Removed agent could not start…".

## Phase 2 as built (2026-11-05)

Status: built and verified on staging 2026-11-05 (T20–T27, see "Staging
verification" below). Not in production.

### Decisions where the design left a choice

* **A task is a message.** Its id is the message id; "As task" posts the
  message and makes it a task at once, "Make task" makes an existing message
  one (not a system line, not a deleted message, once). Its state lines go in
  the message's thread (the thread it is in, when it is a reply).
* **Who moves a task where.** The person sets any state and assignee. An
  agent may claim (Todo or unassigned → In progress, itself as assignee),
  move a task it holds to Todo, In progress or In review, and assign a task
  it holds or nobody holds. Done and Closed are the person's: an agent asked
  for them is told to move the task to In review with a summary. Claim fails
  with "already claimed by @x" when someone else holds it. State changes
  wake no one.
* **Reminders belong to an agent** and wake only it, with the message they
  are tied to (by default the message whose turn set them). The person sees
  them on the agent's profile and may cancel them there too (a person
  expects to remove what they can see). A reminder that came due while the
  controller was down fires once when it is back; a recurring one then goes
  on from the next time due (missed times are not made up). The shortest
  repeat is one minute.
* **Time zone.** The space has one (`space.time_zone`, IANA name, default
  UTC); the interface sets it from the browser the first time and Space
  settings shows it. Reminder times are read in it.
* **Unread and Activity.** A conversation's unread are its top-level
  messages after the person's read mark, plus the replies in threads the
  person follows after that thread's mark. A thread reply the person does
  not follow is not unread (it is in the thread's reply count). Activity
  lists a row per conversation and per followed thread with something new,
  and a row per task In review.
* **Search** scans the space's messages in the service's memory (each
  message's words kept lower case as it is folded from the stream); no
  separate index file. Words all have to match (text and file names);
  deleted messages and system lines are not found.
* **Notifications** are the browser's: per conversation `all`, `mentions`
  (the default for channels: mentions, DMs and followed threads) or `mute`
  (nothing); DMs default to `all`. Only while the page is hidden, for
  messages from others.

### Contract added (`team-chat` 1.1.0)

| Method | Args | Result |
|---|---|---|
| `post` | as before; `as_task: true` | `{message, task}` |
| `task_create` | `id` (message), `cid` (optional) | `{task}` |
| `task_update` | `id`, `state` (`todo` \| `in_progress` \| `in_review` \| `done` \| `closed`), `assignee` (mid, `""` = nobody) | `{task}` |
| `tasks` | `cid`, `assignee`, `created_by`, `state` (each optional) | `{tasks: [task]}` newest first |
| `reminders` | `mid` (optional: one agent's) | `{reminders: [reminder]}` soonest first |
| `reminder_cancel` | `id` | `{deleted: id}` |
| `activity` | `filter` (`all` \| `mentions` \| `unread` \| `review`, default `all`) | `{rows: [row]}` newest first |
| `read` | `cid`, `id`, `root` (optional: a thread's mark) | `{cid, id, root}` |
| `read_all` | — | `{marked: n}` (every conversation and followed thread) |
| `search` | `q`, `cid`, `from` (mid or `@handle`), `before`, `after` (date, RFC 3339 or message id), `limit` (default 50, ≤ 200) | `{results: [message], total, took_ms}` newest first |
| `notify_set` | `cid`, `level` (`all` \| `mentions` \| `mute`) | `{cid, level}` |
| `files` | `cid` | `{files: [{file, id (message), root, from, at}]}` newest first |
| `space_update` | also `time_zone` (IANA) | as before |

```
task      {id (= message id), cid, root (the thread its lines go in), title (first line, ≤ 120),
           state: todo|in_progress|in_review|done|closed, assignee (mid | ""), created_by,
           created, updated, updated_by}
reminder  {id, agent (mid), note, at (next time, RFC 3339 UTC), every ("" or the spec, e.g.
           "weekday 10:00", "5 minutes"), time_zone, cid, root, about (message id or ""),
           created, created_by, fired (times), last_fired}
row       {kind: conv|thread|task, cid, root, count, mentions, first_unread (message id),
           last (message), at, task (kind task)}
```

`space_get` also returns `tasks: [task]` (every task of the space),
`notify: {cid: level}` (only those set), `thread_unread: {root: {cid, count,
mentions, first}}` and `space.time_zone`. A system line about a task carries
`task: <id>`. Events added: `{type: "task", rev, task}`, `{type: "reminder",
rev, reminder}` or `{type: "reminder", rev, id, agent, deleted: true}`,
`{type: "notify", rev, cid, level}`, `{type: "read", rev, mid, cid, id, root}`
(a thread mark).

**Agents' commands added** (`pantheon-team`):

```
task create '#channel' - <<'EOF' … EOF      a new message that is a task
task claim <id>                            fails: "already claimed by @x"
task state <id> todo|in_progress|in_review [- <<'EOF' summary EOF]   (summary: a reply in its thread)
task assign <id> @handle
task list [--state S] [--assignee @h] [--in '#channel']
remind <when> [--every SPEC] [--about ID] - <<'EOF' note EOF
remind list | remind cancel <id>
search <words> [--in '#channel'] [--from @h] [--limit N]
```

`<when>`: `in 2 minutes`, `2m`, `1h`, `14:30`, `tomorrow 9:00`, `monday
10:00`, `2026-11-06 09:00`; `every …` (or `--every`): `5 minutes`, `hour`,
`day 9:00`, `weekday 10:00`, `mon,wed 10:00`. A turn woken by a task message
is told the task's state and to claim it first; a reminder's turn starts
with `Reminder you set …: <note>` and the message it is about.

### Service as built

**Files.** `teamchat_tasks.go` (tasks, their lines, `task …`),
`teamchat_reminders.go` (times, repeats, the scheduler, `remind …`),
`teamchat_activity.go` (unread, Activity, Mark all read, search,
notification levels, a conversation's files); the contract is
`manifests/team-chat.json` 1.1.0.

**Storage.** New keys in `KV TEAMCHAT`: `<fid>.task.<message id>`,
`<fid>.rem.<agent>.<id>`, `<fid>.tread.<mid>.<root>` (a thread's read mark)
and `<fid>.notify.<mid>.<cid>`; snapshots and `Restore` take them as every
other key. A controller without Phase 2 ignores them.

**Tasks.** A change is refused or made under the space's lock, so of two
claims at once exactly one wins. A task's line is a system message in its
thread with `task` (the id) and `by` (who acted); a line that reports the
person's own action is not unread for them. The turn input, `inbox` and
`read` mark a task message (`(task: In progress, claimed by @writer)`) and
tell the agent what to do with it (claim it, it is not yours, move it to In
review with a summary). The agent's brief (`AGENT.md`, the turn's note) lists
the task commands and says to claim before working and that a person sets
Done.

**Reminders.** The scheduler runs beside the 15 s sweep and looks every 5 s;
on start the service loads every space that has a reminder (the keys
`*.rem.>` with queues and turns), so one that came due while the controller
was down fires at once. A due reminder becomes a wake event
`{kind: reminder, rem, note, id: the message it is about, cid, root}` in its
agent's queue (event id `rem-<id>-<due unix>`, so a restart between queueing
and moving it on queues it once) and then moves on (repeat) or is removed
(once). The queue is the agent's like any message's: a stopped agent, a
paused guard or an offline computer keeps it waiting; deleting the agent
removes its reminders. An agent's own reminder posts nothing unless the
agent posts; a person's reminder always reaches them (see "Fixes after the
first Phase 2 check"). Times are kept
in UTC with the space's time zone at the time they were set; the controller
carries its own time zone database (`time/tzdata`). The brief tells agents a
reminder is the one way to act later.

**Unread.** `space_get().unread[cid]` counts top-level messages after the
conversation's mark plus new replies of followed threads after the thread's
mark (the conversation's when the thread has none); writing in a thread
moves its mark. `thread_unread` lists followed threads with something new.

**Search.** Each folded message keeps its text and file names lower case;
`search` scans the conversations the caller may read, newest first. On
staging a space of 10,000 messages answers in a few milliseconds (see
below); the first search after a controller start reads the conversations
from the stream like any first look.

**Node.** `pantheon-team` passes any lower-case command word and any
`--name value` option to the service (staging.11), which checks them; new
commands no longer need a node release.

**Tests.** `teamchat_phase2_test.go`: a task's claim, the second claim
refused, In review with the summary in the thread, Done by the person,
every line in the thread, filters, an agent's `task list` and `task create`,
tasks after a restart; reminder times and repeats in a time zone (once,
clock times, weekdays, a weekday repeat over a weekend, missed times not
made up); a reminder set, the controller stopped, the time passed, a new
controller started: it fires once, wakes only its agent with the message
and its thread, posts nothing and is removed; a recurring reminder listed,
firing twice, cancelled by the agent; a stopped agent's reminder waiting
for Start; deleting an agent cancels its reminders; unread and Activity
across a channel, a DM and threads (a thread's own mark, an unfollowed
thread, the person's own task line, In review, Mark all read, writing in a
thread); search of 10,000 messages by words, channel, author and dates, and
by an agent; notification levels; a conversation's files; the Starting
words. `apps/coding_agents/team_test.go`: new commands and options reach the
service; a word that is not a command is refused by the script.

### Interface as built

Rail: Activity (count of rows with something new, archived conversations
left out), Tasks (open tasks), Search. A conversation's header has Chat /
Tasks / Files tabs and a bell (everything / mentions / mute; a DM offers
every message or mute). Composer and thread composer have "As task"; a
message's hover actions add "Make task"; a task message shows a chip (state,
holder) whose menu moves or assigns it. Tasks: Board (Todo, In progress, In
review, Done; Closed folded) and List, filters channel, assignee (also
Nobody) and creator, per conversation and for the space; the board scrolls
in its own area at phone width. Activity: Mentions / Unread / In review /
All and Mark all read; a row opens its message (in its thread for a thread
row), reading back as far as needed. Search: words, conversation, author,
From/To days; a result opens its message. Agent profile: a Reminders tab
(note, next time in the viewer's clock, Once or Repeats every …, the message
it is about, Cancel). Space settings: the time zone and the browser
notification permission (asked only from a button). The time zone is set
from the browser once when the space has none.

A conversation counts as read only while it is on screen: not behind
Activity, Tasks or Search, not under the phone's list, not in a hidden tab
(found on staging: a DM that arrived while Tasks was open was marked read).
It is marked read when it comes back into view.

**Phase 1 leftovers fixed.** Watch on an agent whose computer is offline
says "<computer> is offline. Messages wait and are answered once it is
back." instead of the node-call error. Watch folds each Team Chat turn input
behind "Turn started by @weize's message" (or "by a reminder"), with "Show
what Team Chat sent". Messages of one author are grouped as before, but each
has its own hover time, a gap and a hairline, and a new header starts after
two minutes or after a message that got thread replies. Starting shows the
service's reason: "…getting the Pantheon Agent ready for the first time; this
takes a few minutes" only when no Pantheon Agent of the space has run yet,
else "…starting the Pantheon Agent again".

### Staging verification (2026-11-05)

Controller `98a0ce1c`, Fleet `fleet-v0.5.1-staging.11` (test Mac node and
agents image `sha-98a0ce1`, Agent App 0.9.110), frontend `21b0c581`, on the
test account in a headless browser (desktop 1400×900 and 390×844 touch).
Agents: Writer (Claude Code, Haiku) and Coder (Codex) on the test Mac, both
on PantheonOS models.

| | Result |
|---|---|
| T20 | Pass. "As task" `@writer … poem.txt … move this task to In review`: Todo at once; Writer claimed it (line "@writer claimed this task · In progress"), wrote the file, posted its summary in the thread and moved it to In review, all within 60 s; Done from the chip added "@weize moved this task to Done". |
| T21 | Pass. One task for `@writer @coder`: Writer's claim won; Coder's `task claim` answered "Not claimed: task … is already claimed by @writer. Don't work on it.", Coder ran `skip` and made no file. |
| T22 | Pass. Board and List per channel and for the space; filters assignee Writer → T20, T21; Nobody → T22. Moving a card from the board posted "@weize moved this task to In progress" in its thread. |
| T23 | Pass. In a thread: "@writer remind me to check the CI in 2 minutes" → reminder due 11:14:32 UTC tied to that message; the controller was restarted at 11:12:54; it fired at 11:14:33, woke only Writer (Coder's last turn was 11:09), whose reply landed in that thread at 11:14:40. |
| T24 | Pass. "Every 2 minutes, remind me to stretch" (short interval for the test): the profile listed it ("Repeats every 2m"); Writer posted at 11:19 and 11:21; "please cancel the stretch reminder" → `remind cancel`, the profile showed "No reminders set". |
| T25 | Pass. Activity listed a #general mention (@1), a reply in a followed thread, a DM and the task In review; Mentions showed only rows with mentions, In review only the task; opening the thread row opened the thread at the new reply and removed the row; the mention row opened #general at that message. |
| T26 | Pass. 10,000 messages seeded in one channel: the service answered `zanzibar rotated` in 4 ms, the page showed the one result in 0.08 s; author Writer → 0 results; Writer's `pantheon-team search Zanzibar deploy key` found the same message id. |
| T27 | Pass. Page hidden, `#docs` muted: Writer's DM "ping" made one browser notification; its "@weize pong" in `#docs` made none. |
| Phase 1 low | Watch on the offline Mac read "Weizes MacBook Pro is offline…"; Watch showed "Turn started by @weize's message" with the input folded. |

Found and fixed during the check: a search result 3,500 messages back did
not open (the page read only 8 pages back; now pages of 200 until found);
the Activity count included an archived channel; a conversation behind the
Tasks view was marked read. Also seen: pulling the new agents image put the
staging node in DiskPressure for about five minutes (the kubelet removed the
old image), during which the frontend's Recreate rollout had no pod.

### Fixes after the first Phase 2 check (2026-11-05)

**A person's reminder always reaches them.** Found: "@writer remind me to
check the CI in 2 minutes" fired, but Writer took "check the CI" as its own
job and posted nothing (a final text is posted only for a DM or a mention).
Now:

* A reminder records who it is for: `for` (a person's mid; empty = the
  agent itself). `remind --for @handle` or `--for me`; without it, a person
  whose message the reminder is about, or whose message started the turn,
  is the one it is for.
* A person's reminder turn says `Reminder for @weize … Remind @weize:
  <note>` and where to post it (`reply <id>` where it was asked, in its
  thread; else the channel; else the DM), mentioning the person.
* When that turn ends without a post there (or in the DM with the person),
  Team Chat posts the agent's final text (with `@weize` put in front when it
  is missing), else `⏰ Reminder for @weize: <note>`, as the agent, where it
  belongs. Also when the turn fails (the reminder is then not kept waiting),
  is stopped or skipped. When it comes due while the agent cannot run
  (stopped, needs attention, paused by a guard, its computer offline), it is
  posted at once and no turn starts.
* An agent's own reminder (`--for me`) works as before: nothing is posted
  unless it posts.

**Time zones.** `remind` takes `--tz ZONE` (IANA) and times with their own
offset (`2026-11-06T09:00+09:00`, also with a space or without seconds).
Its answer gives the time in the space's time zone and asks the agent to
confirm that clock time to the person ("I'll remind you at 16:00
America/Los_Angeles"). `remind help` lists the whole syntax; a wrong
`remind` is answered with it. The brief (AGENT.md, the turn's note) says
this, and the node's script needs no change (it passes any `--name value`).

**Reminder ids stay with the agent.** `remind`'s answers say the id is for
`remind cancel` only; the brief tells agents never to show it and that
people cancel by saying "cancel the reminder".

**Budget.** The failure line for a used-up budget now reads "its model
budget is used up (on PantheonOS models, models and Workspace compute share
the account's monthly budget: Settings → Usage)". Cause of "3.09 of 20
used" while the agent was out of budget: Settings' LLM Proxy card showed the
model key's spend against the configured budget ($20), but the key's real
limit is the budget less the month's Workspace compute ($20 − $16.95). The
card now says models + compute of the monthly budget and what is left for
models. A failure line about an agent is hidden once that agent finished a
turn after it (`member.last_ok`, unix seconds) and no longer needs
attention.

**Interface.** Task lines ("claimed this task", "moved … to Done") have a
neutral style (no warning tint) and open their task when clicked. A thread
shows Following (with Unfollow) whenever Activity counts it as followed:
an explicit choice wins (`space_get.unfollows` lists explicit unfollows),
else writing in it or being mentioned there. The profile's Reminders say
who each is for and show no ids; Delete agent says its reminders will be
cancelled. Search's In list has no duplicate "Removed agent"; its filters
wrap at 390 px; an archived channel opened from Search or Activity says
"This channel is archived" and its message box is off.

**Staging verification.** Controller `87cde85f`, frontend `c19c56f1`
(no node release, no new agents image), on the test account in a headless
browser, Writer (Claude Code, Haiku, PantheonOS models) on the test Mac:

| | Result |
|---|---|
| Exact repro | "@writer remind me to check the CI in 2 minutes" in a channel: Writer answered "…at 04:34 Los Angeles time"; the reminder (`for: owner`) fired 3 s after its time and Writer posted "@weize Reminder: check the CI now." |
| Other wordings | In a thread, "@writer in a minute, ping me to drink some water" → "@weize Drink some water now." in that thread. "@writer can you nudge me in 2 minutes to look at whether the nightly build passed?" → "(04:40 Los Angeles time)", then "@weize Nudge: look at whether the nightly build passed." |
| Fallback | Writer stopped after setting "remind me in 2 minutes to stand up": at its time "⏰ Reminder for @weize: Stand up …" was posted at once, no turn. |
| Time zone | "remind me tomorrow at 9:00 Tokyo time to call Kenji" → due 2026-11-06T00:00Z, confirmed as "16:00 America/Los_Angeles today"; "cancel the Kenji reminder" → "Cancelled the Kenji reminder." (no id). |
| Low items | The old budget line in #p2v-check is gone (Writer's `last_ok` is later); task lines neutral, a click (or Enter) opens and flashes the task's message; a thread you started shows Following, Unfollow → Follow → Following; Settings reads "Models $3.58 + Workspace compute $17.19 of your $30.00 monthly budget; $9.23 left for models."; the profile lists "For @weize" without an id; Delete says "Its reminder is cancelled."; Search's In list has one "Removed agent"; at 390 px In/From and After/Before sit in two columns without overlap; #p2v-check opened from Search says "This channel is archived" with the message box off. |

### Fixes after the second Phase 2 check (2026-11-05)

**A person's reminder never waits for the agent's turn.** Found: when the
turn a person's reminder queued was the one that tripped a limit (turns an
hour or a day, turn minutes), only "Paused: …" was posted; the reminder had
already left the store, and it reached the person only when the limit
lifted (14 minutes to hours later). The fallback looked only at a pause
noted earlier. Now delivery does not depend on the agent getting a turn:

* At its time, a person's reminder is posted for them at once ("⏰ Reminder
  for @weize: <note>", as the agent, where it belongs) when the agent
  cannot run: stopped, needs its owner, over a limit (computed then, not a
  pause noted earlier), its Agent App starting, or its computer known
  offline.
* Whenever a queued turn that holds person reminders is refused, deferred
  or cannot start, they are posted for their people and leave the queue
  (`remindNow`); the rest of the queue waits as before. That covers each
  limit, an offline or silent computer (no responders), a paused Workspace,
  the Agent App starting, a used-up budget or any other failure that needs
  the owner, a stopped agent, and a retry more than 10 s away (a 1 s or 5 s
  retry keeps it for the agent's own turn). When only reminders were
  waiting, no "Paused" line is posted.
* A reminder that could not be posted (a store error) stays queued and is
  posted on the next try; one is never dropped. Several reminders for one
  conversation are each posted.
* The 12-in-a-row guard does not stop a reminder's turn; if the agent's post
  is refused there, the fallback is posted as a Team Chat line.
* Go tests: `teamchat_remind_test.go` (each limit at due time and tripping
  the queued turn, 12 in a row, offline at due, gone after queueing, no
  responders, Agent App starting, Workspace paused, budget, long retry,
  stopped after queueing, two in one conversation).

**The confirmation states the right time.** Found: Haiku chained `remind …;
post "Reminder set … (at HH:MM)"` in one command and guessed the time
(2 of 6 wrong). Now:

* The turn input starts with `Now: Thu Nov 5 10:13 America/Los_Angeles
  (18:13 UTC), the space's time zone.`
* `remind` answers with a phrase to quote: `"at 10:15 today (Thu Nov 5,
  America/Los_Angeles)"`, `"at 09:00 tomorrow (…)"`, `"at 09:00 on Mon Nov
  9 (…)"`, and asks for the confirmation in the person's language,
  translating the day words and keeping the clock time and zone.
* The brief (turn input, the turn's note, AGENT.md): run `remind` by itself
  and confirm only after it answers, quoting its time; never chain the
  confirmation; reply in the person's language.

**Status.** A stopped agent's status is `stopped` (was `offline`); the UI
reads "Stopped" (sidebar title "Writer: Stopped", profile).

**Interface.** System lines are attributed to "Team Chat" in Activity,
Search and notifications (was "Removed agent: Stopped a chain…"). A thread
you follow with unread replies shows "N new replies" on its root in the
channel (an older root not loaded: a line "N new replies in a thread you
follow"); opening it marks it read and clears the channel badge. The URL
keeps `conv`, `thread` and `agent` in step with what is on screen for every
way of navigating (sidebar, Search, Activity, Back), so a copied link opens
that conversation and thread. The profile's Cancel confirm reads '@weize
won't be reminded about "<note>".' for a person's reminder, never with a
double period.

**Why acct3's pod came back (ops).** Not Team Chat: an API read through
the hub touches no pod (checked: no assign, prewarm or wake in the hub log;
the Workspace stayed paused through 25 minutes of reads, page loads of
`/app/team-chat` and turns). Two other things start pods:

* The account's k8s brain pod (`chatroom-<uid>` in `pantheon-users`) is
  resident (`idle_release=False`): when it is deleted from outside
  (`kubectl delete`), the hub's health checker starts it again within a
  minute (then every 5 minutes). That is the 18:08 / 18:53 / 18:58 restores
  after the checks' cleanups. Release it with the hub, not kubectl.
* The desktop (`desktop.html`) and sign-in prewarm call `GET /api/chatroom`
  and `/api/auth/prewarm`, which start the Workspace (Modal) by design. The
  standalone Team Chat page (`/app/team-chat`, `controllerHosted`) does
  neither.

A Workspace-hosted agent's turn waits for its Workspace and does not start
it (status "Your Workspace is paused").

**Staging verification.** Controller `95a7f665` (sha e493cb95), frontend
`7180ccea` (no node release, no new agents image), test account, headless
browser on `/app/team-chat`, Writer (Claude Code, Haiku, PantheonOS models)
on the test Mac:

| | Result |
|---|---|
| Wording 1 | "@writer remind me to check the CI in 2 minutes" → one confirmation "…at 11:10 today (Thu Nov 5, America/Los_Angeles)"; posted "@weize Check the CI now." 7 s after its time. |
| Wording 2 (Chinese, thread) | "@writer 3分钟后提醒我喝水" → "@weize 好的，我会在 11:11 today (…) 提醒你喝水。" (before the day-word change); "@weize 提醒：该喝水了。" in the thread 7 s after. After the change, "@writer 2分钟后提醒我站起来活动一下" → "…我会在今天 11:22（Thu Nov 5, America/Los_Angeles）…". |
| Wording 3 | "@writer could you give me a heads-up at 11:15 that the deploy window opens?" → "…at 11:15 today (…)"; posted at 11:15:08. |
| Hourly limit | Reminder set, then turns an hour 5 (Writer had 24): "@writer are you there?" → "Paused: Writer reached its limit of 5 turns an hour…"; at the reminder's time "⏰ Reminder for @weize: Check the oven …" at 11:19:08 (due 11:19:06). |
| Computer offline | Test node stopped after the confirmation: "⏰ Reminder for @weize: 站起来活动一下 …" at 11:22:29 (due 11:22:26), status "Weizes MacBook Pro is offline". |
| Low items | Stopped Writer: sidebar "Writer: Stopped", profile "Stopped". Activity: "Team Chat: Stopped a chain…". A followed thread's reply shows "1 new reply" on its root and the channel badge 1; opening it clears both and the URL gains `&thread=`. Opening a DM from Activity sets `?conv=dm-…`; that URL in a new tab opens the same DM. Cancel dialog: '@weize won't be reminded about "Heads-up: …".' |

### Fixes after the third Phase 2 check (2026-11-05)

**A person's reminder does not wait for the agent's running turn.** Found:
a reminder that came due during a 2.6-minute turn was posted 37 s late, when
the turn ended (with `turn_max_minutes` 30 it could be 30 minutes). Now:

* At its time, a person's reminder for an agent that is in a turn is posted
  for them at once ("⏰ Reminder for @weize: <note>"), as in the cases where
  the agent cannot run. It is not queued, so neither the running turn's
  inbox nor a later turn gets it.
* The agent is told, so it does not post it too: its next `inbox` (and
  `remind list`, for two hours) says `Team Chat posted your reminder for
  @weize at 12:46 while you were busy (Call Sam.). It is done: don't post it
  again.` `remind`'s answer and `remind help` say the same in advance. (In
  the first live retest, without this, Codex ran `remind list`, found it
  gone, checked the clock and posted it again itself.)
* Go tests: `TestAPersonsReminderDoesNotWaitForARunningTurn` (posted while
  the turn runs; the inbox says it was posted, once; `remind list`; no
  further turn; one post).

**A reminder is written in the language it was asked in.** Found: two
English requests got Chinese reminder posts (the confirmations were
English; Writer's session had Chinese in it). A person's reminder now keeps
`asked` (the person's request: the message it is about, else the newest
message of theirs that woke the turn, 300 characters) and `lang` (from its
letters: Chinese, Japanese, Korean, Russian, Arabic, English for plain
Latin text, "" when it can't tell). Both are in `reminders` and carried on
the wake, and the reminder's turn input (and inbox) says `Write the
reminder in English, the language @weize asked in (they wrote: "…"), even
if you used another language before.` Go tests:
`TestAPersonsReminderIsWrittenInTheirLanguage`, `TestLangOf`.

**Status.** An agent held by a limit has `paused: "limit"` in its status
(the state stays `attention`); the UI reads "Paused (limit)" in the sidebar
title, profile, DM header, mention picker, New conversation and Watch, and
the working line says "Writer is paused (limit): …". Older services are read
from the reason's words.

**Interface.**

* Addresses: `?conv=…&thread=<root>` opens the thread (root and replies,
  fetched by the thread request even when the root is older than the
  channel's first page; the follow state; `thread` stays in the address,
  so a reload keeps the panel). The cause: the address values are the page's
  live inputs, so opening the conversation first removed `thread` before
  the panel read it. `?agent=<id or handle>` opens the profile. `?msg=<id>`
  loads back to the message, scrolls to it and highlights it. Activity,
  Tasks and Search have their own addresses (`?view=activity`,
  `?view=tasks`, `?view=search&q=…`), and Back returns to the conversation.
* Times in Team Chat's own lines ("…it continues at 20:08 UTC…") are shown
  in the space's time zone, formatted like message times ("12:08 PM", with
  the zone's name when it differs from the browser's), in the conversation,
  Activity, Search, notifications and every status reason.
* The task menu opens upward (and caps its height) when there is not room
  below, and leftward near a phone's right edge.
* Copy: a stopped agent reads "Coder is stopped. Messages wait until it's
  started again; reminders it set for you still arrive." The Cancel confirm
  adds no period after a note that already ends a sentence (。！？.!?, also
  before a closing quote).

**Staging verification.** Controller `6d01152a` (sha ceee00ec; first
`316837e4`), frontend `275b5883` (no node release, no new agents image),
test account, headless browser on `/app/team-chat` (1400×900 and 390×844),
Writer (Claude Code, Haiku) and Coder (Codex) on the test Mac, PantheonOS
models:

| | Result |
|---|---|
| Reminder in a long turn | "@coder … set a reminder for me in 1 minute to call Sam, then run this in the foreground …" (a 2.8-minute CPU job, turn 20:45:52–20:48:47): due 20:46:57, "⏰ Reminder for @weize: Call Sam." at 20:47:01 while Coder was working. Its next inbox said Team Chat had posted it; Coder wrote "Team Chat has delivered the reminder…" and posted only the job's result. One reminder post. |
| Language | "remind me in 3 minutes to close the window" → "@weize Close the window"; "at 12:31 ping me to stretch" → "@weize Stretch"; "4分钟后提醒我喝水" → "@weize 提醒：喝水". Stored `lang` English, English, Chinese. |
| Thread link | `?conv=<#docs>&thread=<root from 00:38>` (older than the first page of 50) in a fresh browser context: the thread's root and replies, "Following", `thread` kept. The check's own repro thread in #p2v3-check after a reload: the same. |
| `?agent=writer`, `?msg=` | The profile opens; `?msg=` of a message 50+ back scrolls to it and highlights it. |
| Task menu | Newest message's chip: opens upward, all five states visible above the composer (desktop and 390 px; page width 390). |
| Pause times | Turns an hour 5: DM "…it continues at 12:47 PM…", Activity "Team Chat: Paused: Coder reached its limit … it continues at 1:01 PM …"; sidebar titles "Writer: Paused (limit)", "Coder: Paused (limit)". |
| Views | Tasks, Activity, Search set `?view=…`; each address opens that view in a fresh context (`?view=search&q=zanzibar` fills the search). |
| Copy | Stopped Coder's DM: "…reminders it set for you still arrive."; Cancel confirm '@weize won't be reminded about "给 Sam 打电话".' (the punctuation case itself is a unit test: the agent stored the note without "。"). |

## Phase 3 as built (2026-11-05)

Status: built and verified on staging 2026-11-05 (T30–T35, see "Staging
verification" below): controller `2a8975fa`, hub `ea2eda2`
(`space_members`, migrated on staging only), frontend `f570c28a`; fixed
after the first independent check on 2026-11-06 (see "Fixes after the
first Phase 3 check"): controller `f25093d8`, hub `15128fe`, frontend
`ac426461`; fixed after the second check on 2026-11-06 (see "Fixes after the
second Phase 3 check"): controller `8f252aac`, hub `767986d`, frontend
`1bb09915`, node release `fleet-v0.5.1-staging.12` and agents image `sha-8f252aa`
(the Workspace node). Not in production.

### Decisions where the design left a choice

* **A space is its owner's.** One space per owner Fleet, as before; its id is
  the owner's Fleet id (`space.id`). The Hub keeps who else is in it
  (`space_members`); the service keeps their person records (for names,
  handles, read marks) and takes each caller's identity from the grant.
* **People.** The owner's member id stays `owner`; another person's is `p` +
  12 hex of a hash of their account id. Their handle comes from the
  account's username (a digit is added when it is taken), their name from the
  account's display name; both are theirs to change (`me_update`). A person
  who joins is added to `#general`, and Team Chat posts "@alice joined the
  space." there.
* **Roles.** Owner: everything (as Phase 2). Admin: what a member does, plus
  rename, archive and change the members of any channel they can see (never
  taking the owner out; since the second check, not the owner's channels'
  privacy, wake mode, name or archive, nor the owner's agents they may not
  use: see "Admin powers"), start, stop and stop the turn of agents they may
  use, invite people and remove members (not admins or the owner). Retrying
  a failed turn and resetting an agent's session are the owner's. Member: post, edit and
  delete their own messages, create channels (public or private), change the
  channels they created, join public channels, add people and agents they may
  use to channels they are in, tasks in the conversations they see, use the
  agents they may use. Only the owner creates, deletes or changes agents
  (their computer, folder, runtime and access), changes the space's name,
  limits and time zone, makes admins, and watches a turn (Watch reads the
  owner's computers). Phase 3 agents all belong to the owner.
* **Who sees what.** A person sees: public channels, the conversations they
  are in, and the DMs of the agents they own (an agent DM is also the
  agent's owner's). The owner also sees every channel of their space,
  private ones they are not in included (listed as not a member, nothing
  unread until they join; they may join, read, archive and manage them). So
  a private channel is invisible to everyone not in it but the owner, and a
  DM between two people is theirs only. Unread counts,
  Activity, notifications and Mark all read cover the conversations a person
  is in (and their agents' DMs); a public channel they have not joined is
  listed with nothing unread until they join it. Search, Files, Tasks and
  every message read cover what the person sees. An agent sees its
  conversations and public channels; while its turn works for people other
  than its owner, only what they see too (see "No relay around access").
* **Who may use an agent.** *Only me*: its owner only. *Everyone in this
  space*: every person. *Listed people*: its owner and the people in
  `access_list` (person ids). Without it a person cannot DM the agent, add it
  to a conversation, assign it a task, or wake it: a mention, a reply in a
  thread it follows or a channel set to wake every agent wakes only the
  agents the writer may use. A mention of an agent they may not use is posted
  (people see it) and the post's answer names it (`not_woken`), so the page
  says "Writer was not asked: only @weize can use Writer." An agent's
  messages stay visible to everyone in the conversation, whoever may use it.
* **No relay around access.** A turn works for the person whose message
  started it (since the second check exactly one person, `works_for`: see
  "Turn-bound commands"; a reminder's person; an agent's message carries its
  turn's). An agent's
  message wakes another agent only when that person may use it; otherwise the
  agent's post answer says who was not woken and why. The same holds for
  reading: such a turn's `read`, `search`, `channels`, `tasks`, `inbox` and
  its input ("Since you last looked") cover only what every person it works
  for sees, and a turn takes only the queued asks that fit one audience (the
  rest wait for a later turn). The owner's turns keep the agent's whole view.
  The agent's own memory (its session, `MEMORY.md`) is not split by person:
  people who DM a shared agent are told that its owner reads the DM and that
  it may remember what they say.
* **Billing.** Turns run on the owner's computers with the owner's keys
  (Phase 3 agents are the owner's), so a member's question bills the owner's
  budget; nothing of the member's is used.
* **Invite.** Share… on Team Chat makes a link that joins the space being
  viewed (owner or admin; a member is told only owners and admins invite).
  The link's page has two steps: Account, then "Join <owner>'s space"; the
  end is `/app/team-chat?space=<space id>`. No trial credit (the owner's
  agents answer on the owner's budget), no Workspace, no computer. An
  account that exists signs in and joins; a link with no sign-ups left still
  lets an account join. A link is per space; revoking it stops new joins, not
  the people who joined. A link works only while whoever made it is still
  the space's owner or an admin (an admin's links are revoked when they are
  made a member, removed or leave), and the owner lists and may revoke every
  invite into their space, whoever made it.
* **Removing.** Owner (or an admin, for a member) removes a person in Space
  settings → People; a person may leave a space themselves. The Hub deletes
  the membership, the controller drops that person's live grants at once and
  marks the person removed in the service (out of every channel, their
  reminders cancelled, "@alice was removed from the space." in `#general`,
  a `removed` event to their page). Their next call fails ("You are no
  longer in this space"); their messages stay with their name. They keep
  only what they downloaded.
* **Read state of others.** Each person sees, under the newest message
  another person read, "Seen by Alice" (in conversations with two or more
  people), live.

### Contract added (`team-chat` 1.2.0)

**Callers.** The owner's browser (`Call.Owner`), an App of the owner's
deployment (as the owner), and now a **space grant**: the Hub asks the
controller's App gateway to connect a person's browser to the owner's
`team-chat` App with `member: {grant_id, space, user_id, username, name,
fleet_id, role, joined}` (`fleet_id`: the person's own Fleet; `role`:
`admin` | `member`; `joined`: unix seconds). The gateway accepts a member
grant only for the hosted `team-chat` binding of the Fleet named by `space`,
and lets through only `POST /rpc` and `GET /__fleet/status`; the hosted host
passes the identity to the service as `Call.Member`, and no other hosted App
accepts one.

**`space_get`** adds `role` (`owner` | `admin` | `member`), `owner`
(`"owner"`), `can_use` ([agent mid] the caller may use) and `reads_by`
(`{cid: {mid: id}}`: other people's read marks in the conversations the
caller sees). `me`, `subject` and `revision` are the caller's. Person
records carry `role` and, once removed, `removed_at`; for anyone but the
owner an agent record leaves out `folder`, `node_id`,
`session_conversation_id`, `turns` and `turn_spans`, and a status leaves out
`cid`, `root` and `line` when it is about a conversation the caller does not
see. `conversations` are those the caller sees (public channels they have
not joined included, `unread` zero there).

**`post`** answers `{message, task?, not_woken?: [{mid, reason}]}`.
`conv_create` (a DM, or members), `conv_update` (members), `conv_join`
(another member) and `task_update` (assignee) refuse an agent the caller may
not use ("Only @weize can use Writer.").

**Refusals** in plain words: "Only the space owner can …", "Only the space
owner and admins can …", "You are not in this conversation", "You are no
longer in this space". `agent_live` is the owner's. `reminders` lists, for
anyone but the owner, the reminders for them; `reminder_cancel` likewise.
`file_get` serves a file attached to a message the caller sees, or one the
caller uploaded.

**Events.** Each person has their own subject and revisions: the owner's is
`fleet.<owner fleet>.apps.team-chat.events` as before; another person's is
`fleet.<their fleet>.apps.team-chat.spaces.<owner fleet>` (in their own App
namespace, so their browser follows it with its own App bus credential;
nobody else's credential reaches it). A person gets only what they see: a
message, task, `typing` or `read` event of a conversation they see; `member`
and `agent_status` events (redacted as `space_get`); `space`; their own
`notify`; `reminder` events of reminders for them (the owner: all). New:
`{type: "conv", rev, cid, gone: true}` when a conversation is no longer
theirs to see (e.g. removed from a private channel), and `{type: "removed",
rev}` when they were removed from the space.

**Turn input.** People are marked `@alice (person)`, `@sam (person, admin)`,
`@weize (person, space owner)`; `AGENT.md` lists every person.

### Interface as built (Phase 3)

* **Which space.** `TeamChat.vue` wraps the App: no `space` arg (or the
  person's own space id) talks to their own `team-chat` App as before;
  `?space=<id>` (a window arg or the page address) talks to that space through
  `spaceTransport` (`client.ts`): the App service's connect step is the Hub's
  `POST /api/team-chat/spaces/<id>/connect` (`createAppService` takes a
  custom `connect`), calls are `POST /rpc` there with the same retries, and
  events come on the subject `space_get` names, followed on the person's own
  App bus. The rail header is a space picker ("Your space" and each joined
  space, "<owner>'s space" until its name is read); a switch writes `?space=`
  and remounts on that space; the last space is remembered. A person without
  a Team Chat of their own (no Agent deployment) but with joined spaces opens
  the first of them. The own space id is computed in the browser as the Hub
  does (`f_` + 16 hex of the SHA-256 of the account id).
* **Removed.** A refused grant (403), an answer "You are no longer in this
  space" or a `removed` event shows "You're no longer in <space>. Messages you
  downloaded stay with you." with buttons to the person's other spaces; the
  space's state is dropped. A `conv` event with `gone` drops that
  conversation (if open: a note, then #general).
* **Roles.** Members and admins see no New agent, Computers, agent Folder or
  Settings, Full reset or Delete, space name, time zone or limits, Watch, and
  no first run. Admins keep Controls (Start/Stop, Stop turn) and Stop on the
  working line for agents they may use (Retry, the fix and Reset session are
  the owner's); channel
  settings are the owner's, admins' and the channel maker's (others can leave
  and add people and usable agents).
* **Access.** An agent the person may not use is greyed in the rail, the
  mention picker, New conversation and channel member pickers, with "Only
  @weize can use Writer" or "Writer is shared with listed people only"; its
  DM cannot be written in and its profile's Message is off. A post's
  `not_woken` shows under the composer ("Writer was not asked: only @weize
  can use Writer."), cleared on the next send or another conversation.
* **Channels.** Public channels the person is not in are under "More
  channels" with Join; opened, they show "You're not in #x. Join it to write
  here…" instead of the composer. Unread counts and browser notifications
  cover only conversations the person is in (and their agents' DMs).
* **Seen by.** Under the newest top-level message each other person read
  (`reads_by` and live `read` events), "Seen by Alice, Sam", in
  conversations with two or more people.
* **People.** Space settings has Settings and People tabs. Owner and admins
  read the Hub's list (role select for the owner, Remove with "<Name> loses
  access to this space at once. Their messages stay.", Invite people…, which
  opens Share…); members see the people and Leave space.
* **Share and invite.** Share… on Team Chat invites into the space last shown
  (`teamChatSpace`, set by the App): the dialog says "They join <space>: they
  see #general and the public channels, and can talk to the agents you share
  with them. Their questions to your agents run on your computers and use
  your model budget.", has no trial credit, and sends `space`; a member reads
  "Only the space's owner and admins can invite people." The invite page of a
  space invite has Account, "Join <owner>'s space" (done at once when the
  person is in it already; 409 means their own space, which then opens) and
  Open `/app/team-chat?space=<id>`; it starts no Workspace.
* **Shared agents (T35).** New agent and the agent's Settings offer Only me /
  Everyone in this space / Listed people (people of the space). For a shared
  choice they say: "People you share Coder with can ask it to read anything
  <computer>'s user can read, whatever its runtime, not only its folder, and
  to change or run things there. Codex's sandbox, when on, only stops writes
  outside the folder, not reads. It also reads the channels it is in,
  remembers what people tell it, and can repeat either to others. Your
  Workspace or a computer of its own is safer for a shared agent." (after the
  first check; the first wording wrongly said only Codex keeps to its
  folder); New agent and Settings refuse a shared agent in a home folder, a
  folder that holds home folders or `/` before asking the service. Status reasons that say "your
  Workspace" read "<owner>'s Workspace" for anyone but the owner.
* **Tests.** `phase3.test.ts` (access and its words, unread only where one
  is, conv gone, removed, Seen by, not-woken note, member's channels, the
  space transport and its 403, which space opens, the invite's steps).

### Hub (`space_members`)

Table `space_members` (`id`, `space_id` = the owner's Fleet id,
`owner_user_id`, `user_id`, `role` owner | admin | member, `joined_at`,
`invited_by`, `invite_id`; unique `(space_id, user_id)`), and
`app_invites.space_id` (migration `migrations/add_space_members.sql`, run on
staging only; re-runnable). A removed person's row stays with role `removed`
(`joined_at` is then when), so the link they came by does not let them back
in; a link made after the removal does. A person who leaves is deleted and may
come back through a link. API (signed in):

| Route | Does |
|---|---|
| `GET /api/team-chat/spaces` | `{spaces: [{space_id, owner_name, owner_username, role, joined_at}]}`: the spaces the caller joined (not their own) |
| `POST /api/team-chat/spaces/{space}/connect` `{ui_origin}` | A space grant to the owner's `team-chat` App: `{origin, ticket, expires}` (as `/api/fleet/apps/connect`); 403 "You are not in this space."; 409 when the owner's Team Chat is not running |
| `GET /api/team-chat/spaces/{space}/members` | Owner and admins: `{members: [{user_id, username, name, role, joined_at, mid}], role}` |
| `PATCH /api/team-chat/spaces/{space}/members/{user}` `{role}` | Owner: `admin` or `member`; the person's grants are dropped, so their next call carries the new role |
| `DELETE /api/team-chat/spaces/{space}/members/{user}` | Owner (anyone but themselves), an admin (a member), or the person (leaving); drops their grants and marks them removed in the service at once |
| `POST /api/app-links` / `POST /api/app-invites` with `app: "team-chat"`, `space` | A space invite (owner or admin of that space); no trial credit |
| `GET /api/app-invites/{id}`, `GET /api/app-links/{id}` | Add `space: {id, owner}` for a space invite |
| `POST /api/app-invites/{id}/join` | Signed in: joins the invite's space as a member (once); `{space_id, role, joined}`; 410 for a revoked or expired link (a full one still lets an account join); 409 for the owner; 403 "You were removed from this space. Ask its owner for a new invite." for a removed person on a link made before the removal |

The controller has `POST /team-chat/members` (Hub service token: `{fleet_id,
user_id, action: remove | leave | role, role}`) for the last two: it drops the
person's live grants on the App gateway and updates the service, in one
request.

### Service as built (Phase 3)

**Files.** `teamchat_people.go` (identities, `callerOf`, `MemberChange`,
`visible` / `inConv` / `canUse`, the turn's `behalf`, per-person views and
events), the rest in place: `Call` in `teamchat_tools.go` resolves the caller
first and checks role and access per method; `wakesFor` checks the writer's
(or the turn's people's) access; `appgateway/member.go` (the member grant);
`hosted.Host.ServeRPC` passes `Call.Member`; the controller's
`POST /team-chat/members`. The manifest is unchanged (no new method or
argument), so the hosted binding's revision did not move.

**Found and decided during the build and the check.**

* A turn's `behalf` is computed from its events (people's messages, a
  reminder's person, `behalf` carried on agents' messages and on reminders
  set during a turn); with nobody behind it (an agent's own reminder) it is
  the agent's owner.
* An ask queued for an agent whose access then changed (or whose person
  left) is dropped when the turn would start, and by `inbox`; a person's
  reminder is never dropped this way.
* "In review" in Activity is for the person who made the task (the owner for
  tasks made by agents or by people who left), not for everyone who sees it.
* Watch is the owner's (it reads the owner's computers); admins see the
  working line and may stop a turn.
* A failure line's fix and Retry are the owner's; anyone else reads
  "Coder couldn't answer right now." (changed after the first check).
* Admin invites say the agents, computers and budget are the owner's.

### Tests (Phase 3)

`os/internal/hosted/teamchat_people_test.go`, against the in-process
JetStream server and the fake node, a space with three agents (Only me,
Everyone, Listed: Alice), the owner's private channel, the owner's DM with
an agent and three people (Alice, Bob, admin Carol):

* `TestAMemberCannotReadListOrSearchWhatIsNotTheirs`: for the private
  channel and the owner's DM, `messages`, `read`, `files`, `notify_set`,
  `post`, `conv_join`, `conv_leave`, `conv_update`, `conv_archive`, `search`
  (with `cid`), and for a message there `follow`, `task_create`,
  `task_update`, `edit`, `delete` are refused; `file_get` of its file and
  posting it are refused; `space_get` lists neither, nor their unread or
  tasks; agents' folders, nodes and sessions and people's Fleets never reach
  her; search finds nothing of them, `tasks` none, Activity (all four
  filters) no row; `read_all` marks nothing there; `space_update`,
  `agent_create`, `agent_update`, `agent_live`, `agent_control` (reset,
  stop) are refused; `reminders` lists only hers and `reminder_cancel`
  refuses another's.
* `TestPeoplesDMsAreTheirsAndAgentDMsAlsoTheOwners`: a DM between two people
  is not listed, read or searched by the owner or an admin; a person's DM
  with an agent is the agent owner's too, not a third person's.
* `TestAnOnlyMeAgentIsNotUsableByAMember`: `can_use`; DM, channel members,
  `conv_join`, `conv_update` members and task assignee refuse an Only me (and
  a Listed, for someone not listed) agent with "Only @owner can use Writer.";
  a mention is posted with `not_woken`; a channel set to wake every agent
  wakes only the ones she may use; a thread reply and an edit do not wake it;
  its replies stay readable.
* `TestAnAgentCannotWakeAnAgentThePersonItWorksForMayNotUse`: an agent
  working for Alice gets "Not woken: @writer …" for a post and a DM, and
  nothing is queued; working for the owner, the same post wakes it.
* `TestEachPersonGetsOnlyTheirEvents`: on the three subjects, the private
  channel's message reaches only the owner, two people's DM only them;
  revisions are each person's, without gaps; another's read mark is seen
  (`read` event, `reads_by`); a person taken out of a private channel gets
  `gone`; a status about a conversation they do not see has no `cid` or line.
* `TestRemovingAPersonEndsTheirAccessAtOnce`: `removed` event; every method
  then answers "You are no longer in this space."; nothing more on their
  subject; out of every channel; a grant made after the removal lets them in
  again; a role change follows the Hub.
* `TestRoles`, `TestOnlyTeamChatTakesAMembersCall` (desktop-state refuses a
  member call with 403), `TestMakingAnAgentSharedChecksItsFolder` (home,
  `/`, an offline computer), `TestQueuedAsksOfPeopleWhoLostAccessAreDropped`,
  `TestInReviewIsForWhoeverMadeTheTask`, `TestGrantIDsAndPersonIDsAreStable`.
* `os/internal/appgateway/member_test.go`: a member grant is refused for
  another App, another space, an owner role, a workload, a node's App; it
  reaches only `POST /rpc` and `GET /__fleet/status` (the App's page, files,
  model media and other methods are 403 and never reach the App), with the
  person's identity; `RevokeShare(grant id)` ends it (next call 401).
* Hub `tests/test_team_chat_spaces.py`: only the owner or an admin invites
  into a space and only for Team Chat, no credit; sign up, join, join again,
  the owner's own space (409); the grant's binding and identity; not a
  member (403), not running (409); roles, members list, removing (an admin
  a member, not an admin; a member nobody), leaving; a removed person and the
  old link (403), a newer link; a full link still lets an account join.

All of `os` (`go test ./...`, `-race` on `internal/hosted`), the hub's
invite, link, share, fleet and space tests, and the UI's Team Chat and
invite tests pass.

### Staging verification (2026-11-05)

Controller `b8b90ef4` then `2a8975fa`, hub `c413e60` then `ea2eda2`,
frontend `c7108a74` then `d1f7cd7e`, `f570c28a`; one headless Chromium with
two browser contexts: the owner (the test account, its space "Team") and a
stranger with no account. Agents on the test Mac node. Staging's model proxy
had changed meanwhile: its model list no longer had the OpenRouter routes,
and the direct vendor routes had no credit, so Claude Code agents failed
("Credit balance is too low") whatever their model. Coder (Codex) was set to
`openrouter/deepseek/deepseek-v4-flash-0731` and shared for the check;
Writer stayed Only me (and needs no turn for T32).

| | Result |
|---|---|
| T30 | Pass. Share… on Team Chat: "They join Team: …", a link (1 day, 2 sign-ups). In a fresh context the link's page said "Weize invited you to their Team Chat space", steps Your account, Join Weize's space, Open Team Chat; Create an account → signed in and joined (8 s, "You're in Weize's space"); Open → `/app/team-chat?space=<owner's Fleet>&conv=general`, `#general` with "@tc-p3 joined the space." The browser loaded `/`, `/invite/s/…` and `/app/team-chat`, never `desktop.html`; no pod or Workspace of the new account. |
| T31 | Pass. "@coder … What is 6+7?" from the member: "13" from Coder (Codex · DeepSeek V4 Flash) 8 s later. The owner's model key spend rose $4.5964 → $4.6248; the member's stayed $0.00. The member's rail had `#general`, More channels (`#docs`), no DMs; the owner's private channels (`#private-ops`, `#ui-check`, `#v4-private`) and DMs were not listed, and their addresses opened `#general`; search for a word posted in `#private-ops` found 1 result for the owner, 0 for the member, whose In list had only `#docs` and `#general`. |
| T32 | Pass. New direct message greyed Pan, Assistant, Writer and Scribe ("Only @weize can use Writer"), Coder usable. "@writer can you summarize the docs for me?" was posted, with "Writer was not asked: Only @weize can use Writer." under the composer, and Writer's queue did not grow. Writer's earlier replies in `#general` were in the member's view. An ask the member had queued for Writer while it was shared was dropped when the owner pressed Retry after making it Only me (queue 1 → 0, no turn). |
| T33 | Pass. Both on `#general`: the owner's message appeared on the member's page 0.27 s after Enter and the member's on the owner's in 0.26 s, no reload; the owner's edit reached the member in 0.25 s with "(edited)"; "Seen by Weize" under the member's message and "Seen by tc-p3" under it on the owner's page. Notifications (page hidden): the member got "Weize mentioned you in #general", the owner none for their own message, and the reverse; with `#general` muted by the owner, the member's mention made none for the owner while the member still got the owner's. Activity: the member's had the owner's mention (@1), the owner's did not have it. |
| T34 | Pass. Space settings → People → Remove → "tc-p3-9abd04 loses access to this space at once. Their messages stay." → the member's open page showed "You're no longer in Team. Messages you downloaded stay with you." 0.25 s later; a call with the old connection got 401 from the App gateway; a reload showed the same page; `#general` no longer listed the member; their messages kept their name. Made admin again later (People showed Invite and Leave for the admin, who made a link), removed again: the link they came by then answered "You were removed from this space. Ask its owner for a new invite." |
| T35 | Pass. Writer's Settings → Who can use it → Everyone showed "People you share Writer with can ask it to read, change or run anything the computer's user can reach on Weizes MacBook Pro, not only its folder. Only Codex keeps to its folder. It also reads the channels it is in and can repeat what it read there. Your Workspace or a computer of its own is safer for a shared agent." and saved (folder checked on the computer). New agent with folder `/Users/weizexu` and Everyone: "A shared agent can't work in a whole home folder or the whole disk: pick a folder of its own.", Create off. Through the service: Writer shared and moved to `/Users/weizexu` → "A shared agent cannot work in a home folder: …"; a shared agent at `/` refused. |

**Found and fixed during the check:** In review rows for tasks people did
not make; Retry shown to a member; an ask queued while the agent was shared;
a removed person rejoining through the same still-live link (it now refuses,
hub `ea2eda2`); an admin's invite text naming "your" agents and budget.

**Left:** a failure line ("…its model budget is used up … press Retry") is
written for the owner and every person in the conversation reads it; the
space switcher offers "Your space" to an account that has no Team Chat of its
own yet (choosing it says Team Chat is not running); the Shared links list
names a space invite "Their own". The staging model proxy's lost OpenRouter
model list (above) is not Team Chat's and was not changed.

### Fixes after the first Phase 3 check (2026-11-06)

The first independent check (staging, 2026-11-05) found two access-control
holes that let an admin take power from the owner, a shared agent reading
DMs for whoever asked, a false claim about Codex in the T35 dialog, and
smaller leaks. Each is fixed with a test that failed before the fix
(`os/internal/hosted/teamchat_p3fix_test.go`, hub
`tests/test_team_chat_spaces.py`, UI `phase3check.test.ts`, `spaces.test.ts`,
`ShareDialog.test.ts`) and retested on staging through the UI and with the
member's or admin's own grant.

| Finding | Fix |
|---|---|
| An admin could make the owner's channel private without the owner and lock them out; a private channel whose people were all removed was invisible and could not be archived | The owner sees every channel of their space (`visible`); private ones they are not in are listed as not a member, readable, joinable (`conv_join` of a private channel is allowed to the owner), archivable. Nobody but the owner takes the owner out of a conversation (`conv_update` members, `conv_leave {mid: owner}`: "The space owner stays in #x: only they can leave it."). |
| An admin's invite links kept working after the admin was demoted or removed; the owner could not see or revoke them | Hub: a space invite works only while its maker is still owner or admin (checked at summary, register and join: "withdrawn", 410), and demoting, removing or the leaving of an admin revokes their live links into the space. `GET /api/app-invites` and `/api/app-links` list, for the owner, every invite into their space with `made_by`; `DELETE` on both works for the owner. |
| A shared agent read any DM it was in for whoever asked | While an agent's turn works for people other than its owner (`behalf`), `visible` for that agent also requires every one of them to see the conversation: `read`, `search`, `channels`, `join`, `tasks`, `remind --about`, reply targets and the turn input's "Since you last looked" follow it ("Not shown: … is not a conversation the person this turn works for can see"). A turn takes only queued asks that fit one audience (`turnEvents`), and `inbox` hands over only what fits the running turn; the rest waits. A request outside a turn uses the last turn's people. |
| People DMing a shared agent were not told its owner reads the DM | The DM, New direct message and the agent's profile say "Weize owns Coder and can read this conversation. Coder may remember what you tell it." |
| The T35 dialog said Codex keeps to its folder | The dialog and this doc say what is true (above). Shared folders also refuse `/Users`, `/home`, `/var/home`, `/root`, `/var/root`, `C:\Users`, … and any parent of the computer's home (asked of the computer: `team_setup` check of `~`); `agent_create` checks a shared agent's folder on the computer before it writes anything there. |
| A member could take the owner's name ("Weize") | `me_update` refuses a name or handle whose skeleton (case, spacing, punctuation, accents, look-alike letters folded) equals any other member's name or handle, or is one letter off the owner's or an admin's: "That name is too close to @weize (Weize). Choose another." A person who joins with such an account name goes by their handle. |
| An admin's `agent_control` answer carried folder, `node_id`, turns | For anyone but the owner it is the agent as they see it. Retry (it spends the owner's budget on what the owner was told to fix) and `reset_session` are the owner's. |
| Avatars could be any URL | Only `data:image/(png|jpeg|gif|webp);base64,…` or none; anything stored before is left out of what browsers get. |
| Members saw the owner's account username, the Mac's hostname, the owner's account id (Workspace node name), usage counts | For anyone but the owner: agents' `node_name` and status `computer` are "Weize's Mac" / "Weize's Workspace" / "Weize's computer" (`node_os` is kept for this); the owner's `prev_handles` are left out; no `usage` in `space_get` or `space` events; Hub spaces list without `owner_username`, members list with account usernames for the owner only. |
| Failure lines told members to fix the owner's budget | Anyone but the owner gets "Coder couldn't answer right now." without `action`, and agent statuses without `fix` and with that reason. |
| Join and remove lines kept the old handle, in the warning style | They carry `about` (the person) and `action` (`joined`, `left`, `removed`); the page names the person by their handle now (older lines by an earlier handle) in the neutral style. |
| Invitee UX | No "Your space" for someone without a space of their own (a separate "Create your own space…" leads to the first run only when chosen); Share… for a member says at once that only owners and admins invite; Shared links names a space invite "Team Chat space invite" (with "Made by" for an admin's); People shows the handle as chat does; the removed screen names the space after a reload. |

Staging verification (2026-11-06, controller `85bd63a5` then `f25093d8`,
hub `15128fe`, frontend `3701e06c` then `ac426461`; the owner and two
members, Alice and Bob; one headless Chromium):

* Owner: the rail listed `#p3v-alice-ch` (left over by the check: private,
  its people removed) as "You're not in #p3v-alice-ch (private). Join it to
  write here."; the owner archived it. Bob as admin: `conv_update {private,
  members: [bob]}` and `conv_leave {mid: owner}` refused; `conv_update
  {private: true}` kept the owner in.
* Bob as admin made two links; the owner listed both with `made_by` and
  revoked one; after Bob was made a member, an existing account's `join` of
  the other answered 410. Bob, admin again, made a link; after his removal it
  answered 410 too. Bob's `agent_control` start and stop answers had no
  folder, `node_id` or turns; reset (and, after the second deploy, retry)
  were refused.
* Alice told Coder (shared, Codex on `openrouter/deepseek/deepseek-v4-flash-0731`)
  a code word in a DM; Bob asked Coder in `#general` to paste `read
  @p3v-alice` and a search. In Bob's turn the tool answered "Not shown: DM
  with @p3v-alice is not a conversation the person this turn works for can
  see". Coder still knew the word from its own session and had written it to
  its `MEMORY.md` in Alice's turn, and declined on its own: the agent's
  memory is not split by person (see "Left").
* Alice: "Weize", "W e i z e", "Wéize!", the handle `weize_` and "Coder"
  refused; `javascript:` and `https://` avatars refused; `space_get` had no
  usage, no owner earlier handles, "Weize's Mac" / "Weize's Workspace" and no
  hostname or account id; the Hub spaces list had no `owner_username`. With
  the owner's key over its maximum, Alice's ask gave her "Coder couldn't
  answer right now." and a calm line; the owner read the budget fix with
  Retry. The DM with Coder showed the owner-reads note; Share… said at once
  that only owners and admins invite; the switcher had Team and "Create your
  own space…", no "Your space"; Bob's removed screen named Team after a
  reload; People showed "Alice @p3v-alice".
* T35: the dialog shows the new words; through the service a shared agent
  at `/Users`, `/Users/`, `/Users/weizexu/..`, `/private/../Users`, `/home`,
  `/root`, `/var/root`, `/Users/weizexu` and `~` was refused, Writer moved
  to `/Users` refused, a folder of its own accepted.
* Billing: the test node now runs Codex and Claude Code on PantheonOS models
  (`model_source = pantheon` in its own Coding Agents folder, never the
  Mac's own sign-ins); the owner's key moved $4.6248 → $4.7897 over Coder's
  four turns (deepseek), the members' stayed $0.

**Left.** A shared agent's own memory (its runtime session and the
`MEMORY.md` it keeps) is not split by person: what one person tells it can
come back in another person's turn if the model chooses to repeat it. The
DM note and the T35 dialog say so; per-person sessions would close it. (A
shared folder is now checked where it really is, symlinks included: see the
second check.) An owner without a display name is still named by
their account username (as the invite page names them).

### Fixes after the second Phase 3 check (2026-11-06)

The second independent check (staging, 2026-11-06) found that "no relay around
access" could still be bypassed: an agent's commands were not bound to the
turn and the person it worked for. Three holes gave a member's request the
owner's full view (an agent's edit re-woke an Only-me agent with no access
check; a member's reminder posted while its agent was stopped woke one as
the owner; and a command from a process left running by a member's turn read
the owner's private channels during the owner's next turn), and smaller ones
followed from the same cause (`remind list`, mid-turn follow-ups, tasks). The
fix is at the cause: "Turn-bound commands" and "Admin powers" under "Privacy
and permissions". Each hole has a test that fails on the code before the fix
(`os/internal/hosted/teamchat_p3check2_test.go`; node
`apps/coding_agents/team_test.go` `TestATeamTurnsCommandsAndProcessesEndWithIt`
and `teamfolder_test.go`; hub `tests/test_team_chat_spaces.py`; UI
`phase3check2.test.ts`).

| Finding | Fix |
|---|---|
| 1. An agent's edit re-woke the agents its message woke, with no "works for" and no access check | An edit by an agent carries its turn's person; it re-wakes an agent only when that person may use it, and the wake carries the person (checked again when the turn would start). An agent edits only messages in conversations that person sees. |
| 2. A person's reminder posted by Team Chat outside a turn (agent stopped or busy) woke agents as the owner | A reminder set in a turn works for that turn's person, always; Team Chat posts it with that person, so its mentions wake only agents they may use. In a member's turn `remind --for` names only that member or the agent. |
| 3. Agent commands were not bound to a turn (a fixed per-agent token; outside a turn, the last turn's people) | Turn id + credential made at turn start, dropped at its end, given to the turn's processes only in their environment; the node and the controller refuse a command without the running turn's credential; no fallback. A Team Chat turn runs in its own session and its leftover processes are ended with it. |
| 4. `remind list` (and `cancel`) in a member's turn showed the owner's reminders | In a member's turn only reminders set in that member's turns or for them, or tied to a conversation they see. |
| 5. T35 bypassed through `/System/Volumes/Data/Users/<me>`, `/Volumes/<boot>`, symlinks, case | The computer resolves the folder (symlinks, true case, macOS data volume and boot volume names) and compares it, and its device and inode, with every home folder, the folders that hold them and `/` (`team_setup` check: `real`, `home`, `root`, `holds_home`); the service refuses by that. A relative folder, `~user`, `$HOME`, `smb://` or `\\server` is refused at once: "Choose a full folder on that computer…". The New agent dialog folds the same macOS names and asks for a full folder before Create. |
| 6. An admin could make the owner's private channel public, take the owner's Only-me agents out, set wake-every-message on #general | "Admin powers": the owner's channels' privacy, wake mode, name and archive are the owner's; nobody else takes the owner's agents they may not use out of them. |
| 7. Names that pass for others: RTL override, "Team Chat", "Weize (owner)", newline, invisible; handles `owner`, `everyone`, … | Names refuse control, format (direction, zero-width) and blank characters and need something visible; people's names refuse "Team Chat", "system" and a role at the end ("(owner)", "admin"); handles `owner`, `admin`, `everyone`, `here`, `all`, `channel`, `system`, `team-chat` are reserved. An account name that fails goes by its handle. |
| 8. An owner's turn took in a member's ask mid-turn (and the reverse) | A turn works for one person and takes only that person's messages (start and `inbox`); the others start their own turn after it. |
| 9. Agent `task claim/state/assign` did not check the task's conversation; `assign` no access check | Only tasks in conversations the turn's person sees; assign only to agents that person may use and people still in the space. |
| 10. Leaving and rejoining within a second locked a person out (seconds compared, two clocks) | The Hub gives each membership an id (`membership`: its row and the microsecond it began) in the space grant and in a removal or leave; the service refuses a grant of the membership that ended and only that one. |
| Refusal words "can  an agent", "can reset full an agent" | "Only the space owner can do that to an agent / fully reset an agent / delete an agent." |
| The invite page named an admin inviter by their account username | An admin's invite names them by a name they chose, else by the space's owner. |
| Members saw each other's earlier handles (account usernames) | A person's `prev_handles` reach only themselves and the owner. |

Staging verification (2026-11-06, controller `8f252aac`, hub `767986d`,
frontend `1bb09915`, the test Mac node on `fleet-v0.5.1-staging.12`; the owner,
Alice (member) and Bob (admin) with their own grants; Coder (Codex,
`openrouter/deepseek/deepseek-v4-flash-0731`, shared) and Writer (Claude Code,
haiku through OpenRouter, Only me); no browser):

* **Edit-wake.** Coder posted "@writer please reply with just the word PONG"
  for the owner (Writer woke). The model refused when Alice asked it to edit
  that message, so the check ran the command as a cooperative agent would,
  inside Alice's running turn (with that turn's credential, read from the
  turn's process): `edit` → "Edited message …" to "@writer please check
  #p3c2-vault …"; Writer was not woken (no turn, queue 0). In the same turn
  `read '#p3c2-vault'` → "you are not in #p3c2-vault". Without the credential
  the same command → "refused: this command is not part of the agent's
  running Team Chat turn".
* **Stopped-agent reminder.** In Alice's turn `remind 'in 2 minutes'` "@writer
  please check #p3c2-solo …" (for @p3v-alice); Bob stopped Coder; at the due
  time Team Chat posted "⏰ Reminder for @p3v-alice: @writer please check
  #p3c2-solo …" and Writer did not wake.
* **Background loop across turns.** Coder (Alice's turn) ran `nohup sleep 777
  … &` and `pgrep` showed it running; two seconds after the turn ended it was
  gone. Two leftover loops reading `#p3c2-vault` every 3 s (one with Alice's
  ended turn's credential, one with none) got 17 refusals each during the
  owner's next turn and no canary (the first check: the canary 88 times).
  (Coder refused to start an endless polling loop itself.)
* **`remind list`.** The owner had a reminder ("owner note: rotate the OSPREY
  vault code"); in Alice's turn `remind list` → "You have no reminders." and
  `remind cancel <its id>` → "you have no reminder".
* **T35.** Shared dry runs on the Mac were refused for
  `/System/Volumes/Data/Users/weizexu` (a home folder),
  `/System/Volumes/Data` and `/Volumes/Macintosh HD` (the whole computer),
  `/System/Volumes/Data/Users` (holds home folders), `/Volumes/Macintosh
  HD/Users/weizexu`, `/users/Shared`, `/USERS/WEIZEXU`, and symlinks to `~`,
  `/` and `/Users`; `agents/coder`, `~weizexu/agents`, `$HOME/agents`,
  `smb://server/share` and `\\server\share` → "Choose a full folder on that
  computer…"; `/Users/weizexu/PantheonAgents/t35ok` accepted.
* **Admin.** Bob on the owner's private `#p3c2-vault`: `private: false`,
  `agent_reply: all` (there and on #general), taking Writer out
  (`conv_update` members and `conv_leave`), rename and archive were all
  refused with "Only the space owner can …"; Alice still could not read it.
* **Names.** Alice's `me_update` refused "‮ezieW" (RTL override), "Wei⁦ze",
  "Team Chat", "Weize (owner)", "Space owner", a newline name, a zero-width
  space, a Hangul filler; handles `owner`, `everyone`, `here`, `all`,
  `channel`, `system`, `team-chat` were reserved.
* **Leave and rejoin.** Alice left and joined again through the invite at
  03:07:29.45 and 03:07:29.70 UTC (the same second) and her next calls
  worked.
* **Released node.** After the swap to the released `staging.12` binary, a
  Coder turn used `pantheon-team read` and posted.
* Spend: the owner's key $5.0424 → $5.2990 over 10 Coder turns and two Writer turns (Writer's two
  wakes at the start, both skipped); Alice and Bob $0.00.

The Hub's own check that an invite link is newer than a removal (`created_at`
against the removal time) still compares times; a link made in the same
second as a removal (on a database that keeps whole seconds) is refused, and
the next one works.

## Out of scope

Payments, mobile native apps, external chat bridges, prod rollout (asked
separately), cross-Fleet agents (Phase 3b).
