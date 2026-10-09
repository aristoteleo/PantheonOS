# Fleet-owned App orchestration

Status: design, 2026-10-09. Supersedes the orchestration half of `pantheon.platform`.

## 1. Goal

Fleet manages Apps. An owner states *what* should run (a deployment: Apps,
configuration, wiring); Fleet decides *where*, starts it, keeps it running and
restores it after loss — on any node, including a single machine that is the
whole system. `pantheon.platform` is removed: no per-owner Python process sits
between the browser and the owner's Apps.

Concretely:

- One desired-state object per owner deployment, stored and reconciled by the
  Fleet controller (Go). No Python coordinator, no journals on a "home" pod.
- Placement by capability at reconcile time, not baked in at setup; an App can
  be pinned or moved by the owner; Apps of a lost node are re-placed.
- Owner intent is explicit (`running` / `stopped` per App). Recovery never
  infers intent from operation-ID prefixes or transient node states.
- The same controller binary runs in the cloud and on a single machine.

Non-goals: changing the per-node Runner protocol (its ledger, generations and
fences are kept as is); changing App packages' contracts; multi-controller HA.

## 2. Where we are (facts this design builds on)

| Concern | Today | Code |
|---|---|---|
| Desired state | Hub `AppStartupPreset` row (recipe JSON, CAS revision); `node_id` fixed per App at setup | `hub/api/app_startup.py` |
| Coordinator | Python `AppDeployment` (install → prepare_start → grants → configure → start), owner journals on the platform pod, polled every 1 s by `AppPreset._drive` | `pantheon/apps/deployment.py`, `pantheon/platform/app_preset.py` |
| Recovery | 30 s Python watch; infers "lost" vs "owner stopped" from stop-operation ID prefixes | `pantheon/platform/preset_resume.py` |
| Grants | Platform → Hub `/api/fleet/apps/dependency-grants` → controller app gateway mints and stores the token; platform renews every 30 s | `dependency_assembly.py`, `fleet/internal/appgateway/dependency.go` |
| Credentials | Platform delivers owner keys to node vaults (`node-secret://`) with ECDH+AES-GCM import | `owner_credentials.py`, `fleet/internal/lifecycle/credential_import.go` |
| Model services | Python wrapper: providers phase → Hub directory registration → hash fence → consumers phase | `pantheon/models/bootstrap.py` |
| Placement | `first_run.place` once at setup; linux-amd64 nodes only | `pantheon/platform/first_run.py` |
| Node lifecycle | Go Runner: idempotent op ledger, generation CAS, prepared reservations, start fence, clone_data, recover; status by pull, heartbeat to JetStream KV | `fleet/internal/lifecycle/*` |
| Controller | Node registry, join/token/revoke, app gateway, grant store; no owner state; local mode already runs controller + NATS + runner on one machine | `fleet/cmd/fleet-controller`, `pantheon/platform/local_fleet.py` |

The recurring failures of the last days (resume giving up after a transient
check, hanging status reads, a stopped-instance race after a pod restart,
registration fences tripping, half-started graphs) all live in the Python
coordinator/watch layer. Moving that layer into one reconcile loop with
explicit intent removes the classes of bug rather than patching each.

## 3. Target architecture

```
browser ──(owner auth)──► Hub ──(service token, owner id)──► Fleet controller
                                                      │  deployments store
                                                      │  reconciler
                                                      │  placement
                                                      │  grant + secret authority
                                   NATS (per fleet)   ▼
                           Runner (node A)   Runner (node B)   …   (unchanged protocol)
                               Apps               Apps
```

- **Fleet controller** gains: a deployments store, a reconciler, placement,
  secret custody, and internal (no Hub hop) grant issuing/renewal.
- **Runner**: unchanged lifecycle protocol. Two additions: an operation
  completion event (push, so the reconciler does not poll), and a data export /
  import pair for moving an App's state between nodes (§8).
- **Hub**: remains the account authority (login, budgets, platform keys, model
  directory, Store). It proxies owner requests to the controller, as it does
  for grants today. The `AppStartupPreset` table is retired (migrated, §12).
- **Brain pod**: an ordinary Fleet node (`kind: pod`). It is optional — a
  deployment that places nothing there needs no pod.

## 4. The deployment object

Stored by the controller per `(fleet_id, deployment)`; revisioned (CAS).

```jsonc
{
  "deployment": "agent",                 // owner-chosen name; the Agent setup uses "agent"
  "revision": 7,                         // CAS; every write bumps it
  "spec": {
    "release": {"url": "https://…/agent-release-set.tar.gz", "sha256": "…"},
    "apps": {
      "agent": {
        "package": "agent",              // alias in the release set; variant chosen per node platform
        "scope": "agent",                // data identity (unchanged meaning)
        "intent": "running",             // running | stopped — the ONLY source of owner intent
        "placement": {"node": null, "prefer": [], "avoid": []},   // null = Fleet chooses
        "config": { /* component values; refs below */ },
        "bindings": { "allocator": {"$app": "allocator", "methods": {…}} }
      },
      "connector": {
        "package": "connector", "scope": "model-platform", "intent": "running",
        "provides": {"model_service": {"deployment_id": "platform", "models": […]}},
        "config": {"connector": {"engine": "api", "endpoint": "https://hub/litellm/v1",
                                 "secret_ref": {"$secret": "platform-budget"}}}
      }
    },
    "secrets": ["platform-budget", "owner-hub", "owner-controller"]   // names only; material in custody (§7)
  },
  "status": {                            // written by the reconciler only
    "observed_revision": 7,
    "apps": {"agent": {"node_id": "n_…", "instance_id": "…", "revision": "…",
                       "generation": 12, "state": "ready", "since": "…"}},
    "conditions": [{"type": "Ready", "status": "true", "reason": ""}]
  }
}
```

Differences from today's recipe:

- No `node_id`, `generation` or `operation_id` in the spec: those are
  reconciler bookkeeping (status), not intent. Generations stay the Runner's
  CAS fence; the reconciler reads and fences them, the owner never writes them.
- `intent` replaces "owner stop vs lost" inference. Stopping an App from the
  UI writes `intent: stopped`; the reconciler stops it and never restarts it.
  Idle policies that stop Apps write the same field with a reason.
- `$secret` names instead of `node-secret://` refs: the controller materializes
  per-node refs when it places an App (§7).
- `provides.model_service` replaces the `kind: model-services` wrapper (§9).
- `package` is a release-set alias; the controller resolves the variant digest
  for the target node's `os-arch` (multi-platform release sets, §6).

API (controller, called by the Hub with the service token and the
authenticated owner's fleet id; local mode: owner key directly):

| Method | Path | Notes |
|---|---|---|
| GET | `/deployments` | list (spec + status) |
| GET | `/deployments/{name}` | |
| PUT | `/deployments/{name}` | body `{revision, spec}`; CAS; validates against release manifests before accepting |
| PATCH | `/deployments/{name}/apps/{app}` | narrow edits: `intent`, `placement`, `config` (CAS) |
| DELETE | `/deployments/{name}` | stops and removes instances' runtime; data retained |
| PUT | `/secrets/{name}` | owner submits secret material (sealed, §7) |
| GET | `/deployments/{name}/events` | recent reconcile events for the UI |

## 5. Reconciler

One goroutine per deployment (bounded worker pool), woken by: spec writes,
Runner operation-completion events, node registry changes (join, loss,
capability change), grant expiry timers, and a 30 s safety tick.

Each pass is a pure comparison of spec and observed node state followed by at
most one step per App, so a crash at any point resumes correctly:

1. **Observe.** Read every relevant node's ledger (`status`) and the registry.
   A node that is offline past a grace period (default 90 s) is *lost*.
2. **Place.** For each App with `intent: running` and no live instance:
   choose a node (§6). Record the choice in status (sticky until the node is
   lost or the owner changes placement).
3. **Order.** Topologically sort by `$app` bindings (cycles rejected at write
   time). A consumer is only prepared once all its providers are `ready`
   at the generation the reconciler expects.
4. **Converge each App** using the existing Runner actions, with operation IDs
   derived as `sha256(fleet, deployment, revision, app, step, generation)` so
   retries are idempotent:
   - not installed → `install`;
   - stopped and should run → `prepare_start` (reservations), issue grants for
     its bindings (§7), `configure`, `start`;
   - running but its pinned provider generation changed (provider restarted) →
     restart the consumer (stop → prepare → start) with fresh grants;
   - `intent: stopped` and running → `stop`;
   - instance `degraded`/`failed` → `recover` once, then back off and surface a
     condition; never loop silently;
   - package revision changed in spec (release update) → §10.
5. **Maintain.** Renew grants within 300 s of expiry; revoke grants of
   instances that are gone or replaced (moves `reconcile_once` from Python).
6. **Report.** Write status and conditions; emit events.

Properties:

- No "needs attention, stop watching" state. Failures are conditions on the
  deployment; the loop keeps observing and retries with backoff. Only an
  explicit owner action changes intent.
- A node restart, pod replacement or late registration is just an observation
  that changes on the next pass — the races hit in staging (instances still
  reading `ready` after their process died; a node not yet registered) resolve
  themselves when observation catches up, because nothing gives up early.
- Whole-graph restarts are no longer a special case: provider restarts
  propagate to consumers through the generation pins (step 4).

## 6. Placement

Inputs: App manifest `placement.requires` / `prefer` (unchanged format), the
deployment's `placement` override, node caps/kind/os/arch from the registry,
resource fit (`memory_gb`, `disk_gb`, current reservations), and the release
set's available platform variants.

Rule: filter nodes where `requires ⊆ caps`, a package variant exists for the
node's `os-arch`, and resources fit. Score: owner pin > `prefer` kinds >
co-location with the App's bound providers > current node (stickiness) >
least loaded. Ties by node id.

Consequences:

- **Single machine.** A machine advertising `proc, fs:workspace, net, display`
  satisfies every App; everything lands on it. No special mode.
- **Cloud without a brain pod.** The sandbox satisfies everything; Apps that
  need nothing beyond `proc` co-locate there.
- **Agent on a Mac.** Owner sets `placement.node` (or `prefer: [machine]`);
  requires a darwin-arm64 variant in the release set (§6.1).
- **Re-placement** after node loss is automatic for Apps whose data policy
  allows it (§8); otherwise the App waits for its node and the condition says so.

6.1 **Multi-platform release sets.** The release builder already supports
POSIX targets per package; the published release set includes `linux-amd64`,
`linux-arm64`, `darwin-arm64` (Windows later; it needs the Runner's
PowerShell path for hooks). The controller picks the variant per node.

## 7. Grants and secrets

**Grants.** The controller is already the token minter and store
(`appgateway/dependency.go`). The reconciler calls that code in-process; the
Hub hop and the Python `DependencyStarter` disappear. Grant pinning is
unchanged (fleet, consumer and provider identities with generations, methods,
argument rules, ≤ 15 min expiry). The allocator App keeps serving *runtime*
grants for Agent tool sessions; its owner credential becomes a controller-issued
scoped credential instead of a raw owner key.

**Secrets.** Moving and re-placing Apps means the system must be able to
deliver a secret to a node that did not exist at setup. The controller takes
custody:

- The owner submits secret material once (`PUT /secrets/{name}`, via the Hub,
  e.g. the platform budget key at Agent setup). The controller seals it with a
  key held only in its state directory (age/X25519; file mode 0600; local mode:
  the user's machine).
- When placing an App that references `{"$secret": name}`, the controller
  imports it into that node's vault with the existing ECDH challenge
  (`credential_prepare` / `credential_ensure`) and writes the resulting
  `node-secret://` ref into the instance configuration.
- Rotation: a new version of the secret re-imports to nodes and restarts
  dependents through the normal generation path.

This widens the controller's trust (it now holds owner secrets). It already
holds every owner's NATS authority and grant tokens, so the trust boundary is
the same machine; the design makes it explicit and sealed at rest.

## 8. Moving App data between nodes

Today `clone_data` copies within one node. Re-placement needs a cross-node
copy. Add a Runner pair over the existing dataplane:

- `export_data {digest, scope, generation}` → a content-addressed archive
  offered on the dataplane (bounded by the existing 64 MiB App-state limit;
  larger state must live in the workspace or the model cache, as today);
- `import_data {digest, scope, generation:0, source{node, digest, generation}}`
  → materializes the archive as the new instance's state (same receipt
  semantics as `clone_data`).

Data policy per App (manifest `state.portable: true|false`, default true for
state under the limit). Non-portable Apps are never moved automatically.

## 9. Model services without a wrapper

Today `ModelServiceBootstrap` adds four model-specific steps. In this design:

- **Credentials.** The connector's budget key is an ordinary `$secret`.
- **Registration.** The connector App registers itself: on start it calls the
  model directory with a narrowly scoped grant (`model_directory.register` for
  its own `deployment_id`), which the reconciler issues like any binding. The
  directory row's binding therefore always matches the running instance; the
  re-registration races (stale rows, routes blocking deletion) disappear
  because the row is keyed by `deployment_id` and updated in place by its owner
  instance.
- **Readiness.** Consumers bind to the connector through `$app`; the
  connector's readiness probe (`status.accepting`) is the generic Runner
  readiness check, so "providers before consumers" is ordinary ordering.
- **Catalog publication and tier routes** (curated platform models, tier
  routes) become the connector's own startup work, driven by its config.

`provides.model_service` in the spec only declares the `deployment_id` so the
controller can issue the registration grant.

## 10. Release updates and rollback

A release update is a spec write: new `release` and per-App package revisions.
The reconciler, per changed App in dependency order: stop → `clone_data`
(same node) or `export/import` (moved) into generation 0 of the new revision →
prepare → start; unchanged Apps restart only if a provider they pin restarted.
The previous revision's instance and data are retained, so rollback is a spec
write back to the old release, which restarts the retained generation —
the same semantics as today's `preset_release`, executed by the controller.

Configuration changes (e.g. new model tiers) are spec writes too; the
reconciler restarts only the Apps whose config changed. This replaces the
"update Agent setup" flow.

## 11. What happens to `pantheon.platform`

Every browser call it serves today moves to an owner of that concern:

| Concern (today's tools) | New home |
|---|---|
| Startup preset / Agent setup / release (`platform_agent_*`, `platform_app_preset_*`) | Controller deployments API via Hub; Agent setup = browser composes a deployment from the release set's profile template (§11.1) and PUTs it |
| Fleet inventory and App lifecycle views (`fleet_inventory`, `fleet_app_lifecycle`, node join/revoke, HPC) | Controller API via Hub (most already exist on the controller: `/nodes`, `/join-tokens`, `/revoke`); App lifecycle reads become deployment status |
| Model Services management (37 `model_services_*` tools) | The existing `model-management` App (already serves the Agent); the UI calls it through the app gateway like any App |
| Model directory / settings (`list_available_models`, `get_model_details`, …) | Hub (account-level catalog) |
| OAuth connectors (`oauth_*`) | Hub (tokens are account-level) |
| Store install/uninstall, skills | Desktop App (it owns the App catalog) |
| Projects (`list_projects`, …) | Files App (workspace owner) |
| App services (`resolve_app_service`, `call_app_service`, `get_toolsets`) | App gateway (already the data path) |

The pod image no longer starts a platform process; a pod node runs the Runner
only. The Python packages `pantheon.platform`, `pantheon.apps.deployment*`,
`dependency_assembly` (coordinator half), `models.bootstrap` and the owner
journals are deleted once their callers are gone.

11.1 **Profiles.** Composing the General Team (`general_agent_preset`,
`compose_profile`) moves to release-build time: the release set ships
`profile.json`, a deployment spec template with typed holes for owner inputs
(model tiers, routes, secrets, placement overrides). The browser fills the
holes from the setup form and PUTs the result; the controller validates it
against the release manifests. No Python runs on the setup path.

## 12. Migration (direct cut-over, no transitional mode)

Order of work (each step merged with tests; staging cut-over at the end):

1. **Controller store + API** (deployments, secrets; file-backed, fsync'd like
   the grant store), Hub proxy endpoints, owner auth mapping.
2. **Reconciler** with in-process grants; Runner op-completion event.
3. **Placement** + multi-platform release sets.
4. **Connector self-registration** and tier routes / catalog in the connector.
5. **Profiles** in the release builder; browser setup writes deployments.
6. **UI rewiring** per §11 (Fleet views, Model Services via `model-management`,
   settings/OAuth/model directory via Hub, Store via Desktop, projects via Files).
7. **Data export/import** for re-placement.
8. **Cut-over on staging:** a migration job converts each owner's
   `AppStartupPreset` recipe into a deployment (App aliases, scopes, config and
   bindings carry over; current generations seed status so retained data is
   reused), switches the pod image to Runner-only, and deletes the platform
   journals. Owners' data stays where it is (same digest/scope instances).
9. Remove `pantheon.platform` and the Python coordinator code.

Production is untouched until staging has run on this for an agreed period.

## 13. Testing

- Go unit tests for reconcile passes as table tests over (spec, observed
  ledgers) → expected next actions, including: node lost / late registration /
  stale `ready`, provider restart propagation, owner stop, failed start with
  backoff, release update and rollback, re-placement with and without
  portable data.
- Fake Runner (in-memory ledger implementing the protocol) for end-to-end
  controller tests; the existing Python lifecycle tests document the protocol
  contract the fake must honour.
- Staging scenarios (scripted): fresh owner, pod/sandbox replacement, idle
  reclaim and return, owner stop/start, release switch and rollback, single
  machine (desktop local mode), Agent pinned to a Mac node.

## 14. Open questions

1. Controller durability: file store (like the grant store) is enough for one
   controller; do we need Postgres before production?
2. Should model directory registration move off the Hub into the controller
   (one owner store), or stay Hub-side behind a scoped grant (this design)?
3. Brain pod: keep a resident pod per owner for fast Agent start, or rely on
   the sandbox (one fewer node, slower return after idle)? Placement allows
   both; it is a product/cost choice.
4. Windows nodes: when to add a `windows-amd64` variant and Runner hook support.
