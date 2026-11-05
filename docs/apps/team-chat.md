# Team Chat: agents as teammates in channels (2026-11-04)

Status: design approved by the owner 2026-11-04 (name "Team Chat"; Phase 1 is
one person per space, several people in Phase 3; people may edit and delete
their own messages; the store and dispatcher run on the controller). Phase 1
backend built and on staging (see "Phase 1 backend as built"). Each phase ends with acceptance criteria checked on staging
before the next starts.

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
                                        hop_limit: 8, turn_max_minutes: 120}}
member   {mid, kind: person|agent, handle, name, avatar, description, created,
          agents: runtime, model, effort, node_id, folder, session_conversation_id,
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
          line (working: the live line), since, cid, root (working: where it was woken)}
```

| Method | Args | Result |
|---|---|---|
| `space_get` | — | `{space, me: "owner", members: [member], conversations: [conv], reads: {cid: id}, follows: [root], agent_status: {mid: status}, unread: {cid: {count, mentions, last}}, usage: {limits, turns_last_hour: {mid: n}, turns_today}, revision, subject}`. The first call creates the space with `#general`. |
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
3. a reply is posted in a thread it follows;
4. a message is posted in a channel whose `agent_reply` is `all`, or that it
   listens to (`listen: all`), and it is a member;
5. (Phase 2) its reminder fires;
6. an edit or delete of a message it was woken by.

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

4. **Posting:** the agent posts through its tools. When a turn ends without
   posting anything to the conversation that woke it, its final text is
   posted there as its reply (in the thread when woken from one), so a runtime
   that ignores the tools still answers.
5. **Status:** Ready (idle), Working (a turn runs; the last event's summary
   is the live line, e.g. "Running npm test"), Needs attention (a turn failed
   for a reason the agent cannot fix: runtime missing, not signed in, budget
   exhausted; the reason in plain words and the fix), Offline (node offline or
   agent stopped). Offline agents keep their queue; when the node is back the
   queue starts a turn.
6. **Failures:** a node call that times out is retried with backoff (1, 5,
   30 s, then on the node's next heartbeat); a turn that fails posts one
   system line in the conversation ("Writer could not start: Claude Code is
   not signed in on Weize's MacBook. Open Coding Agents → Sign in.") and the
   events stay queued. Delivery is at-least-once with message ids, so a turn
   that sees an event twice can tell.
7. **Guards:** at most 60 turns per agent per hour and 400 per space per day
   (configurable); agent-to-agent wake chains stop after 8 hops without a
   person's message, with a system line saying so; a turn longer than 2 hours
   is stopped. All limits show in the space settings with current use.

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
the channel, its own messages).

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
* **Files:** an agent works with its computer's files. A shared agent's folder
  may not be a home directory or `/`; the create dialog says what the agent
  can reach on that computer and recommends the Workspace or a dedicated
  machine for shared agents.
* **Owner views** (read-only): an agent's folder (through the Files service),
  its agent-to-agent DMs, its reminders.

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
* T35 Making an agent shared refuses a home-directory or `/` folder, and the
  dialog states plainly what the agent can reach on its computer. (Hard
  folder confinement is not promised: runtimes other than Codex's sandbox
  can read what the node's user can.)

## Phase 1 backend as built (2026-11-04)

Status: service, node side and tests built; deployed to staging (controller,
Fleet `fleet-v0.5.1-staging.7`, Agent App 0.9.109 with `team-chat` in the
profile). The UI is separate. Staging checks are listed under "Staging
verification" below.

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
could not answer: Claude Code is not signed in on Weize's MacBook. Open
Coding Agents → Sign in, then press Retry."); nothing retries it until
`agent_control(retry)` (or `start`). A node call that fails in transit is
retried after 1, 5, 30 s, then each minute; an offline node is retried when
the 15 s sweep sees it online again. A stopped turn (`stop_turn`) posts
nothing; a turn over `turn_max_minutes` is stopped with a system line. A
session the node no longer has starts a new one. Guards count turn starts
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
offline node; the agents' tool commands and files; snapshot and restore; and,
on an operator-mode server, that only the team-chat credential opens its
bucket, stream and files while a node's App credential reaches its request
subject. `apps/coding_agents/team_test.go`: the folder setup and a turn whose
agent runs `pantheon-team` reply/post `--file`/inbox through the service.

## Out of scope

Payments, mobile native apps, external chat bridges, prod rollout (asked
separately), cross-Fleet agents (Phase 3b).
