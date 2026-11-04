# Team Chat: agents as teammates in channels (design draft, 2026-11-04)

Status: draft for review. Nothing built.

## Goal

People and agents work in one chat: channels, direct messages, threads and
tasks. An agent is a member with a name, a role, memory and a computer. It is
woken by messages addressed to it, works on its computer with its own tools,
and reports back in the conversation. People give direction and review.

Today PantheonOS has the pieces but not the place: the Agent (cloud, one
conversation at a time), Coding Agents (Claude Code / Codex / Gemini /
`pantheon` on Fleet nodes, with conversations that can be resumed), the Team
view (sub-agent runs), Agent Canvas, Fleet nodes and the platform budget.
Team Chat is the App that puts them in one conversation space and keeps them
running without a desktop window open.

## Concepts

| Concept | Meaning | Built on |
|---|---|---|
| **Space** | A team's shared place; everything below belongs to one | P1: one per account. P3: a hub `space` with members from several accounts |
| **Member** | A person or an agent; `@name`, avatar, role text | hub users; agent records (below) |
| **Computer** | Where agents run | Fleet nodes, including the Workspace |
| **Agent** | Persistent member: name, description (its role and brief), runtime (`pantheon`, `claude`, `codex`, `gemini`), model, computer, working folder, memory, who may talk to it | Coding Agents conversation service; Agent App for `pantheon` |
| **Channel** | Public or private conversation; `#general` holds everyone | new message store |
| **DM** | Two members; agent-to-agent DMs readable by the owners | same |
| **Thread** | Replies to one message; followers get notified | same |
| **Task** | A message with state Todo → In progress → In review → Done / Closed, one assignee, Claim | same |
| **Reminder** | A wake-up an agent sets for itself, once or recurring, tied to a message | dispatcher schedule |
| **Activity** | Inbox of what is new for me: mentions, unread, tasks to review | per-member read marks |

## How an agent works

1. **Wake.** A new message wakes an agent when it is a DM to it, @mentions it,
   is in a thread it follows, or is in a channel it joined and concerns its
   role (the agent decides; it stays silent otherwise). A reminder firing also
   wakes it. Nothing else uses model budget.
2. **Turn.** The dispatcher starts one turn of the agent's runtime on its
   computer, in its working folder, resuming the agent's own session (the
   Coding Agents conversation service already runs "one turn as JSON lines"
   and resumes by id). The turn's input is the new messages with their
   channel/thread context and the agent's brief.
3. **Tools.** The turn gets Team Chat tools through an MCP server the node
   provides to every runtime (and as a toolset for the Pantheon Agent):
   `post` / `reply`, `read_channel`, `search`, `create_task`, `claim`,
   `set_task_state`, `remind_me`, `dm`, `join_channel`, `list_members`.
   A turn that ends without posting gets its final text posted as a reply.
4. **Memory.** The agent keeps `MEMORY.md` and `notes/` in its working folder
   and reads them at the start of a fresh session; the message history is
   always searchable through `search`. Session reset keeps notes; full reset
   clears the folder.
5. **Busy.** Messages that arrive mid-turn are queued and delivered at the
   next step (the steer queue the Agent already has); an agent works one task
   at a time, so parallel work means several agents.
6. **Status.** Ready / Working / Needs attention (error with plain reason) /
   Offline (computer asleep or stopped). While working, the message being
   handled shows a live line ("Running tests…") and **Watch** opens the live
   session (tool calls, terminal) in a side panel; **Stop turn** ends it.

## Where things run

* **Message store and dispatcher run on the controller**, next to the
  desktop state (`docs/os/desktop-state.md`, KV on NATS JetStream), so the
  chat and agents keep working when no browser or Workspace is open. The
  dispatcher is small: route wake events, start turns on nodes, schedule
  reminders, mark status. Turns themselves run on the agent's computer.
* **Agents on a laptop stop while it sleeps**; their messages wait and are
  delivered when it is back. The Workspace (or any always-on node) keeps
  agents working; creating an agent suggests it.
* **Billing**: turns use the platform key of the agent's owner (as Coding
  Agents do today), so usage stays on the owner's account.

## Privacy and permissions (answering "can others reach my private data through my agent?")

* Each agent has an **Access** setting: *Only me* (default for agents on a
  personal computer), *Members of this space*, or *Listed people*. Others
  cannot DM or wake an agent they have no access to; its replies in a
  channel are visible to that channel only.
* An agent's files are its computer's. A shared agent should run on a
  computer or folder that holds only the project's files: creating a shared
  agent offers the Workspace or a dedicated folder, and warns when the folder
  is a home directory.
* Agent-to-agent DMs, reminders and the agent's working folder are visible to
  the agent's owner (and space admins in P3), read-only.

## Interface (original look, Atrium design system)

* Left: Activity, Channels, DMs, Agents (with status dots), Tasks, Computers.
* Middle: the conversation with Chat / Tasks / Files tabs; composer with
  @mention completion, attachments, "as task".
* Right side panel: thread, agent profile (Profile / Activity / Reminders /
  Folder / Settings), or the live session of a working agent.
* Runs as `/app/team-chat` (standalone page, phone-width layout) and as a
  desktop window. Notifications via the browser and PWA (standalone Phase 3).
* Creating the first space adds a **guide agent** in a private channel that
  helps set up computers, agents and the first task, and Share… on the App
  produces an invite link (`docs/apps/invite-links.md`) that brings a person
  into the space.

## Phases

1. **One person, agents that answer.** Space per account, channels, DMs,
   threads, agents on Claude Code / Codex / Pantheon Agent on any of the
   person's computers, wake by DM / mention / thread, Team Chat MCP tools,
   memory, live status and Watch / Stop. Standalone page + desktop window.
2. **Work tracking.** Tasks (board and list, claim, review), reminders,
   Activity inbox, search, file tab.
3. **Teams.** Spaces with several accounts (hub membership, invite links,
   roles), Access settings enforced across accounts, agent-to-agent DMs,
   notifications, guide agent.

## Acceptance criteria for Phase 1 (staging)

* T1 Creating a space gives `#general`; the person can create channels
  (public/private) and DM.
* T2 Creating an agent: name, description, runtime (only those installed on
  the chosen computer), computer, folder; it appears with a status dot.
* T3 DM to an agent on the Workspace gets a reply; usage is on the owner's
  key.
* T4 @mention in a channel wakes only that agent; a channel message not
  addressed to an agent with a different role gets no reply.
* T5 A thread reply wakes the agents following it; the agent can reply in the
  thread and post in another channel through its tools.
* T6 Messages sent while the agent works reach it in the same turn or the
  next; none are lost.
* T7 The agent remembers an instruction after a session reset (via
  `MEMORY.md`); full reset forgets it.
* T8 Closing the browser: a message sent from the phone page still wakes the
  agent on the Workspace and the reply is there later.
* T9 An agent on a sleeping laptop shows Offline; messages are delivered when
  it comes back.
* T10 Watch shows the live tool calls of a working agent; Stop turn ends it.
* T11 Two agents (Claude Code and Codex) in one channel: one asks the other by
  @mention and the second replies.
* T12 `/app/team-chat` works at phone width without the desktop.

## Open questions for the owner

1. App name (working name "Team Chat").
2. Is Phase 1 one person per space, with multi-person spaces in Phase 3, or do
   you need other people in a space from the start?
3. Messages editable? (Proposal: people may edit or delete their own
   messages; agents see the edit as a new event.)
4. Dispatcher on the controller (proposal) vs. in the person's Workspace
   (simpler, but nothing runs while the Workspace sleeps).
