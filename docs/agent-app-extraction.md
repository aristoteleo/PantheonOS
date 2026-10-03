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
| P2 | Generic owner references, interface bindings, grants, sessions and leases; two Agents have independent Shell state and share stateless files | Prepared per-component configuration delivery implemented locally; consumer grants, bindings, sessions and live acceptance pending |
| P3 | Package Agent runtime, configs, instances, conversations, runs and replayable events; preserve inference routes and cancellation | Ordinary ToolSet App host shutdown implemented locally; Agent packaging and domain APIs pending |
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
