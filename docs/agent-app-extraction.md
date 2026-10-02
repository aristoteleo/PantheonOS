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
- Hub: the group-container worktree; revalidate its HEAD before editing.
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
| P1 | Move platform RPCs out of ChatRoom; connect desktop independently; stop Agent and exercise Files, Terminal, Fleet, Store, Jupyter, Browser, Model Services | Fleet/model management, generic App calls and project registry extracted into independent host; desktop/Hub cutover pending |
| P2 | Generic owner references, interface bindings, grants, sessions and leases; two Agents have independent Shell state and share stateless files | Pending |
| P3 | Package Agent runtime, configs, instances, conversations, runs and replayable events; preserve inference routes and cancellation | Pending |
| P4 | Package GUI; independent client/store per deployment; remove static Agent imports from Atrium; support App intents | Pending |
| P5 | Inventory, backup, import and validate data; fence old writer; preserve project asset references; test failed migration recovery | Pending |
| P6 | Publish one frontend/backend release; isolated candidate, drain, schema checks, cutover and rollback; self-edit demonstration | Pending |
| P7 | Replace Hub brain-specific bootstrap with generic App deployment; remove transitional paths; complete cross-node acceptance | Pending |

M1 completes P0/P1, M2 completes P2/P3/P4, M3 completes P5/P6, M4 completes P7.
No milestone is complete merely because its files or manifest exist.

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
descriptors load a separate compatibility adapter. Hub does not yet advertise or
provision this service, and root GUI/auth/bootstrap dependencies still need work.
Current Hub topology explicitly selects the node hosting `chatroom`; the runtime
entrypoint likewise execs ChatRoom from `PANTHEON_NODE_APPS`. Both need coordinated
migration before enabling platform discovery in a live environment.

For development, with the same authenticated bus/Fleet environment used by the
platform deployment and a distinct service seed:

```sh
python -m pantheon.platform --id-hash USER_PLATFORM_SERVICE_SEED
```

This does not by itself switch Hub or desktop discovery. No production deployment
has occurred. `docs/agent-app-rpc-inventory.json` records the original 134 public
RPC signatures; remaining owners and call sites must be migrated before M1.

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
