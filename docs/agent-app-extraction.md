# Pantheon Agent App extraction

Pantheon-Agent will be an ordinary versioned App containing its GUI and runtime.
PantheonOS must remain usable with the Agent stopped or uninstalled. This document
tracks the accepted migration; passing one stage does not complete the project.

## Source baseline

- Runtime: `6e58904a`, now isolated on `codex/agent-app-extraction`.
- UI: `c83069fe` plus existing local changes in `pantheon-ui-apps`, preserved in
  local baseline commit `05c8fb78` in `/Users/weizexu/Projects/agent-app-extraction/ui`.
  The original UI checkout was not modified; generated assets are copied but not
  included in the source baseline commit.
- Hub: `cd4fad77`, isolated in `/Users/weizexu/Projects/agent-app-extraction/hub`
  on `codex/agent-app-extraction`.
- `agent` remains the App id, with Pantheon-Agent as the display name.
- The current Agent manifest is frontend-only (`ui:agent`). Desktop connections
  use the ChatRoom proxy; the first priority is removing that dependency.

## Ownership

| Platform | Pantheon-Agent | Other Apps |
| --- | --- | --- |
| Identity, grants, transport, discovery | Execution, delegation, cancellation | Files and Shell |
| App deployment and immutable releases | Agent configs and revisions | Notebook and Browser |
| Nodes, HPC, generic usage leases | Agent instances, teams, conversations | Model inference and deployment |
| Windows, durable data facilities | Memory, tasks, compression, GUI | Search, image generation, Evolution |

Platform implementation must not import the Agent execution package. Agent GUI
uses the ordinary App SDK rather than private desktop stores. A preset may
install/autostart Agent, but platform login and readiness do not depend on it.

## Milestones and acceptance

| Stage | Required work and evidence | Current status |
| --- | --- | --- |
| P0 | Classify every public ChatRoom RPC, UI dependency, durable data root; record functional and performance baseline | RPC inventory started; UI/data/performance audit pending |
| P1 | Move platform RPCs out of ChatRoom; connect desktop independently; stop Agent and exercise Files, Terminal, Fleet, Store, Jupyter, Browser, Model Services | Independent host, desktop transport, explicit Hub topology discovery/health and snapshot bootstrap implemented; remaining platform endpoints and full desktop cutover pending |
| P2 | Generic owner references, interface bindings, grants, sessions and leases; two Agents have independent Shell state and share stateless files | Prepared configuration, scoped grants, owner renewal and provider resource-session contract implemented locally; automatic session coordination, gateway recovery and live acceptance pending |
| P3 | Package Agent runtime, configs, instances, conversations, runs and replayable events; preserve inference routes and cancellation | Ordinary ToolSet host, Agent drain, explicit domain composition and initial scoped tool factory implemented locally; final package, model/plugin isolation and revised domain APIs pending |
| P4 | Package GUI; independent client/store per deployment; remove static Agent imports from Atrium; support App intents | Pending |
| P5 | Inventory, backup, import and validate data; fence old writer; preserve project asset references; test failed migration recovery | Pending |
| P6 | Publish one frontend/backend release; isolated candidate, drain, schema checks, cutover and rollback; self-edit demonstration | Pending |
| P7 | Replace Hub brain-specific bootstrap with generic App deployment; remove transitional paths; complete cross-node acceptance | Pending |

M1 completes P0/P1, M2 completes P2/P3/P4, M3 completes P5/P6, M4 completes P7.
No milestone is complete merely because its files or manifest exist.

P1 frontend follow-up: UI commit `4b0e5ac0` removes the shared identity
store/HTTP client's imports of the Agent page router and delegates cleanup to
loaded resource owners. Password and primary OAuth adoption wait for old-owner
cleanup; stale HTTP rejection/credential responses cannot clear or repopulate a
replacement login. Workspace and Agent teardown remain independently owned.
105 identity/connection/OAuth regression tests and the separate real authenticated
NATS/Python Files integration passed; type checks passed. The touched legacy UI
stores retain 12 lint findings reproduced on their baseline. This is not a
live deployment or completion of P1/P4; see the UI migration document for scope.

UI commit `1d85a63a` subsequently removes the root desktop's Agent UI-store
dependency and loads AgentApp/WindowChatPane on demand. Generic host presentation
actions retain file/image routing without importing the consumer. Both normal
web and Hub client-shell builds passed a source-and-output dependency audit:
neither the 607 static modules nor the seven eager chunks contain the checked
Agent implementations. 73 regressions and type checking passed. Agent remains
in the UI distribution and uses shared stores; its separate deployment binding,
frontend artifact, legacy OAuth fallback and full live desktop gate remain.

## Resource model

An App release is immutable code. An App deployment runs that release on a Fleet
node. A config revision is an immutable Agent recipe. Agent instances have stable
identities and bindings; runs are individual executions. Conversations and teams
are durable App resources, not window identities.

Tool services and sessions have different lifetimes. Shared file services receive
workspace and authorization on every request. Each Agent instance owns its default
Shell session across turns. Child Agents receive separate sessions unless sharing
is explicitly granted. Notebook kernels belong to notebook/compute sessions;
Agent termination releases usage rather than killing the user's kernel.

Bindings identify consumer, provider instance, node, interface version, optional
session, generic owner reference, grant and generation. Fleet must not acquire
Agent-specific concepts. Recovery never replaces a lost Shell silently. Closing
a window is not termination; idle and background-task policies remain explicit.

## Migration and release guarantees

- Keep existing APIs through temporary adapters while callers migrate. New
  platform endpoints must not call back into ChatRoom to work.
- Reuse execution and UI logic before redesigning it. Do not create a second chat
  implementation, second transport, or per-Agent copy of every service.
- Preserve budget, BYOK and Fleet model routes. No master credentials move into
  App code or frontend data.
- Keep code immutable and user data separate. Pin frontend/backend/API/schema
  compatibility in one release. Config edits are not App releases.
- Back up and validate migration; only one runtime may write a data namespace.
  Preserve external project files and attachment references.
- Drain old runs before version cutover. Unknown side effects are not replayed.
  A crashed execution is marked interrupted if it cannot be safely resumed.
- Candidate self-edits use a working copy and isolated test data. Fleet/Store,
  independent of Agent health, performs cutover and rollback. Incompatible data
  downgrades require an explicit snapshot restore rather than code-only rollback.

## Current extraction

`pantheon.platform.service.PlatformService` serves the Fleet and model management
APIs over the existing user-scoped service bus. It starts no Agent/team/memory
workers. `ChatRoom` temporarily inherits these same implementations so old clients
retain their signatures. `pantheon.chatroom.fleet_session` is a compatibility alias
for credentials now owned by `pantheon.platform.fleet_session`.

Generic `call_app_service` carries an explicit workspace; Agent's legacy
`proxy_toolset` retains chat-specific memory routing in its wrapper. The project
registry now lives in `pantheon.platform.projects`; its compatibility alias
preserves legacy imports. Platform selection does not change cwd, memory or
templates. Registry I/O runs off the RPC event loop, uses atomic file replacement
and coordinates cooperating local processes with a sidecar lock. This lock does
not coordinate independent cloud-volume replicas: deployment must retain one
registry owner. Registry initialization is lazy and preserves platform selection
across restart.

Verification on 2026-10-02: 101 passed, 1 skipped across platform, projects,
multi-project memory, recovery, Fleet/HPC and model-service suites. One existing
model-service HTTP fixture thread warning was reproduced on the original
baseline. The real local authenticated NATS test runs a separate platform process
with Agent imports prohibited and exercises App discovery and project RPCs.
Additional tests cover concurrent registration, corrupt-file protection, failed
atomic writes, and event-loop responsiveness during slow registry I/O. These are
component results, not proof of desktop independence or deployment completion.

The isolated UI now has a platform connection path selected by an explicit
`platform_service_id` in the Hub descriptor. Its RPC and stream clients do not
import Agent stores, and handshake failures cannot fall back to Agent. Legacy
descriptors load a separate compatibility adapter. The isolated Hub now supports explicit `platform` topology nodes and advertises
`platform_service_id` only after a live readiness probe. Legacy configuration
remains unchanged. Root GUI/auth dependencies and remaining platform endpoints
still need work before enabling this configuration in a live environment.

For development, with the same authenticated bus/Fleet environment used by the
platform deployment and a distinct service seed:

```sh
python -m pantheon.platform --id-hash USER_PLATFORM_SERVICE_SEED
```

This does not by itself switch Hub or desktop discovery. No production deployment
has occurred. `docs/agent-app-rpc-inventory.json` records the original 134 public
RPC signatures; remaining owners and call sites must be migrated before M1.

## Platform bootstrap and discovery (P1, opt-in; not deployed)

A topology service node may explicitly declare `apps: "platform,chatroom"` during
transition, or `apps: "platform"` without Agent. The platform service seed is
`platform:` plus `ID_HASH`; its NATS identity is SHA256 of that seed. The legacy
Agent `service_id`, user-scoped subject prefix, assignment IDs and volume names
are unchanged. Both container startup readiness and periodic Hub probes use the
platform identity for this topology. Platform health cannot infer Agent activity,
so these pools do not reclaim the node using the legacy Agent idle signal.

The container starts `python -m pantheon.platform --deployment-id "$ID_HASH"`.
When `chatroom` is also declared it passes `--legacy-agent --` followed by the old
Agent CLI arguments. This optional child is transitional, not the final App
supervisor. It may fail/exit without terminating the platform; no automatic Run
replay or child restart occurs. The platform process imports no Agent modules.
The transitional Agent must share its platform's state host until P3/P5 establish
separate durable App namespaces; a split topology is rejected rather than giving
two workers ownership of one snapshot.

Shared snapshot implementation now lives in `pantheon.platform.state_sync`, with
a compatibility module alias. Platform bootstrap owns one initial restore and a
serialized periodic/final push loop. A configured restore must succeed (an HTTP
204 is authoritative empty state) before services or the child start. Child state
credentials are removed and an explicit external-owner marker prevents its .env
from re-enabling sync. A lifetime filesystem lock excludes duplicate cooperating
platform publishers locally; cross-replica fencing remains a P5 requirement.
Shutdown rejects new platform RPCs, drains accepted requests, stops the child,
and attempts a final snapshot. Failed/oversized final writes fail shutdown instead
of being reported as saved. The unsafe legacy in-place re-exec RPC is disabled on
the platform host; its lifecycle belongs to the supervisor.

The Hub descriptor returns 503 `platform_not_ready` for an opted-in deployment
whose platform does not answer, including an old container still hosting only
ChatRoom. It never silently falls back to Agent. Enabling the topology requires a
coordinated runtime/Hub/UI rollout after all remaining endpoint migrations pass.

Verification includes authenticated local NATS subprocesses with Agent imports
prohibited, optional child exit isolation, initial restore failure, final writes,
duplicate snapshot owner rejection and RPC drain. The actual Atrium TypeScript
platform client also connects over a local authenticated NATS WebSocket to the
Python host and continues project/App discovery after the child exits. This
proves the transport path, not full desktop independence, production deployment,
or the final ordinary-App Agent lifecycle.

## Final acceptance

Verify the desktop without Agent, failure isolation, session isolation and cleanup,
shared stateless services, UI-close behavior, process restart, event reconnect,
unknown-effect handling, version coexistence, migration/rollback, and self-upgrade.
Run real Linux and macOS scenarios, then ordinary App execution on an allocated HPC
node. Windows automated/build checks are not a substitute for unavailable hardware.
Record startup, first response, idle memory, reconnect and post-close resource use
against the P0 baseline. Do not report the whole plan complete with a narrow unit
test or an undeployed manifest.

## Detailed execution plan

The contracts and field names below are proposals to validate against the current
App schema. They are not claims that these APIs already exist.

### P0 — Establish the migration contract

1. Complete the RPC inventory with every caller, authentication boundary,
   persistence owner, and replacement route. Keep the original 134 signatures as
   a compatibility baseline until their callers migrate.
2. Trace desktop login, connection, reconnect, project selection, window restore,
   app startup, and Agent sidebar entry points. Record static imports and global
   store dependencies, including `network/pod.ts`, `apps/registry.ts`, and
   `apps/agent/AgentApp.vue` in the UI repository.
3. Inventory conversations, attachments, templates, config, memories, task state,
   project registry, credentials, and external file references. Identify actual
   writers and storage roots before moving any data.
4. Measure platform readiness, Agent readiness, warm/cold App startup, idle RSS,
   first response, and reconnect with fixed scenarios. Separate inference latency
   from initialization overhead. Preserve all current UI changes in the baseline.

Deliverables: caller/owner matrix, storage map, baseline scenarios, and an explicit
legacy-to-new compatibility table. Do not infer baseline performance from memory.

### P1 — Make the platform independent of Agent

1. Extend `pantheon/platform` with platform-owned discovery, App lifecycle,
   project registry, resource access, and credential references. Separate mixed
   methods: project selection must not reset Agent memory or templates.
2. Move Playground execution and inference-specific business logic toward their
   own App packages. PlatformService is a transitional service boundary, not the
   destination for all code removed from ChatRoom.
3. Give the desktop an independent platform connection/client. Opening the
   desktop must not load teams, conversations, or Agent configuration.
4. Add generic Hub discovery/bootstrap alongside legacy routes, then switch
   desktop callers. Keep temporary adapters one-way: old Agent callers can use
   platform services; platform services cannot depend on Agent.
5. Expose truthful independent platform/App health and reconnect states.

Gate: stop the Agent process and exercise Files, Terminal, Fleet, Store, Jupyter,
Browser, and Model Services. Verify in addition that platform import/startup does
not import Agent, ChatRoom, Team, or memory modules. Do not rely only on mocks.

### P2 — Model dependencies, sessions, and ownership generically

Separate five concepts rather than encoding everything as an App dependency:

| Concept | Purpose | Example |
| --- | --- | --- |
| Package dependency | Compatible code/interface requirements | Files interface major version |
| Binding | Chosen provider deployment and target node | Files on the workspace node |
| Service instance | Provider process and its data scope | A shared Files backend |
| Resource session | Stateful object hosted by a provider | Shell session or notebook kernel |
| Usage lease / grant | Lifetime reference / authorization | Agent may execute in a specific shell |

Proposed binding fields: consumer deployment, provider deployment, interface and
version, target node, resource/session reference, grant reference, generation.
Generic owner references contain an App/deployment identifier and an opaque
resource type/id; Fleet does not implement AgentInstance semantics.

Implement acquire/release, reconnect, generation checks, and abandoned-owner
cleanup. Providers define session persistence and idle policy. Leases are not
permissions, and service reuse never implies cross-user sharing. Installation
resolves required dependencies and reports optional unavailable capabilities;
missing a GPU model provider must not prevent opening Agent settings.

Default policies:
- Shell: one default session per Agent instance; children get their own sessions.
- Files: shared service inside the authorized node/security scope; every call
  carries explicit workspace context, never process-global current directory.
- Notebook: a durable notebook session owns its kernel; Agent borrows access.
- Browser: explicit browser/profile session; sharing requires an explicit binding.
- Model service: shared authorized provider; model loading/cache is provider-owned.
- Agent memory/tasks: Agent-owned data or explicitly bound external Apps.

Gate: two Agents retain different shell cwd/environment while sharing Files;
closing GUI does not end work; terminating an owner cleans only its resources;
provider restart invalidates stale session handles visibly; timeouts do not
automatically replay commands with unknown side effects.

### P3 — Package the Agent backend

Create a deployable Agent backend using existing execution logic. Keep a small
bootstrap adapter until the App host can load it through the ordinary backend
entrypoint. Separate the Agent Python distribution from the platform installation
requirements so import independence becomes installation independence.

The domain model is:
- App release: immutable frontend/backend code and compatibility metadata.
- Deployment: a running release on a Fleet node, with a durable data namespace.
- Config revision: model route, prompts, capabilities, and policy settings.
- Agent instance: stable identity referencing config and service bindings.
- Conversation/team: durable collaboration resources owned by the Agent App.
- Run: one execution with an instance, config revision, bindings, and status.

A deployment may host many instances. A config edit creates a revision without
publishing a release; a Run retains its selected revision. Define an explicit
mapping for existing team/member/session identities during migration.

Provide versioned APIs for configuration, instances, conversations, Runs,
cancellation, approvals, and resumable event subscription. Persist event sequence
numbers and Run state. Reconnection replays recorded events; it must not rerun
side-effecting tools. Preserve platform budget, BYOK, and Fleet model bindings.

Gate: ordinary App install/start/stop/logs/health works; multiple instances are
isolated; streaming, cancellation, delegation, approvals, memory, and recovery
match baseline behavior.

### P4 — Package the Agent frontend

Move conversation UI, team/config editors, Agent stores, and API client into the
Agent frontend package. Instantiate stores per deployment rather than reusing a
global chatroom singleton. Reuse current components; this is not a UI redesign.

Replace the static AgentApp registry import with normal App frontend loading.
Generalize sidebar/window embedding and intents such as opening a conversation
with an authorized file reference. Desktop owns window placement and generic App
launch; Agent owns rendering and interaction. File selection, credentials, and
notifications use the App SDK.

Gate: the same release opens as a normal window and sidebar; two deployments do
not share state accidentally; closing/reopening preserves server-side Runs; Agent
frontend failure does not break desktop navigation or other Apps.

### P5 — Migrate durable data with a single writer

Build a repeatable dry-run migration report before cutover. Back up source data,
copy/import into a versioned App data namespace, preserve identifiers and external
project asset references, and validate counts plus representative content.

Fence the legacy writer before enabling the new runtime. An interrupted migration
must resume safely or restore the untouched source. Credentials remain in the
credential facility and are referenced by opaque identifiers, never copied into
release artifacts. Moving the project registry must preserve existing project IDs.

Gate: old conversations, attachments, configs, memory, and team references load;
failed migration leaves a recoverable source; old and new runtimes cannot write
the same namespace concurrently.

### P6 — Ordinary App releases and self-modification

Extend the existing release system only where necessary: pin frontend/backend
artifacts, SDK/API compatibility, dependency interfaces, and data schema range in
one immutable release. No independent frontend/backend upgrade that creates an
unsupported pair.

Self-edit flow: create working copy → edit → build/test isolated candidate →
review → drain active Runs → migrate if needed → switch release → health check.
The platform owns switching and recovery, including when Agent is unavailable.
Candidate and stable releases use separate test data unless an explicit migration
cutover has fenced the old writer.

Code rollback is automatic only when the existing data schema is compatible.
Otherwise require a planned snapshot restore; explain possible loss of writes
since that snapshot. Do not promise arbitrary in-flight Run migration.

Gate: Agent edits its own candidate frontend/backend, candidate tests pass, normal
App versioning publishes it, and Fleet can roll back a deliberately broken
candidate without calling Agent.

### P7 — Remove legacy embedding and complete integration

Replace Hub's brain/chatroom-specific provisioning with generic App deployment and
discovery. A default user preset may install/autostart Agent, but platform login,
workspace readiness, and health checks must succeed without it. Migrate state
sync paths and remove legacy routes only after their callers are gone.

Run real macOS/Linux acceptance, then ordinary App execution through an allocated
HPC node using the same contracts. Windows CI/build checks are recorded separately
from unavailable hardware verification. Compare memory and latency against P0;
address regressions without dropping features. Verify an Agent uninstall/reinstall
preserves data according to the ordinary App uninstall policy.

## Implementation sequencing and decision points

Use reviewable commits per milestone and keep legacy operation available until
its replacement gate passes. P0/P1 comes first; P2 precedes stable backend binding
contracts; P3/P4 converge before migration; P5/P6 precede production cutover; P7
removes compatibility code last. Do not undertake all repository cutovers at once.

Default decisions: retain App id `agent`; one deployment can host many Agent
instances; retain current user data on uninstall; dependencies bind through
existing Fleet/App mechanisms; close-window and stop-runtime remain distinct.

Ask the user when a concrete choice changes data placement, account isolation,
privilege grants, or upgrade interruption. Present migration/cutover results and
active Run impact before a disruptive live deployment. Do not ask again for
routine code organization already covered by this plan.

## Platform readiness and telemetry (P1 follow-up)

Host/Fleet telemetry now lives in `pantheon.platform.health`, shared with the
legacy Agent host. The platform worker registers its own `_ping` status callback
and returns `activity_scope: platform`; it does not report that all hosted Apps
are idle. Fleet node snapshots refresh asynchronously with a bounded probe and
single-flight task, so a slow registry cannot block desktop readiness. Both hosts
cancel their refresh on cleanup. The transitional child skips the platform's
workspace disk scan, retaining the existing delayed/throttled scan in its owner.
Agent execution activity remains in ChatRoom until the Agent App owns it.

The desktop recognizes the explicit Hub `platform_not_ready` response. After
initial acquisition it polls read-only pod status, reacquiring the descriptor
only when the independent service is healthy. One deadline bounds the whole
operation; removed assignments, changed accounts and cancelled connection
generations terminate it. A late authorization failure from an old acquisition
cannot sign out a different account. Generic Hub errors are not converted into
startup waits, and no pending platform falls back to the Agent service.

Verification: 55 runtime tests passed across platform health/bootstrap/RPC,
project registry and instance recovery; 46 UI tests passed across startup,
workspace recovery and platform transports, including authenticated NATS
WebSocket communication with an Agent-import-blocked Python platform. UI type
checking and targeted lint passed. These changes are local and not deployed;
full desktop-without-Agent acceptance and the remaining endpoint migration are
still required before M1 is complete.

## Store content boundary (P1 follow-up)

`StoreAPI` now serves `install_store_package`, `uninstall_store_package`,
`get_installed_store_packages`, and `get_local_skills` from the independent
platform. ChatRoom inherits the same RPC signatures for compatibility. The
selected project is captured before a download starts, and filesystem work runs
off the RPC event loop. This is the legacy content-package installation API;
ordinary versioned App releases continue through the existing App Store manager.

Skill content types/storage now live under `pantheon.skills`, with module aliases
at the old learning-system paths preserving class identity. Store's read-only
catalog constructs no learning worker or extraction-state directories. It uses
the same project/global/factory precedence and exclusions, reports modified
factory overrides and seeded Markdown resources, and does not apply the Agent's
200-item prompt-index cap to the Store inventory. The factory assets still ship
with the current distribution; independent content packaging remains part of P3.

Installation records retain the package-id map and display metadata, with explicit
per-workspace `_install_locations`. This lets the same package keep distinct
versions in different projects. Cooperating writers use the stable sidecar lock
and atomic manifest replacement; corrupt registries fail before content writes.
Listing never prunes a record because another project is active or a volume/file
is unavailable. Old unscoped entries remain in `_legacy_install` when a new scoped
installation is recorded; listing/removal inspects only the selected project and
global content roots. If both contain the legacy package, removal fails with an
owner ambiguity instead of guessing. Legacy metadata remains available for the
later migration audit, including when its current-project files were removed.
Downloaded paths are validated before content writes, including rewritten skill
bundle paths and symlink escapes.

The legacy installer is not a multi-file transaction: a manifest-save failure
following content installation/removal is reported explicitly as a partial
operation, not success. Previous registry bytes survive failed atomic replacement.
This does not replace the P5/P6 backup, release transaction or distributed-writer
requirements. Do not run pre-extraction Store writers alongside the scoped
registry writer during rollout; the in-tree transitional ChatRoom uses this same
implementation.

Verification: 192 tests passed across Store, learning-system compatibility,
platform health/bootstrap/RPC/projects and service recovery. After clarifying
partial-operation error reporting, the 18 Store tests were rerun and passed.
The real authenticated NATS subprocess test downloads content from a local HTTP
Store fixture, exercises all four RPCs with Agent/learning imports forbidden,
and verifies file removal. Separate tests cover distinct project versions,
concurrent writers, project changes during download, slow filesystem work,
corruption/atomic-write failure, unscoped legacy records, and catalogs larger
than the Agent index limit. No live rollout or full M1 desktop gate is claimed.

## Model directory and project settings (P1 follow-up)

The platform and transitional ChatRoom now share seven model-directory RPCs:
saved models, provider discovery, available models, OpenRouter search, model
details, Ollama status, and masked credential status. Project settings scope also
lives in the independent project API. All eight retain their original signatures.
Directory results retain platform-budget, BYOK, and Fleet model groups, including
catalog freshness and reasoning-effort metadata. Fleet unavailability does not
hide other model sources. These are metadata/configuration APIs, not inference.

Platform provider settings use a project-local environment mapping; loading or
reloading one project's .env does not modify another App's process credentials
or reset runtime caches. Expansion honors the same environment precedence as the
legacy loader. The model selector uses its supplied Settings object instead of
silently consulting a global singleton. Slow configuration I/O is off the RPC
event loop. A settings write retains the captured project during concurrent
selection changes; cooperating writers use locked, atomic read-modify-write and
refuse to overwrite corrupt settings. Legacy Agent settings behavior is retained.

Verification: 115 tests passed across model-directory, legacy model selection,
platform bootstrap/health/projects/Store and real authenticated NATS transport.
One image-model priority test was deselected only after its identical failure was
reproduced in the untouched baseline (it expects Gemini while defaults choose
gpt-image-2). The wire test reads/writes model selections and project settings and
queries masked key status in an Agent-import-blocked platform subprocess, with
and without a terminated child fixture. Additional cases cover slow I/O, project
switches, concurrent writers, .env expansion, corrupt files and atomic failure.

OAuth and the legacy process-local budget toggle are not migrated by this change.
Moving set_llm_proxy alone would change only the platform process environment,
leaving Agent inference on the old route. Runtime configuration synchronization
and the remaining desktop dependencies still block full M1 acceptance. No live
deployment or complete Agent App extraction is claimed.

## OAuth ownership and responsive login (P1 follow-up)

The seven OAuth management RPCs now live in a shared platform API, preserving
their signatures for ChatRoom callers. Status refresh, CLI import, callback-server
creation/cleanup and token exchange run off the RPC event loop. Login waiting
polls provider events with zero blocking wait, so it does not occupy a thread for
the old 300-second callback wait. A per-session async lock serializes completion,
parallel waits and cancellation; successful results cache only public metadata.
Timed-out waits retain the paste-URL fallback.

Each host owns its callback sessions, with at most 16 pending/retained sessions
and automatic expiry at the provider's advertised deadline. Another host cannot
finish or cancel them. Existing browser-open login remains a compatibility path;
the split flow still leaves browser opening to the frontend. During a platform
rollout, an in-progress login must remain routed to its original service or be
restarted explicitly. Sessions do not survive a process restart.

Graceful shutdown rejects new OAuth operations, closes callback servers, releases
waiters and awaits accepted work before draining the platform worker and taking
its final snapshot. Disconnected callers cannot orphan a late callback-server
start or an in-progress credential write. Already-started token exchange uses
the provider's existing network timeouts and may delay shutdown; this is not a
promise of instantaneous cancellation or recovery after a forced process kill.

Verification: 108 tests passed across OAuth providers/platform flows, legacy RPC
signatures, model settings, projects, Store, health and bootstrap. Both providers
use real local HTTP callback servers and isolated temporary auth files with fake
token exchange; no real login or user credentials were exercised. The targeted
OAuth/wire suite was rerun after expiry/shutdown changes. Real authenticated NATS
tests start/wait/cancel OAuth with Agent imports prohibited and stop the platform
with an accepted 300-second wait still outstanding, within the normal test
shutdown deadline. A Gemini selector test double was completed with the scoped
Settings API introduced by the prior model-directory change.

This removes another Agent-owned platform path. Budget-toggle propagation,
remaining desktop discovery/settings callers, Playground ownership and full
desktop-without-Agent acceptance remain unfinished. No live deployment occurred.

## Generic service binding and desktop file data (P1 follow-up)

`resolve_app_service` exposes a service binding without chat/session context.
It accepts a service name and explicit workspace or node, uses the existing App
resolver, and returns the service id plus invocation form. The default workspace
is the platform deployment's root rather than process cwd. Explicit node access
retains the resolver's Fleet membership/capability checks and its current
Files/PTY restriction. Node file transfers use the Files service's transfer
envelope; the result never substitutes a workspace node for the requested node.
This is service discovery, not the P2 persistent binding/grant/lease model.

Legacy `get_endpoint(session_id)` keeps its original signature and resolves the
chat's project in ChatRoom before using the shared resolver helper. New desktop
file calls do not use that Agent wrapper. The UI has extracted its byte-transfer
core from the Agent stores and gives desktop operations an independent, leased
data connection. Office, node previews, upload and download callers migrate to
this client while Agent conversation isolation and replay behavior remain in the
Agent adapter. Data handles stay on their original connection and service;
reconnect reports invalidation instead of rerouting an open file.

Verification: 28 Python tests passed for binding, platform RPC, project scope and
legacy signatures, including the existing authenticated subprocess suite. The UI
suite passed 105 tests; a subsequent socket race fix was covered by a targeted
rerun. Its real WebSocket test starts ordinary Files workers with Agent imports
blocked, ends the child fixture, and verifies actual upload, exact disk bytes,
chunk/push downloads, range reads and handle cleanup. Local placement replaces
Fleet provisioning in that fixture. Full desktop/live Fleet acceptance, remaining
platform endpoints, and P2–P7 still remain; these changes are not deployed.

## Playground backend ownership (P1 follow-up)

Playground inference, modality handling, temporary media and six RPCs now live in
`apps/llm_playground`, with an ordinary process manifest and a versioned interface.
`pantheon.apphost` can construct and serve this backend without importing Agent,
ChatRoom, Team, factory or memory code. ChatRoom inherits the App API temporarily;
the former module paths are identity-preserving aliases. PlatformService does not
host Playground business logic. The RPC inventory verifies signatures against
the correct owner rather than assuming every extracted RPC belongs to platform.

Each standalone worker reads its fixed project's Settings in an isolated mapping
and snapshots routing before a call. Credential/configuration reads run outside
the RPC event loop. Routes retain explicit platform-budget/BYOK selection and
provider-specific endpoints; an OAuth refresh mutates only its request's copy.
No source is silently substituted. The legacy host retains its own settings.
The App owns its Fleet model client's pools; stopping one worker does not close
another App's client. The legacy adapter continues borrowing its host client.

Shutdown revokes admission, cancels and awaits accepted observers, closes owned
connections and removes temporary media. Cleanup itself survives a disconnected
waiter. An early cancellation still prevents submission while settings load.
Cancelling an observer is distinct from explicitly cancelling a durable Fleet
job. The implementation retains the original job reference and does not replay
it. These are graceful cleanup guarantees, not a promise after forced process
termination. Generic supervisor stop behavior remains part of lifecycle testing.

Verification: the broader Playground/model/App-host regression ran 142 passed,
1 skipped. After owned-client and shutdown checks, 82 targeted tests passed.
A real authenticated NATS subprocess launches the manifest via `apphost`, blocks
all Agent imports, and calls a local OpenAI-compatible HTTP fixture through both
BYOK and platform-budget routes. It verifies exact endpoint, credential, model,
messages, usage, cancellation-before-submission, and media upload/read. Scoped
project tests prove credentials do not leak through the host environment. Two
existing registry failures (model-service's missing tools face and file-manager
signature metadata) and the existing model route-probe HTTP fixture warning were
reproduced in the untouched baseline; they are not reported as passing checks.

Frontend routing is deliberately still transitional. The Fleet resolver currently
forwards bus coordinates, but not the per-App platform virtual key, OAuth store,
or Hub/Fleet model credential. The next step must implement generic authorized
credential/configuration delivery and pinned client bindings before switching
Playground off ChatRoom. This change does not widen the resolver's environment
allowlist. The frontend still ships in Atrium; no paired independent release,
live deployment, cross-node credential acceptance, or M1 completion is claimed.

## Platform settings reload (P1 follow-up)

Desktop Settings can now call `reload_settings` on the independent platform.
It refreshes an isolated view of the selected project's configuration off the
RPC event loop, preserves deployment environment precedence, and returns its
explicit platform scope/project. It neither mutates process credentials nor
claims to reconfigure other running Apps. Subsequent platform metadata reads
continue resolving fresh project settings. The legacy ChatRoom override retains
its existing process-local reload. Failure responses do not expose parser or
credential contents.

The desktop displays the endpoint's reload message instead of always claiming
all settings were applied. The settings file's literal NUL sentinel is written
as a JavaScript escape, preserving its value while fixing a Vue parser warning.
App-wide configuration notification and credential delivery remain P2/P3 work;
this endpoint is not a global broadcast or an App restart.

Verification: 26 model-directory/project/real-RPC tests passed, including scoped
reload, deployment-key precedence, redacted failures and the legacy override.
Both authenticated NATS subprocess variants exercised the new RPC with Agent
imports prohibited, one after its optional child exited. UI type checking passed.
The touched Settings component retains its existing explicit-any lint finding;
its pre-existing NUL parser error is fixed. Nothing has been deployed.


## Generic prepared App configuration (P2 foundation)

Fleet now delivers a declared, immutable configuration to an exact prepared App
start. This is generic lifecycle functionality with no Agent/Playground App-ID
exceptions. The owner sends values and endpoint-pinned node credential references
through `configure`; Fleet validates the installed component declarations and
materializes private per-component JSON before hooks/processes or consuming the
prepared generation. Public ledger/status contain neither values nor references
nor resolved keys. Containers use an individual readonly mount; native Apps keep
the existing same-OS-user trust boundary. No broad host environment, credential
vault directory or Fleet/Hub master credential is added to App launches.

Generation/preparation identity, immutable retries, old-Runner ledger fencing,
blocked-stop retention, cancellation, restart and dead-process cleanup are covered.
The standard-library Python SDK validates the injected component identity and
fails on stale data instead of selecting ambient credentials. The Python owner
coordinator snapshots configuration before awaiting transport. Authenticated NATS
and job HTTP dispatch use the same Manager command. Job bootstrap stops at a
prepared instance for configured Apps and keeps control available, allowing the
owner to configure/start through the ordinary protocol; other Apps retain their
existing startup behavior. See `fleet-app-lifecycle.md` for the wire contract.

Verification on 2026-10-03: lifecycle, Runner, job-worker and node-credential Go
regression suites passed; targeted configuration tests passed under `-race`.
Actual owner-scoped NATS and job HTTP tests launch native child processes; a
separate child loads the shipped Python configuration reader, verifies its
endpoint/key, and checks that an unconfigured sibling and both children do not
inherit configuration/master host keys. Missing keys, wrong endpoints, stale
identities, modified/partial files, blocked stop and Runner restart are exercised.
The Python configuration/lifecycle suite passed 35 tests. Windows lifecycle tests
and the Linux job worker cross-compile; Windows execution and actual Linux/HPC
allocation tests are still required. Container mount/identity checks are local
unit checks, not a running-container credential acceptance result.

Remaining: issue/revoke scoped consumer grants, provision authorized credentials
across nodes/jobs, bind providers and sessions, and wire real Playground/Agent
launches to these contracts. Node-secret references currently refer only to the
existing target node's local API vault; they do not automatically transfer a
platform budget virtual key or OAuth token. No live deployment, Playground GUI
cutover, Agent packaging, completed P2 milestone or desktop-without-Agent gate is
claimed by this component work.

## ToolSet App process lifetime (P3 foundation)

The ordinary `pantheon.apphost` now owns setup, admission stop, App shutdown
policy, accepted-call drain, cleanup and owned transport disposal. SIGTERM/SIGINT
set the same stop event; repeated signals do not interrupt cleanup. Partial setup
and worker creation failures unwind once. Startup and cleanup errors remain
failed exits, including when both fail. The supervisor still owns the hard stop
deadline; forced termination is not reported as a graceful cleanup. Embedded
ToolSet callers retain their existing lifetime ownership.

TCP workers track accepted calls independently of client connections. Shutdown
removes discovery and closes admission, waits for accepted mutations/replies,
then closes clients. Avoiding `Server.serve_forever()` cancellation's implicit
client wait fixes the Python 3.12 deadlock that otherwise prevents reaching the
drain phase. NATS workers reject admission before draining; their owned backend
flushes queued replies before closing. Failure/exit of either dual-channel worker
also ends its sibling. App-hosted services cannot enable the old Agent-specific
`_restart_in_place` RPC; Fleet remains the restart owner.

Playground uses the optional `begin_shutdown` hook to cancel its owned observers
before RPC drain, while preserving the distinction from upstream durable video
jobs. Other Apps default to finishing accepted work before cleanup. This does not
define resource ownership, authorize consumers or replay interrupted operations.

Verification on 2026-10-03: real macOS CLI subprocesses and authenticated NATS/TCP
RPCs exercise stop during a synchronous write, result delivery, rejection of new
calls, released sockets, removed discovery, setup failure, cleanup failure and
repeated stop signals. Tests prohibit Agent imports in the subprocess. Real
Playground inference/media RPC tests pass with both SIGINT and SIGTERM. Agent
host/Playground suites pass 32 tests; 50 platform, bootstrap, App-spec and ToolSet
regressions also pass. Agent packaging, consumer grants, ordinary versioned launch, live deployment and the
full M1/M2 gates remain pending; this is host lifetime evidence only.

## Scoped dependency RPC grants (P2 implementation, 2026-10-03)

Hub now has owner-authenticated dependency issuance/revocation routes, backed by
the existing Controller and Fleet lifecycle RPC. The resulting opaque bearer is
limited to a pinned consumer and provider generation, a method allowlist, allowed
caller argument names, owner-bound arguments, a timeout ceiling and an expiry of
at most 15 minutes. Consumer and provider may be on different nodes in the same
Fleet. No Fleet management key, NATS credential, provider RPC key or delegated
provider JWT is returned to the consumer. The provider's Runner injects its
existing internal RPC credential on the fixed `/rpc` invocation.

Every admission checks the consumer's exact revision/generation and actual owned
process/container liveness, then rechecks state after probing. The same command
works through native NATS and the job worker's owner-authenticated control route.
Issuance may target the next generation of an exact prepared start; that grant
cannot invoke anything before that generation becomes ready. Cancellation and
restart advance generations, so old grants cannot silently attach to a new run.
Old nodes explicitly reject the new command; there is no broad credential fallback.

The gateway accepts only server-to-server POST `/rpc` for these bearers. Browser
cookie exchange, other HTTP paths, query routing, streaming, media and management
are unavailable. It rejects duplicate JSON keys, undeclared arguments, replacement
of bound arguments and oversized calls. Revocation is owner-scoped and prevents
new admissions, including a request waiting on a liveness probe. Already accepted
mutations may finish; an unavailable provider produces an unknown-outcome error,
not an automatic replay. Gateway restart invalidates its in-memory grants.

`pantheon.apps.dependency_client.DependencyClient` consumes an explicit
`RuntimeCredential` from the ordinary configuration snapshot. It uses HTTPS with
normal certificate verification, ignores ambient proxy/login/Fleet credentials,
follows no redirects and performs no retries. These are scoped **bearer** grants,
not proof-of-possession identities. Their resource boundary depends on the
provider enforcing the owner-bound workspace/session arguments; this mechanism
does not magically sandbox arbitrary paths or create isolated sessions.

Verification: all six touched Go package suites passed (Controller, gateway,
transport, lifecycle, Runner, job worker). Actual authenticated NATS connects two
node Managers with native Python App processes; the provider requires its private
RPC key. Tests exercise successful bound calls, stopped/restarted consumers,
stopped providers, prepared starts, revocation races and lifecycle state races.
Existing Runner NATS and job HTTP acceptance tests also exercise `check_instance`.
Hub owner/scope/validation regressions passed 23 tests; the consumer SDK passed 12
tests. Targeted Go tests pass under the race detector, and the Windows lifecycle
test binary cross-compiles. These are local tests, not a live HPC/Windows rollout.

Remaining P2 work: declaration-to-interface compatibility checks and a binding
coordinator; secure grant provisioning across nodes/jobs; renewal/replacement for
long-lived consumers; session ownership and cleanup. Issuance APIs are implemented
but are not yet wired into automatic App dependency resolution. The existing
node-local credential vault can hold a grant for a prepared configuration; no new
remote vault-write API or master credential distribution is implied. Playground
and Pantheon-Agent packaging/cutover, GUI extraction, live deployment and M1/M2
acceptance remain pending.


## Initial dependency assembly (P2 follow-up; local, not deployed)

The owner platform now exposes `fleet_app_start_dependencies` as a hidden
management RPC. A caller supplies an exact prepared consumer, its preparation and
stable start-operation IDs, explicit provider bindings, method/argument policies,
and declared component inputs. This does not discover providers or auto-install
missing dependencies; it establishes the binding/start portion of P2.

Native Runner and job-worker control both expose `app_manifest`. The Manager
reads the SHA-256-verified installed archive, checks App identity/version against
its execution declaration, and returns its manifest and definition. It does not
read an editable extracted file or the catalog's current version. The owner
compiler checks declared `dependencies`/`uses`, provider interface versions,
method membership, required arguments and credential aliases before requesting
any grant. Stable version ranges `*`, exact, `>=`, caret and tilde are supported;
unsupported/prerelease ranges fail explicitly. Every dependency needs a binding;
optional dependencies and replacement-provider selection remain future work.

`DependencyStarter` journals one immutable recipe per node/start-operation ID in
a private local platform directory outside App working copies. This directory is
excluded by the existing platform snapshot whitelist. It pins the owner, provider
and consumer identities, records exact grants before delivery, retries unchanged
configuration after a lost acknowledgement, and submits a stable durable start
operation. Restart/retry observes an already accepted operation rather than
reissuing grants or starting another generation. The owner journal clears bearer
keys after the node acknowledges its immutable configuration. A cancelled caller
cannot release the local attempt lock while a checkpoint is still being written.
This is a single-platform-owner local journal, not a cross-replica coordinator;
loss of that storage during an unacknowledged configuration requires inspection
and cancellation/re-preparation, not inference that an operation failed.

The node accepts dependency credentials only through the owner configuration
channel and only under declared credential aliases. Grant hash, endpoint,
expiry, Fleet, node, instance, revision and upcoming generation are checked.
The private source is materialized into the existing App SDK snapshot; no
provider secret, Fleet key or ambient model key is handed to the consumer. An
expired grant fails before hooks/process creation and before consuming the
preparation. First use writes ledger fence 7 so an older Runner cannot silently
ignore the dependency configuration. The wire protocol remains 1. Capability
markers are `app-manifest: 1` and `app-dependency-config: 1`.

An actually live starting consumer can call its provider before its own
readiness succeeds. This prevents circular readiness when initialization requires
a dependency. Prepared reservations and resource intents with no live process
cannot call; stale, dead, failed, stopped and draining consumers remain denied.
A starting instance has ReadyGeneration zero and its exact new generation; a
ready instance still requires all owned component resources alive. Admission
rechecks state after the liveness probe. Providers themselves must be ready.

The current platform coordinator requires POSIX private-file ownership checks;
Windows consumer configuration uses Fleet's existing ACL implementation. This is
not a claim of a Windows coordinator or real Windows/HPC deployment acceptance.
Initial grants last at most 15 minutes. Renewal, session acquire/release,
long-running provider leases, authorized stream/binary channels, gateway restart
recovery and integration into the ordinary launch UI remain P2 work. Agent cannot
be switched to this path as its final long-lived runtime until those are done.

Verification includes failure-before-grant contract tests, lost configure/start
acknowledgements, immutable-recipe conflicts, expiry, local concurrent attempts,
cancellation during checkpoint, private storage and snapshot exclusion. The
Controller integration runs the actual Python assembly and consumer SDK against
an authenticated NATS bus, independent lifecycle Managers, native processes and
a real TLS gateway. Consumer readiness requires the provider response. Local
management HTTP fixtures replace Hub authentication, and test DNS/CA settings
route the wildcard host to an ephemeral TLS listener; they do not replace RPC,
process execution or gateway admission. Runner NATS and job-worker HTTP tests
also exercise installed-manifest reads. No live deployment is implied.

Verification for this change: 81 Python tests passed across dependency assembly,
App lifecycle/configuration/consumer SDK and platform service/real RPC checks.
The complete Controller, lifecycle, Runner, job-worker, gateway and transport Go
packages passed. Targeted race checks passed for the four execution/control
packages. Windows amd64 lifecycle tests cross-compiled successfully; they were
not run on Windows. These are local component/integration results, not P2/M2 or
whole-plan completion. No runtime, Hub, UI or Fleet deployment was performed.

## Agent lifetime on the ordinary App host (P3 foundation, 2026-10-03)

ChatRoom now inherits the full ToolSet `run` contract, including host-owned
cleanup. Its redundant override previously rejected `cleanup_on_exit=False`,
so the generic App host could not start it. Agent-owned admission and cleanup
live in `pantheon/chatroom/lifecycle.py`; they do not add Agent concepts to Fleet.

Stopping closes admission to new chat calls, including internal notifications
and channel callers. Foreground runs retain ownership through their final
persistent save. User steer messages accepted before stop drain as continuations
after the preceding save; the local continuation permission is consumed on entry
and is not an RPC parameter or inherited by descendant tasks. Notification
callbacks cannot create new turns while stopping. Queued continuations register
ownership before getting CPU, closing the cleanup/admission scheduling gap.

Background tool tasks may outlive a chat response; shutdown waits for their real
completion rather than canceling them and declaring their side effects stopped.
Then it cancels/awaits tracked observers, shuts down each plugin once, joins the
memory-routing initializer and channel threads, and closes the owned event
transport. Channel start admission closes under the same lock as registration.
Repeated cleanup observes the same result. The supervisor still owns the hard
deadline; this is not arbitrary in-flight Run migration or tool replay.

The final chat save previously used `shield` alone: caller cancellation could
return while the save continued detached, without setting Thread's completion
event. Repeated cancellation now waits for the save to settle. A failed save
raises to the caller and makes Agent cleanup fail instead of reporting a clean
data drain. Plugin failure does not skip remaining cleanup.

Verification: 153 tests passed across Agent/App-host lifecycle, title generation,
Claw, chat creation/recovery, background tools and independent platform RPCs.
The new real macOS CLI/TCP cases construct ChatRoom and execute its real Thread
and memory path through `pantheon.apphost`, exercising SIGTERM during a run,
rejected new RPCs, queued steer drain, background writes, final persisted state,
one-time plugin shutdown and discovery removal. Only team model work and external
warmups are fixtures; there is no paid LLM request. Other cases cover repeated
cancellation during a blocked synchronous save, disk failure, partial cleanup,
closed event transport and a real channel thread that calls back to the Agent
loop. Old title-test doubles now include the real Thread completion event and
allow the final save to execute.

The production `agent` manifest is deliberately still frontend-only. The process
fixture is not a shipping Agent package: ChatRoom still imports platform APIs and
Playground, uses legacy project/configuration facilities and lacks the final
declared dependency/domain API boundary. Immutable Agent artifacts, scoped runtime
bindings, data migration, independent GUI packaging, live deployment and the full
M1/M2 gates remain required. No live services were changed by this verification.

## Agent domain service boundary (P3 follow-up; not deployed)

`pantheon.chatroom.runtime.AgentRuntime` now owns the existing execution,
conversation, template, memory, channel and token-accounting methods. It inherits
only the Agent lifetime and ordinary ToolSet host. `room.ChatRoom` is the legacy
composition of that same implementation with the platform/Playground API mixins;
there is no second chat engine. All 134 baseline legacy RPC signatures remain.
The core exposes the 52 Agent-owned business methods from the ownership inventory,
not Fleet, Store, OAuth, project registry, arbitrary App proxy or global model
configuration RPCs. `set_active_project_for_chat` is classified as a legacy mixed
platform-selection operation: a new host reads `get_chat_workspace` and selects
its project separately.

An explicit `AgentEnvironment` supplies a read-only project view, templates,
settings access, dependency preparation, agent construction and model validation.
The core cannot construct a default global environment. The legacy facade supplies
the old registry/settings/resolver behavior, while future App bootstrap must
supply scoped bindings. Local Agent workspace operations now consult this supplied
settings accessor. This is a composition boundary, not a filesystem sandbox or a
replacement for dependency grants. Model/Team/plugin internals still contain
process-scoped settings and require follow-up work before independent deployment.

Importing a ChatRoom submodule no longer eagerly imports the legacy facade or CLI.
Agent startup no longer warms the combined host's model directory, scans transfer
instances or reports host-wide activity. Its health counts its own running turns
and unique background managers (including an embedded default team). Legacy health
retains the aggregate behavior. OAuth waiters and platform/Playground shutdown
hooks now belong to that facade; core cleanup only owns Agent resources.
Token accounting moved from the terminal's utility module to an Agent module,
with a compatibility export for the terminal, eliminating an indirect legacy
bootstrap import when the Agent GUI requests context statistics.

Verification: 269 tests passed across the domain boundary, real App host, legacy
RPC signatures, independent platform APIs, Claw, Files/PTY routing, event delivery,
conversation recovery and background work; 9 further project/routed-memory tests
passed. The four real CLI/TCP drain cases cover both legacy and core composition,
each with/without an accepted steer turn. Core processes prohibit imports of the
legacy host, platform controllers, project registry, Playground and terminal UI;
they run without the Playground package in their App catalog. Wire requests for
platform methods fail, while token statistics, real Thread execution, persisted
memory, background writes and signal-driven drain succeed. Model work and core
composition are deterministic fixtures, not paid LLM/provider acceptance. Two
explicit environments additionally create independent conversation/workspace
state and use only their supplied dependency callbacks, propagating failure
without an owner-resolver fallback.

Remaining: compose the actual immutable Agent backend artifact from declared,
renewable dependency grants; migrate process-global execution/configuration
internals; implement config/instance/Run APIs and durable replay; migrate storage
with a single writer; extract GUI; deploy and complete live cross-node gates.
The ordinary `agent` manifest and all live services remain unchanged this turn.

## Provider resource sessions (P2 follow-up)

`fleet/appsvc.SessionRegistry` provides a reusable, Agent-independent
`resource-session@1` interface. It accepts opaque owner references and stable
acquisition IDs; duplicate acquisition returns the original receipt rather than
starting a second resource. Renewals preserve identity, cannot shorten expiry,
and cannot resurrect released/expired/lost/failed resources. Cleanup failures
retain `closing` state for retry. Provider callbacks determine resource creation,
liveness and bounded release; no Shell or Agent branch exists in the registry.
Borrowed durable resources must release attachments rather than delete user data.

The Shell App implements this contract over its existing authenticated NATS
surface, adding four hidden manifest-declared control methods. Managed shell IDs
are checked against lease state on normal tool admission. The provider runs its
own expiry sweep, so abandoned owners need not make another request for cleanup.
Owner references/session IDs are not credentials: ordinary consumers still need
method-scoped grants with their specific shell ID bound by the coordinator.

Verification: all 11 Shell tests passed with the race detector, including actual
authenticated NATS calls and real shell subprocesses. A real 30-second lease
expired and its shell exited after 30.003 seconds without owner RPCs. The generic
SDK tests cover concurrent retries, owner mismatch, expiry, failed cleanup retry,
no resurrection, capacity and one owner's slow cleanup not blocking another's
renewal. App manifest parsing and interface-method compilation passed; the Fleet
command package also passed its race-enabled tests. After making sweeps skip
already-busy entries, SDK tests and the short Shell suite were rerun.

This is provider-side support, not the completed P2 gate. Platform durable
resource-owner registration/maintenance, dynamic Agent instance binding,
termination-triggered release and consumer-grant integration still need wiring.
Go Shell currently uses its builtin App transport; exposing it as a standalone
managed App service remains necessary for the exact-generation dependency path.
Tombstones are in memory, bounded to 4,096 per provider process; exhaustion rejects
new acquisitions rather than forgetting an old intent. Durable receipt retention,
restart fencing and complete detached/background process-tree cleanup require
further acceptance. No live rollout or other provider adoption is claimed.

## Shell isolation prerequisite (P2 follow-up)

The existing Go Shell App used to pick any idle shell when the current chat's
shell was busy, potentially borrowing another owner's cwd/environment. It now
returns a structured `busy` result on the original shell; deliberate parallel
work uses explicit `new_shell`. A per-session admission mutex also excludes
concurrent command/output readers, so a second request cannot overwrite a pending
completion marker or steal the first command's output. A timed-out command keeps
running and must be drained before the next command. Closing a shell clears its
legacy chat mapping; an explicit closed/exited shell ID cannot create a replacement.

Eight Shell tests pass with the Go race detector on macOS, using real shell
processes. They cover distinct cwd/env under a timed-out owner, concurrent command
and output-reader rejection, preserved completion/output and explicit lost-session
failure. These provider changes are not yet generic resource leases: automatic
owner session acquisition, renewal, release and the Agent composition wiring
remain required. The legacy owner-authenticated service still trusts supplied
shell IDs; scoped dependency grants must bind them for ordinary consumers.

## Owner-maintained dependency grants (P2 follow-up)

The platform now owns a periodic dependency maintainer. Initial assembly saves
non-secret grant receipts before submitting the prepared consumer and clears
bearers from its journal. A replacement platform process on the same private
state root resumes those receipts. Exact live consumer/provider generations can
renew the same grant through an owner-only Hub PATCH; identities, method scopes,
bound parameters, timeout and token do not change. Apps cannot renew themselves.
The temporary legacy ChatRoom composition runs the same generic maintainer;
AgentRuntime does not own it. Platform shutdown cancels maintenance without
revoking still-live consumers.

Renewal updates the gateway grant atomically and cannot undo revocation or
expiry, including a concurrent revoke during node probes. A call admitted after
renewal still accepts the unchanged token. HTTP 410 means the original grant
is no longer renewable; node-generation unavailability returns 409. A lost
acknowledgement may leave the stored expiry stale, so local time alone does not
retire a receipt. No failure path mints replacement grants, reconfigures a live
App or replays tools. Authoritative stopped/replaced consumers cause revocation;
node outages and foreign-owner snapshots defer maintenance instead.

Verification includes real Python owner processes communicating over
authenticated NATS with a native consumer/provider and the TLS dependency SDK.
A fresh owner process renews the live consumer grant, the consumer calls again
using its unchanged credential, and stopping the consumer causes receipt-driven
revocation and a rejected renewal. Local management endpoints replace Hub's auth
wrapper in that integration; Hub authentication/response validation is tested
separately. 116 Python regressions and 28 Hub tests passed; the App gateway,
Controller and lifecycle Go packages passed with the race detector. Unit tests
advance the maintenance clock across multiple grant TTLs
and cover lost acknowledgements, scope tampering, lock contention and transient
node failures. This is not a multi-minute live-deployment soak test.

Remaining: generic stateful session acquisition/leases, dynamic per-instance
bindings, durable gateway grant recovery, distributed owner fencing, final App
packaging, and live acceptance. Existing pre-receipt journals are not silently
adopted. A gateway restart still loses its grants and requires explicit recovery;
this change alone does not make P2 or the full migration complete.

## Explicit Agent tool bindings (P2/P3 follow-up)

`DependencyToolProvider` adapts the existing HTTPS dependency SDK to Agent's
ToolProvider interface. The owner supplies caller-visible function schemas and
the component's endpoint/key pair. Tool menus need no discovery RPC. Calls enforce
the declared method and top-level argument set locally; the Fleet gateway remains
authoritative for permissions, bound session/workspace arguments and generations.
No context-variable dictionary, Agent callback, owner credential or global
resolver is sent. The ordinary App RPC success envelope is unwrapped to the tool
result; a provider exception or malformed response is an unknown outcome, never
an automatic retry. Model-schema formatting cannot mutate the admission schema.

`create_agent(..., tool_bindings=AgentToolBindings(...))` chooses the explicit
path, including when supplied bindings are empty. Required missing tools fail
assembly. Configured MCP entries must also be explicit dependency providers;
the path neither reads global MCP settings nor obtains an unrestricted gateway
URI. Explicit agents require exact provider-qualified tool names, while local
engine/plugin functions remain callable by their exact names. Legacy factories
without supplied bindings retain their existing discovery and partial-team
behavior. Explicit team assembly requires a mapping for every config identity
and fails instead of silently omitting a required member. Agent templates cannot
supply their own binding objects.

The composition root can use `bindings_from_runtime_configuration()` with the
already generation-validated `RuntimeConfiguration`. It reads the following
owner-supplied value and resolves credential aliases only from that component's
credential snapshot (schemas below are abbreviated):

```json
{
  "agent_tools": {
    "protocol": 1,
    "agents": {
      "config-id": {
        "toolsets": {
          "shell": {
            "credential": "shell_a",
            "timeout_seconds": 60,
            "functions": [{"name": "run_command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}]
          }
        },
        "mcp_servers": {}
      }
    }
  }
}
```

This is consumer-side assembly, not session acquisition or automatic schema
projection. The composition root must acquire a separate grant/session for each
stateful Agent instance, not reuse a config-id mapping across independent teams
and accidentally share Shell state. The current MCP gateway exposes management
and an unrestricted URI; scoped MCP execution still needs its provider contract.
The test's explicit MCP binding is an RPC fixture, not proof that existing MCP
servers have been migrated. Optional capability policy, dynamic instance binding,
owner/lease cleanup and final App bootstrap remain unfinished. Grant renewal is
implemented in the follow-up below.

Each provider bounds concurrent transport calls. Cancelling a caller repeatedly
waits for its accepted HTTPS request to return before cancellation propagates.
Shutdown rejects queued/new calls and drains accepted transports. A timeout can
still mean unknown remote effects; transport drain is not remote process death.
Agent runtime cleanup closes deployment-owned providers once after background
tools and plugin shutdown, leaving legacy shared singleton providers alone.

The dependency compiler now applies the existing interface-version default of 1
when reading raw manifests. Shell's actual catalog manifest omits that field;
previously it failed assembly despite being valid under the App schema. Explicit
invalid versions and requests for unsupported interface versions still fail.

Verification: 139 tests passed across the new Agent binding/TLS suite, dependency
assembly and SDK, configuration snapshots, legacy provider discovery/recovery,
concurrent team assembly, Agent core boundary and real App-host shutdown. The TLS
suite uses real Agent factories, tool menus/routing, configuration-file loading,
SDK serialization and certificate verification. The HTTPS server is a controlled
provider/grant fixture: tests prove credential selection, no ambient discovery,
denial/no-replay, separate supplied session bindings, cancellation/queue drain and
cleanup ordering. They do not prove live Fleet authorization, automatic session
creation, paid model calls or the complete Agent App. No deployment occurred.
