# Apps follow their Store version

Status: design, 2026-11-11. Staging only. Acceptance criteria U1–U14 at the end
are checked on staging before this is considered done.

Most Apps should upgrade by themselves to the version their Store repository
publishes, unless the user chose a version. This document makes that concrete
for each kind of App, says where the choice is stored, what triggers an
update, when a running App is restarted, and what happens when an update fails.

## 1. The rule

Every installed App has an **update policy**:

- **Follow Store** (the default). When the Store publishes a new release of the
  App's repository, the user's install takes it without being asked. The next
  launch runs it. A running backend moves to it when it is idle, never in the
  middle of work.
- **Pinned** to a version the user chose. It never upgrades by itself until the
  user sets it back to Follow Store.

Independently of the policy, an App the user **modified** (their own commits
on a development branch, or uncommitted files) is never changed automatically.
It shows *Update available*; the user merges the new release when they want
(Develop/Terminal, as today).

Only published Store releases are followed: a release is an immutable `v<x>`
tag bound to one commit by the Store's release API (store-app-management.md),
or, for the System Apps, the release set the Hub pins. Unpublished commits of
a repository, pull requests under review and other branches are never taken.

The previous version is always kept with its data. A new version that does
not come up is rolled back automatically, the user is told, and that release
is not tried again for that user (no retry loop). A later release is tried as
usual.

## 2. Which Apps are which

| Class | Apps | Where they run | How versioned today | Who updates them |
|---|---|---|---|---|
| **Store Apps** | Discover Apps in the user's library: `cytoscape gosling igv imagej molstar msa phylotree qupath rdkit spatial3d vitessce viv volume3d` (`pantheonos.appkit.distribution.STORE_APPS`) and any community/official repository the user installed | Frontend in the desktop; a backend, if any, as a Fleet App instance on a node of the user's Fleet (`app_placement.ensure`), one instance per revision | One local Git repository per lineage on the Workspace volume (`<user>/.pantheon/app-store`, install record `app-store/<key>.json`). Branch `official` tracks the Store repository's `main`; **Pull update** advances it by hand (`app_upstream.sync_upstream` → `local_repository.update_official`). Launch default: `app-store/defaults/<app>.json`; mode `latest` follows a branch head, otherwise a pinned commit (`app_versions.launch_default`) | The **Desktop App** (this design: its Store updater, §4) |
| **System Apps** | The deployment's Apps: `agent`, `desktop`, `files`, `shell`, `notebook`, `web`, `team-chat` (controller-hosted), `desktop-state` (hosted), `evolution`, model access, … (the `general-team` deployment) | One Fleet deployment per owner, held by the Fleet controller, converged by its reconciler | One **release set** for all of them (`spec.release = {url, sha256}`), published to the Store as the `pantheon-general-team` release package. The Hub pins the recommended one (`TOPOLOGY_AGENT_RELEASE_URL/_SHA256`). An owner moves to another with `POST /api/fleet/apps/agent-setup {release}`; an admin with `POST /api/admin/agent-setup/rollout` (by hand) | The **Hub** (this design: its follower, §5) |
| Builtin shell components | Desktop `ui:` components, Settings, Store, Files viewers inside the Desktop bundle | In the browser, from the desktop frontend | Ship with the frontend and the Desktop App | Not Apps with their own version: they update with the frontend deployment and the Desktop App's release. Out of scope |

An owner can run their **own build** of a System App (`spec.apps.<name>.digest`,
*Run as my Agent*, agent-app-repository.md §4). Such an App is **modified**:
setting the deployment up again would replace that build with the release
set's package, so the follower never does it (§5.3).

## 3. Data model

### 3.1 Store Apps: the install record

The policy lives with the install, in the Desktop's install record on the
Workspace volume (the same file as `upstream`, `branch_model`, …), field
`auto_update`:

```json
"auto_update": {
  "policy": "follow",                 // or "pinned"
  "failed": {"<commit>": {"version": "1.3.0", "reason": "…", "at": "…"}},
  "previous": {"commit": "<commit>", "version": "1.2.0"},   // kept for rollback
  "applied": {"commit": "<commit>", "version": "1.3.0", "at": "…"}
}
```

`policy` decides; the launch default is kept consistent with it:

- *Follow Store* writes the default `{mode: latest, branch: official}` of that
  repository: future launches run the head of `official`, which the updater
  advances.
- *Pin this version* writes the default `{commit, version}` (no `latest` mode)
  and `policy: pinned`.

**Migration** (no record has `auto_update` before this change; read lazily,
nothing is rewritten until the user or the updater acts):

- an explicit launch default of this repository pinned to a commit (an earlier
  *Set default* of a version) → **Pinned** to that commit: the user already
  chose a version;
- otherwise → **Follow Store**;
- either way, an install with commits of its own or uncommitted files is
  **modified** (§4.2).

Notices for the user (an update applied, an update rolled back) are kept in
`app-store/update-notices.json`, at most 50, until the desktop acknowledges
them.

### 3.2 System Apps: the Hub user record

The owner's choice is per deployment (one release set for all System Apps),
in the Hub database, `users.metadata.system_apps_update` (a JSON column that
exists already; no schema migration):

```json
{
  "policy": "follow",                 // or "pinned"
  "failed": {"<release sha256>": {"tag": "agent-app-v0.9.124", "reason": "…", "at": 1731300000}},
  "applying": {"release": {...}, "previous": {...}, "since": 1731300000, "revision": 42},
  "deferred": {"release": {...}, "reason": "a chat is running", "since": 1731300000},
  "notice": {"kind": "updated|rolled_back", "tag": "…", "at": 1731300000, "acknowledged": false}
}
```

Missing record or missing `policy` → **Follow Store** (existing owners follow).
Choosing a release other than the Hub's pin through the owner's
`POST /api/fleet/apps/agent-setup` (Store → Versions) sets `pinned`; *Follow
Store* sets `follow` (and the follower then moves the owner to the pin).

The deployment spec is not used: the controller decodes stored deployments
strictly, so a new spec field would make the records unreadable to an older
controller after a controller rollback.

## 4. Store Apps: the Desktop's updater

### 4.1 Trigger

The Desktop App runs a background task while it runs (started with the
service, cancelled in `cleanup`): first pass 60 s after start, then every
10 minutes. A pass is also run on demand (`desktop_store_updates check`, the
Store's Refresh).

One pass:

1. Read the library (no Git status for every App: install records only), keep
   installs whose record names a Store repository (`upstream.id`, or the
   publication of an `origin: store` install).
2. Ask the Store for those repositories' heads in batches of 100
   (`GET /api/store/apps/heads`), with the Desktop's Store binding. The head
   is the commit of the newest published release.
3. For each install whose current `official` commit differs from the head:
   skip if Pinned, if the head is in `failed`, or if modified (§4.2; recorded
   as *Update available*). Otherwise fetch and fast-forward `official` to the
   head (the existing `sync_upstream(pull=True, expected_commit=head)` path
   under the library lock), check the new version (§4.3), and record
   `previous`/`applied`.

The next launch resolves the default (branch `official`, mode `latest`) and
runs the new commit. Windows already open keep their version (a launch is
pinned by commit, store-app-management.md).

### 4.2 Modified

An install is modified when any of:

- a local branch other than `official` has commits not on `official`
  (`git rev-list --branches --not refs/heads/official`), or the launch default
  names another branch;
- the checked-out working tree has uncommitted changes;
- for an older non-branch-model install: commits beyond the installed or last
  pulled commit, or a branch other than `main`.

A modified install is not fetched into or merged. Its Store status is
*Update available: v1.3.0 — you changed this App; merge the update in Develop
when you want it*, with the existing Pull update / merge actions.

### 4.3 Running backends and readiness

An App whose new version cannot become a launchable snapshot (invalid or
foreign manifest, an archive that does not unpack, a frontend or backend
entry file the release does not contain) fails readiness: `official` is moved back to `previous` (the earlier commit is
kept under `refs/pantheon/official-before/<commit>` as well), the release is
recorded in `failed`, a notice is written, and the next launch runs the
previous version.

A backend of this App that runs on a Fleet node as a long-lived (resident or
kept-alive) instance of the previous version moves to the new version when
idle: no open window lease and no call in flight, as the node's usage counter
reports (`usage.windows == 0 && usage.calls == 0`). A busy instance is left
alone and looked at again on the next pass. Moving it is: start the new
revision on the same node (`placement.ensure`), wait until it is ready, then
stop the old instance. If the new one does not become ready within 3 minutes,
it is stopped, the old instance keeps running (it was never stopped),
`official` is moved back, the release is marked failed and the user is told.

Instances started only for an open window are not restarted: they end with
their window (the node's idle stop), and the next window runs the new version.

### 4.4 UI

- Store → My apps → an App, and its Versions: **Updates: Follow Store / Pinned
  to vX** with *Follow Store* and *Pin this version* (on a version). The status
  line says one of: *Up to date (v1.2.0)*, *Updating to v1.3.0…*, *Updated to
  v1.3.0*, *Update available: v1.3.0 (you changed this App)*, *Pinned to
  v1.2.0; v1.3.0 is available*, *v1.3.0 did not start; kept v1.2.0*.
- A notice becomes a desktop toast once (*Cytoscape updated to v1.3.0*, or
  *Cytoscape v1.3.0 failed to start; it stays on v1.2.0*), live through the
  desktop broadcast topic `store.updates`, and on the next desktop load for
  notices not yet acknowledged.
- The Agent's `pantheonos_manage_app set_version` keeps working; setting a
  version pins, `latest`/`branch:official` follows.

## 5. System Apps: the Hub's follower

### 5.1 Trigger

The Hub runs one follower task (enabled by `SYSTEM_APPS_FOLLOW=1`, set on
staging; off by default so another environment opts in explicitly). It wakes
every 2 minutes. It needs no "pin changed" event: it compares each owner with
the current pin, so changing the Hub's `TOPOLOGY_AGENT_RELEASE_*` (which
restarts the Hub) is picked up on its first pass. Several Hub replicas are
safe: the controller's revision check rejects a second write, and each pass
holds a Postgres advisory lock when one is available.

One pass walks the users that have an Agent deployment (pages of 500, at most
20,000 users, 4 at a time), and for each:

1. Policy `pinned` → skip.
2. Release equals the pin → nothing to do (clear `deferred`).
3. The pin's SHA-256 is in `failed` → skip (no loop).
4. Any App runs the owner's own build (`digest`) → modified: skip; the Store
   shows *Update available*.
5. An App the owner runs is starting, installing or failing → defer (the
   deployment is in flux). Apps that are ready, and Apps that wait for their
   computer to come back (a paused Workspace, a released brain Pod: state
   `waiting`, "waiting for node …" / "no online node …"), are settled: nothing
   of an away App runs, so changing its release interrupts nothing. The
   update never wakes anything; the owner's computers start on the new release
   when they return, and the next launch is already the new version.
6. The Agent is ready and busy (§5.2) → record `deferred` with the reason and
   wait. An Agent that is away runs no chat.
7. Otherwise record `applying` (the release it runs now as `previous`) and run
   the owner's own setup path (`FleetControl.setup_agent(user, pin)`), keeping
   the intent of Apps the owner stopped.

### 5.2 Idle

New controller route (the Hub calls it with its service token, like every
deployment route):

```
GET /deployments/{name}/activity
→ {"apps": {"agent": {"busy": false, "running_chats": 0}}, "unknown": []}
```

For the deployment's `agent` App the controller invokes the Agent App's own
`list_running_chats` RPC through its node (the node's ordinary `invoke`, 4 s).
`busy` means a chat or turn is running. An App that cannot be asked (no ready
instance, a node that does not answer) is listed in `unknown`; the follower
treats unknown as busy. The node's window/call usage counter is not used for
the Agent: an open Agent window keeps a call open, which is not work.

### 5.3 Verify and roll back

On every pass an owner in `applying` is checked:

- every App ready and `observed_revision == revision` → done: notice
  *updated*, `applying` cleared;
- an App `failed`, `blocked`, `backoff` or `attention` on two passes in a row,
  or not ready 15 minutes after the change (time spent waiting for a stopped
  Workspace does not count) → **roll back**: `setup_agent(user, previous)`,
  which resumes each App on the data that release last had (a revision that ran
  before resumes its own data; the new release's data is retained separately),
  record the pin in `failed`, notice *rolled back*.

The user is told by the desktop: the Agent setup card / a toast reads the
notice from `GET /api/fleet/apps/agent-setup/update` and acknowledges it.

### 5.4 Owner API and UI

```
GET  /api/fleet/apps/agent-setup/update   → {policy, pinned (the Hub's pin), running, state, deferred, failed, notice}
PUT  /api/fleet/apps/agent-setup/update   {policy: "follow"|"pinned"}
POST /api/fleet/apps/agent-setup/update/ack
```

`state` is one of `up_to_date`, `pending` (the follower will apply it),
`deferred` (waits for idle / for the Workspace), `applying`, `pinned`,
`modified`, `failed` (the pin failed and was rolled back).

Store → the startup Apps → Versions: **Updates: Follow Store / Pinned**, the
state line, and the existing release picker. Running another release from the
picker pins; *Follow Store* returns to the recommended release.

The admin rollout (`POST /api/admin/agent-setup/rollout`) skips Pinned owners
unless their `user_ids` are named explicitly; it otherwise stays as it is.

`POST /api/admin/system-apps-follow/pass {user_ids?, release?}` runs one pass
now. With `user_ids` and `release` it moves only those owners toward that
release instead of the pin, through the same apply/verify/rollback path: an
operator checks one release on a test account without changing the pin.

## 6. Out of scope

- Production. The follower is enabled on staging only.
- Automatic merging into the user's own branches.
- Store Apps' frontends already open: they keep their version until reopened.
- Desktop `ui:` components and the desktop frontend: they ship with the
  frontend deployment.

## 7. Acceptance criteria

Each is checked on staging (acct4), with evidence recorded in the builder's
notes.

- **U1** A new Store install has policy Follow Store; an existing install
  without an explicit pinned default is Follow Store; one with an explicit
  commit default is Pinned to it; one with its own commits or dirty files is
  reported modified. No existing record is rewritten by reading it.
- **U2** A new release published to a Store App the user follows reaches the
  install within 15 minutes while the Workspace runs (or within 2 minutes of
  the Desktop App starting), with no user action; the next launch resolves to
  the new commit.
- **U3** A running long-lived backend of a followed App that is idle moves to
  the new version; one that is busy (a call in flight / a window open) keeps
  running the old version until it is idle.
- **U4** A Pinned install is unchanged by a new release: same `official`
  commit, same launch revision; the Store shows the newer version as available.
- **U5** A modified install is unchanged: no fetch into or merge; the Store
  shows Update available.
- **U6** Setting Follow Store on a pinned App moves it to the newest release on
  the next pass (or Refresh); Pin this version keeps it there.
- **U7** A new Store App version that fails readiness is rolled back
  automatically (launch default back on the previous commit), the user sees a
  toast, and the release is not tried again on later passes.
- **U8** Changing the Hub's pin moves every Follow owner with a running, idle
  deployment to it without an admin call (Hub logs `[system-apps-follow]`).
- **U9** A Pinned owner is not moved by a pin change; choosing a non-pin
  release in Store → Versions makes the owner Pinned.
- **U10** An owner with a running chat is deferred (state `deferred`, reason a
  chat is running) and moved within one pass after the chat ends.
- **U11** An owner whose computers are away (Workspace paused, brain
  released) is moved without being woken: the spec changes, nothing starts, and
  verification waits until they return (time away does not count).
- **U12** An owner running their own build of a System App is not moved;
  state `modified`.
- **U13** A pin whose release fails to become ready is rolled back to the
  previous release automatically, the user is told (notice/toast), the pin is
  recorded failed and not retried on later passes.
- **U14** The owner/admin accounts behave the same as any other account (no
  role-based exception).

## 8. As built and staging verification (2026-11-11)

Live on staging: controller with `GET /deployments/{name}/activity`, Hub follower
(`SYSTEM_APPS_FOLLOW=1`), Agent App 0.9.131 (the Desktop's updater), frontend #55.

- U1, U2: a new install of a test Store App follows; v1.1.0 published at
  19:07:10 UTC was installed by the Desktop's pass about 2 minutes later, with
  the toast "… updated to v1.1.0".
- U4, U6: Pin kept v1.1.0 with "v1.2.0 is available"; Follow Store moved it to
  v1.2.0 in 11 s.
- U5: a development branch with its own commit: "Update available: v1.5.0. You
  changed this App (it has commits of your own) …"; `official` unchanged.
- U8, U9, U14: changing the pin to 0.9.129 moved 44 owners (the owner and admin
  accounts included) with no admin call; the Pinned test account stayed.
- U10: a running chat (`sleep 150`) deferred the move ("a chat is running"); the
  chat finished, and the move followed within a pass.
- U11: owners whose computers were away moved without being woken; a newer pin
  (0.9.131) replaced the one they had not run yet.
- U12: an App running the owner's own build: state `modified`, not moved.
- U13: a release set with an App that never becomes ready was rolled back
  automatically within 3 minutes, recorded failed, not retried, toast shown.
- U3, U7 for Store Apps: unit tests (idle/busy backend, backend that does not
  start). The Store itself refuses a release whose entry files are missing, so a
  DOM release that cannot launch cannot be published. An App with a `build`
  step is not checked for entry files its build makes.

Open: a rollback returns to the previous release, but the reconciler carries
data from the revision it located last. When the new revision's data copy
failed (one staging owner, Desktop: "source data is not an owned directory"),
the previous revision then tries to copy from the failed candidate and stays in
backoff. The data source must be an instance that has data; to be fixed in the
reconciler.
