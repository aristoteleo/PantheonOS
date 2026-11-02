# Desktop state belongs to the user, not to a node

Status: design, 2026-11-02. Phases P0–P2 below; each ends with acceptance
criteria that are checked on staging before the next phase starts. P0 and P1
are live on staging; P2 is implemented and tested locally ("P2 as built").

## Problem

Opening the desktop waits for the Workspace (the sandbox node). Until it is up:

- the screen is at the browser's size, not the desktop size last used;
- no window comes back, including windows of Apps that run on other nodes
  (a Mac's Coding Agents or Terminal) and never needed the Workspace;
- "Right-click to create something here." shows in the middle of a desktop
  that already has App icons, because it only counts files.

The cause is where the state lives. Everything a desktop needs to draw itself
is a file on the Workspace:

| State | Where now |
|---|---|
| Open windows, geometry, spaces, stacking, nominal size | `apps/desktop/desktop_session.py`, record `<workspace>/.pantheon/desktop.json`, deltas on the Workspace's `desktop` stream |
| Dock, desktop Apps, icon positions, wallpaper, open-with, clock | `ui/src/desktop/settings.ts`, `<workspace>/.pantheon/atrium/desktop.json` (localStorage cache) |
| Appearance | `<workspace>/.pantheon/atrium/users/<uid>/appearance.json` |
| Installed Apps and their node placement | the Desktop App on the Workspace (`desktop_app_registry`, `app_placement.py`) |

PantheonOS is a distributed OS: the Workspace is one node among the user's
nodes, and may be stopped (idle reaper) while the others run. The always-on
part of a Fleet is the controller and its NATS JetStream. State that belongs to
the user goes there; state that belongs to one node stays on that node.

## Ownership

1. **User state → controller.** Desktop session (windows, spaces, nominal size,
   stacking), desktop settings, appearance, and (P2) the installed-App index
   with placement. Stored in JetStream KV on the controller's NATS, served by a
   controller-hosted service. No node is needed to draw the desktop.
2. **Window → its node.** A window record names the node its App runs on.
   On restore each window connects to its own node; only windows whose node is
   not up wait, each with its own placeholder.
3. **Node data stays on the node.** Files on the Workspace's disk, icon
   positions of files in a node's Desktop folder (keyed by that folder), Agent
   chats, App backends and their large state.
4. **Container-bound windows stay ephemeral** (`agent-view`, `xwindow`), as now.

## Storage and scale

One bucket for all Fleets, `DESKTOP` (not one per Fleet: 10⁵ users would be
10⁵ streams on one server). Keys:

```
<fid>.<uid>.meta            nominal_w/h, nominal_locked, spaces, top_z
<fid>.<uid>.win.<window id> one window record
<fid>.<uid>.settings        desktop settings (settings.ts DesktopSettings)
<fid>.<uid>.appearance      {preference}
<fid>.<uid>.apps            P2: installed-App index + placement
<fid>.<uid>.icon.<App id>   P2: an App icon too large for the index
```

`History: 1`. Writes are compare-and-set on the key's revision (a stale write
is retried on the fresh value, never blindly overwritten). Watching
`<fid>.<uid>.>` replaces the Workspace's seq/delta stream; a KV revision is the
ordering, so the "gap → resync" rule becomes "watch restarted → read all".

Size: a window is 0.5–2 KB, settings 2–10 KB; ~20–50 KB per user, ~5 GB at
10⁵ users against the controller's 50 GB file store. Limits enforced by the
service: a window record ≤ 64 KB (an App's large shared state belongs on its
node), ≤ 200 windows per user, all window records plus the meta key ≤ 448 KB
per user (so a whole desktop fits one 512 KiB dependency reply; at the
0.25–1.5 KB records measured on staging that is ~300 windows), settings
≤ 256 KB. Writes are at click rate
(a drag writes once, on release).

Durability: the staging controller runs one NATS server with one copy. The
service writes a snapshot of the bucket to `<state>/backups/desktop-<date>.json`
daily (keep 7), and the Workspace's json files are left in place (read-only
after import) for 30 days as a fallback. Replicated JetStream (R3) is a
production decision, not part of these phases.

## Access

The browser does not get raw KV permissions. The controller hosts a service,
`desktop-state`, the same way it hosts `fleet` and `allocator`
(`os/internal/hosted`), and is the only KV client. The browser reaches it over
the App bus it already uses (`appRpc`, `/bus-credential` → `fleet.<fid>.apps.>`)
and gets changes on a subject in that namespace; the Workspace's Desktop App
(the Agent's desktop tools) calls the same service as a dependency. Every call
is scoped to the caller's `<fid>.<uid>` by the controller, never by an argument.

## P0 — draw the last desktop at once (frontend only)

1. The empty-desktop hint shows only when the desktop has no items at all
   (no App icons and no files), and only once the file list has loaded.
2. The browser caches the last session document (nominal size, spaces,
   windows) beside the settings cache, per signed-in user and Fleet. On load the
   desktop takes the cached nominal size and paints the cached windows before
   any node answers; the authoritative session replaces it when it arrives,
   without windows flickering out and back.
3. A restored window whose App runs on a node other than the Workspace (Coding
   Agents, Terminal, Files on a Mac node…) opens and works while the Workspace
   is still starting. A window whose node is not up shows a placeholder in the
   window ("Waiting for <node>…") instead of an error, and fills in when the
   node comes up. Windows the user opens in this state are kept: when the
   Workspace session arrives, they are sent to it as intents, not dropped.

Acceptance (staging, test account, a Mac node up, Workspace stopped then
opened):

- A1 Within 2 s of the shell painting, the surface has the last-used nominal
  size and every previously open window is in place at its geometry.
- A2 A Coding Agents (or Terminal) window bound to the Mac node is usable
  (lists sessions / takes input) before the Workspace is ready.
- A3 A Workspace-bound window shows "Waiting for Workspace…", then its content
  once ready, with no reload.
- A4 No "Right-click to create something here." while App icons are on the
  desktop; it shows on a desktop emptied of Apps and files.
- A5 After the Workspace is ready the window set equals the server's session
  (no duplicates, no lost windows, including one opened during start).
- A6 vitest for the touched areas and `vue-tsc` pass; new tests fail without
  the change.

## P1 — the session, settings and appearance on the controller

1. `desktop-state` hosted service in the controller (Go) over the `DESKTOP`
   bucket: `get` (everything for the caller), `intent` (the same intent kinds
   `desktop_session.py` applies today: open, close, move, resize, raise, set,
   spaces, nominal, bind_browser), `settings_put`, `appearance_put`, and a
   change subject. Ids minted by the service. Limits and snapshots as above.
2. The UI's `desktopSession.ts`, `settings.ts` and `appearanceSync.ts` use the
   service instead of Workspace files and the Workspace stream. The P0 cache
   stays as the first paint.
3. The Workspace's Desktop App applies desktop intents by calling the service
   (the Agent's desktop tools and other viewports see one session).
4. Import: when a user's keys are absent, the service is given the Workspace's
   `.pantheon/desktop.json`, `.pantheon/atrium/desktop.json` and appearance
   file once (by the Desktop App when it starts, or by the UI when it first
   reaches the Workspace), and stores them; later the KV wins and the files are
   no longer written.
5. Docs: this file updated with what was built; `docs/os/pantheon-fleet.md`
   lists the bucket.

Acceptance:

- B1 With the Workspace stopped (and P0's browser cache cleared), the desktop
  opens with the last nominal size, windows, dock, wallpaper and appearance.
- B2 Two browser tabs: opening, moving and closing a window in one shows in
  the other within 1 s; concurrent moves of two different windows both stick.
- B3 An Agent desktop tool that opens a window shows it in an open tab.
- B4 A user whose state is only in the Workspace files sees the same desktop
  after the switch (import), and its keys exist in the bucket afterwards.
- B5 Another user's / Fleet's keys can be neither read nor written (test).
- B6 A window record over 64 KB is refused with an error the UI shows.
- B7 A snapshot file is written; restoring it into an empty bucket brings the
  desktop back (test).
- B8 Go tests (`go test ./...` in `os/` for the touched packages, with -race for
  the service), Python tests for the Desktop App, vitest + vue-tsc pass.

### P1 backend as built (2026-11-02)

Status: implemented and tested locally (Go, Python); not deployed. The UI half
(item 2) is separate.

**Service.** `os/internal/hosted/desktopstate.go` (tools, bucket, revision
checks), `desktop_session.go` (the intent reducer, a rule-for-rule port of
`apps/desktop/desktop_session.py`, with its tests ported to
`desktop_session_test.go`) and `desktopstate_backup.go` (snapshots). The
contract is `os/internal/hosted/manifests/desktop-state.json` (App id and
interface `desktop-state`, version 1.0.0); unlike the other hosted services
it has no Python package, so no release drift check applies. The controller
registers it with `--deployments` and connects it to NATS with its own
credential (`auth.MintDesktopState`, re-minted on every reconnect): the only
credential that names `KV_DESKTOP`; Fleet, node and App-namespace credentials
cannot open the bucket.

**Keys.** `<fid>.<uid>.meta` holds `{seq, nominal_w, nominal_h,
nominal_locked, spaces, top_z, next_id, boot}`; `<fid>.<uid>.win.<id>` one
window record; `.settings` the settings object as the UI stores it;
`.appearance` `{preference}`. An intent applies the reducer to the desktop,
writes the changed windows and then the meta key, each with the revision it
was computed from; a conflict re-reads the desktop and re-applies the intent
(up to 8 times). One process serializes each desktop's calls.

The service is the bucket's only writer, so it keeps each desktop it used in
memory with its revisions and reads it from the bucket (one filtered watch, an
ephemeral JetStream consumer) only on a miss: once per desktop, not per click.
A cached desktop is dropped after any write error or conflict, after a refused
intent (the reducer may have touched it), after an import or a restore, when
untouched for 30 minutes, and beyond 4,096 cached desktops (least recently
used first); the next call reads it again. A second writer would therefore be
seen only when one of its changes conflicts with this service's next write.

**Caller scoping.** The Fleet is the hosted binding the call was admitted at
(`Call.Fleet`, set by `Host.invoke` from the binding the gateway resolved;
`scopeOf` in `desktopstate.go`); no tool takes a Fleet or user argument, and
unknown arguments are refused. The user segment is `owner`: a Fleet belongs to
one Hub user (`fid = f_ + sha256(user id)[:16]`, which the controller cannot
invert), and the only callers a hosted service admits act for that owner — the
owner's browser (an App connection only the owner gets) and Apps of the owner's
deployment through dependency grants; visitors of shared Apps never reach a
hosted service. Keys are therefore `<fid>.owner.…`: **the user part is
`owner`, because a Fleet has one user.** If a Fleet ever has several, the Hub
would pass the authenticated user with the App connection and dependency
grant, `Call` would carry it, and it would replace `owner` here.

**Deployment.** The cloud profile (`release_profile.compile_profile`) adds the
App `desktop-state` (`{"hosted": "desktop-state", "scope": "desktop-state"}`)
and binds the Desktop to it as `desktop_state` (methods `get`, `intent`,
`import`). The Desktop package declares it an **optional** dependency
(`"binding": "optional"`, credential `desktop_state` not required): bound when
the deployment binds it, absent otherwise. The single-machine profile does not
add it. **Rollout order: the controller ships before any release whose profile
binds desktop-state.** A controller without the service reports the hosted App
`blocked`, and a Desktop bound to it would wait for it. Existing owners get the
binding when their deployment is filled from such a profile.

**Desktop App.** `managed._desktop_state` builds a dependency client when the
runtime configuration carries the `desktop_state` credential; that is the
whole detection. With it, `DesktopSessionBinding` uses `RemoteDesktopSession`
(`desktop_session.py`): `apply` → `intent`, `current` → `get`, same surface as
the file store, so the Agent's desktop tools and every viewport share one
session. Before its first call in a process it sends `import` with
`<state>/.pantheon/desktop.json` (the Desktop's own record), the user
workspace's `.pantheon/atrium/desktop.json` and newest
`.pantheon/atrium/users/*/appearance.json` and its container `boot` token, and
logs one line saying which parts it sent and the service stored. The user
workspace is the Desktop's configured data root (`/workspace/default_workspace`,
the Files App's cwd), not the Desktop App's own workspace directory, which is
where 0.9.101 looked and so imported no settings; it then writes none of these files (they stay
on disk, unread, as the fallback copy). A failed call fails the tool rather
than falling back to files. It still publishes its deltas on the Workspace's
`desktop` stream for viewports that listen there. Without the credential (a
single machine, an older profile) the file store is used as before.

**Snapshots.** Hourly, the service writes the day's snapshot when it is
missing: `<state-dir>/backups/desktop-YYYY-MM-DD.json` (0600, atomic rename;
`{"bucket": "DESKTOP", "taken", "entries": [{"key", "value"}, …]}`), keeps the
newest 7, and clears delete markers of closed windows. **Restore is a tested
function without an operator command yet:** `DesktopState.Restore(ctx, path)`
puts a snapshot's entries back, keeping keys that exist, and drops the cache
(`TestSnapshotRestoresIntoAnEmptyBucket` restores into an empty bucket).

#### Tool contract

Over the App bus as every hosted App: `appRpc('desktop-state', method, args)`
→ `{success, result}`, or `{success: false, error}` with the reason as text.

| Method | Args | Result |
|---|---|---|
| `get` | — | `{session: {seq, nominal_w, nominal_h, nominal_locked, spaces, top_z, windows: {<id>: window}}, settings: object \| null, settings_revision: int (0: none), appearance: {preference} \| null, subject: string, initialized: bool}` |
| `intent` | `kind`, `args` | `{seq, ops, …result}` — the Desktop App's `desktop_intent` reply without `success`/`host` |
| `settings_put` | `settings` (whole object) **or** `patch` (top-level keys; `null` removes one), optional `revision` | `{revision, settings}`; with a stale `revision`: `{conflict: true, revision, settings}` (current values, nothing written) |
| `appearance_put` | `preference`: `light` \| `dark` \| `system` | `{preference}` |
| `import` | optional `session` (a `.pantheon/desktop.json` record, v1 or v2), `settings` (object), `appearance` (`{preference}`), `boot` | `{imported: {session, settings, appearance}, dropped: [window id]}` |

Intent kinds and their results are those of `desktop_session.py`: `open`
(`app_id`, optional `window_id` to reuse, `args`, `title`, `path`, `x`, `y`,
`width`, `height`, `space`, `opened_by`) → `{window_id, reused}`; `close`
(`window_id`) → `{closed}`; `move` (`x`, `y`), `resize` (`width`, `height`),
`raise`, `set` (`patch` of `title`, `minimized`, `maximized`, `fullscreen`,
`status`, `path`, `space`, `args`) → `{}`; `spaces` (`count`) → `{spaces}`;
`nominal` (`w`, `h`, `mode`: `propose` | `set` | `auto`) → `{nominal_w,
nominal_h, nominal_locked}`; `bind_browser` (`window_id`, `expected_page_id`,
`page_id`, `operation_id`, `url`) → `{binding}`. Ops: `{op: "upsert", id,
window, seq}`, `{op: "remove", id, seq}`, `{op: "meta", seq, …changed meta
fields}`. Ids are minted by the service (`win-<n>`).

Refusals (`success: false`): `no window '<id>'`, `unknown desktop intent
'<kind>'`, `window <id> is <n> KB; a desktop window record is limited to 64 KB
(keep an App's large shared state on its node)`, `a desktop holds at most 200
windows; close one first`, `this desktop's windows would take <n> KB; a desktop
is limited to 448 KB (close a window, or keep an App's large shared state on
its node)` (only for a change that grows the desktop; moving or closing is
always allowed), `desktop settings are <n> KB; the limit is 256 KB`.

`import` stores each part only while the caller has none: a session when there
is no meta key, settings and appearance when their keys are absent. Windows the
limits refuse (per window, count, total; the topmost are kept) are left out and
listed in `dropped`. With `boot`, a desktop
last claimed by another Workspace container loses its `agent-view` and
`xwindow` windows (one delta) and is marked with this `boot`.

**Changes.** The service publishes on `get().subject`,
`fleet.<fid>.apps.desktop-state.owner`, inside the App namespace the browser's
`/bus-credential` subscribes to. Plain JSON, no stream envelope:

- `{"type": "desktop.delta", "seq", "ops"}` after an intent (and after an
  import's `boot` drop);
- `{"type": "desktop.settings", "revision", "settings"}`;
- `{"type": "desktop.appearance", "preference"}`;
- `{"type": "desktop.reset"}` after an import stored something: read with `get`;
- P2: `{"type": "desktop.apps", "revision", "apps"}` after the App index
  changed, and `{"type": "desktop.icon", "app_id", "hash"}` after a stored
  icon did.

A viewport applies a delta whose `seq` follows its own, ignores one it has, and
calls `get` on a gap, on (re)subscribing, and on `desktop.reset`.

**Which path.** The UI uses the service when the Agent deployment has an App
named `desktop-state` (its binding resolves) and the Workspace files otherwise
— the same condition the Desktop App sees as its credential.

**Open.** Restore has no operator command yet; the Desktop App's calls are
synchronous inside its async tools, as the file store's were.

## P2 — the installed-App index and placement on the controller

1. The list of installed Apps (id, version, launcher metadata needed to draw
   icons and the dock) and each App's placement (node, policy) are kept by the
   controller (the deployment it already reconciles, plus the `apps` key for
   what the desktop needs to draw), not read from the Workspace's Desktop App.
2. The launcher, dock and desktop icons, and "which node does this window
   connect to", resolve without the Workspace. Installing / uninstalling /
   moving an App still runs through the existing paths and updates the index.
3. Apps on a node that is down are drawn (icon in place) and open to a
   "Waiting for <node>…" placeholder.

Acceptance:

- C1 With the Workspace stopped, the launcher and dock list every installed
  App (including ones from the Store) with correct icons, and an App placed on
  the Mac opens and works.
- C2 Moving an App to another node (existing UI) updates the index; a reload
  with the Workspace stopped connects to the new node.
- C3 Installing an App from the Store while the Workspace is up shows it in a
  second tab's launcher without reload.
- C4 Tests as in B8.

### P2 as built (2026-11-02)

Status: implemented and tested locally (Go, Python, UI); not deployed.

**What moved, and what did not.** The Workspace's Desktop App still owns the
App library (Store installs, forks, versions, placement records) and still
stages and installs packages on nodes: those need its disk. What moved is a
copy of what a desktop needs to *draw and route*: the index, written by the
Desktop App to the controller, read by browsers from there. The controller's
deployment (`os/internal/deployments`) is not the source: it lists the Apps
the Agent deployment runs, not the user's App library, so the index is the
`apps` key, as the design above planned.

**Service** (`os/internal/hosted/desktopstate_apps.go`; manifest 1.1.0, three
new tools). The key `<fid>.owner.apps` holds the index as a JSON list,
≤ 128 KB, ≤ 500 entries. The service reads only two fields of an entry: `id`
(one entry per App, `[A-Za-z0-9][A-Za-z0-9_-]{0,79}`) and `icon_hash`; the rest
is the Desktop App's. A small icon rides inline in its entry (`icon`, a data
URI); a larger one is stored as `<fid>.owner.icon.<App id>` (≤ 256 KB, a JSON
string `data:image/…`) and named in the entry by `icon_hash` (the first 8
bytes of its SHA-256, hex). An icon no entry names is refused, and one whose
entry is gone is deleted on the next `apps_put`, so the bucket holds no unused
icons. Icons are not part of `get`: a desktop reads one only to draw it.

| Method | Args | Result |
|---|---|---|
| `get` | — | as P1, plus `apps: [entry] \| null` and `apps_revision: int` (0: none) |
| `apps_put` | `apps` (the whole list), optional `revision` | `{revision, missing_icons: [App id]}`; an index equal to the stored one is not written (same revision, no event); with a stale `revision`: `{conflict: true, revision}` |
| `apps_icon_put` | `app_id`, `icon` | `{hash}`; refused unless the index names this icon (`icon_hash`) for `app_id` |
| `apps_icon` | `app_id` | `{icon, hash}`, or `{icon: null}` |

Refusals: `apps must be a list of App entries`, `every App entry needs an id
(letters, digits, - and _)`, `App <id> is listed twice`, `App <id>:
icon_hash is 16 hex digits`, `the App index lists <n> Apps; the limit is
500`, `the App index is <n> KB; the limit is 128 KB (store large icons with
apps_icon_put)`, `an icon is a data:image/ URI`, `the icon of <id> is <n> KB;
the limit is 256 KB`, `the App index names no such icon for <id>; put the
index first`.

**Entry** (written by `apps/desktop/app_index.py`, one per App with a
frontend, as `desktop_app_registry` lists them):

```
{id, name, version,
 kind: "builtin" | "store" | "packaged",   builtin scope / Store install / workspace, user or fork
 scope, dir,                               where the package is on the Workspace
 manifest,                                 without provides, skills, readme, notes, dependencies,
                                           changelog, config (only launcher fields if still too big)
 icon | icon_hash,                         ≤ 4 KB inline; ≤ 250 KB stored; larger: left out (glyph)
 placement: {node_id | null, policy},      the App's placement record
 installed: {<node id>: {digest, component}}}   the package this Workspace last installed per node
```

`installed` is new in the placement record (`app_placement.py`): `install()`
records the digest and component it installed on a node, when they changed.
It is what lets a desktop start the App on its node without the Workspace.

**Desktop App.** `AppIndexPublisher` builds the index (registry scan +
placement records) and writes it with `apps_put` when it differs from the last
one this process wrote, then sends the icons the reply lists as missing. It
runs in the background, one write at a time (a change during a write runs
another after it). Triggers: Desktop App start (`managed.create_service`),
every `desktop_app_registry` call (each desktop start reads it), any write of
a placement record (`set`, `policy`, an install that changed `installed`),
`desktop_store_manage` actions that change the library (install, remove,
restore, fork, copy, purge, tag, default, published, fork_download, preview,
initialize), `desktop_app_store` fork and pull, `desktop_app_develop` writes
and `desktop_sync_apps`. A failed write is logged and retried on the next
trigger. The binding grants `apps_put` and `apps_icon_put`
(`release_profile.DESKTOP_STATE_METHODS`); a deployment bound before P2 does
not, the gateway answers 403, and the publisher stops asking (one log line).

**UI** (`ui/src/desktop/network/appIndex.ts`). The index comes with `get`
(applied whether or not the session is initialized) and `desktop.apps`; it is
cached per user and Fleet in localStorage beside the session cache and
registered on mount (`Desktop.vue`) for the first paint. While this
connection has read the Workspace's registry (`stores/workspace.ts`
`loadPackagedApps`), that stays the source — full manifests, App files — and
a new index revision re-reads it (so an install in another tab appears
here); if it cannot be read (the Workspace stopped), the index is registered
instead. An empty Workspace registry (still provisioning) does not replace a
non-empty index. Large icons are read with `apps_icon`, cached by hash.

Routing (`shell/windowNode.ts`, `apps/packaged/PackagedApp.vue`,
`network/managedApp.ts`): a packaged App is served by the Workspace *or by
the node its backend runs on* — the node serves the installed package's
files and manifest. A packaged window waits for the Workspace only when it
has no such node: neither an instance binding on a non-Workspace node nor an
index placement on a node with an `installed` package. Otherwise it waits
for that node ("Waiting for <node>…") and opens when it is up:
`startPlacedInstance` reads the node's lifecycle through the Fleet App, starts
the installed package's instance if it is stopped, and the window binds to it
as a restored window does (manifest from the node, frontend from the node's
`/package/`). The resident policy is read from the index too
(`keepResidentAlive`).

**Limits of this phase.** An App placed on a node it was never installed on
(moved, not opened there since) needs the Workspace to stage it: its window
says it is no longer installed there and opens once the Workspace runs. The
package started without the Workspace is the one last installed on that node,
which may be older than the App's current launch default. Apps placed
automatically run on the Workspace and wait for it. File opens into a
packaged App still mint Workspace data URLs.

**Rollout order.** Controller first (the new methods; an old controller
refuses `apps_put` with "unknown method", which the publisher logs and retries
on the next trigger — harmless). Then a release whose profile grants the new
methods and whose Desktop App publishes the index; existing owners get the
grant only when their deployment's Desktop binding is refilled from that
profile — until then their Desktop App is refused (403) and stops asking.
The UI can ship in either order: without `apps` in `get` it draws from the
Workspace as before.

Acceptance as built: C1 — launcher, dock and icons list every App in the index
(including Store installs) with their icons, from the cache at once and from
`get` with the Workspace stopped; an App placed on the Mac starts and opens
there. C2 — moving an App writes its placement record, which republishes the
index; after a reload with the Workspace stopped its windows go to the new
node, provided it was installed there once. C3 — a Store install republishes
the index; the second tab receives `desktop.apps` and re-reads its registry.
C4 — Go (`-race`), Python and vitest tests below.

