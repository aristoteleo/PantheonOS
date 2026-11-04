# Invite links: one link, guided to a working App (2026-11-04)

**Status (2026-11-04):** live on staging only. Hub `app-invites` API and
tables (migration `add_app_invites.sql`, run on the staging database), the
invite page, the owner's Invite people… dialog, `onboarding` for Coding
Agents, Agent Canvas and the Agent, and App pages that connect without the
Workspace for an App on the account's own computer. Verified on staging:
I1–I13 and I15–I18; I14 for Agent Canvas, while the Agent ends on its App
page, which until the Agent has a page of its own (standalone Apps phase 2)
says it works on the desktop. Not on prod.

**Status (2026-11-04, later):** the two leftover first-run frictions are
fixed on staging: fleet node `fleet-v0.5.1-staging.6` (controller
`latest-tag`, and Workspace image `nanguage/pantheon-agents@sha256:72c8e002…`)
answers Claude Code's first-run questions on a Workspace, and the invite
page's installer log renders the installer's output whole (frontend
`e00009e5`). Verified with a new account invited by acct3: Claude Code's
first terminal took a prompt at once; a conversation started with
`pantheon coding-agent run` replied and resumed in a terminal without a
question.

An owner sends one link. The person who opens it (the invitee) may have no
account, no Workspace, no node and no coding agent. The link page takes them
through every step until the App is open and usable on **their own** account,
then opens `/app/<id>`. It works for every App: built-ins (Coding Agents,
Agent Canvas, Agent, Terminal, ...) and packaged Apps.

This is different from hosting (`docs/apps/app-hosting.md`): a hosted `host`
link lends the owner's instance; an invite never reaches anything of the
owner's. It replaces the hosted `install` mode's "open the desktop" ending
with the guided page below; `install` links keep working and get the same page.

## Owner side

* App menu (desktop window and App page top bar) and Store "My apps":
  **Invite people…** for every App, next to Host… (Host stays for Apps with a
  `share` section or no backend).
* The dialog: optional note shown to the invitee, expiry (default 14 days),
  max sign-ups (default 10), trial credit per new account (default $5,
  0 allowed, capped by the hub setting `invite_trial_credit_max_usd`,
  staging 20), and for a packaged App the pinned release (as hosted `install`
  pins it today, including the link's own release when the Store lacks it).
* Result: the link `<hub web>/#/i/<id>` with Copy, and a list of invites with
  opened / signed-up / reached-the-App counts and Revoke.

## The invite page `/#/i/<id>`

A single page, no desktop, App icon and name at the top, the owner's note, and
a vertical checklist. Each step shows one of: done (with a one-line result),
in progress (with real progress text, never a bare spinner), needs you (one
primary button), or failed (plain-language reason and Retry). Steps are
detected, not remembered: reloading the page, closing it and opening the link
again later, or opening it on another device re-checks every step and resumes
at the first one not done. Nothing asks a question that has a sensible default.

The invitee never sees the desktop: no step opens `desktop.html` or a desktop
window, installs and setup run from this page, and the end is `/app/<id>`.

Steps, in order, shown only when the App needs them. Which steps exist is
decided from the App's `onboarding` declaration and the invitee's choices,
before any is run: an App that needs no Workspace (a `public` App, an App that
runs on a Fleet node, Coding Agents placed on the invitee's own computer)
never shows a Workspace step or its start-up progress, and never starts one.

1. **Account.** Signed out: "Create an account" (username, email, password,
   inline on the page) or "I already have an account" (sign in, password or
   OAuth popup). An account created through a live invite can be used at once;
   the verification email is still sent, and the account is marked
   `invited_by` (owner) and `invite_id`. Signed in already: shown as done
   ("Signed in as …", with "Not you?").
2. **Credit.** Only for an account created through this invite: the trial
   credit is added to the account's LLM budget once (idempotent per account
   and invite). Done shows the amount. A pre-existing account skips this step
   and keeps its own budget; if its budget is exhausted the step says so and
   points to Settings → Usage rather than failing later inside the App.
3. **Fleet / computer.** For an App that needs a node of the invitee's
   (`node_capability`, or a placement that runs on a node): when they have
   none, a guided setup: pick the OS (detected), copy the one-line install +
   `pantheon node up` command for their own fleet (created for them if
   missing, no fleet id to type), and the step turns done by itself when the
   node shows online, with "Waiting for your computer…" and help for the
   usual failures (command not found, firewall, asleep). Apps that can use
   either place offer **This Workspace** (when the App allows it) and **My own
   computer**; the choice decides whether step 4 exists.
4. **Workspace.** Only when the App or the choice in step 3 needs it: starts
   the invitee's Workspace and waits, with the real stages the desktop
   already reports (n/14 ready).
5. **Install.** Packaged Apps not installed at the pinned (or a newer)
   version: installs it (the existing `installRelease` path). Built-ins skip.
6. **App requirements.** From the App's `onboarding` declaration (below). For
   Coding Agents the place was chosen in step 3 (**This Workspace**
   preselected). Then "Install Claude Code" on the chosen node (skipped when
   that computer already has it): runs
   the agent's install task non-interactively and streams its output in a
   collapsed log; done when detection reports the agent installed. Sign-in is
   the platform key, created on first use, nothing to type.
7. **Open.** "Open <App>" goes to `/app/<id>` (plus the App's optional
   `onboarding.start` args, e.g. a starter project for Coding Agents) and
   records "reached the App" for the owner's counts. When every step is
   already done on load, the page opens the App directly.

A revoked, expired or full invite shows why, plus "Open <App> with my
account" when the visitor is signed in (a normal `/app/<id>` link).

## `onboarding` declaration

Per App, in the manifest (`"onboarding": {...}`) or in `registry.ts` for
built-ins:

```json
"onboarding": {
  "requires": ["workspace", {"node_capability": "coding_agents", "agent": "claude"}],
  "start": {"args": {}}
}
```

Step kinds: `workspace`, `install` (implied by a packaged App),
`node_capability` (a node of theirs with that capability, Workspace offered
first), `coding_agent` (an agent installed on the chosen node). Each kind has a
`detect()` and a `run()` in one module (`src/desktop/invite/steps.ts`), shared
with the App page so an App opened directly without its requirements shows
the same step instead of an empty state.

## Hub

Table `app_invites`: `id` (URL token, 128-bit), `owner_user_id`, `app`
(built-in id or `pkg:<id>`), `package_id`, `version`, `app_release` +
`app_bundle` (as `app_shares`), `note`, `trial_credit_usd`, `max_signups`,
`expires_at`, `revoked_at`, `created_at`; `app_invite_events` (`invite_id`,
`user_id` or anonymous session, `kind`: opened / signed_up / credited /
reached_app, `at`).

API: owner `POST/GET /api/app-invites`, `DELETE /api/app-invites/{id}`;
invitee `GET /api/app-invites/{id}` (public summary: App, owner name, note,
status), `POST /api/app-invites/{id}/register` (creates the account, counts
against `max_signups`, rate-limited per IP), `POST /api/app-invites/{id}/credit`
(login; idempotent), `POST /api/app-invites/{id}/event`. Migration in
`migrations/`, run on staging only.

## Acceptance criteria (staging)

* I1 Owner opens App menu → Invite people… on Coding Agents, gets a link,
  copies it; the invite list shows it with zero counts.
* I2 A fresh browser (no session) opens the link: the page shows the App,
  owner's name and note, and the checklist with Account as the current step.
* I3 Creating an account on the page signs in without leaving the page and
  without waiting for email; the account exists, `invited_by` is the owner.
* I4 The new account's LLM budget increases by exactly the trial credit, once:
  reloading or calling credit again does not add more.
* I5 Workspace step starts the Workspace and shows n/14 progress until ready;
  no raw error text appears during a cold start.
* I6 Coding Agents: "This Workspace" is preselected; Install Claude Code
  installs it on the Workspace with visible progress; detection then lists
  Claude Code as installed and signed in.
* I7 Open goes to `/app/coding-agents`; a first Claude Code conversation in
  the Workspace replies, and the usage is billed to the new account (its key),
  never the owner's.
* I8 Closing the tab mid-way (after Account, before Install) and opening the
  link again resumes at the first unfinished step; a second device signed in
  as the same account does the same.
* I9 An existing account opening the link: Account is done, Credit is
  skipped, remaining steps as above.
* I10 Packaged App (Cytoscape at the owner's local version): the invite page
  installs the pinned release into the invitee's Workspace and opens
  `/app/pkg:<id>`; works when the Store lacks that version.
* I11 A `public` App (Game of Life): no account needed, opens directly.
* I12 Revoked, expired and full invites show the reason; register on them
  answers 4xx; the owner's counts show opened / signed-up / reached-the-App.
* I13 Nothing of the owner's is reachable: the invitee's Fleet, nodes, App
  list and keys are their own (checked via API as the invitee).
* I14 Another built-in (Agent Canvas) and the Agent work through an invite
  with the same page and no App-specific code outside `onboarding`.
* I16 No step of any invite opens the desktop (`desktop.html` never loads;
  checked from the browser's navigation and network log).
* I17 An invite whose App needs no Workspace (Game of Life; Coding Agents with
  "My own computer") shows no Workspace step and does not start the
  invitee's Workspace (checked via the controller: it stays stopped).
* I18 An invitee with no fleet and no node choosing "My own computer": the
  page creates their fleet, shows the command for their OS, and turns done
  within seconds of the node coming online (a test node started from the
  shown command); the App then lists that node.
* I15 Existing hosted `install` links still work and end on the same guided
  page.

## As built (staging, 2026-11-04)

* The link `/#/i/<id>` forwards to `/invite/<id>`, the page itself (served
  as `app.html`, like `/app/<id>`). A hosted `install` link forwards to
  `/invite/share/<id>`: the same page, with sign-in instead of account
  creation and no credit. `/invite/app/<App id>` is the same steps for an App
  of one's own; an App page whose App lacks what its `onboarding` needs (no
  computer with the capability) links to it.
* The page and an App page for one of the account's own computers
  (`?node=<id>`) connect over the controller and the App bus without the
  Workspace (`workspace.withoutWorkspace`): no prewarm, no `GET
  /api/chatroom`, nothing asks the hub for a sandbox. Only the Workspace step
  (`startWorkspace`) starts it. An App page whose node turns out to be the
  Workspace starts it after all.
* Node services need the Agent deployment (its hosted Fleet App), so the
  page sets the deployment up when the account has none. Set up this way, its
  packaged Apps carry `placement.avoid: ["machine"]`: they wait for the
  Workspace instead of being installed on the invitee's own computer.
* "This Workspace" is preselected and confirmed with one click; a computer
  of theirs that is online, or a Workspace already in use, is detected as the
  choice (another device resumes there); the choice is also kept in the
  browser.
* Sign-in on the page is by password or OAuth popup. A packaged App is
  installed from the page into the Workspace (`manageApp('install')`), from
  the Store at the pinned version or from the invite's own release. A public
  App's invite opens a hosted `frontend` link of the owner's, signed out.
* The page opens the App by itself only when nothing had to run on load.
* Claude Code's first terminal opens on a session: no theme, trust or
  bypass-permissions question (docs/apps/coding-agents.md, First start).
* The installer log is the terminal's text as a screen shows it (cursor
  moves, carriage returns and erases followed, colours dropped), so
  Claude Code's installer, which redraws lines in place, reads whole.

## Out of scope

Payment, email invitations sent by the platform, prod.
