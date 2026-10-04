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
| P2 | Generic owner references, interface bindings, grants, sessions and leases; two Agents have independent Shell state and share stateless files | Native Agent/allocator/Shell/shared-Files/migrated-MCP joint calls, logical-owner retirement and whole-consumer cleanup verified locally; complete Files surface, cross-replica fencing and deployed acceptance pending |
| P3 | Package Agent runtime, configs, instances, conversations, runs and replayable events; preserve inference routes and cancellation | Ordinary ToolSet host, prepared-config launcher, scoped model selection, owned App composition and namespaced data implemented locally; process chat/restart and ordinary HTTP hosting/event replay verified locally; final package, complete model/plugin delivery and revised domain APIs pending |
| P4 | Package GUI; independent client/store per deployment; remove static Agent imports from Atrium; support App intents | App-owned RPC/replay, private settings/skills and scoped Files verified in a real production-GUI browser gate; default App navigation implemented; scoped resource intents, persistent UI preferences, shipped packaging and Atrium cutover pending |
| P5 | Inventory, backup, import and validate data; fence old writer; preserve project asset references; test failed migration recovery | Stable project IDs, inventory, local fencing, resumable backup/import, saved-team identities, settings/dotenv/runtime API-key, global fallback, captured budget and captured MCP tool-App conversion plus startup admission implemented locally; OAuth, remaining MCP gateway features/default-template/configuration conversion, attachment resolution and distributed cutover/rollback acceptance pending |
| P6 | Publish one frontend/backend release; isolated candidate, drain, schema checks, cutover and rollback; self-edit demonstration | Paired POSIX release builder, locked dependencies and isolated Fleet installation verified locally; publication, migration/cutover/rollback and self-edit acceptance pending |
| P7 | Replace Hub brain-specific bootstrap with generic App deployment; remove transitional paths; complete cross-node acceptance | Opt-in platform startup and owner/profile-scoped Hub recipe delivery implemented locally; production provisioning, default cutover, legacy-path removal and cross-node acceptance pending |

M1 completes P0/P1, M2 completes P2/P3/P4, M3 completes P5/P6, M4 completes P7.
No milestone is complete merely because its files or manifest exist.

### Files-owned image previews survive Agent restart

The opt-in managed Files package is now v0.6.10 and provides `image-preview@1`.
Its preview RPC reads the configured provider workspace, rejects resolved paths
outside it, and never discovers an Agent image store or global settings. Raster
encoding reuses the existing Pillow implementation with bounded input bytes,
source pixels and concurrency; cancellation retains the worker slot until the
thread finishes, including repeated cancellation. GIF/SVG source bytes remain
intact within the byte limit. This is a preview-specific path check, not an OS
sandbox for all Files methods.

The native deployment fixture declares and grants this ordinary App interface to
the Agent GUI. Before and after a packaged Agent restart it decodes a real Files
workspace image at the requested size, and rejects missing/outside-provider
paths even when an image exists in Agent-private storage. Shared Files remains
running. Both Fleet identities run on this Mac; this is not physical multi-host
acceptance. Hub authorization/directory and model responses remain fixtures.

The production-GUI browser gate now uses the managed Files preview implementation
and checks rendered image dimensions, pixels and a Blob URL after actual file
selection. It still uses a fixture gateway and the legacy transfer implementation
for uploads/downloads; it does not establish full independent file-transfer
delivery. UI commit `ca131a6b` fixes concurrent preview requests invalidating each
other, ignores obsolete image loads, and rechecks thumbnail file signatures so
reopening a changed file does not keep an hour-old image. Explicit refresh still
invalidates the cache. Cache partitioning by App/workspace remains separate work.

Validation: 112 Python regressions passed, with one existing OpenAI-key-dependent
test skipped; 10 image-cache and 5 AgentViewFiles tests passed. The rebuilt Agent
GUI browser gate passed in 7.23 seconds. Logs are
`/tmp/agent-preview-regressions-final.log`,
`/tmp/agent-preview-ui-regressions-final.log`,
`/tmp/agent-preview-files-ui-final.log` and `/tmp/agent-gui-preview-complete.log`.
The final authenticated NATS/native Fleet gate passed in 123.816 seconds,
including `NativeAgentDeployment` (110.64 seconds) and `ResourceSessionOwner`
(4.30 seconds); its log is `/tmp/agent-native-preview-complete.log`.

This closes the GUI's missing image-preview provider capability. Model-input
`file://` history expansion and generated-image scanning still use Agent-local
paths, and require explicit resource resolution before cross-node attachment
cutover. Full transfer/document helper delivery, migration, publication, default
cutover and real Linux/HPC acceptance are still open. No live deployment changed.

### Restarted Agent releases renew dependencies while keeping shared Model Services

The generic owner API `fleet_app_restart_plan` reads a completed App deployment
and authoritative node state after explicitly draining/stopping selected Apps.
It preserves their artifact, scope, private configuration and vault references,
and returns a recipe for ordinary `fleet_app_deploy`. It does not stop processes,
issue credentials, or submit lifecycle work. In the recorded deployment graph,
every App whose bindings/configuration reference a restarted App must join the
restart, including consumer-generation policies in allocator/model-access Apps.
This is a graph-local check, not global reverse-dependency discovery.

Retained Apps must still be at their original ready generation. Their symbolic
references become exact bindings; selected Apps retain symbolic references that
resolve together to new generations during prepare/start. The planner refuses
incomplete original deployments, running or subsequently replaced selections,
remaining resources, changed retained providers and reuse of an already submitted
restart operation. Pending or lost replies resume the same deployment journal.
This path covers a normal explicit stop of the original generation; recovery of
failed operations and upgrades still require their own reviewed workflows.

The macOS native gate now restarts the packaged Agent, allocator and model-access
Apps from the actual Model Services bootstrap consumer journal. Fresh coordinator
objects resume that recipe. Agent instance/data identity stays stable, the live
generation advances from 2 to 5, imported history/member identity remains, and a
previously deleted conversation cannot execute again. The existing model catalog
and streamed inference work through the original Connector. The same shared
Files and MCP providers remain running; Shell allocates a fresh session for the
new consumer generation. Old sessions/grants remain retired, new grants carry
generation 5, and stopping the restarted Agent retires those resources too.
No new installation operation occurs and platform-budget acquisition stays at
one. The fixture counts 27 inference rounds for 13 real tool calls plus one plain
conversation across the two Agent generations.

Validation: 202 deployment/model-control/preset regression tests passed; the two
release-dependent cases initially skipped also passed after supplying the clean
release runtime and built assets. The expanded authenticated NATS/native Fleet
gate passed in 125.362 seconds. Logs: `/tmp/agent-restart-regressions.log`,
`/tmp/agent-restart-package-tests.log`, `/tmp/agent-native-restart.log`.
Hub directory/auth and model business output remain fixtures; actual Fleet
processes, gateways, Connector transport, tool calls, migration and restart are
exercised. No live deployment or default cutover was performed. Distributed
rollback, remaining migration/features and Linux/HPC acceptance remain open.

### Imported histories run in the native Fleet release without touching legacy data

The native deployment gate now imports two saved conversations (JSON and
JSONL/metadata) into the exact Fleet Agent data directory before ordinary App
startup. A separately assembled Agent artifact pins that destination's content
and scope identity; paired delivery must reproduce the same artifact. Import
uses the real fenced backup, explicit saved-member Model Services conversion,
captured MCP conversion and provider-node credential vault. Repeating the import
returns the same receipt.

The installed Agent reads both original histories and retains the imported
logical member identities. These migrated members, rather than newly created
test substitutes, perform the native gate's ten actual Shell/Files/MCP calls
through the original Model Service Connector and streamed model responses.
Independent Shell state, shared Files/MCP processes, per-owner grants, deletion,
consumer retirement and MCP child stop/restart remain checked. A final source
inventory must still match the backup after new Runs and conversation deletion.
Hub directory/auth, DNS routing and model business output remain fixtures;
Fleet managers, packaged processes, authenticated NATS and gateways are real.

This exposed two release/ownership defects. MCP admission imported an owner-side
conversion module omitted from the Agent package; its read-only binding check
now lives in the shipped admission module. Also, `chat()` still created
`<legacy-project>/.pantheon/images` on every Run. Independent Apps now use private,
per-conversation preview directories and an explicit size limit, without reading
ambient Claw configuration. Original CLI/Desktop compositions retain their
project-local preview path and channel-configured limits. An actual App chat
regression verifies preview emission, separate release directories and unchanged
legacy workspace contents with ambient settings access forbidden.

Validation: 61 targeted App/migration/release/image/runtime-boundary tests passed
(the optional GUI gate was deselected); the stricter preview isolation test also
passed independently. The macOS native migration/dependency gate passed. Logs:
`/tmp/agent-import-release-tests.log`, `/tmp/agent-preview-isolation.log`, and
`/tmp/agent-native-import-final.log`.

This is not production migration or a distributed rollback gate. Agent restart
with renewed generation-bound dependencies, remote-node preview production and
Files-based attachment resolution, remaining MCP features/OAuth conversion,
post-cutover rollback, and Linux/HPC acceptance remain required. No live user
data, installed Apps or deployment defaults were changed.

### Captured MCP bindings now admit legacy data and constrain actual allocation

`MCPConfigurationConversion.prepare_import` pairs an unchanged candidate with
its exact Fleet node, artifact, scope-derived instance and running generation.
It produces an explicit conversion accepted by `import_backup`. Private MCP
launch files stay in the fenced backup; the Agent data records only schemas,
default selection and provider identities. Credentials are provisioned through
the existing node vault before import can commit. The artifact is rechecked at
import and before committing; candidate/placement changes require a new review.

Saved members and YAML Agent/Team declarations are checked against the effective
MCP selection without rewriting tools or instructions. Missing provider coverage
blocks import before populating its target. Definitions or gateway features not
covered by capture, including explicit sampling configuration and uncaptured
auto-start servers, still require conversion rather than being silently dropped.

The committed migration state hashes its MCP binding record. Agent startup checks
the selected owner/node, exact MCP profiles and default selection before opening
its instance store. The ordinary instance provisioner additionally supports
provider-pinned profiles and rejects a different returned grant identity before
constructing a tool client. Candidate profiles use normal `$app` references,
resolved by the existing deployment coordinator; no MCP-specific lifecycle or
grant issuer is introduced. Configured Agent consumers also verify grant ownership.

Validation: 160 migration, deployment, model, allocation and launch checks passed
with native vault/release prerequisites enabled. The 16 focused import checks
cover configured-App reopen, real stdio tool calls, startup-record corruption,
provider substitution, incomplete member/template coverage and failed-vault
recovery. The joint case imports both model and MCP selections, preserves Agent
identity across reopen and uses the original Model Service Connector/HTTP/SSE
path for inference; it does not use the available direct BYOK endpoint. Its
model service uses a real TLS endpoint and the original Connector with a fixture
engine; MCP allocation/RPC is an in-process transport fixture.

The separate native NATS/Fleet gate passed in 81.72 seconds (native deployment
69.72 seconds), now resolving and checking the candidate's provider-pinned MCP
profiles in real App processes. This establishes the deployed dependency path,
not a production data cutover. Logs: `/tmp/mcp-import-admission.log`,
`/tmp/mcp-import-faults.log` and `/tmp/agent-native-mcp-pins.log`.
Full native migrated-data cutover, provider upgrade/rebinding after migration,
remaining gateway features/OAuth and production Linux/HPC acceptance are pending.

### Preserve legacy MCP selection before Agent dependency preflight

The private MCP capture now records effective `enable_mcp_tools` and hashes of
both project/global settings files. Conversion checks those hashes against the
fenced backup alongside the MCP configuration hashes. Captures lacking selection
can still build a provider, but cannot guess an Agent's automatic defaults.
The original factory-level `enable_mcp` switch remains an explicit input distinct
from the settings switch.

Candidate composition returns reviewed dependency defaults that preserve the
legacy factory's unified-MCP precedence: when `mcp` is selected, redundant named
MCP declarations are removed from the effective execution recipe. Saved templates,
prompts and conversation recipes remain unchanged. This is an explicit migration
policy; ordinary new-App defaults continue to retain independently named providers.

The App instance factory now prepares effective recipes before runtime preflight
as well as durable allocation. Previously preflight could reject `mcp:docs` before
the factory collapsed it into an authorized unified provider. Requirements are
computed per member, so a different member that still needs `docs` retains it.
Local `think`, `task` and `skills` tools remain excluded from endpoint preflight.
The optional environment callback leaves legacy CLI/Desktop compositions unchanged.

Validation: 105 migration/configuration/default/deployment/application checks
passed with native vault/release prerequisites enabled. After restoring the local
tool exclusion, all 55 focused runtime/application/launch/default checks passed,
including mixed-member MCP requirements and unchanged source recipes. The native
Fleet gate also exercises saved declarations containing `mcp`, `mcp:docs` and
`docs` while only the unified provider has an approved grant profile. Its final
macOS run passed in 84.67 seconds (NativeAgentDeployment: 71.73 seconds), recorded
in `/tmp/agent-native-mcp-selection.log`; all seven native Apps ran through the
same original Connector/scoped HTTP/SSE and dependency gateway as described below.

This established selection preservation for candidate composition. The subsequent
import-admission work above ties reviewed MCP bindings to the migration receipt;
unconverted gateway features still block import. OAuth conversion, final
cutover/rollback, production deployment and Linux/HPC acceptance remain outstanding.

### Migrated MCP runs through the native Fleet deployment and grant gateway

The opt-in native Agent acceptance now captures a real legacy stdio gateway,
fences and backs up its private configuration, explicitly relocates its command
and working-directory asset, provisions its secret through the actual Fleet
CLI vault, and builds the ordinary MCP App candidate. The NATS fixture nodes
use the normal `state/apps/<owner>` layout so the supervisor and local credential
CLI address the same vault. No installed user Fleet or legacy data is touched.

The generic release/deployment path starts seven independent App backends:
Agent, allocator, Model Services access, the original Model Service Connector,
Shell, Files and MCP. Agent calls reach the real scoped RPC gateway and a live
stdio child. Two logical Agent owners receive different MCP grants but observe
the same child PID and consecutive state changes. Their Shell sessions remain
separate and their Files provider remains shared. Deleting the first chat leaves
the second owner's MCP and file access intact. Stopping Agent revokes every
recorded dependency grant and releases its Shell sessions, while shared MCP and
Files remain independently callable through owner RPC.

Stopping MCP must terminate its stdio child. Re-deploying the same artifact with
the new expected generation uses ordinary prepared configuration and the same
vault reference; it creates a new child with preserved assets/environment and
fresh in-process state. Stopping that generation also terminates its child. The
candidate check does not create the migration target or admit a data import.

Validation: `TestDependencyRPCOverAuthenticatedNATSAndNativeApps` passed on
macOS arm64, including its ResourceSessionOwner and NativeAgentDeployment
subtests (83.01 seconds; the extended native acceptance took 70.39 seconds).
The model fixture counted exactly 21 inference requests: one plain completion
and ten real tool calls with their follow-up responses. The 18 MCP migration
configuration/composition tests also passed with the native vault gates enabled.
The authoritative log for this run is `/tmp/agent-native-mcp-acceptance.log`.
Hub directory/auth wrappers, local DNS routing and model output remain fixtures;
the lifecycle managers, NATS authorization, App processes, credential reader,
dependency gateway, original Connector/SSE path and MCP transport are real.
This does not establish Linux/HPC/Windows acceptance, production publication,
default Agent cutover, or the remaining migration/OAuth/sampling requirements.

### MCP candidates compose with ordinary Agent deployment and live bindings

`MCPConfigurationConversion.prepare_deployment` now builds the candidate and
returns its content-addressed artifact, normal provider-App recipe, runtime
dependency declaration, Agent profiles and allocator method policies together.
The owner explicitly names every captured provider's grant alias and selects
the candidate scope/generation on the reviewed credential node. Wrong-node,
reserved-name, incomplete/duplicate-alias and oversized configurations fail
before creating a package. The ordinary dependency limits are enforced rather
than truncating a captured provider's tools.

The outputs feed the existing Agent release builder and `compose_deployment`.
Captured provider views supply both the Agent's function schemas and the
allocator's exact callable method/argument lists. Named views retain their
captured scope; the unified `mcp` view is not silently substituted. All views
reference one ordinary MCP provider App, retaining the old gateway's shared
process lifetime. Each logical Agent owner receives its own grant; retiring one
owner revokes its admission without stopping the shared App or other owners.

Validation: 71 configuration/deployment/default-policy/lifetime checks passed,
including literal-only and native-vault credentials through this composition.
The tests build the actual paired Agent package with its MCP dependency,
advance the real generic deployment coordinator against a deterministic node
ledger, allocate two distinct logical-owner grants, and call a real stdio child
through Agent tool routing. Private key bytes never enter the deployment
recipe, Agent configuration or published package. Node scheduling and grant
issuance remain fixtures; this is not deployed Fleet gateway authorization
acceptance.

This is owner-side candidate preparation, not a second launcher or an import
receipt. Artifact staging, readiness on the actual target, final defaults,
sampling/OAuth conversion, and fenced data cutover remain required. No live
deployment or default switch was made.

### Captured MCP launch configuration builds an ordinary App candidate

The legacy gateway v0.8.0 adds hidden `export_migration_configuration`. It
captures the actual stdio executable, arguments, working directory, declared
and explicitly selected inherited environment, HTTP coordinates and observed
tool/provider contracts in one immutable private handoff. Stdio startup now
freezes executable resolution and cwd, so a later owner cwd/PATH change cannot
silently change a delayed reconnect. Unrecorded launch coordinates are rejected.
The RPC response contains only the capture path and fencing requirement.

`mcp_configuration_file` is inventoried without reading credentials and backed
up as opaque configuration. It replaces, rather than competes with, an env-only
MCP handoff. Captured user/project override hashes must match the backup;
changed sources also block later candidate build/provisioning. The converter
requires explicit target command/cwd or HTTP URL for every captured server,
so source-node paths and localhost are not implicitly reused on another node.
These are reviewed coordinates, not proof that target assets are installed.

`MCPConfigurationConversion` produces ordinary prepared MCP values, node-vault
credential references and a versioned tool package. Credentials use the existing
idempotent Fleet vault conversion. HTTP, empty-env and literal-only stdio
services need no artificial secret slot. Real stdio tests preserve returned
values after relocating the server and its working-directory asset; the child
does not inherit the Fleet owner key. Real HTTP MCP calls use the captured tool
contract. Source commands and credentials remain outside the package.

Validation: 110 configuration, handoff, tool-contract, native-vault, registry and
versioning checks passed. A second lifecycle/sampling/inventory/backup batch
passed 109 checks; its packaged-Agent model migration gate required the separate
release environment and then passed with that environment supplied. This gate
starts the packaged Agent and calls the original Model Service Connector without
giving the Agent a provider key. No live user gateway or node was changed. Candidate
publication, target launch-asset validation, allocator/default-provider binding,
OAuth/sampling decisions and complete Agent import admission remain pending.

### Legacy MCP tool names and result semantics survive App packaging

The compatibility gateway v0.7.0 exposes hidden
`export_migration_tools(providers)` metadata capture. It observes the actual
unified gateway and mounted server catalogs while holding the lifecycle locks.
It invokes no user tool. The compiler matches exact gateway names and source
schemas, rejects collisions/catalog drift, and retains the old provider's
prefix filtering (including overlapping names such as `docs` and `docs_more`).
The returned exports and provider views include no server coordinates, command
or environment. Unsupported or oversized contracts fail before packaging;
tools are never silently omitted from a selected provider.

The ordinary MCP package builder accepts `--legacy-catalog` with that captured
contract and produces v0.8.0. `migration-tools.json` versions the per-provider
function schemas with the exports. The build validates that each view matches
the package and has no unselected exports. For example, a gateway tool named
`docs_echo` remains callable by an Agent as `mcp__docs_echo` or
`docs__docs_echo`, according to its original provider selection.

Migrated exports explicitly select `result_format: legacy-agent`. They retain
the original MCPProvider structured-JSON/text extraction and one-layer JSON
unwrapping; MCP errors remain failures without retry. New exports keep complete
MCP content/metadata envelopes by default. This behaviour lives in the MCP App,
not a second Agent-specific execution service. Isolated packaged-process tests
exercise both modes, credentials, real stdio calls and child drain with imports
of Agent, settings and factory code forbidden.

Validation: 153 MCP, sampling, environment-handoff, native-vault, registry and
versioning checks passed with no skipped native gates. After final CLI/provider
view changes, all 63 targeted catalog and packaged-process checks passed. The gateway/Agent
comparison uses real FastMCP sessions and real Agent tool routing; its dependency
RPC link is an in-process fixture, so it does not prove deployed Fleet delivery.
The captured catalog and built package are not import receipts. Full original
server configuration, commands/assets and placement, default-provider selection,
sampling/OAuth decisions, provider publication and migration admission remain
required. No live user gateway or deployment was changed.

### App contracts distinguish required parameters from optional defaults

The registry failures recorded below are resolved. Python reflection now treats
the legacy funcdesc `not_defined` marker as absence of a default, and manifest
parsing normalizes old releases that incorrectly marked that sentinel optional.
The emitter preserves explicit null for optional parameters. Native Fleet also
reads older `required:false` parameters whose null defaults were omitted; false,
zero and empty-string defaults remain unchanged. The legacy bus wire format is
unchanged, and correcting this metadata does not create a false breaking change.

Auditing every reflectable App exposed additional stale declarations. File
Transfer v0.7.0 declares its existing `stat_path`; Fleet v0.8.0 declares its
existing HPC and node-update methods; Notebook v0.7.0 declares the existing
widget channel defaults. These additive contracts pass the minor-version gate.
Task's node annotation now matches its published optional-string spelling.
The registry checks the Model Service Connector's HTTP execution manifests on
every declared platform instead of demanding an Agent ToolSet interface. Shell's
hidden resource-session interface is checked alongside its ordinary tool face.

Validation: 29 parameter/wire/registry/versioning tests passed, including actual
Python calls, legacy-to-generated compatibility and rejection of a genuinely
breaking optional-to-required change. A wider batch passed 78 tests, including
isolated real Fleet/AppClient process calls, Agent process calls, Notebook
widgets and portable App serving. One separately configured live Fleet smoke
test was skipped because no external test Fleet was supplied. Native appsvc,
supervisor, Shell, PTY and node-files tests also passed. No live deployment or default cutover was made;
the migration and release acceptance requirements above remain incomplete.

### Original MCP launch environments can be captured and converted

The compatibility MCP gateway v0.6.6 now has a hidden migration control method,
`export_migration_environment(operation_id, servers)`. It captures the existing
stdio transport's saved environment after that server reached running state.
All declared fields and explicitly selected inherited fields are included;
unrelated owner-process variables are not. Changing the gateway/migrator's
current environment cannot replace the captured launch values. The immutable
owner-private file contains explicit absences, while the RPC response contains
only its path/scope and the continuing requirement to fence old writers.

The inventory accepts `mcp_environment_file` independently of the model handoff.
It checks permissions, canonical identity and separation from Agent data without
reading secret bytes; backup keeps them opaque. `MCPEnvironmentConversion` can
use this verified handoff to resolve legacy references and captured factory
environment declarations. Every captured variable requires classification and
credentials use the handoff path as provenance. Backed-up override declarations
must match the original gateway. Changed source files, scope mismatches and
explicit absent values block conversion; no ambient substitution occurs.

The acceptance test starts a real legacy stdio child, observes its original key
and inherited value, changes the owner's environment, exports/backs up the
original values, provisions an isolated native Fleet vault, then starts the
prepared MCP App child and checks the same behaviour. The new child receives no
Fleet owner key. Full server commands/placement, tool schemas/prefixes, resource
semantics and default/sampling choices still need conversion; this environment
handoff cannot admit the entire legacy MCP configuration for Agent import.

Validation: the expanded regression batch passed 274 tests with no skipped
native migration gates. Three existing registry checks failed outside the
changed MCP contract: the blanket requirement that every headless App have a
ToolSet face rejects the HTTP Model Service Connector; the Shell interface list
predates `resource-session`; reflection reports required Files parameters as
optional because of the legacy descriptor's undefined-default representation.
These are recorded follow-up work, not waived checks or a green suite claim.
After the final path-validation change, all 52 targeted handoff/inventory/backup
tests passed, including the native vault and real-child acceptance case.

Model Services reuse clarification: `ModelServicesAPI` is already shared by
`PlatformService` and the compatibility `ChatRoom`, and desktop `callChatroom`
routes directly to Platform when an independent platform descriptor is present.
That compatibility function name alone is not evidence of an Agent dependency.
Default deployment cutover and removal of legacy routes remain unverified; no
live rollout or traffic observation is claimed by this source-code checkpoint.

### Private MCP environment credentials reach the ordinary MCP App

Prepared stdio servers can bind individual environment variables to Fleet
credential slots, with an exact endpoint pairing. Only the private process
configuration receives the key; reviewed values, release files and conversion
descriptors retain references. Literal/credential collisions, unused credentials
and invalid bindings fail before a child starts. The MCP package builder now
uses native Fleet credential-field names and its 16-field group limit.

The owner-side `MCPEnvironmentConversion` reads fenced backup bytes from selected
user/project `mcp.json` files, preserves recursive override precedence, and
requires every declared environment variable to be classified as a literal or
API credential. It provisions through the existing Fleet vault's idempotent
ensure operation without rotating conflicting values or creating Agent data.
The selected source must be the effective override. Runtime `${VARIABLE}`
references and uncaptured factory/ambient settings still require a private
runtime handoff; the migrator never substitutes its own process environment.
This converter does not consume an entire MCP configuration or grant import
admission. Complete server/tool/default/sampling migration remains pending.

Validation: 75 targeted MCP environment, packaged-process and sampling tests
passed, including a real isolated native Fleet vault and stdio child. The wider
credential/backup/dependency regression batch passed 114 tests; its two gated
packaged-Agent migration cases were then run with their release prerequisites
and both passed. These batches overlap. No live node or user credential was used.

### Explicit deployment defaults preserve inherited tool dependencies

The independent Agent's prepared `agent.dependencies` configuration now accepts
optional `defaults: {"toolsets": [], "mcp_servers": ["mcp"]}`. Names must exist in
the corresponding approved profiles; the owner-side preset composer and launcher
reject unbound defaults before provisioning or creating Agent data. Omitting
defaults retains the prior explicit-only behaviour. These are ordinary allocator
dependencies, not a new discovery/connection path, and never enable local
`think`/`task` plugins or read `Settings.enable_mcp_tools`.

The dynamic instance factory snapshots defaults and applies them to the resolved
execution recipe before reserving the durable instance/revision. It preserves
saved template text and explicit tool order, deduplicates already-declared names
(including `mcp:name`), and keeps instance identity across default changes while
issuing a different revision/allocation operation. Empty template lists cannot
silently remove deployment-required dependencies. Each Agent still receives its
own clients and owned resource grants; shared providers remain explicit. The
standalone release includes the same lightweight validator/merger as the owner
composer, without bringing the platform host into the Agent package.

This supplies the destination representation needed to migrate legacy implicit
MCP injection. It does not infer a unified gateway's exported tools from its name,
discard named MCP declarations, or read/copy an old `mcp.json`. The converter must
still review the effective legacy server/tool set, prefixes and sampling model,
preserve project/default/template choices, convert credentials and pin the
resulting provider publications before import/cutover can be accepted. Full P5
configuration migration is not claimed here.

Validation: 175 dependency-default, launch, dynamic-instance, binding, assembly,
Agent application/setup/preset, MCP/sampling and isolated-release tests passed.
One opt-in rendered GUI gate was skipped because no frontend acceptance script
was supplied. A real MCP session is called through actual Agent/provider/factory
objects before and after factory restart; removing a default changes revision but
retains the member ID. Prepared launch tests allocate two distinct Shell owners
and retain shared Files output handling even when the templates declare no
tools. Authority/transport fixtures are explicit; this is not live Fleet rollout.

### MCP sampling uses the original Model Service as an ordinary dependency

The scoped MCP entry now accepts an explicit sampling model/route and its own
consumer grant to `model_services_control`. A server can request generation only
while an exported tool is executing, within owner-defined token/request budgets
and a bounded concurrency limit. Concurrent calls on one server share their
aggregate allowance; MCP supplies no trusted parent-call identity. Request model
preferences cannot change the bound publication or route. Text/history/system
prompts and inline images retain their content, and image capability checks,
route policy, grant revocation, Connector SSE and cancellation remain owned by
the original Model Services client. Unsupported audio, implicit context and
sampling tool loops fail before submitting inference.

This uncovered a model-client packaging dependency on the old LLM helper and
OpenAI SDK. Shared message normalization now lives in `models/messages.py`, with
compatibility imports at the old CLI/Desktop paths. Its behavior is unchanged.
The ordinary App client bundle includes canonical model/control/transport modules
and no Agent, global settings or provider SDK. MCP packages may include the same
workload-only native transport; absence disables ambient executable discovery
and leaves only relay-allowed placements. Agent release packaging includes the
new shared module and reuses the transport-format validator.

Validation: 156 MCP, Model Services, consumer dependency, provider-configuration,
message/vision and isolated Agent release checks passed. Two opt-in tests were
not run: real Ollama inference and rendered Agent GUI acceptance. The 19 new
sampling cases include real MCP callback → scoped TLS model dependency → original
Connector → controlled SSE engine, both exact model and route references,
revocation, request/token budgets, cancellation, unsupported requests and a
fresh package process that rejects Agent/settings/provider-SDK imports. Engine
output and grant authority remain fixtures; no production inference is claimed.

This is a prerequisite for preserving legacy MCP sampling during configuration
migration. Effective legacy `mcp.json`, implicit MCP injection, OAuth/stdin secret
mapping and other remaining P5 conversions are still pending. No live deployment
or default architecture switch was performed.

### Existing MCP App gains a prepared, scoped tool-execution entry

The `mcp-gateway` sources now include an ordinary prepared Fleet backend whose
immutable release declares exact RPC exports and upstream schemas. Runtime
configuration provides only selected HTTP or stdio servers, with endpoint-paired
vault slots for HTTP authentication. Consumers use the existing
`DependencyToolProvider` / `mcp_servers` profile and ordinary dependency grants;
they receive neither gateway URLs nor server-management operations. The package
contains the MCP client and portable App host, without Agent, global settings,
ToolSet or legacy gateway startup dependencies.

The backend keeps real MCP sessions for the App lifetime, rejects changed schemas
and unexported/invalid calls, limits concurrent/queued calls, preserves structured
and content results, and drains admitted calls even after consumer cancellation.
AnyIO contexts enter and exit on one dedicated task. Provider shutdown closes
stdio children. HTTP credentials cannot follow redirects or ambient proxies.
The legacy CLI/Desktop MCP gateway is unchanged.

Validation: 27 scoped MCP checks passed, including real HTTP authentication,
stateful stdio subprocess calls, child exit on drain, consumer cancellation,
unknown-outcome handling, content/metadata preservation and six-platform artifact
validation. The 46 existing Agent-dependency, App-host/lifecycle and legacy MCP
checks also passed in the regression run. The isolated package process refuses
Agent/settings imports; the Agent composition test uses an in-process RPC fixture.
Only the local Mac process was executed; Linux/Windows artifact checks do not
prove those native platforms or a live Fleet deployment.

Usage and migration limits are documented in `apps/mcp/README.md`. This closes
the missing provider-execution surface, not the full MCP migration:
owner export discovery/review, saved MCP configuration conversion, stdio secrets,
OAuth/SSE, resources/prompts/roots/elicitation and live cross-node
acceptance remain. The sampling prerequisite is implemented in the entry above. Unsupported configurations must remain on the legacy path
until converted explicitly. The existing Model Service App remains the intended
model dependency for MCP sampling; this entry never creates an implicit
Agent or inherits the old host's model credentials. No live rollout occurred.

### Imported template and saved-instruction prompt paths retain their resources

The importer now rewrites prompt path tokens in Agent/Team instruction bodies,
project/user prompt libraries and saved member instructions against the immutable
backup's relocation map. Cross-root references point into App-owned libraries;
unchanged relative references retain their spelling. Nested includes are handled
in their own files. Namespaced IDs, escaped placeholders, ordinary prose paths,
parameters, metadata and untouched file bytes remain intact. Prompt metadata can
be YAML, TOML or JSON; Agent/Team model scalar conversion is still YAML-only.
Unbacked path includes or relative saved includes without their original source
fail before target creation. The import never discovers or reads additional files
from instruction text. Resume validates the same transformed bytes and keeps the
source untouched.

The shared PromptResolver also retains the actual loaded file's origin in its
cache. Named project/user overrides and nested factory IDs now resolve nested
relative includes and default path parameters from that file, rather than the
factory prompt root. Explicit caller path parameters retain their caller base.
The two-value `_load_prompt` compatibility API remains available to CLI/Desktop.

Validation: 323 migration, scoped-template, legacy template-manager and system
prompt tests passed, including packaged migration acceptance. After adding the
metadata-format coverage, 98 affected template/migration checks passed. A real
TemplateManager gate compares both the imported default team and saved members
with their pre-import instruction expansion while refusing all reads of the old
configuration libraries. It verifies nested/cross-root includes, interruption and
resume, cache origins, exact CRLF preservation and unresolved reference rejection.

This does not finish default/factory configuration, arbitrary path-valued
parameters, attachment/external-asset mapping, OAuth/MCP migration or the full
deployment/cutover plan. No live workspace was modified.

### Global proxy configuration migrates into the original Model Service vault

Explicit `global_fallback` conversion now accounts for backed-up `LLM_API_BASE`
and `LLM_API_KEY` without introducing global environment settings into Agent.
Provider bindings preserve the old field-wise base/key precedence, including
provider-detection sentinels and the legacy secondary OpenAI key fallback. The
effective key source may differ from the base source; runtime absence does not
resurrect a stale dotenv value. Base-only input provisions no global credential;
key-only input requires an owner-paired endpoint. Duplicate references/aliases,
wrong effective source/base and malformed credentials fail before vault or target
mutation. Existing conflict-preserving vault provisioning remains unchanged.

This conversion requires explicit saved-member/template/plugin/tier Model Service
selections. Its nonsecret provisioning descriptor remains outside the Agent's
model configuration; the Agent gets only Fleet references and its model dependency.
It does not publish an engine, infer a matching model, rewrite native model IDs or
reproduce ambient routing. Owner selection and ordinary deployment review still
pair the intended existing publication with the exact endpoint/key configuration.
Global proxy credentials coexist with the separately reviewed platform budget.

Validation: 346 migration, original Model Service, provider-configuration and
legacy-routing tests passed; one opt-in live Ollama engine test was skipped.
The 38 new fallback cases include four original Connector + isolated/source Agent
conversation tests, with platform budget enabled/disabled, private provider vault
reads, upstream endpoint/key/model assertions and normal process drain. Upstream
inference and Hub control are controlled local fixtures, not a production rollout.
The first regression command used a nonexistent frontend build directory; rerun
with the existing verified GUI build passed the packaged acceptance cases.

OAuth/MCP/default-template/assets migration, distributed writer fencing,
publication/cutover/rollback, default startup and live cross-node acceptance remain
part of the full extraction plan. No live user model service or default entry was
changed by this work.

### Desktop budget state migrates through the original Model Service

The credential converter now accepts the confirmed legacy browser budget choice
and the existing Hub budget provisioning receipt alongside its runtime handoff.
It checks enabled state, owner/provider node, model mode and the proxy API prefix;
the only prefix expansion accepted is the paired Hub's standard `/v1` suffix.
Original BYOK credentials remain separately preserved. A disabled unconfigured
budget needs no new key, and a budget-only source needs no synthetic BYOK binding.
Vault conflicts preserve the existing key and leave the candidate unstartable.

Budget-enabled import also requires a read-only publication review on the original
Model Services client. Existing directory and route planning must show that every
selected model and every fallback candidate uses the provisioned node and exact
Connector configuration revision, with published text/tool/context capability.
The saved audit includes the existing deployment bindings and route revisions;
the generic deployment path must revalidate them before starting. The review is
not a grant, reservation, inference or engine wake. Saved members, templates,
plugin choices and quality tiers must remain within the reviewed references.
OAuth source models are rejected from this conversion because the old budget
toggle did not reroute their billing.

Validation: 297 migration, original Connector, platform-budget and routing tests
passed; the one skipped case is the opt-in live Ollama engine smoke test. After
the explicit OAuth guard, 33 budget migration tests passed, including four isolated
Agent package cases. These resume saved conversations with budget on/off, direct
and OpenRouter model IDs, and a model route, while checking upstream credentials,
unchanged BYOK vault entries, secret-free Agent configuration and process drain.
Hub provisioning/control and model responses remain controlled fixtures.

This closes captured Desktop force-proxy conversion, not legacy `LLM_API_*`
fallback migration, OAuth migration, automatic UI orchestration or production
cutover. The same owner Model Service publication and generic dependency startup
remain responsible for live installation and access; no second model or billing
system was introduced.

### Legacy runtime model environment joins the existing Model Service migration

The legacy owner RPC `export_model_migration_handoff` captures its Settings
environment into an owner-private, immutable-per-operation file. The response
contains only its local source path and roots. Explicit `model_environment_file`
input joins the existing inventory, fenced backup and resumable import; the
snapshot is opaque during inventory and never copied into candidate Agent data.
Location validation runs before conversation or configuration scanning so a
misplaced handoff cannot be hashed as ordinary Agent data.

Credential conversion preserves actual Settings precedence, including legacy
aliases, runtime overrides and absent/empty runtime fields. It never resurrects
an overridden dotenv key or consults the migrator process's credentials. The
existing provider-node Fleet vault and Model Service Connector remain the only
credential storage/inference implementation. Combined with ModelSelectionConversion,
the migrated Agent uses published Fleet model references and its scoped model
dependency without receiving the provider API key.

Validation: 212 migration/routing regression cases passed, including clean-package
acceptance with the real Fleet vault and original Connector. After moving location
validation ahead of history scanning, 33 focused cases passed. The new acceptance
resumes a saved conversation both in source and in an isolated shipped Agent
process; it verifies the runtime-selected upstream endpoint/key, unchanged legacy
history, secret-free Agent configuration and normal drain. Upstream inference and
directory/grant issuance are controlled fixtures, not a production rollout.

Export does not fence configuration writers or capture arbitrary in-memory Settings
mutations. Freeze source configuration for export/backup and exclude old writers
before import. Nonempty fallback/budget/Ollama environment fields are recorded but
still block provider-only conversion. OAuth, those additional conversions,
distributed migration/cutover and the default production switch remain pending.

### GUI placement uses Fleet installations and exact node credentials

New Agent setup now offers node, installed release and scope controls for
Pantheon-Agent, its dependency allocator and its Model Service access provider.
These use the existing Fleet inventory and lifecycle status APIs; owner, node
and dependency-protocol identity must match. Releases are exact installed
digests filtered by App id. The original prepared artifact can be explicitly
installed through the ordinary lifecycle API, with a stable ledger intent
across lost replies/reloads. Reading/editing never triggers installation, and
existing pending/failed operations are observed rather than replayed with new
IDs. Installed artifacts are reused; no App is started by this panel action.

Changing node/release/scope creates a candidate with expected generation zero;
it cannot adopt even a stopped instance under an edited identity. Unchanged
prepared stopped generations retain their original expectation. Moving control
Apps requires the existing owner provisioning descriptor for the destination;
Agent-specific provider-key references cannot silently follow a node change.
Complete project/plugin/tool/provider configuration is preserved, including
reactive UI copies of nested provider Apps. Changing targets invalidates review;
new setups must pass shared backend manifest/dependency preview before save.
The composer's returned placement must match the selected exact targets.

Validation: 43 UI/network tests pass, covering node-local credential protection,
exact releases/generations, scope conflicts, account-change races, preservation
of prepared providers, stable install intents and changed preview responses.
Type checking, targeted ESLint and production build pass (the existing large
bundle warning remains). The real Chromium fixture gate covers an explicit
prepared install, cross-node selection, model/target review and revision-checked
save; narrow/dark dropdown and placement screenshots were inspected. All node,
Hub and model replies in this browser gate are fixtures, not live deployment.
UI implementation: `53730d9b` on the isolated `codex/agent-app-extraction` branch.

This is not data migration, version cutover or a deployed default change. Initial
profile and credential preparation, release publication/delivery UI, complete
dependency authoring and the remaining P0–P7 acceptance still need work.

### First startup preset can be reviewed without an existing saved recipe

Fleet Startup apps now accepts a complete setup specification in addition to an
already composed recipe. New setup uses the original read-only
`model_services_agent_preset` API; existing recipes keep the canonical update
API. Both share the Model Services catalog/tier selectors, service-wide access
review, installed-target check and revision-checked save. Reading a file,
reviewing or saving does not start, replace or migrate a running Agent. Account
changes invalidate pending file reads/reviews; unknown saves still require a
reload rather than automatic replay.

The owner-side `pantheon.apps.agent_setup` joins exact release-delivery targets,
the complete Agent profile and the previous control-credential descriptors.
References are selected by each control App's actual node. Projects, settings,
tool/MCP profiles, extra grants and prepared provider Apps pass through unchanged;
no plugins are disabled to construct a minimal Agent. The output contains no
resolved model policy and cannot itself be sent as a deployment recipe. It must
go through model selection/review. Existing composer validation remains shared.

Validation: 78 focused runtime tests passed including packaged composition cases;
one additional shared-provider preservation case was then added and the setup
suite passed all nine cases. Frontend tests passed 19 cases, type checking,
targeted lint and production build passed. A real Chromium fixture exercised
existing-preset edits, target checks, disabling startup, first-setup import,
model selection and revision-checked save, with project/plugin preservation and
an unclipped narrow-window dropdown. Hub/RPC results in that browser gate are
fixtures; production startup/cutover is not claimed.

This connects prepared setup artifacts to the UI. The follow-up above adds
installed release/placement selection; initial credential acquisition and the
full profile/dependency authoring experience are still pending. Those and migration, live rollout
and P0–P7 acceptance remain required. See the setup command in
[release delivery](agent-release-delivery.md).

### Durable owner credentials reuse platform keys and the node vault

The paired Hub now explicitly accepts its existing revocable `pbk_` keys on
Fleet workload APIs. It validates the active key and owner on every request and
exposes a no-store workload identity/controller descriptor. Full login, admin,
key CRUD, startup edits and budget-key retrieval remain outside this credential
scope. No new key database, refresh token or long-lived JWT is introduced.

`pantheon.platform.owner_credentials` provisions an explicitly supplied private
platform-key file to exact trusted control nodes. It verifies the paired Hub
owner/controller and all node import identities before any vault mutation, then
uses existing encrypted Fleet delivery and returns ordinary `{ref, endpoint}`
descriptors. The allocator consumes Hub/controller references; Model Services
access consumes only Hub. Agent receives only its scoped dependency grants.
The helper is not in the Agent release. Repeating delivery preserves an existing
matching value; conflict never rotates it. Rotation requires new references and
an explicit control-App cutover. Revocation blocks future Hub calls/renewal;
already issued grants and NATS credentials have their own bounded lifetimes.

Validation: 141 Hub tests passed, including real-database key creation/revocation,
owner isolation and rejection at full-login endpoints. Runtime provisioning,
model-dependency and composition regressions passed 144 cases (two optional
packaged cases skipped). The dedicated Go-race native gate separately passed:
two isolated local Fleet Managers, encrypted delivery/replay/conflict rejection,
six real App processes, original Model Service Connector SSE, fifteen inference
rounds and isolated Shell/shared Files behavior. Hub identity and upstream model
responses in that gate are controlled fixtures. No production rollout or live
billing acceptance is claimed.

See [owner control credentials](owner-control-credentials.md). This closes the
explicit provisioning path's twelve-hour-token problem; automatic first-run key
acquisition, initial configuration UI, production rotation/cutover, migration
and the remaining P0–P7 gates are still required.

### Paired release-set preparation and exact node delivery

`pantheon.chatroom.release` now builds the paired Agent plus its ordinary
allocation and Model Services access Apps in one atomic output directory, for
explicit native transport targets. Additional tool/plugin declarations and
credential aliases pass through unchanged. Optional ordinary provider packages
use the existing portable adapter/native manifests. An already deployed Model
Service is reused through model selection, not rebuilt by this command.

The owner-side `pantheon.apps.release_set` delivery command validates every
selected package against its indexed ordinary Fleet artifact digest, checks all
target owners/platforms, and stages original bytes through `stage_exact`. It
returns the composer's existing target map with digests filled in. The index
contains relative code paths and public release metadata only; it introduces no
App version/lifecycle protocol and no credential distribution. Verified bytes
are spooled privately rather than retaining all App payloads in coordinator
memory. Installation, dependency grants, configuration and startup stay with
the existing generic deployment coordinator. Installed digests use a fresh
node observation and skip transfer; a delivery retry never runs install hooks.

Validation: 79 focused delivery, lifecycle, deployment/preview and paired-release
tests passed. A freshly built Agent GUI and native transport passed the Go race
detector joint gate with two local authenticated Fleet nodes and six real App
processes, original Model Service Connector/SSE inference, independent Shell
sessions and shared Files. This gate now uses release-set building/delivery;
replaying delivery both before installation and after readiness preserves the
original targets and does not add lifecycle operations. Hub directory/auth and
upstream inference are still controlled fixtures. macOS ARM64 and Linux AMD64
distributions were built from the current sources; only macOS executed in this
gate, so Linux/HPC production acceptance is not claimed.

See [release delivery](agent-release-delivery.md) for commands and boundaries.
This removes manual multi-App packaging/digest assembly, not the remaining
initial Agent configuration, automatic credential setup, Store
publication, migration or default deployed cutover. A 12-hour Fleet session
credential is not a permanent node secret; the explicit owner provisioning path
above uses revocable platform keys instead. No current
Atrium runtime, CLI/Desktop data or remote release was changed by this work.

### Read-only deployment target review for setup

The generic owner `fleet_app_deploy` API accepts `action=preview` with the exact
ordinary recipe. It reads each node and immutable installed release once, checks
owner/protocol, target scope and expected stopped generation, and verifies every
startup dependency's version, interface, arguments and required configuration.
New providers referenced with `$app` use their selected release declarations;
external providers must be the selected ready generation. Existing deployment
operations require inspection instead of being represented as a fresh preview.

The declaration compiler is shared with the authoritative prepared-start path;
there is no parallel Agent-specific dependency validator. Preview does not
install artifacts, reserve instances, issue grants, read credential contents,
start maintenance, write a journal, or change the recipe. The response reports
only target/version/dependency names and `read-only-snapshot`, never configuration
values or credential references. Fleet Startup apps exposes **Check deployment
targets** on ordinary presets and invalidates the report after model selection,
configuration review, reload or identity changes. Target conflicts are reported
as requiring an explicit cutover; the UI does not stop the existing Agent.

Validation: 152 focused runtime cases passed, including the real paired release
manifests, configured/native Agent model selection, ordinary deployment recovery,
and platform preview in a subprocess that rejects all Agent execution imports.
A race test changes the provider version after preview and confirms that actual
start rejects it before issuing grants. Frontend suites passed 44 cases; the real
Chromium fixture performs the target check between model review and save, then
reloads/disables the preset. Type checking and the production build passed.
The existing authenticated-NATS native deployment gate also passed with the Go
race detector: two isolated local Fleet Managers ran six real App processes,
Connector/SSE inference, isolated Shell sessions and shared Files with sibling
access preserved after retirement. This regression exercises the shared start
compiler; the new preview transport uses deterministic node fixtures in the
focused tests. Hub and upstream model responses in the native gate are fixtures,
not production identity/billing or remote-node acceptance.

This is a setup prerequisite, not complete onboarding or a readiness promise.
Releases must already be installed for review; missing releases are reported
without running installation hooks. Credential validity, application-specific
configuration semantics, runtime resource availability, engine health and later
state changes remain authoritative at deployment/start. Saving a startup preset
is still separate from executing it. Initial release provisioning, credential
preparation, configuration creation without an imported seed, migration/cutover
and live cross-node acceptance remain outstanding.

### Fleet startup preset editor — existing model selection

Fleet's Hub-mode App instances view now exposes **Startup apps**. It reads the
existing owner/profile-scoped Hub preset, displays its App targets, and lets the
owner choose published Model Service models or routes for normal/high/low tiers.
The page uses the shared themed, searchable selector and a scrolling compact
layout. It preserves the saved engine-wake choice. Model routes are checked
against every candidate's published text/tool/context capabilities, not merely
the route's requested capabilities.

The read-only platform RPC `model_services_agent_preset_update` extracts and
round-trips a canonical ordinary Agent composition before replacing its model
selection and operation ID. Existing provider Apps, tool allocation policies,
credentials and additional bindings must survive unchanged; custom components,
changed core grants and provider-bootstrap recipes are rejected before directory
access instead of being silently dropped. The editor displays other recipe types
and permits disabling future startup, but does not rewrite their graphs.

A separate review shows the Connector-wide authorization scope, including future
model publications. Only an explicit save writes the existing Hub preset with
its revision. Conflicts, lost replies and mismatched acknowledgements require a
reload, not an automatic overwrite/retry. Account changes, disconnection identity
changes and page disposal invalidate outstanding UI work. Saving/disable does not
start, stop, migrate or restart any running App.

Validation: 58 backend tests passed, including packaged/native Agent selection
and platform RPC execution with Agent imports blocked. The six focused frontend
suites passed 42 tests covering selection, review invalidation, persistence,
identity changes, unknown save outcomes and preserving other startup graphs.
`scripts/test-startup-apps-ui.mjs` exercises the real Vue UI in Chromium against
isolated owner/RPC fixtures: change a model tier, review, save, reload the saved
selection, and disable startup. It checks dropdown bounds at 680x570, light/dark
screenshots and absence of browser exceptions. These fixtures do not exercise
real Hub/Fleet rollout or paid inference. Frontend type checking and the production
build also passed; existing large-bundle warnings remain.

This editor still requires an existing saved canonical Agent preset or an
imported prepared preset. It does not yet discover and provision the initial
release targets, node-vault references or provider-bootstrap graph. Default
onboarding, coordinated candidate cutover and deployed cross-node acceptance
remain required; this is not completion of P7 or the Agent extraction.

### Existing Model Services can compose an Agent startup preset

The owner platform now exposes read-only `model_services_agent_preset(spec,
fleet_tiers, allow_wake=false)`. `spec` contains the ordinary Agent composition's
owner, operation ID, exact App targets, Agent configuration, tool policies and
node-vault references, plus optional extra bindings/provider Apps. It omits the
hand-written `models` authorization policy. `fleet_tiers` supplies a required
`normal` and optional `high`/`low`, each an existing `fleet-model://` or
`fleet-route://` reference. Reusing one reference for several tiers is explicit;
an omitted tier never silently falls back to a direct provider.

The planner reads the original owner Model Services directory, copies exact
Connector bindings and route revisions, preserves every declared route candidate,
and generates the existing `model-services-control` policy. Agent selections
must publish text operation, confirmed tool support and a positive context
length, matching the Agent picker. Missing models, ambiguous directories,
unusable bindings and conflicting existing tier choices fail before deployment.
No model inference, connection grant, engine wake, installation or persistence
occurs during preview; directory readiness is not a live engine health check.

The response contains `recipe` (the unchanged generic `fleet_app_deploy`/startup
preset format) and `model_selection` (selected model metadata and authorization
review). Authorization remains **whole Connector publication**, including other
models on that Connector; the preview explicitly lists that scope and the
currently published references. It is not a new model-level ACL. The existing
control facade still rejects replaced instance generations or changed route
revisions at consumption time. The returned review is not an issued grant.

The pure owner composer now lives in `pantheon.apps.agent_deployment`; the old
`pantheon.chatroom.deployment` import and CLI remain compatible. The platform RPC
is executable with all Agent/ChatRoom imports blocked. Existing explicit BYOK
configuration is preserved rather than silently removed or treated as budget
intent. Normal/high/low selections use the generated Model Service mapping.

Validation: 103 focused selection, App composition, platform boundary, native
Agent/Connector and startup-preset tests passed with no skips. Both exact-model
and route selections were composed into a prepared native Agent, invoked via
the `normal` tier, streamed through the original Connector and revoked. The
upstream inference output and directory/grant issuer remain controlled fixtures.
The actual paired App manifests also passed ordinary deployment assembly with
the generated policy. Both old and new composer CLI module paths produce the
same private recipe and refuse to overwrite it. This is local acceptance, not
a deployed cross-node rollout or live provider/billing test.

This removes manual model-policy assembly at the API boundary. A shipping
onboarding UI still needs to select release targets, show this review and persist
the approved owner preset. This does not implement that UI, change current user
model choices, publish a release or cut over the deployed Atrium. Distributed
migration and all remaining P0–P7 acceptance gates remain outstanding.

### Legacy budget preference observation and migration audit

The legacy local Desktop now distinguishes its persisted requested budget choice
from the exact backend connection's acknowledged state. `set_llm_proxy` replies
must confirm success and the intended enabled flag before the picker calls the
budget active. Mutations are serialized so a delayed enable cannot overtake a
later disable. Reconnect fetches missing credentials before applying the choice;
logout discards late credential/catalog responses and synchronizes disable.
Failed or stale acknowledgements remain unconfirmed, and the picker prevents
additional clicks while a change is pending. The independent Agent App remains
excluded from this legacy process-global path.

A read-only `get_llm_proxy_state` compatibility RPC returns only protocol,
enabled and configured booleans. It exposes no endpoint, key, environment or
provider settings and is absent from AgentRuntime and the platform host. The
legacy budget store's `captureMigrationBudget` checks this state against the
current browser choice and connection, rejecting pending work, mismatches,
disconnects, source-service switches, another browser tab changing the stored
choice, and older runtime responses. Its result
contains only protocol, source service identity and the enabled flag.

`ModelSelectionConversion` can retain this exact observation via paired
`budget_choice` and `source_service_id` arguments. The existing fenced selection
audit/digest binds it to the migration; a resumed import cannot substitute a
different choice. Unknown sources, extra credential fields, nonboolean flags and
foreign service identities fail before destination writes. This is reviewable
provenance, not a signed identity grant or automatic model-route conversion.
Explicit target mappings still determine the new App's model routes. The full
migration UI must collect this observation, present its target provider mapping,
and coordinate source shutdown/fencing; it is not wired to a shipping wizard yet.
Hub-mode and CLI routing need their own source-state capture, not a fabricated
local-browser preference. Existing callers without browser input stay supported.

Validation: 23 focused UI store/composer tests and 54 runtime migration, legacy
RPC and boundary tests passed. The runtime run supplied the real Fleet credential
reader, clean packaged-Agent Python and built artifacts, so its native migration
and model-call cases were exercised rather than skipped. Vue type checking also
passed. No live UI, model account, deployment or user's saved preference was changed.

### Explicit budget provisioning is part of model startup

Owner startup recipes may now declare `credential_source: platform-budget` for
an ordinary prepared API Connector. The independent platform host receives an
explicit paired Hub origin and a private full-owner login file separately from
the recipe. It uses the existing remote Fleet vault importer before deploying
providers, registering models or starting Agent. A recipe cannot carry an inline
login/key, select a different credential source, or redirect delivery away from
the exact Hub-reported endpoint. Omitting the declaration retains the previous
pre-provisioned-provider behavior; ambient process credentials do not enable it.

A private acknowledgement records only owner, node, source, model mode and exact
Connector configuration. Normal polling and host restarts reuse it without
fetching the budget key again. Missing/invalid acknowledgements block App start;
uncertain delivery/checkpoint failures can reconcile the same value through the
existing vault. The public startup status exposes no credential receipt. The
paired Hub schema persists this optional intent under its existing owner/profile
revision checks. The platform CLI requires both new budget options together and
reads the login lazily only for requested provisioning.

The native acceptance now uses an authenticated API upstream instead of its
previous unauthenticated Ollama fixture. Hub identity, directory and upstream
model responses remain controlled fixtures, while the Connector, credential
reader, encrypted owner delivery, App deployment and Agent/tool processes are
real. The test builds the Fleet CLI for the local credential pipe: a Go test
binary cannot serve that command. This tests budget-key use, not just storage.

Validation: 180 focused runtime tests and 37 paired Hub startup contract tests
passed. The native six-App test passed under Go's race detector (62.3 seconds for
the native subtest), with 15 authenticated inference rounds and seven real tool
calls. It verifies exactly one Hub key acquisition across pending polls and
restart, then removes the owner login file and resumes from the saved receipt.
Startup journals contain neither login nor budget key. Initial runs exposed
missing credential capability metadata and use of the Go test executable as the
credential reader in the fixture; both were corrected without weakening product
checks. These are local macOS results, not live LiteLLM billing or cross-node
production acceptance.

Remaining: default UI/onboarding, capture of the old browser-local budget toggle,
owner preset creation, release publication and deployed cross-node acceptance.
The former toggle is browser local storage pushed into legacy Agent environment;
an existing key or service configuration cannot establish that it was enabled.
No current user preset, model selection or deployed release is changed here.

### Remote credential delivery reuses the Fleet vault

The owner can now prepare a selected node's Model Service credentials without
logging into that node or copying a plaintext key through an App command.
`RemoteModelCredentialVault` uses an authenticated owner-only, single-use
P-256/HKDF/AES-GCM delivery to the same endpoint-bound vault used by the local
CLI and Connector. Expiry, replay, changed destination/context and conflicting
stored values fail; an identical retry never rewrites an existing credential.
There is no remote export, deletion or rotation API. Pending keys are bounded
and cleared on Manager shutdown; lifecycle ledgers contain no delivery payload.
The live status protocol prevents old nodes from being mistaken for capable ones.

`provision_platform_budget` supports local and remote vaults. Its existing Hub
owner check, exact API prefix and LiteLLM virtual key remain authoritative.
The owner CLI accepts explicit paired controller/credential-file arguments for
remote delivery and emits the same non-secret Connector descriptor. The separate
owner delivery transport reuses the existing authenticated connection but does
not broaden the dependency allocator's allowed operations. Agent consumers
receive neither the full Hub login nor the budget key through this workflow.
No new runtime dependency was added.

The native six-App acceptance no longer has a plaintext `/fixture/secret` shortcut.
It acquires a real owner NATS connection, encrypts credentials in Python, imports
them in Go, accepts same-value retries, rejects a changed key, then starts the
original allocator/model-access/Connector/Agent/Shell/Files path. The node keys
are used by the running Apps, so successful transport alone is not the gate.
Hub identity/directory and upstream model responses remain controlled fixtures.

Validation: 125 focused Python provisioning/Connector/bootstrap/platform and
model-access cases passed. Go vault and Runner suites passed with the race
detector, including the real owner-NATS decoder; the focused Manager import gate
also passed. The six-process owner-join/encrypted-delivery/inference/tool gate
passed with the race detector in 67.7 seconds. This is local macOS fixture-backed
acceptance, not live LiteLLM billing or Linux/HPC/Windows validation.

Still pending: wire this explicit preparation into default UI/onboarding and
owner preset selection; preserve budget enabled state/provenance; publish and
cut over paired releases; validate deployed Linux/HPC and all remaining P0–P7
requirements. This change does not deploy or change the current user's routing.

### Model providers and consumers share a resumable owner startup intent

`ModelServiceBootstrap` now sequences prepared providers, original Model Service
registration, and ordinary consumer App deployment. It owns no process/key/model
implementation: two deterministic child ids reuse `AppDeployment`, and explicit
`$model` references become the registered provider's exact running binding. The
original intent and registration receipts use private atomic owner checkpoints.
A lost registration reply or checkpoint can be reconciled without duplicate
publication; directory/configuration/admission changes and stopped/replaced
instances block consumer startup rather than auto-healing. Pending polls check
receipts instead of repeatedly discovering models or installing dependencies.

The existing platform file/Hub preset driver accepts `kind: model-services`,
through the owner `model_services_bootstrap` API. The paired Hub schema preserves
these references and existing owner/profile/CAS protections. Legacy ordinary App
recipes remain supported. This is independent platform startup; no Agent runtime
is imported for orchestration and no owner token enters the Agent.

Validation includes deterministic operation/acknowledgement/checkpoint failures,
restart with unchanged operation ids, conflicting owner/recipe/model state, and
real platform preset dispatch. The six-process native gate now runs the whole
startup intent through the preset driver, original Connector registration and
Agent model discovery/inference/tool calls, passing with Go's race detector.
Hub auth/directory and upstream inference remain controlled fixtures, so this is
local macOS acceptance rather than live model billing or Linux/HPC/Windows proof.

Validation for this increment: 88 runtime startup/deployment/platform cases
passed (two separate packaged cases deselected), 34 paired Hub startup/model
contract cases passed, and the native six-process race-detector gate passed in
68.6 seconds. That duration is a controlled installation/integration gate, not
an end-user launch-latency measurement.

Still required: acquire/deliver credentials as part of onboarding, preserve budget
provenance/enabled state, publish/select actual owner presets and paired releases,
default cutover, distributed migration fencing and remaining P0–P7 work. The
bootstrap requires staged artifacts and provisioned node-vault references; no
production deployment or active user preset has been changed.

### Prepared Model Service registration uses the original directory

The owner Model Services API now exposes `model_services_register_prepared`.
It validates an already-ready ordinary Connector's exact node, App, scope,
artifact and generation, compares the requested configuration with the original
Connector's preview/status/discovery, and publishes explicitly selected chat
models using existing context/capability rules. No lifecycle/configuration RPC
is issued. It reuses the original Hub deployment schema and create CAS, and a
lost acknowledgement can be retried by exact comparison without rewriting owner
changes or adopting a newer generation. Non-chat publication remains available
through the original Model Services controls; an empty selection is supported.

The six-process native acceptance now performs ordinary Connector deployment,
owner registration/publication, scoped directory discovery and Agent inference
without a fixture directory-injection shortcut. It passed under Go's race
detector, including tool calls and consumer/provider cleanup. Focused tests cover
real Connector HTTP discovery, admission/configuration/generation changes,
lost acknowledgements, concurrent directory conflicts and explicit model choices.
The paired Hub contract test checks direct-ready insertion, normalization, owner
isolation and create-CAS conflict. The native test still uses fixture Hub auth/
directory and upstream model output; this is local macOS evidence, not deployed
LiteLLM billing or cross-platform acceptance.

Validation for this increment: 31 prepared-registration cases and 12 platform
RPC/service cases passed; 33 Connector package/platform-budget cases passed with
the real local Fleet credential CLI enabled; five paired Hub directory cases
passed; the six-process native gate passed with Go's race detector. Broader
Model Services/recipe regression cases also passed; optional packaged cases were
not rerun by that suite (the native packaged gate ran separately).

Remaining: durable default owner bootstrap sequencing, budget provenance and
enabled-state migration, paired release distribution, live cutover and the other
P0–P7 gates. No production deployment was updated.

### Original Model Service Connector accepts prepared App startup

`pantheon.models.connector_package` builds a candidate v0.1.24 of the original
`model-service` App for ordinary configured deployment. The backend, engine
adapters, data plane, node credential pipe and readiness/drain implementation are
unchanged; a small entrypoint uses the shared generation-bound runtime-config
reader to initialize `values.connector`. No new runtime library is required.
This removes the post-start configuration RPC from that deployment path.

The value accepts the original attached engine, endpoint and optional named
credential reference. The existing platform-budget provisioning descriptor is
directly usable. First startup initializes an empty service. Identical retained
configuration is accepted without rewriting it or clearing admission/recovery
state; a conflict rejects startup and requires explicit owner recovery. Inline
secrets, owner credentials, legacy credential files and managed-engine settings
are excluded from this prepared value. The existing interactive package and
managed-engine workflow retain their current entrypoint and behavior.

Validation: 75 focused package/Connector/budget/recipe tests passed (one optional
case skipped, two packaged-recipe cases deselected). The new process
case uses real Fleet vault provisioning, starts from the budget descriptor, runs
discovery/inference through the original Connector and restarts without rewriting
its retained configuration. The six-process native Agent/allocator/model-access/
Connector/Shell/Files gate also passed under Go's race detector, now starting the
Connector via the real generic deployment coordinator without a configure RPC.
It still verifies scoped inference, isolated Shell state, shared Files, revoked
access and lifecycle cleanup. Model output and Hub directory/auth are fixtures;
this is macOS local acceptance, not Windows/Linux/HPC or live LiteLLM billing.

The registration/publication step is implemented in the increment above. Durable
owner bootstrap sequencing, budget enabled-state/provenance, full migration,
release distribution and default Atrium cutover remain required. No deployment was updated and the full P0–P7 goal is
still incomplete.

### Team path references move with their Model Service-backed Agent templates

The importer now relocates team `agents` file references independently of model
conversion. Backed-up absolute paths point into App-owned data; relative paths
that cross relocated project/global roots are recomputed. Relative references
whose meaning is unchanged retain their exact bytes, as do ID references and
path-shaped inline member definitions. Scalar edits preserve comments, CRLF,
Unicode and instruction bodies. A reference outside the mapped template library
fails before target creation or credential provisioning instead of reading an old
direct-provider configuration after cutover.

The source spec also accepts explicit `agent_libraries` directory roots. These
join the existing cooperative fence, immutable backup and source-check transaction.
Each external Markdown library gets a distinct imported path namespace; colliding
filenames remain separate. Model mappings keep the original source path and member
ID, so external recipes use the same Model Services binding as project/global
recipes. No file is discovered or copied merely because prompt content names it.
Overlapping roots, symlinks and unsupported non-template content fail validation.
External editors/old runtimes still require separate exclusion; the local fence
is not distributed protection.

Validation: 173 migration/inventory/backup/credential/model tests passed, followed
by two isolated packaged-Agent cases. The new external-library package case
continues saved history and creates a new template-based conversation through the
original Model Service Connector while the old recipe still names its direct
provider. The upstream service is a controlled fixture, not a live provider.
Prompt-body includes, parameter/asset paths, other frontmatter formats, default
bootstrap provisioning, production cutover and the remaining full P0–P7 gates
remain incomplete. No live user data or deployment was changed.

### Plugin text-model settings join the Model Service migration

The same explicit `ModelSelectionConversion` transaction now covers six plugin
selectors: compression, memory selection/flush/dream, and learning/extraction.
Mappings identify the original settings file and exact field path. Global and
project layers remain separate, including disabled and overridden selections;
unmapped direct models fail preflight. The audit pins the mappings for resume and
runtime admission. Plugin enablement, thresholds, null/auto inheritance and bound
quality tags are preserved. It does not enable background work during migration.

Scoped auxiliary execution also retains a quality selector's `+think` suffix
through model resolution and passes its effort separately from the Fleet model
reference at inference. Explicit request parameters still take precedence and
caller-owned parameter dictionaries are not mutated. Agent-backed helpers already
parse this suffix; lightweight memory selection/flush/note calls now do too.

Controlled-service acceptance imports both settings layers, constructs the real
memory/learning/compression components with the App model scope, and runs memory
selection, flush, session note, explicit skill extraction and compression through
the original Model Service Connector. It also verifies the actual upstream
model IDs and reasoning parameters and rejects ambient model resolution. Skill
extraction is invoked explicitly for this test; its original disabled automatic
schedule remains disabled. This is local HTTP/TLS fixture evidence, not a live
provider or production rollout. Vision/image-generation preferences, third-party
plugin fields, external template references, production migration and the other
P5/P6/P7 acceptance gates remain outstanding.

Validation for this increment: 192 focused migration, model-scope, auxiliary and
plugin tests passed. The final Connector case verifies six upstream requests,
including both the auxiliary and compression reasoning-effort paths.

### Source template models use the existing Model Service binding

The owner-side `ModelSelectionConversion` now also accepts explicit model
mappings for imported project/global Agent and Team Markdown libraries. It edits
only the effective YAML model scalars, including inline team members with their
own IDs. Direct selections cannot pass unnoticed; missing/stale/duplicate/unused
mappings fail preflight. Existing Fleet references and explicitly bound quality
tiers remain intact. Empty/omitted child-model declarations retain inheritance
semantics. Prompts, tool fields, comments, Unicode and line endings are preserved.
The same backup fence, mapping audit and resumable import transaction cover these
changes, including installations with no saved conversations. Conversion holds
one bounded template at a time, not a duplicate library in memory. YAML aliases,
ambiguous metadata and non-YAML frontmatter require explicit conversion.

Acceptance exposed a real startup problem: legacy factory reclaim deleted an
untracked imported `researcher.md` override simply because its filename matched
the package. Independent Apps now skip legacy template materialization,
reclaim/retirement and config seeding when `seed_settings=False`. Their owned
data survives startup; package fallback remains available. Default CLI/Desktop
bootstrap behavior is unchanged.

Local controlled-service acceptance covers an imported standalone Agent used by
a referenced team, saved-chat continuation, new-chat creation and team switching
through the original Model Service Connector and provider-node vault. An
isolated packaged Agent also continues saved history and creates/runs a chat from
that imported library; tampering with its mapping audit still blocks startup.
No real provider billing, live user migration or production deployment occurred.
External/absolute template-reference closure, remaining plugin model settings, all template
formats, delegation end-to-end acceptance and the remaining P5/P6/P7 gates are
still outstanding.

Validation for this increment: 132 focused migration, template, compatibility and
App composition tests passed, plus three isolated packaged migration cases.
The packaged inventory excludes both owner-side model migration modules.

### Platform budget reuses the original Model Service Connector

Owner-side `pantheon.models.platform_budget` now provisions the existing user's
LiteLLM virtual key into the selected node's existing Fleet vault and emits an
ordinary API Connector configuration. The paired Hub response supplies the
authenticated Fleet owner and explicit API prefix; no new budget/key database or
budget-specific inference/lifecycle implementation is introduced. The shared
vault helper has moved out of the Agent migration package so platform/model
provisioning does not import Agent execution code. Neither helper is shipped in
the independent Agent App.

Local acceptance includes real Fleet vault writes and original Connector
discovery/streaming in both platform modes, and a native Agent process receiving
a response through its scoped Model Service dependency without any budget key
in its prepared configuration. Hub identity and upstream inference are controlled
fixtures, not production billing evidence. The existing owner attach/publication
APIs consume the connector configuration; automatic bootstrap integration, budget
provenance in the directory/UI, old selection/enabled-state migration and live
cutover remain outstanding. See `model-service-platform-budget.md` for the
owner command, exact boundary and acceptance instructions.

Validation for this increment: 54 runtime provisioning/migration cases and two
clean-environment packaged migration cases passed; 17 matching Hub contract/auth
cases passed. The release inventory gate confirms the owner credential helpers
are absent from the independent Agent package. No live deployment was changed.

### Explicit saved-conversation model migration

`ModelSelectionConversion` now allows the owner-side importer to map each saved
conversation/config member's exact previous model selection to an explicitly
chosen `fleet-model://` or `fleet-route://` selection. Every saved member must be
covered, and unrelated/duplicate/stale entries fail before creating target data
or provisioning keys. Fallback lists retain their shape and positional mapping;
reasoning effort cannot be silently dropped or changed. The owner also supplies
all three quality tiers for new/default Agents. There is no catalog-order or
model-name equivalence guess.

The importer preserves logical member IDs, non-model recipe fields and message
history. It pins the mapping digest alongside placement and model dependency in
startup admission, persists a separate bounded selection audit, and rejects a
different mapping when resuming an interrupted import. Startup verifies the audit
as a bounded regular file. If credential conversion is also requested, those keys
are provisioned only in the provider node vault; their references are recorded
as provisioning provenance, not Agent credential inputs.

Acceptance includes continuing a migrated conversation through the original
Model Service Connector with its migrated API key, and starting the isolated
paired Agent package with the same mapping. A modified audit blocks startup
before inference. These use local controlled dependency/engine services, not
live user migration. Source template libraries were added in the later increment
above; plugin-specific model settings,
budget enabled-state/OAuth semantics and available/capable published route
validation still need end-to-end migration work. This is saved-conversation
conversion, not a claim that every template format or configuration is migrated.
See `agent-app-release.md` for the API and scope.

Validation: 73 focused importer/model/environment/credential cases and three
clean-environment packaged migration cases passed. No user source, live model
service, default routing or deployed release was changed in this increment.

### Legacy data inventory and stable project identity

The legacy registry has no intrinsic project IDs: its entries use canonical
paths, display names and timestamps. New registrations now receive persisted
UUIDs. Existing entries retain their old shape on ordinary reads until an explicit
`get_project_snapshot` export assigns missing IDs under the registry's existing
cross-process write lock. Existing IDs are retained. Names must be unambiguous;
duplicate/invalid stored IDs cannot be silently replaced. The snapshot contains
`projects`, `active_project` and `default_project`, directly consumable by the
Agent launch configuration. Legacy path-based selection/registration remains.
Renaming a project preserves its ID; relocating storage must preserve the saved
registry IDs rather than rebuilding identity from new mount paths. Older binaries
that discard unknown registry fields must be fenced before migration/cutover.

The read-only `python -m pantheon.chatroom.migration --spec INPUT --output REPORT`
inventories explicit legacy roots. Its spec combines the project snapshot with
`home_memory`, `global_config` and `project_config` (absolute directories), plus
optional `memory_overrides` keyed by project ID for custom conversation stores.
It does not instantiate the legacy Settings/MemoryManager or discover credentials.
It writes a new owner-private report without overwriting an earlier report.

Verified source/target mapping:

| Existing source | Agent App destination / required treatment |
| --- | --- |
| Explicit home conversation directory | `conversations/home`; aliases of a project store are inventoried only once |
| Each project's `.pantheon/memory` or explicit override | `conversations/projects/SHA256(project ID)`; preserve conversation filenames/IDs and embedded team metadata |
| Selected `.pantheon/{agents,teams,prompts,skills,brain,learning,memory-store,MEMORY.md}` | `configuration/.pantheon/` with matching relative paths |
| Global Agent templates/skills/memory | Matching paths under `user/` |
| Settings, MCP configuration, environment and credential files | Explicit conversion and credential-reference provisioning required; dry run does not read them |
| Other projects' Agent configuration | Inventoried separately; scope mapping is still required, never implicitly merged into selected settings |
| Project assets and platform registry/Fleet/Store data | Retained at existing locations; not copied into the Agent App |

The report hashes regular source files, validates/counts JSON and JSONL history,
records project/conversation identities and embedded-team presence, and reports
duplicate conversations, damaged histories, symbolic links, unknown companions
and unmapped settings. It does not include conversation text or configuration
contents. Known configuration files that may hold credentials are not hashed.
File changes during scanning fail visibly. This is not a consistent snapshot of
a running writer: reports always state `requires_writer_fence=true` and
`ready_to_import=false`. Backup/import, attachment resolution, member-instance
mapping, credential conversion and distributed cutover fencing remain incomplete.

Validation: 35 distinct local project, platform-service and migration inventory
tests passed. They include four-process ID allocation, atomic registry failures,
legacy route compatibility, both history formats, source immutability, custom
memory roots and private/non-overwriting CLI reports. No live user history has
been imported or changed; this does not complete P5.

### Cooperative legacy writer fencing

Updated `ChatRoom` compatibility instances now hold shared filesystem leases on
user/project configuration roots and each opened conversation store. Dynamic
project routing acquires a lease before opening a new store, including explicit
per-project chat creation. A failed selection does not change the active memory
route; selecting a missing project does not create its directory. Ordinary legacy
peers can continue sharing the same roots as before. New Agent Apps retain their
separate namespace writer lock and do not lease the legacy directories.

`fence_legacy(spec, operation=..., target=..., namespace=...)` derives all source
roots from the explicit inventory spec, including custom memory overrides. It
obtains every exclusive lease and validates every existing owner before writing
any migration markers. Markers pin the operation, full root set, destination and
namespace. Closing/killing a migrator leaves markers in place: updated legacy
writers cannot restart into those sources. An identical operation can resume;
changed intent is rejected. Interruption between marker writes leaves a partial
but resumable reservation; corrupt/partial marker content fails closed for
explicit recovery. Inventory excludes these control files without changing the
source data digest. Lease files are never unlinked/replaced while peers may hold
locks. Control file symlinks/hard links are rejected.

Legacy cleanup releases its leases only after accepted work, routing, plugins,
observers and conversation flushing have completed successfully. Failed saving or
shutdown retains the leases until process exit. Source release is an explicit
rollback primitive under the matching exclusive owner; it is **not** an automatic
cleanup step. Its future coordinator must stop/fence the destination writer before
releasing sources. No automatic expiry or PID-based stale-lock override is used.

This is cooperative **local** fencing, not distributed ownership. Older binaries,
standalone configuration writers/editors and replicas on separately cached or
non-lock-coherent volumes still require deployment-level exclusion. Windows has a
byte-range lock implementation but lacks real Windows acceptance. Neither a local
lease nor `fence_legacy` is sufficient evidence to permit production import on
Modal/HPC/shared storage. The inventory still reports `ready_to_import=false`.
Backup/import, credential conversion, source/destination rollback and distributed
release coordination remain required P5/P6 work.

Validation: 89 focused tests passed, including two-process legacy leases, killed
migrator recovery, partial multi-root reservation, conflicting operation intent,
malformed/link control files, concurrent first access, lazy project access,
real compatibility runtime construction and flush barriers, failure to drain,
existing CLI/App-host lifetime coverage, independent Agent App data and project
registry behavior. Tests use temporary data only; no live data was migrated,
no installed runtime was changed, and this increment is not a deployment.

### Resumable private source backup

`migration_backup.backup_legacy(spec, fence=..., directory=...)` now consumes a
live `MigrationFence` covering the exact declared inventory roots. It creates an
owner-private data archive with a pinned operation/source manifest, bounded file
copies and checksums; it never creates an App release or modifies source files.
Known configuration files that can contain credentials are retained as opaque
private backup blobs with no App import target. Unknown regular configuration
files are also preserved, with unresolved inventory issues retained. Symlinks and
non-regular sources are rejected rather than followed or silently dropped.
Platform data/project assets remain at their existing locations.

Archive intent is persisted before copying. Each completed blob is synced and
verified before reuse. Interrupted partial copies can be retried after reacquiring
the same source fence. Changes to source bytes or operation intent cannot overwrite
a prior archive. A second inventory/configuration scan detects added/removed or
changed files before atomic snapshot publication. Completed snapshots are verified
without being rewritten; corruption fails visibly. Files and directories are
owner-private, and source-overlapping backup destinations are rejected except
nested locations already excluded as platform-owned (such as Fleet data storage).

`verify_backup(snapshot_directory, digest=receipt['sha256'])` verifies the manifest
and every blob without requiring the original source directories to exist. The
receipt contains only the snapshot location, digest, counts and byte total; no
configuration values or conversation text. The expected digest must be retained
by the future migration coordinator outside the archive. This is a data-file
backup, not a whole-filesystem ACL/metadata snapshot or an importer. Opaque
credential-bearing originals must stay private data and must never be included in
release artifacts or silently applied to new App settings.

Validation: 80 focused migration/fence/Agent lifetime/application/project tests
passed. New coverage includes real process termination during copying and retry,
reuse of completed blob mtimes, source mutation during copying, source loss,
manifest/content corruption, missing/extra files, unsafe archive destinations,
byte limits and unknown configuration preservation. No live history/configuration
was backed up or migrated. Snapshot receipts still say `ready_to_import=false`:
explicit configuration/credential conversion, identity mapping, validated import,
rollback, external-writer exclusion and distributed cutover remain incomplete.

### Validated data import and startup admission

`migration_import.import_backup(snapshot, digest=..., fence=...)` consumes the
verified private backup while holding its exact live legacy source fence. It
rechecks source contents, validates the whole conversion plan, and only then
populates an empty private Agent data root. The destination is the Agent-owned
data root: for the ordinary Fleet host this is `ctx.state_dir / 'agent'`, not the
parent host state directory. Namespace and destination must match the prepared
candidate configuration and the durable migration intent.

Both JSON and JSONL histories preserve conversation IDs, project metadata, saved
team/member configuration IDs, model selectors, tool declarations and message
contents. Saved template source paths are remapped only when the corresponding
file is actually imported. External asset references remain unchanged; this
preserves references but does not prove the external assets are still resolvable.
Each saved member receives a deterministic new runtime instance UUID, unique per
conversation and stable across retry/restart. Seeding the instance journal does
not replay Runs, provision tools or perform inference. Normal startup subsequently
resolves dependencies and records a new configuration revision under that ID.

Known non-credential Agent settings are converted into App-private project/user
settings. Platform settings remain in the untouched original tree, with field
names recorded in the receipt. Nonempty API keys require the explicit converter
below; environment files, MCP or unmapped configuration still block import.
Missing saved teams are not replaced with today's default template. Preserving a
symbolic model selector does not itself prove equivalence of the new runtime's
bound provider/route; that needs model binding conversion and cutover validation.

The importer persists an `importing` state before writes. Runtime startup checks
this under an admission lock shared with the importer, then acquires the existing
namespace writer lock. A partial/aborted/malformed or wrong-namespace migration
cannot start. Completed copies and identity registrations are verified and reused
on retry; conflicting destination bytes are never overwritten. Only after final
archive/source checks and identity registration does an atomic receipt/state
publication admit the candidate. Retrying a committed import returns its checked
receipt without overwriting later App writes. Conversion retains only one bounded
conversation document at a time rather than all history bodies in memory.

`abort_pending_import(fence=...)` blocks an uncommitted destination permanently,
retains partial data for inspection, and releases the unchanged old sources so
the compatible legacy CLI/Desktop can resume. It refuses a committed import:
post-cutover rollback must account for new writes and belongs to the release
coordinator. These remain cooperative local filesystem locks, not proof of
distributed exclusion on Modal/HPC or against old binaries.

Verification passed: 99 migration/fence/application/lifecycle tests, 11 launch
tests, and four independent-package acceptance cases using a clean Python
environment with no installed Pantheon source. The package gate rejects a pending
import, then opens the committed legacy history and continues it through the
existing Model Services route with a controlled engine response. It checks durable
member IDs after drain. A rendered-GUI release case was skipped because its
acceptance script was not supplied; these checks do not establish UI or production
acceptance. Process-kill/resume and pre-commit rollback are tested on temporary
local data.

No live data has been imported and no production cutover is enabled. Remaining P5
work includes all unsupported configuration conversion, default-template capture,
remaining credential provisioning, attachment resolution and distributed writer exclusion;
P6 still needs publication, cutover and post-use rollback.

### Model API credentials through the existing Fleet vault

`migration_credentials.ModelCredentialConversion` binds a verified backup and
live source fence to explicit provider/source/alias/endpoint/reference entries.
The source must be the effective key's inventoried global/selected-project
`settings.json` or the selected launch dotenv file. Each entry consumes that
provider's API-key and API-base fields, resolving dotenv > project > user
precedence. An absent base requires an explicit owner-provided
endpoint; no SDK default, process environment, OAuth login or Hub key is guessed.
Unaccounted nonempty keys, global `LLM_API_*` fallback configuration, or
unsupported configuration still block the
entire import before any credential is provisioned. This converts declared
settings, not a snapshot of an arbitrary running process's effective environment.

The converter uses `LocalModelCredentialVault` with the exact Fleet executable,
state directory, owner and persisted node ID. The ordinary local command
`fleet credentials ensure ... --stdin` either creates the named credential or
verifies the identical endpoint/key already exists. It never rotates/replaces a
different credential. Keys travel over stdin with child output suppressed; only
endpoint-bound `node-secret://` references appear in the conversion descriptor,
App deployment recipe and migration receipt. The existing store's OS protection
is unchanged (owner-private files on POSIX, DPAPI on Windows). This is not a new
credential database or a remote key-management API.

Pass the plan as `import_backup(..., model_credentials=conversion)`. After whole
data/config preflight, the importer records pending intent including the binding
digest, ensures credentials, writes a private binding document and imports data.
Interrupted provisioning is resumable; a conflicting existing key keeps the
target unstartable. Aborting retains vault entries, since another App may already
use a reference; it does not silently delete shared credentials. The old private
backup and source still contain their original values for recovery.

The descriptor's `models` value and `credentials` references feed the existing
Agent deployment composer. At startup, the prepared launch must match the
migration's provider/alias/endpoint mapping and owner/node before the data opens.
This prevents stripping old keys and accidentally selecting a different provider.
Actual key rotation at the same reference/endpoint remains possible through the
existing explicit vault operation. Changing placement or provider composition
requires a deliberate rebind/cutover operation; that coordinator is still pending.
No raw key is included in the Agent release artifact or imported settings.

Existing Model Service Connectors consume these exact same references. The
native acceptance test provisions a backed-up synthetic key and performs real
Connector discovery against a local API using that key. Another test resumes an
old Agent conversation against its original API endpoint; the clean independently
packaged Agent also rejects a changed endpoint and successfully continues with
the converted credential. These are controlled local endpoints, not paid model
or deployed-user tests. This increment preserves explicit BYOK while the existing
Model Services dependency remains available; it does not automatically publish
API models, convert provider names into Fleet routes, or replace platform budget.

Validation: 13 native credential/conversion cases, 11 Agent launch cases and 124
migration/application/model-scope/credential regression cases passed (148 distinct
Python cases). Fleet credential store/CLI tests passed under Go's race detector,
including concurrent identical provisioning and conflicting key/endpoint retries.
Only a temporary Fleet state directory and synthetic credentials were used. The
installed Fleet binary, live Agent and Hub were not updated.

### Launch dotenv backup and conversion

The inventory now includes the launch directory's default `.env`, outside
`.pantheon`, without reading its contents in the dry run. Its existence is part
of the inventory; a present file is retained as an opaque owner-private backup
blob. An explicitly configured alternative requires `environment_file` in the
source spec (absolute path). Import resolves user/project `env_file` in the same
order as Settings and relative to the selected launch directory, then verifies
that the declared file was actually inventoried. An explicit default declaration
with no file, or a comments-only file, no longer unnecessarily blocks migration.
Backups predating this inventory need to be retaken before import.

The model converter accepts provider keys/bases from that file, including values
overriding old user/project keys and endpoints. Bindings must identify the source
of the effective key, not an overwritten key. Empty environment values preserve
the legacy Settings fallback to merged settings. All overwritten nonempty model
fields are accounted for and stripped from App settings; originals remain in the
private backup. Dotenv quoting/export syntax and file-local interpolation/defaults
are parsed without reading the migrator's environment. Missing interpolation
inputs, malformed lines and non-model variables (including assigned empty flags)
block before provisioning or data writes. Raw dotenv files never enter App data
or an App release. Both Agent and the original Model Service Connector can use
the same existing Fleet vault reference.

Creation, modification or deletion of the declared file after backup prevents
import. This is checked by the same source revalidation as histories/settings.
The existing configuration-root leases exclude updated legacy runtimes; they do
not lock external dotenv editors or stop old/distributed writers. As with other
source files, deployment-level writer exclusion remains required for cutover.

This is declared-file conversion, **not** capture of a running process's model
state. A live `set_llm_proxy` platform-budget selection and process environment
overrides still need an explicit handoff. OAuth, global `LLM_API_*` fallback,
non-model environment and other project scopes remain unresolved; they must not
silently change billing, provider selection or execution behavior.

Validation: 101 inventory/backup/import/fence/model-conversion/launch cases and
two clean independently packaged Agent cases passed (103 distinct cases). The
new checks compare precedence with the original Settings implementation, continue
a saved conversation over actual localhost inference, exercise the original
Model Service Connector against that same API/vault reference, reject stale
source files and prevent malformed/unsupported environment from writing target
data or credentials. The package cases cover both settings and dotenv sources
and reject a changed API endpoint. All credentials and histories are synthetic;
no live Fleet, Hub or Atrium deployment was changed.

### Preserve API paths during generic credential delivery

The generic Fleet configuration resolver previously used the vault's lookup
normalization as the delivered API base, implicitly adding `/v1` to a root URL.
The migration converter did the same. That conflated key-record identity with
transport routing and changed custom root endpoints or native SDK request paths.
Both now preserve the explicitly supplied base path (ignoring trailing slashes).
Vault validation and its historical root-/v1 record equivalence are unchanged;
the original Model Service API Connector still normalizes its own OpenAI API
base. No provider-specific branch was added to Fleet.

A regression first reproduced the unwanted rewrite in normal App preparation.
Updated tests cover root, trailing-root, `/v1` and custom-path endpoints, including
the actual native App SDK child. Migrated OpenAI-compatible and Anthropic Agent
conversations exercise real localhost HTTP/SSE and verify `/responses` (or
`/chat/completions`) versus `/v1/messages`, with their original authorization
formats. These are independent native provider compatibility paths, not a claim
that Model Service now translates Anthropic's protocol.

Validation: 88 focused migration/model-scope/launch cases passed, as did the App
configuration lifecycle suite under Go's race detector. The authenticated-NATS
joint gate also passed with two local Fleet managers and six actual native App
processes: packaged Agent, dependency allocator, model-access facade, original
Model Service Connector, Shell and shared Files. It verified scoped inference,
independent Shell sessions, and sibling Files access after Agent retirement.
Hub/engine responses in that gate are controlled fixtures. This proves the local
composition, not a production deployment, paid API call or remote-node rollout.

### Hub startup recipe delivery

Hub now stores versioned startup recipes by authenticated user and workspace
profile. Full owner sessions can save with revision compare-and-swap; Fleet
workload credentials can read but cannot change the recipe. Other narrowed
credentials cannot read it. Disabled/absent recipes return null and start no Apps.
Credential slots accept only endpoint-paired node-vault references. Configuration
values remain ordinary JSON and must not contain secrets. Saving changes neither
running workspaces nor running Apps. Conflicting operation intent is rejected by
the platform's existing durable deployment journal.

The explicit Hub switch `PANTHEON_APP_PRESETS_ENABLED=true` injects a discovery
URL into newly created independent `platform` hosts on K8s and Modal. It excludes
legacy/transitional `chatroom` hosts. The runtime accepts `--app-preset-url` or
`PANTHEON_APP_PRESET_URL`, reuses its existing Fleet credential and validates the
returned owner against its user identity. Reads require the paired HTTPS Hub,
disallow redirects and ambient proxies, limit response bytes and overall elapsed
time, and revalidate the complete recipe before advancing any node operation.
Failure is reported separately from platform readiness. File/URL sources are
mutually exclusive. This is one startup read, not live configuration polling.

Existing workspaces are not restarted/adopted into new settings automatically.
Artifacts, vault entries and persistent owner journal storage still need to be
provisioned. Default topology and CLI/Desktop behavior remain unchanged. No live
Hub, Fleet or Atrium rollout has occurred. Data migration, deployment fencing,
release publication, full provider composition and default GUI cutover remain
required before completing the extraction plan.

Validation: 61 platform/startup/deployment checks passed, and Hub's startup,
Fleet API, K8s/Modal creation and platform topology suites passed (100 distinct
tests). This includes competing initial/update saves with one winner. The
six-process native gate passed under Go's race detector after one authenticated
HTTPS startup read, with fifteen inference rounds and seven actual tool calls;
the engine responses and Hub directory remain controlled fixtures. Its cleanup
poll now waits for explicit revocation even when a newly issued grant has not
yet acquired a maintenance state field. Production acceptance remains pending.

### Joint native Agent / Model Services deployment

The platform entry point now accepts an explicit `--app-preset` (or deployment
environment `PANTHEON_APP_PRESET`) naming the private output of the generic App
deployment composer. It advances the ordinary coordinator in a platform-owned
background task; the platform does not import Agent or wait for its readiness.
Missing/invalid configuration or failed App startup appears in the separate
`platform_app_preset_status` endpoint. Platform shutdown drains an accepted
advancement, but does not stop the Apps. Restart retains original operation IDs
and validates the recipe against the persistent deployment journal. Failed or
unknown operations require explicit recovery and are not silently retried;
manually stopped Apps are not respawned. The driver is bounded and stops once
the startup attempt reaches readiness. It is not a replacement health supervisor.

This runtime path is opt-in. Existing legacy child and CLI/Desktop launch paths
remain available. The native gate below invokes the startup driver using actual
Fleet Managers and the existing coordinator, with a test lifecycle transport;
it is not a live Hub rollout or proof of deployed Atrium login/bootstrap. Live
preset provisioning, release distribution and data migration remain outstanding.
Validation: 66 focused platform/deployment tests passed, including CLI flag/env
delivery, the legacy child command, Agent-import exclusion, malformed/private
file handling, same-operation restart and draining startup work. The six-process
native gate also passed under Go's race detector through the startup driver.

The candidate preset now has a combined native acceptance gate, in addition to
its earlier simulated-node recipe checks. Two isolated Fleet Managers run the
paired Agent release, dependency allocator, Model Services access App and the
existing Model Service Connector, managed Shell and Files Apps as six distinct
native processes. Real install
hooks, prepared configuration, node vault references, authenticated NATS and the
production dependency gateway participate in the same run.

The gate verifies exact upcoming generations, repeat advancement without a
second start, authorized model selection, one streamed inference through the
Connector and HTTP dependency relay, recorded Agent history, model removal after
the access provider stops, and lifecycle cleanup of all six Apps. Three further
Agent turns perform actual Shell calls, followed by one more sibling turn after
logical retirement. Three file turns write from Agent A and read from Agent B,
both before and after A is deleted (fifteen inference rounds total). Distinct
logical Agents share one Shell provider while retaining isolated sessions; state
persists on a later turn of the original Agent. Deleting its conversation releases
only its session, fences further turns and leaves the sibling able to call Shell.
After consumer stop, the remaining scoped grant is revoked and its session is
released while Shell and Files remain running. Both Agents use distinct Files
grants to the same provider, with the filename bound by the gateway; no Files
resource session is created. The project file survives conversation and App
retirement. The engine
response and Hub directory/auth wrapper remain fixtures; process-local test
routing retains TLS hostname/CA verification. This supersedes the simulated-node
limitation for this specific combined startup/call path only. It does not prove
production bootstrap, real GPU calls, complete tool/plugin provisioning, deployed
Desktop/CLI compatibility, migration or cutover. See `agent-app-release.md` for
the opt-in native command and exact test boundaries.

The joint test deliberately does not treat a closed GUI as logical retirement.
The explicit conversation-delete path now closes admission and drains accepted
chat/steer turns, background work from every configuration revision, and accepted
provider calls before retiring its logical owners. The ordinary dependency owner
RPC persists a tombstone, fences every revision, revokes grants, then requests
session release. Shared providers and other logical owners remain active.

Dependency allocator v0.1.1 adds `retire_dependencies`; new Agent packages require
at least that version. The gateway pins policy/consumer identity; callers supply
only their logical owner reference. Lost acquire/issue/revoke/release replies are
retried under the same owner and original operation identities. Pending provider
release remains `retiring`; a lost provider is reported as `lost`, not confirmed
cleanup. Owner maintenance can finish pending retirement after a restart.

The Agent instance journal migrates schema 1 to 2 without changing instance or
revision identities. Durable conversation tombstones prevent reassembly after
restart. Failed retirement retains history for explicit deletion retry; retrying
an already deleted conversation also succeeds after restart. Before unlinking,
the App joins that conversation's pending metadata persistence so a delayed save
cannot recreate the deleted record. App shutdown waits for accepted deletions.
Component tests cover concurrent in-flight transport, old-revision background
work, accepted steer continuations, partial allocation, lost acknowledgements,
restart/migration and invalid receipts. These checks and the native gate do not
establish cross-replica fencing, deployed acceptance or full project completion.

### Shared Files prepared package — local candidate

`apps/file/build_managed.py` builds an ordinary, prepared-config filesystem App
(v0.6.9) using the existing FileManager implementation and standard ToolSet host.
It advertises `fs@1`, `outline@1` and directory/list/stat methods, with explicit
workspace/response limits. It does not ship Agent, settings discovery, a model
SDK or a global ToolSet bus. Legacy constructors retain their original settings
and template fallback; the managed provider receives explicit settings and never
falls back to another Agent's template directories. Native file metadata uses the
Runner-protected `PANTHEON_NODE_ID` before legacy node hints; the joint gate
asserts reads identify the actual provider node. An isolated subprocess gate
forbids Agent/settings/factory/legacy transport imports while executing read,
write, update, search and outline methods from the built package.

The Agent preset now accepts `provider_apps`: ordinary staged App specifications
joined to the same generic deployment recipe, without replacing its three core
components. Existing `$app` references resolve their exact future generations.
No new lifecycle controller or file-specific routing was added. The native gate
installs/configures the Files provider through this path and uses actual gateway
RPCs for both logical Agents. Its path grant is a single bound project filename;
this is not proof of a general filesystem sandbox or arbitrary path confinement.

This candidate is deliberately not a replacement for the shipped combined Files
service yet. Transfer/preview/document helpers and model-assisted operations must
be delivered with their dependencies before full Files/GUI cutover. Their legacy
entry remains available, and the new manifest does not advertise missing methods.
Windows, a separate physical provider machine, deployed Hub/Atrium acceptance and
cross-replica data fencing remain unverified. Native macOS race acceptance passed
with six processes and fifteen connector inference rounds; the associated
package, legacy file and deployment tests passed (56 passed, 3 optional skips).

### Paired Agent release delivery — current increment

The prepared Agent model configuration now supports explicit `fleet_tiers` for
`normal`, `high` and `low`. Previously, selecting an exact Fleet model worked,
but ordinary quality tags still queried the direct-provider selector: a model-
service-only Agent could not use a normal default template. Each configured tier
now names one authorized model or route, reusing existing Model Services
transport, metadata and route fallback. Unconfigured/unavailable tiers and
unconfirmed capabilities fail closed even when BYOK is also bound. No catalog
ordering or model name is used to guess quality. Deployments without the mapping
retain local provider selection; explicitly chosen BYOK IDs still work.

The model catalog exposes the tier mapping. Component acceptance exercises both
exact-model and route tiers against the real Connector with controlled engine
responses, including revocation and capability rejection. The joint native gate
now uses `normal` in its Agent templates instead of embedding a concrete Fleet
reference. This configuration work does not itself deploy the default Atrium
Agent or convert all existing provider settings to Model Services publications.
Verification passed: 102 Python tests, two optional release-input skips, and the
six-process native deployment gate under Go's race detector (fifteen inference
rounds, scoped Shell and shared Files, drain/retirement). Engine output is still
controlled fixture data; no production deployment or paid inference was run.

`pantheon.chatroom.package` now assembles the production GUI and Agent backend
at one explicit version, including the ordinary App host, exact dependency
declarations, pinned/hashed Python requirements and Fleet's existing QUIC workload
client. It does not include a Fleet Runner, Controller, model engine, combined
ChatRoom host or other builtin App implementations. The task state machine remains
Agent-owned. BYOK/OAuth adapters remain available; scoped Model Services is a
declared dependency. The existing CLI/Desktop entry points and legacy App manifest
are unchanged. Build and verification commands are in `agent-app-release.md`.

The actual package exceeded the ordinary 32 MiB tar wire limit. Generic App
delivery now preserves tar for small releases and deterministically compresses
large code packages. Wire bytes remain bounded at 32 MiB; the complete decoded
stream is capped at 128 MiB and extracted incrementally. Digest verification,
path/link restrictions, gzip trailer verification and immutable manifest reads
cover both formats. Clients require the live `artifact_compression: gzip-v1`
capability before sending compressed artifacts. Native and HPC workers share
the same lifecycle implementation. Existing installations and small tar digests
are preserved; compression requires a node update for new large releases.

Evidence: the complete macOS artifact was staged and installed with an isolated
Fleet `NativeDriver`, including the real Python install hook and immutable
manifest query. Repeated preparation reused the cached environment. In clean
Python 3.12 and 3.14 environments, the packaged backend passed exact-model and
route-based Model Service conversations, BYOK, restart/history, access revocation
and drain tests. The paired GUI passed conversation/render/refresh/settings
acceptance with the packaged backend. These model endpoints use deterministic
responses and local control fixtures, not production GPU inference. Linux helper
cross-compilation is not Linux runtime acceptance; Windows packaging is explicitly
rejected until durable owner locks are ported.

Remaining: wire the full owner bootstrap recipe to this release, provision all
enabled plugin/App dependencies, publish and deploy on real nodes, validate data
migration and writer fencing, complete upgrade/rollback and exercise the actual
installed CLI/Desktop compatibility gates. No live installation was changed by
this increment and no P0–P7 milestone is marked complete.

### Agent deployment preset and runtime dependency declarations

The release builder now accepts additional ordinary App dependency declarations
instead of emitting an Agent that can consume only its two startup services.
This closes the missing manifest authorization for Shell/Files/MCP provider Apps;
the generic live dependency owner still checks the installed provider interface
and version before issuing any per-instance resource or grant.

`pantheon.chatroom.deployment` emits a private, reviewable input for the existing
`fleet_app_deploy` operation. It composes exact Agent, allocator and model-access
targets, keeps vault references on their appropriate Apps, ties both policies to
the future consumer generation and preserves approved runtime tool bindings.
It supports extra declared startup bindings for GUI/plugins. It adds no Agent
branch to Fleet's lifecycle manager and discovers no nodes or credentials.

Empty approved tool/model policies can now initialize without selecting an
unrelated service. They reject allocation/inference; an empty model catalog needs
no Hub/GPU request. This preserves initial BYOK/platform-budget-only use while
keeping Model Services explicitly wired. The former non-empty-only validators
would have blocked the real owner Apps even though Agent-only fixture tests ran.

The preset is verified through AppDeployment using the actual paired Agent and
owner-service manifests, with both empty and populated model/Shell policies.
The resolved configurations pass the real policy constructors and retain exact
consumer generations. Node operations and grant issuance in that composition
gate are simulated; production Hub/Atrium bootstrap integration, actual remote
provider calls and the remaining migration/cutover gates are still pending.

### Model Services integration audit — current priority

The initial source audit confirmed a delivery gap in the independent Agent, not an
absence of the earlier Model Services implementation. `apps/model-service` is
the existing Fleet connector; `pantheon.models.client.ModelServices` owns model
references, route resolution and inference transport. The ordinary Agent LLM
dispatch already delegates `fleet-model://` and `fleet-route://` calls to this
client. The legacy GUI lists published models through its Model Services adapter.

At that audit, `ConfiguredAgentApplication` constructed `AppModels` without supplying
its optional `fleet_client`. Its serialized model configuration accepts providers,
platform budget, OAuth and Ollama but has no Model Services consumer binding.
The independent GUI deliberately avoids the legacy global Fleet directory, while
its owned catalog does not yet replace it with authorized Fleet entries. Thus the
normal prepared/native App launch cannot currently consume Fleet model references;
scope-only tests with manually injected clients do not prove that delivery works.
The focused missing-binding/explicit-client regression was rerun and passed.
No live deployment was inspected or changed by this audit. The following
implementation entry supersedes these initial wiring findings, but not the
remaining production authorization and deployment requirements.

Prioritize this before further optional GUI extraction:
1. Provision a consumer-scoped Model Services inference/catalog binding through
   ordinary App launch configuration. Reuse existing connectors, references,
   routing and transports; do not copy engine/model management into Agent or pass
   the broad Fleet owner key to it. Keep management authority separately granted.
2. Use that same binding for the Agent's model picker, capability metadata and
   inference, including cancellation, stream cleanup and revocation/generation
   changes. Do not silently fall back to an unbound API or a different node.
3. Validate a prepared native Agent process end to end against an actual connector,
   including exact model and route selection, tool use, disconnect/cancel and two
   separately authorized consumers. Follow with real-node acceptance. Preserve
   local CLI/Desktop compatibility without mandatory remote Hub/Fleet access.

Direct BYOK/platform-budget/OAuth adapters remain supported compatibility routes;
their presence must not substitute for the requested Model Services integration.

### Model Services consumer binding — implemented locally, issuance still pending

The prepared Agent model configuration now accepts `model_services`, a credential
alias for an ordinary dependency RPC endpoint. `AppModels` constructs its owned
`DependencyModelServices` client and closes it during App drain. No Fleet owner
key or ambient Hub client is imported. The adapter reuses the existing exact
model references, route selection, data transport, SSE parsing and cancellation.
Only catalog, route resolution, connection authorization and enabled engine wake
control cross the dependency RPC; prompts and inference tokens do not.

The owner-side `ModelServiceControl` facade accepts immutable policies pinning
the consumer identity, connector bindings and route revisions. `policy_id` must
be injected by an authenticated dependency gateway, never supplied by the Agent.
Policies authorize entire connector publications, not subsets of models sharing
one connector. An updated connector generation or expanded route is unavailable
until its owner supplies a new binding. Model management is excluded. A mandatory
consumer-aware connection issuer is the extension point for the data plane: when
absent, connect returns unavailable, never an owner workload token. This facade
is not yet packaged into the owner host or deployed.

The native Agent catalog now supplies `fleet_models` and readiness/error fields;
the GUI uses those entries only for an independent App. Failed refresh clears
stale selection entries and metadata while retaining independent BYOK operation.
The legacy GUI directory and CLI model paths remain unchanged.

Local evidence includes actual child Agent HTTP processes, real HTTPS dependency
RPC, the actual Model Service connector and deterministic engine responses for
both exact-model and route-selected conversations. Tests check catalog revocation,
drain, control cancellation, two policies' isolation, generation fencing and
refusal to obtain broad owner grants. Hub directory and gateway/issuer enforcement
are fixtures in this gate; it is not a real Fleet or installed-release claim.

Remaining before shipping: implement consumer-lifetime-bound relay/direct grants
in the Fleet gateway (including revocation and in-flight cancellation), compose
the authenticated owner facade and normal launch provisioning, verify managed
engine wake and direct-only routes end to end, then run real-node and GUI model
selection acceptance. Existing broad workload grants cannot satisfy this gate.
P3/P4 and the overall migration remain incomplete.

### Consumer-bound HTTP dependencies — relay transport implemented locally

The generic Fleet dependency gateway now supports HTTP method/path grants as an
alternative to RPC method grants. The Hub owner-only `dependency-http-grants`
endpoint pins both App generations and signs the exact upstream provider
identity. Consumers receive an opaque bearer and HTTPS origin, not the owner's
Fleet key or upstream JWT. Canonical paths, segment-bounded prefixes and bound
headers are validated; browser access and Fleet control paths are rejected.
Inference bytes reuse the existing outbound App tunnel and streaming proxy.

In-flight requests expire at the grant deadline, cancel immediately on explicit
revocation or journal closure, and recheck both App identities every second.
An unreachable lifecycle check also cancels the stream after the bounded check
timeout. Client disconnection propagates to upstream. The durable journal
preserves policy and credentials across restart; retries cannot expand paths or
extend the upstream token's deadline. HTTP renewal requires a fresh grant instead
of silently extending the gateway receipt beyond its signed upstream credential.

Verification uses actual HTTP/WebSocket/TCP streams, covering prompt delivery,
incremental response delivery, credential isolation and all six cancellation
conditions. Hub API tests cover owner authentication, identity signing, path and
header validation, and response filtering. These are local fixtures, not a live
Fleet deployment or real engine performance result. The previous streaming test
fixture had to consume its POST body before waiting for disconnect, as a real
inference handler does; tests also guarantee cleanup on assertion failure.

Still pending: connect this issuer to the owner-side Model Services facade and
normal App provisioning, add consumer-aware direct transport, verify managed
engine wake and model selection on real nodes, and deploy. This transport alone
does not make the independent Agent integration ready to ship.

### Packaged Model Services access App and relay issuance

`python -m pantheon.platform.model_dependency_package --output <new-directory>
--platform <os-arch>` now builds `model-services-control` v0.1.0 as an ordinary
headless process App providing `model-inference@1`. It is a stateless owner-side
authorization facade for the existing `model-service` connectors, not another
inference engine implementation. Its only extra dependency is pinned `httpx`;
it imports neither Agent, LiteLLM, NATS nor the model inference transport stack.
The App uses the standard host, readiness/drain hooks, Runner RPC token and
prepared generation-bound configuration. No policy or secret is in the artifact.

The backend requires one value, `model_services`, containing protocol 1 and the
immutable policies described above, and one endpoint-paired `hub` credential.
An optional `trust_roots_pem` supports an explicit private CA. It never reads
ambient Fleet keys, proxy settings, or a default Hub. Shutdown rejects new
control requests, drains admitted requests and then closes its HTTP pool.

`ModelDependencyControl` now supplies the facade's real relay issuer through
Hub's owner-only `/api/fleet/apps/dependency-http-grants`. It validates the exact
consumer/provider/owner identities, origin, opaque token and bounded expiry,
and returns only origin/token/expiry. Grant retries share a stable operation ID
within a 30-second window, including after host restart; the 300-second TTL
leaves room for the consumer's refresh margin. No failed call or redirect is
replayed. The granted paths cover text, embeddings, route probes, cancellation,
typed multimodal jobs and media artifacts. They exclude `/rpc`, drain and engine
management. The connector's existing request `X-Model-Config` check is preserved:
the issuer must not overwrite a stale caller revision with newer directory data.
Policies currently authorize the whole connector publication, including its
shared job/artifact namespace; they are not per-model or per-job isolation.

The normal `AppDeployment` coordinator needs no model-specific lifecycle branch.
An Agent package declares a startup dependency on `model-services-control`
(`^0.1.0`, `uses: ["model-inference@1"]`) and a backend credential alias such as
`model_services`. Its prepared `agent.models.model_services` value names that
alias. A deployment recipe binds it as follows:

```json
{
  "model_services": {
    "app_id": "model-services-control",
    "component": "backend",
    "provider": {"$app": "model-access", "component": "backend", "port": "http"},
    "methods": {
      "model_services_control": {
        "arguments": ["operation", "arguments"],
        "bound": {"policy_id": "agent"}
      }
    }
  }
}
```

The `model-access` App's policy uses `consumer: {"$app": "agent"}`, explicit
connector bindings and route revisions. The coordinator resolves both references
to their future running generations, starts the provider first, and issues the
ordinary scoped RPC grant for the Agent. The owner Hub key stays in the
model-access App's node vault configuration. The same recipe also includes the
existing dependency allocator; it does not share Shell sessions between Agents.

Verification includes a real packaged child process serving authenticated RPC,
real HTTPS to a directory/Hub fixture, forbidden ambient/heavy imports, grant
validation, lifetime drain and normal coordinator assembly using the actual
packaged provider manifest. Existing native Agent/connector inference tests and
CLI-compatible model client tests remain separate gates. These do not prove a
live installed Fleet deployment or direct transport. The final Agent artifact
still needs these declarations; the legacy frontend-only `apps/agent` manifest
has deliberately not been switched before complete packaging is ready.

The consumer-bound direct follow-up below supersedes this stage's relay-only
restriction. Complete Agent package/provisioning and GUI cutover, managed
wake/direct-only real-node acceptance, and deployment remain outstanding.
All P0–P7 requirements above remain the completion criteria.

### Consumer-bound direct Model Services transport

The packaged access App now requests `/api/fleet/apps/dependency-direct-grants`
for direct connections. Hub first obtains the same durable HTTP dependency grant
used by relay, then exchanges its ID through the owner-only Controller endpoint
`/apps/dependencies/direct`. The exchange never calls the broad workload-owner
direct API. Direct tokens remain opaque, single-use and bound to the caller's
QUIC peer; responses contain no provider credential or Controller proof.

The node receives the exact consumer/provider generations, inherited HTTP rules
and bound headers, and a private read-only proof. It checks the parent authority
through its saved Controller origin before acknowledging QUIC, before admitting
each HTTP request, and every second while the connection is open. Each check is
bounded by five seconds. Revocation, consumer/provider loss, expiry, unavailable
journal or unavailable Controller stops the stream rather than retaining stale
authority. Authorization checks use the control plane; inference bytes stay on
the direct data plane. This does introduce control requests per active connection;
production latency and load have not been benchmarked.

Controller verification compares the complete stored scope; the proof alone
cannot mint grants or proxy data. The node does not accept a callback URL from a
grant, follow redirects or inherit an HTTP proxy for this check. Issued scope is
copied so later mutation of the control request cannot change its authority.
Nodes advertise `app-direct-dependencies:1` only with the verifier configured.
An older node's strict decoder rejects the new dependency field; a newer node
without a verifier also rejects it. Existing unscoped owner direct connections
are unchanged. Relay fallback remains subject to the existing route policy;
authorization rejection does not permit a broader grant or inference replay.

Local verification uses actual QUIC peers, HTTP streaming and the production
Controller verification client. Seven cases cover explicit revoke, consumer
loss, provider loss, expiry, disconnect, journal closure and control unavailability,
and assert upstream cancellation without relay/replay. Additional tests reject
revocation before QUIC acknowledgement, missing verifiers and mutated scope.
The direct, gateway, transport, Runner and Controller Go packages pass with the
race detector. Hub's 73 dependency tests pass, including owner authentication,
scope inheritance and malformed direct receipts. These tests use local lifecycle
callbacks/directory fixtures: they do not establish a deployed Agent-to-GPU
acceptance result or the complete P0–P7 migration.

## Ordinary HTTP Agent host and durable event replay

`pantheon.chatroom.native:register` now loads the prepared Agent application in
the existing portable HTTP App host. `register_toolset` is the generic adapter:
it runs the supplied ToolSet in embedded mode, registers its declared RPCs,
requires the Runner token, retains concurrent interrupt/status handling and owns
setup-failure cleanup, admission stop and drain. It constructs no NATS/TCP worker
or global service connection. Caller-supplied framework context is rejected;
explicit method parameters and declared metadata kwargs remain usable. Hidden
ToolSet methods remain frontend APIs, subject to the App/gateway authorization.

The native Agent owns an SQLite WAL event journal inside its private data mount.
The legacy NATS adapter and the new journal share transport-independent chunk,
tool-delta, step and completion hooks. `read_agent_events(chat_id, cursor, limit)`
returns protocol 1, ordered JSON fragments, the next epoch/sequence cursor,
`has_more` and `reset_required`. Large events are fragmented into bounded pages
below the 512 KiB dependency RPC envelope. The UI must reassemble an event before
applying it and preserve unfinished fragments alongside its paging cursor.

The journal retains complete events up to a target of 16 MiB / 8,192 fragments;
a single newest event is never truncated merely to meet that target. A cursor
behind retention, from a different journal epoch or ahead of the persisted log
returns an explicit reset, not apparently complete empty history. Clients must
then refresh authoritative conversation history. Restart preserves the journal
epoch and event ordering. Cancellation waits for admitted disk work before
unlocking or closing, and Agent cleanup closes the event writer before releasing
the App data lock. This is one local App writer, not distributed replica fencing.

Verification: 78 tests passed across generic ToolSet hosting, event storage,
actual native Agent HTTP processes, portable hosting, owner-service hosting,
Agent lifecycle/composition, old apphost processes and CLI recovery. Follow-up
checks for metadata kwargs and the original apphost CLI also passed (12 tests).
The real-process test uses the normal HTTP host and a local HTTP/SSE model
fixture, forbids combined-host/platform imports and ambient RPC construction,
executes two turns across restart, recovers chat and Agent identity, replays
chunks/completion without cross-chat leakage, rejects unauthenticated RPC and
checks successful drain. Event tests cover multi-page Unicode messages larger
than the gateway envelope, restart mid-fragment, retention gaps, wrong epochs,
cancellation, private paths and identical legacy event shaping.

This entry is opt-in; the shipped Agent manifest and Desktop/CLI launchers are
unchanged. GUI event consumption, final frontend/backend release,
full model/plugin delivery and live Fleet/packaged Desktop acceptance remain
required. The ordinary HTTP path is not yet advertised as a replacement for the
complete existing Agent UI. No extraction rollout occurred.

## Immutable history snapshots and an explicit App client

The native Agent now exposes `open_agent_history`, `read_agent_history` and
`release_agent_history`. The old `stream_chat_messages` remains a NATS API for
legacy clients; the ordinary HTTP App no longer needs that inbox to deliver a
large history. A snapshot contains full detached messages, total count and the
active stream prefix, without the legacy presentation field truncations. Each
128 KiB ASCII JSON fragment fits below the 512 KiB RPC envelope even after JSON
escaping. The descriptor binds its random id to the conversation and carries
the part count, byte count, SHA-256, event cursor and expiry. Reads are repeatable
and survive process restart. No moving offset pagination or silent truncation is
used. The legacy presentation reader now deep-copies memory before truncating it,
so merely displaying history cannot change authoritative message dictionaries.

Snapshots expire after ten minutes and can be explicitly released. At most eight
active snapshots are retained; capacity exhaustion is explicit and does not evict
another reader. The private history database has a separate lock and transaction
from the event journal so serializing a large snapshot cannot monopolize the
streaming writer. Failed serialization rolls back partial pages. Snapshots are a
transfer cache, not a conversation backup or a migration mechanism.

The event cursor and active prefixes are captured atomically **before** copying
memory. Chunks of an unfinished response are retained separately until its step
or chat completion, even after the bounded replay log evicts them. This prevents
a reconnect from losing text/tool-argument prefixes not yet in saved history.
Native startup emits `chat_finished` with `status: interrupted` for streams left
by a dead process before admitting new producers. It does not resume Python runs.

In the isolated UI checkout, `src/agent/AgentAppClient.ts` takes an explicit App
bridge call and imports no global bus, credentials or desktop store. It validates
and reconstructs history, verifies its checksum, reassembles event fragments and
returns a new cursor state without mutating the caller's prior state. The caller
must render snapshot messages and its `inflight` events, then replay from the
returned state; apply events before committing that state. Persist pending event
fragments along with the cursor. Overlapping deltas for IDs already in the
snapshot are suppressed; completed step messages must be upserted by ID by the
view layer. A replay gap requires a new snapshot. This is not an exactly-once UI
rendering claim, nor a fully extracted GUI. The integration described below now
feeds the shared chat model; the complete GUI entry/service facade remains open.

Verification includes multi-megabyte Unicode history and raw/image fields,
restart during page reads, wrong-chat access, digest checks, fixed expiry,
snapshot capacity, serialization rollback, slow-snapshot/nonblocked live events,
active-prefix retention and interrupted-process recovery. The normal portable
HTTP host + prepared Agent + local HTTP/SSE model also passed the actual compiled
TypeScript client's create/history/chat/replay/history flow. This gate is a Node
protocol integration, not a rendered browser or packaged Desktop acceptance.

Cross-repository gate: build the UI client with
`pnpm exec esbuild src/agent/AgentAppClient.ts --bundle --platform=node --format=esm --outfile=/tmp/pantheon-agent-app-client-acceptance.mjs`,
then run `tests/test_agent_native_process.py` with
`PANTHEON_TEST_AGENT_APP_CLIENT=/tmp/pantheon-agent-app-client-acceptance.mjs` and
the runtime checkout on `PYTHONPATH`. Without this supplied artifact the
cross-repository case explicitly skips; it is not silently counted as evidence.
Backend regression including the enabled cross-repository gate: 48 passed.
Frontend unit/legacy streaming tests: 14 passed; `vue-tsc --build` and targeted
ESLint passed. The complete GUI, package, model/plugin delivery, migrations,
cutover/rollback and installed CLI/Desktop release gates remain open.

## P4 progress: App-owned chat services

The shared GUI now obtains `ChatManager`, `ChatStatus` and `StreamingManager`
from its own Pinia owner. `createAgentChatServices` must run before that owner's
stores/components are constructed; callbacks capture the services during setup
instead of rediscovering the active App after an `await`. Unregistered owners
retain the existing Desktop/page defaults. The new factory is a composition
primitive, not yet the shipped Agent entry point.

`StreamingManager` accepts an injected chat event source and rejects late events
from a released subscription, including one whose setup finishes after disposal.
The injected source never falls through to the global NATS backend. Chat disposal
unsubscribes its listener/transport, clears deferred timers and prevents a pending
history request from reviving the view; it does not stop the backend Agent. The
chatroom status listener and background-task poller also release with their Pinia
store scope. Stores retain their originating Pinia when calling each other after
asynchronous work.

Conversation/composer, timeline, task/output results, canvas/replay and status
consumers use the captured owner. Workflow parsing and notebook tool navigation
accept the originating chat manager rather than looking up another App's tool
result. Existing default utility callers remain compatible. The legacy chat RPC
no longer sends the unused reserved `context_variables: null` argument, which the
ordinary ToolSet App adapter correctly refuses as a framework-owned parameter.

Verification: 113 tests across ten suites passed, covering the real chat manager
with two App owners sharing identical chat/message IDs, async history while the
active Pinia changes, late history after disposal, late subscription setup,
legacy service fallback, task-result ownership, existing conversation/timeline
behavior, and local/Hub connection regressions. `vue-tsc --build` passed. New
service files passed ESLint; a comparison against HEAD found no introduced lint
findings in changed files (117 existing findings remain). These are source-level
unit/component gates, not installed Desktop acceptance.

Remaining P4 work is material: provide the ordinary App RPC service facade,
package the complete GUI, isolate persisted UI settings,
and replace private Desktop/file/Notebook operations with App intents/services.
The full frontend/backend release, model/plugin delivery, data migration,
cutover/rollback and packaged CLI/Desktop gates remain open. No runtime or GUI
was deployed by this change.

## P4 progress: native replay pump into the shared chat model

`AgentReplaySource` now implements the instance-bound event source through
`AgentAppClient`. It loads and verifies the complete snapshot before subscription
readiness, resets the shared GUI's history and transient buffers, restores the
active prefix, then polls replay pages from the snapshot cursor. Reads and explicit
refreshes serialize per chat. A failed read retains its cursor and partial event;
retries back off to a bounded delay. A journal gap or failed event delivery rebuilds
from a new snapshot. Closing cancels scheduling and fences pending delivery without
stopping the Agent. Aborted multipart downloads release their backend snapshot.

`createNativeAgentChatServices` composes this source with the existing chat model.
The native path no longer loads presentation-truncated history from the legacy
chatroom store. Snapshot replacement accepts same-sized edits and reverted/shrunken
history, clears streaming-text/reasoning/tool buffers and transport deduplication,
and preserves local queued/unacknowledged input. The ordinary legacy stream path
is retained. This factory does not yet attach the full chatroom RPC facade or
replace the shipped GUI entry point.

Native snapshots additionally include the current `running` boolean (optional for
older snapshot readers). This clears stale busy state even when the terminal event
has expired from the replay journal. Thread/background-task activity is sampled on
the Agent loop alongside the detached memory copy; replay still begins before the
copy, with the same overlap reconciliation rules as above.

Verification: 129 frontend tests across 12 suites passed, including real shared
chat-model snapshot/prefix/live-step handling and the existing local/Hub connection
regressions. Type checking passed and changed-file lint introduced no findings
(20 pre-existing findings in the files changed in this increment). Backend history,
event journal and native process suites passed 16 tests with both cross-repository
gates enabled. The new gate runs the actual compiled TypeScript replay pump against
the ordinary authenticated HTTP Agent host and local HTTP/SSE model: initial
snapshot, live reply, injected transient read failure/retry, explicit refresh,
close and reopen. It verifies backend shutdown too. This remains Node/protocol
plus GUI-model testing, not rendered-browser or installed-product acceptance.

To enable the new gate, also build
`pnpm exec esbuild src/agent/AgentReplaySource.ts --bundle --platform=node --format=esm --outfile=/tmp/pantheon-agent-replay-source-acceptance.mjs`
and set `PANTHEON_TEST_AGENT_REPLAY_SOURCE` to that file alongside
`PANTHEON_TEST_AGENT_APP_CLIENT` when running `tests/test_agent_native_process.py`.
Without the two artifacts that gate explicitly skips. No deployment was made.
The outstanding facade, GUI packaging, intents, persistence isolation, full package,
migrations, cutover and installed CLI/Desktop gates still prevent P4–P7 completion.

## Native Agent RPC facade and chat-store connection

The GUI now has an `AgentAppConnection` bound to one ordinary App bridge for its
entire lifetime. It adapts that bridge to the existing `ServiceProxy` contract,
so the shared chat store and network helpers can issue chat, stop, configuration,
project and conversation operations without discovering a global backend. The
native backend exposes `get_agent_app_info` with explicit RPC/history/event
protocol versions. It is registered only after portable-host setup succeeds;
the GUI validates it before publishing a usable connection identity.

`createNativeAgentChatServices` registers the connection before stores are
constructed. The chatroom store then connects/reconnects through that fixed App
owner, including when the surrounding page is in Hub mode. A failed health check,
explicit disconnect or owner disposal fences the current proxy, cancels replay,
and invalidates pending connection attempts. Late RPC/project/list results cannot
revive a closed view. A reconnect creates a new proxy for the same bridge. It does
not replay a mutation or reassign the App to a different backend. Metadata reads
run after readiness in the background, preserving responsive startup.

Native stores do not invoke Hub chatroom provisioning, consume the ambient Hub
test-user project, register with the legacy Hub session owner, or clear other
views' global file/image caches. Legacy connect/lifecycle/cache behavior remains
on the original path. Until App-host lifecycle controls are wired into the new
GUI, old restart/release actions on a native-owned store explicitly direct the
caller to its App host; they must never restart a Hub pod instead. This does not
complete the independent GUI, file-service intents or persistence isolation.

An integration regression exercising the actual shared ChatManager, chat store,
facade and replay source exposed a first-send bug: restoring the initial snapshot
erased the optimistic user message before `chat()` had been submitted. Pending
send ownership now starts before snapshot setup and is released even if setup
fails. The pending send's busy state is reapplied after an idle initial snapshot.
The regression first failed with an empty message list and then passed after the
fix. Existing queued-message and missed-completion behavior remains covered.

Verification: 136 frontend tests in 14 suites passed; full TypeScript checking
passed. Changed-file lint introduced no findings (29 existing findings in the
changed files); the new store integration suite also passed ESLint directly.
Backend history/journal/native-host suites passed 17 tests with all three
cross-repository gates enabled. The added gate uses the compiled TypeScript
service facade against the actual authenticated HTTP Agent host and fixture
model: handshake, create, chat, list, close, reconnect, recovered reply and owner
disposal. No closed-proxy call is sent to the server, and the backend drains on
shutdown. These remain protocol and shared-GUI-model checks, not installed
Desktop or rendered-browser acceptance.

Build the additional gate artifact with
`pnpm exec esbuild src/agent/AgentAppConnection.ts --bundle --platform=node --format=esm --outfile=/tmp/pantheon-agent-app-connection-acceptance.mjs`
and set `PANTHEON_TEST_AGENT_APP_CONNECTION` alongside the two previously described
client/replay artifacts when running `tests/test_agent_native_process.py`.
The facade gate explicitly skips without its artifact. No deployment or shipped
manifest change was made. Full GUI entry/packaging, host intents, scoped persistent
UI data, final App delivery/migration, cutover/rollback, installed Desktop/CLI and
cross-node acceptance remain outstanding.

## P4 progress: rendered standalone Agent GUI package

The UI now builds an ordinary App entry with `pnpm build:agent-app`. Its shared
`AgentWorkspace` renders the existing conversation and sidebar components; the
original Desktop `AgentApp.vue` remains a compatibility adapter for window state,
references and conversation presence. Local navigation wins over stale host
echoes, and an explicit new-chat selection cannot adopt another window's chat.
Legacy Desktop streaming and Fleet model selection are installed at the original
composition roots, rather than importing the Desktop implementation into the
new App bundle. This does not remove the existing Desktop/page entrypoints.

Each mounted native GUI owns its Pinia, connection and replay subscriptions. The
entry isolates its document's storage before importing GUI modules, so old Hub
credentials and platform-budget settings are not read from the host origin.
This is an ephemeral view cache, not completed durable preference migration.
Model choices come from the attached Agent's bindings; native setup neither
fetches platform-budget credentials nor calls `set_llm_proxy`. Workspace metadata
comes from the native host's `get_active_project`, backed by its prepared project
snapshot, and does not grant filesystem access.

The real-browser test caught a missed template-loading watcher and an unsupported
startup project RPC. UI watchers are now installed before connection readiness;
the native metadata method resolves the latter. Hidden workspace trees are not
mounted until requested. Settings, file-preview, canvas and editor code load on
demand. The build checks that no Desktop implementation is bundled and that the
startup import closure contains no Monaco, Fabric or PDF.js. This build's startup
JavaScript is 6,115,953 uncompressed bytes, versus approximately 13 MB before the
editor split. This is an artifact-size observation, not a measured heap reduction.

Verification: 98 frontend tests in 15 suites passed, including original chat,
streaming, UI, Desktop surface/connection and API-key form regressions. The form
test now isolates its unrelated budget panel and saved-model backend. Full
TypeScript checking passed. Backend history/journal/native-host suites passed
18 tests with the three earlier protocol gates and the new packaged-GUI gate
enabled. That gate launches headless Chromium against the production GUI build
and an authenticated native Agent process with a fixture model: send, rendered
reply, reload/recovered history without resending, storage isolation, no external
requests, and view disposal. It also verifies hidden editors were not fetched.

To run the browser gate, build the UI to `AGENT_APP_BUILD_DIR`, then set
`PANTHEON_TEST_AGENT_GUI` to the UI repository's
`scripts/test-agent-frontend.mjs` when running `tests/test_agent_native_process.py`.
The test explicitly skips when its artifact/script is not supplied. A local
Playwright Chromium installation is required. The test is not a live-provider,
Fleet deployment or installed native Desktop acceptance test.

The shipped Agent manifest remains unchanged. Native settings, authorized file
operations, Notebook/Desktop App intents, persistent view preferences and full
GUI parity still need completion before switching launchers. The independent
package is therefore an opt-in integration artifact; P4 and the overall plan
remain incomplete.

## P4 progress: explicit GUI service bindings

`agent.view_dependencies` optionally binds the human interface's services by
stable project ID, separately from the execution-instance allocator and plugin
auxiliary clients. Each project must already belong to the prepared snapshot.
Entries reuse ordinary dependency credentials and caller-visible function
schemas, for example (credential names, never secret values):

```json
{
  "view_dependencies": {
    "project-id": {
      "toolsets": {
        "file_manager": {
          "credential": "view-files",
          "functions": [{
            "name": "read_file",
            "parameters": {
              "type": "object",
              "properties": {
                "file_path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "max_chars": {"type": "integer"}
              },
              "required": ["file_path"],
              "additionalProperties": false
            }
          }]
        }
      }
    }
  }
}
```

This example admits only text reads. Directory listing, mutations and the separate
`file_transfer` service need their own authorized descriptors and grants. The owner issues grants
with workspace/session arguments bound at the provider. A GUI request selects an
attached workspace and declared service; it cannot discover a global fallback,
borrow an Agent execution session, obtain the credential or mint a new grant.
`call_view_service` is excluded from the Agent's model-facing tool menu. View
clients close and drain with the App backend.

The shared file-client entry now selects an App-owned client for a native GUI.
Each asynchronous file operation captures its connection and workspace, including
all chunk reads and handle cleanup. A later project/view switch cannot redirect
the remainder of a transfer. Replay caches are per App and cleared when its view
connection closes. Legacy Desktop file routing remains on its existing path.
File transfer uses bounded RPC reads and does not open the legacy global data bus.

Verification: 45 frontend tests passed across native GUI services, chatroom and
legacy file-client coverage. Backend launch, App, history, event and native-process
tests passed 46 cases. The new native-process case sends an ordinary authenticated
App HTTP request through the actual TLS dependency client to a deterministic
grant fixture, verifies the delivered credential and rejects an unattached
workspace without forwarding. Separate tests cover method/argument rejection,
no retry/fallback, isolated grants and draining an accepted call on shutdown.
The existing real-browser chat/reopen gate remains enabled in that backend run.

This is not full file-UI acceptance: live Files grants, isolated conversation
workspace bindings, cross-node file references, large transfers, and rendered
preview/edit/upload/download workflows still need deployment and validation.
Settings and Notebook/Desktop intents remain separate outstanding work. No
shipped manifest or live node has been switched.

### Rendered file editing through ordinary dependencies

The independent Agent window now exposes its workspace tab. Opening a file
expands that window's own detail panel; the shared page still uses its existing
store-controlled panel. Native views do not fall back to a Hub volume preview or
borrow Hub account state when a bound Files service rejects a request. The Hub
sharing dialog remains available on the legacy path; an App-owned sharing intent
is still required before that action can be offered in the independent package.

`tests/test_agent_gui_files.py`, enabled with `PANTHEON_TEST_AGENT_GUI` pointing to
the UI repository's `scripts/test-agent-frontend.mjs`, exercises the production
GUI build in Chromium against an actual native Agent HTTP process. The Agent
calls a TLS grant fixture which executes the real `FileManagerToolSet` and
`FileTransferToolSet` on a temporary directory. The browser opens the file tree,
reads a Python file through bounded transfer calls, enters edit mode, types into
Monaco, saves, reloads the page and reads the saved content. The gate separately
checks exact disk contents, transfer handle cleanup calls, and absence of the
legacy `proxy_toolset` route. It also retains the chat/replay and host-storage
isolation assertions. This caught a real collapsed-panel navigation bug; the
test uses ordinary pointer and keyboard input rather than forced clicks or
editor-model injection.

Verification: the browser/file gate passed, 32 frontend regressions passed across
shared Agent workspace and existing/native file clients, the production bundle
built, full Vue type checking passed, and changed-source lint introduced no new
findings (four existing findings retained). This proves the small text-file
preview/edit/reopen path locally, not uploads, large-file behavior, live Fleet
grant provisioning, packaged Desktop execution, or full P4 completion.

The same gate now uploads a 120,000-byte text file through the workspace file
input, waits for the completed file-tree entry, and verifies exact disk contents
and removal of staging files. Native GUI contexts request destination-directory
staging and 48 KiB upload chunks; the old client's global `/tmp` staging would
violate a project-only grant. These options belong to the generic file-client
context, not an Agent-specific branch in the Files provider. Existing desktop
and legacy transport defaults remain unchanged. Scoped cancellation closes the
owned handle and attempts to delete only its staging file; the destination is
not moved over. The provider fixture rejects staging and move paths outside its
workspace. Frontend coverage now passes 34 cases, including workspace changes
during upload and cancellation. Native-process rendered upload/edit/reopen passed
against actual Files implementations; this does not prove arbitrary large files,
native download pickers, isolated conversation workspaces or remote Fleet grants.

## Recoverable configured-App deployment

`AppDeployment` and the platform-only `fleet_app_deploy` RPC now advance a bounded
set of ordinary configured Apps. The owner supplies exact target nodes, staged
artifact digests, scopes, expected generations, configuration and dependency
bindings. Store/package transport still owns uploading the immutable artifacts.
The recipe is checkpointed before any node mutation. Subsequent calls use the
same deployment operation ID; queued/running results require another explicit
advance. There is no detached deployment loop after platform shutdown.

The coordinator reuses installed revisions, installs missing staged revisions,
prepares all App identities, and starts providers before their consumers through
`DependencyStarter`. Structured `{"$app": "name"}` configuration references resolve
to the exact upcoming running generation. A dependency provider reference also
specifies `component: backend` and `port: http`. References in bindings establish
startup order; references in configuration do not. This lets an allocator's
immutable policy name its future consumer without creating a startup cycle.
Only after the provider is ready does the consumer receive its scoped grant.

Dependency declarations distinguish binding time:

```json
{
  "dependencies": {
    "dependency-binding": {"range": "^0.1.0", "uses": ["dependency-binding@1"]},
    "shell": {"range": "^0.6.0", "uses": ["shell@1"], "binding": "runtime"}
  }
}
```

Omitted `binding` means `startup`, retaining the existing required-binding
behavior. `runtime` declarations are fulfilled through the live owner's scoped
allocation policy; they neither grant ambient discovery nor make the dependency
optional. This supports a separate Shell session for each logical Agent after
its durable identity is reserved. Services with no startup dependencies can now
use the same prepared configuration/start path (for example the allocator).
A runtime declaration cannot be supplied as an initial startup grant.

Lost install/prepare/configure/grant/start acknowledgements resume the original
node operation or issuance ID. A failed operation, stopped/replaced instance or
missing original installation requires explicit recovery; the coordinator does
not create a new attempt, stop other Apps or silently restart a consumer. Public
progress contains identities and phase only; recipes, policies, vault references
and bearer credentials remain private. `inspect` is explicitly the last durable
checkpoint, not a fresh readiness assertion. Grant renewal stays with the
existing independent platform maintenance task.

Verification: 191 Python regressions passed across deployment failure injection,
owner-service startup, dependency/session assembly, portable hosting, configured
Agent processes and CLI recovery. The subsequent runtime-binding isolation test
also passed (24 live-binding tests total): two logical Agents retain distinct
Shell sessions while Files grants share an explicitly bound workspace. The
race-enabled Go controller integration exercised the actual Python coordinator,
authenticated NATS, Fleet Manager, native consumer process and scoped TLS gateway.
It installed a previously staged revision, prepared/started it, invoked its
provider, reconstructed the coordinator across polls, and repeated the completed
operation without reinstalling or disturbing a separately running instance.
The control HTTP wrapper and provider are fixtures; this is not a live rollout.

Final Agent frontend/backend packaging, production recipe delivery, distributed
owner fencing, packaged Desktop/CLI acceptance and data migration remain open.
This generic orchestration does not complete P2/P3 or advertise the existing
frontend-only Agent manifest as a standalone runtime.

## Owned Agent application and restart follow-up

`pantheon.chatroom.application.AgentApplication` assembles the actual AgentRuntime,
TemplateManager, durable instance factory and enabled scoped plugins. Local and
Fleet launchers supply the same explicit project/model/dependency integrations;
the application does not import the combined ChatRoom or construct a controller.
Delivered instance clients, auxiliary clients and optional allocator cleanup are
drained in the Agent lifecycle. Enabled plugins with missing capabilities fail
setup instead of silently disappearing.

`AgentAppData` holds the namespace's local writer lock before runtime/template
construction and retains it through conversation/plugin/tool drain. Stable
project IDs route memories under the App data mount. Stable and candidate Apps
can therefore access the same project files without sharing conversation stores.
Renaming or relocating a project with its existing ID keeps its conversations;
workspace resolution reverses the explicit binding, never treats the App data
directory as a project. No automatic import of `.pantheon/memory` occurs. Legacy
CLI/Desktop composition retains its original project-local routing.

The restart test found pending metadata writes after shutdown (including a new
chat's template). Agent cleanup now joins/cancels debounce timers and strictly
flushes every opened memory store before releasing the writer lock. It does not
use the old whole-store save/prune operation, which can delete unloaded histories.
An injected disk failure produces a failed shutdown; it cannot be reported as a
successful drain. Recovery/cutover policy after such failures remains a P5/P6
supervisor responsibility.

Conversation recovery/storage now belongs to `pantheon.internal.memory`.
Existing REPL module paths are aliases to the same implementation, preserving
CLI imports and hooks. Scoped Agent runs retain their per-run workspace context
without changing the process cwd when restoring an isolated conversation; the
legacy terminal's worktree restoration behavior is retained.

Verification: 103 targeted Python tests passed, including real generic-apphost
child processes and TCP RPC. The process test creates two chats, invokes a local
HTTP/SSE model fixture, stops, starts a fresh process, restores the same Agent
instance ID/template/history and continues the conversation. It denies imports
of the combined legacy host/REPL. Separate integration tests exercise real HTTPS
tool clients, two data namespaces over one workspace, immediate restart, pending
tool drain, failed writes and JSON/JSONL histories that were never loaded. These
checks also cover existing REPL recovery and memory-routing compatibility.

The original process test supplies launcher capabilities programmatically. The
serialized entrypoint follow-up below also runs this acceptance using the actual
configured composition. Owner bootstrap, complete OAuth/Fleet model delivery,
final frontend/backend package and packaged CLI/Desktop gates remain outstanding.
`apps/agent/app.json` has not been advertised as a ready standalone backend, and
no extraction changes are deployed. This advances P3 and prepares P5; it does not
complete either milestone.

## Prepared Agent startup and scoped model selection

`pantheon.chatroom.launch:ConfiguredAgentApplication` is now an opt-in backend
entrypoint for the ordinary `pantheon.apphost`. It assembles AgentApplication
directly from the generation-checked `PANTHEON_APP_CONFIG` snapshot. It does not
import the legacy ChatRoom, start platform services, discover a Fleet owner key
or substitute a local dependency when a grant is missing. `data_dir` is an
explicit launcher argument, separate from shared workspace files. Prepared
configuration now retains its validated owner/node identity in the SDK object;
callers cannot choose a different consumer in the Agent configuration.

The `values.agent` protocol-1 configuration contains:

| Field | Meaning |
| --- | --- |
| `namespace` | Stable App data namespace; changes require a separate data mount/migration |
| `projects`, `active_project`, `default_project` | Explicit stable project IDs and paths; no global registry fallback |
| `settings` | Deployment defaults beneath App-private saved settings; credentials and `env_file` do not belong here |
| `models.providers` | Provider name to a credential alias in the prepared component snapshot; the endpoint and key are used together |
| `models.platform_budget` | Optional dedicated budget credential alias; selects platform OpenRouter routing without modifying BYOK credentials |
| `models.oauth` | Explicit list of OAuth providers using `<data_dir>/oauth/<provider>.json`; no automatic OS-user/CLI credential import |
| `models.ollama` | Explicit Ollama origin (or `/v1` URL); discovery and OpenAI-compatible inference use that same origin |
| `dependencies.allocator` | Credential alias for the scoped live-binding RPC, not a Fleet management key |
| `dependencies.profiles` | Approved tool/MCP names, dependency aliases and caller-visible schemas |
| `auxiliary` | Optional explicit tool/MCP bindings for enabled memory/learning work, using the existing credential/function schema |

Shell bindings are allocated only after reserving each logical Agent's durable
identity. Task output verification uses that Agent's Files binding and rejects
a different requested node; it never checks the Agent host's local disk instead.
The owner must still provision and renew these grants. Enabled plugins continue
to fail visibly when their required bindings are absent; the launcher does not
disable plugins to force readiness.

`AppModels` supplies the same scoped model selector to initial construction,
quality tags, model listing and per-chat model changes. Scope-specific OAuth and
Ollama state replace legacy global caches, and credentials disappearing from an
App cannot be masked by an old cached provider. Platform-budget mode includes
OpenRouter and validates the paired proxy credential. Incomplete scoped model
configuration reports an error rather than inventing an unbound fallback.

Two regressions found while wiring the entrypoint are fixed: explicit Settings
reload no longer replaces its environment with `os.environ`, and `.env`
interpolation uses that private environment rather than borrowing host variables.
Legacy Settings without an explicit environment retain their behavior. New App
bootstrap does not copy package settings over deployment defaults. A scoped
per-chat model change updates the saved conversation configuration, not the
immutable packaged template or a template shared by other conversations.

The generic-host subprocess acceptance runs both programmatic and prepared
configuration, real TCP RPC and HTTP/SSE model calls, followed by graceful exit,
fresh process startup, stable instance identity and continued conversation.
Additional launch tests cover BYOK and budget credential/endpoint pairing,
two logical Agents with different Shell owners, Files-backed task outputs and
closed clients after drain. Dependency issuance/transport in that last test is
a fixture; actual gateway authorization remains covered separately.

Verification for this follow-up: 250 tests passed across configured launch,
scoped and legacy model selection, model-call routing, prepared configuration,
Agent composition/process recovery, instance factories, plugins, platform
settings, templates, lifecycle/drain and REPL recovery. One pre-existing image
priority discrepancy described below was reproduced separately, then deselected
in the combined run; it is not counted as passing. `git diff --check` passed.

Known baseline discrepancy: `test_resolve_image_gen_model_prefers_gemini_when_both_providers_available`
expects Gemini first, while the pre-change HEAD implementation returns OpenAI
`gpt-image-2` first. This extraction retains that existing image-generation
priority; the mismatch is not a newly introduced scoped-selection failure.

Remaining P3 delivery: platform-owned allocator bootstrap/prepared credential
delivery, a capability-scoped Fleet inference client (never the owner's general
ModelServices key), OAuth session provisioning/migration, complete enabled-plugin
profiles and local CLI/Desktop launch integration. Model-call/OAuth isolation has
local tests; no live OAuth login or live Fleet inference through this new entrypoint
is claimed. The public Agent manifest and shipping launch paths remain unchanged.

## Scoped remote allocation follow-up

`DependencyBindingService` exposes only allocation under an immutable owner policy;
the existing dependency gateway binds its policy selector, consumer identity and
provider generation. `RemoteDependencyBindings` submits logical owner/revision IDs
and approved aliases over the ordinary HTTPS RPC SDK. It cannot upload policies,
choose a node/provider/workspace, obtain management credentials or renew grants.
`DependencyInstanceProvisioner` uses either this remote capability or the local
scoped capability. Agent instance/configuration behavior remains the same.

The remote client bounds concurrency and drains accepted requests before closing;
observer cancellation cannot detach a live transport thread. Failed allocation
does not mint a new operation ID. Owner-side replay, sessions and maintenance use
the existing durable implementation. The facade suppresses upstream secret-bearing
errors and requires authenticated host/gateway composition; it is not a public
unauthenticated allocation endpoint.

Verification: 207 Python tests passed across allocation, Agent instance factories,
dependency clients/resources/maintenance, platform host and REPL compatibility.
The controller integration also passed with `-race`: a separate managed native
consumer acquires a binding through a managed allocation provider and the actual
TLS gateway/NATS, then invokes the provider on the other node Manager. Replays do
not allocate another binding; policy/identity overrides and unapproved aliases
are rejected; stopping the consumer denies subsequent allocation.

That Go integration's privileged provider bootstrap and scoped-credential handoff
are private test fixtures. The separately packaged owner service described below
now supplies production startup and maintenance code; automatic initial
prepared-start handoff and the packaged Agent/GUI remain outstanding. This is
not P2/P3 completion or deployment. Legacy CLI/Desktop launch paths are unchanged;
their full release gates below still apply.

## Prepared owner allocation service

`pantheon.platform.dependency_package` builds an opt-in, headless
`dependency-binding` App. It uses the ordinary portable native host, lifecycle
hooks, configuration delivery and dependency gateway. Only the allocation method
is registered. The artifact contains a bounded set of platform modules and pinned
`httpx` / `nats-py[nkeys]` dependencies; it contains no Agent engine, GUI, secrets,
policies or mutable journals. The generation-bound policies arrive through
prepared configuration, not editable package files or consumer RPC arguments.

Build code with `python -m pantheon.platform.dependency_package --output <new-dir>
--platform <linux-amd64|linux-arm64|darwin-amd64|darwin-arm64>`, then use the existing
Fleet stage/install/prepare/configure/start APIs. The owner journal currently
requires POSIX; Windows consumers may use its gateway but Windows owner hosting
is not implemented. The build does not install, stage, publish or start anything.

The backend's declared inputs are:

- `values.dependency_binding`: `{protocol: 1, policies: {...}}`, where each fixed
  policy has one exact consumer identity and approved provider/method/resource
  bindings. Optional `trust_roots_pem` supplies explicitly configured private CA
  roots; verification is never disabled.
- `credentials.controller`: an endpoint-bound **owner** vault reference for the
  HTTPS Fleet controller. Its authenticated join must return the configured Fleet
  owner; wrong-owner, missing-credential and unavailable joins fail startup.
- `credentials.hub`: a separate endpoint-bound **owner** vault reference for the
  HTTPS Hub grant authority. Neither credential uses an ambient key or proxy.

This service is trusted platform infrastructure. Never pass its owner credentials
to Agent or install it with an Agent-writable data/code binding. The consumer gets
only a normal gateway grant for `dependency-binding@1`, with `policy_id` bound by
the gateway and only `owner_ref`, `operation_id`, `aliases` callable. To authorize a
prepared Agent start, the policy pins that consumer's upcoming running generation;
the existing `DependencyStarter` supplies the allocator grant in its declared
credential slot before starting it. Automated orchestration of this full sequence
and selection of production vault references remain to be wired into deployment.

`DependencyBindingHost` holds one private owner/instance data lock, runs independent
grant and resource-session maintenance loops, drains admitted calls before closing
its control connection, and resumes durable receipts after restart. Shutdown does
not revoke a still-live consumer. Temporary NATS credentials are private and removed
on failure/close; reconnect obtains credentials only from the same configured
controller and does not replay mutations. The control transport offers only status,
installed manifests and resource-session calls, with no default-node fallback.

The portable host now supports `AppContext.require_rpc_token`. This service opts
in, requiring the Runner's per-generation token for RPC and drain POSTs. Its stop
hook is bound to the backend component so Fleet supplies that token and assigned
port. Bound probes use Runner-owned coordinates instead of mutable endpoint-file
contents. Existing Apps retain their previous behavior unless they opt in.

Verification: 174 Python tests passed across the owner package, dependency assembly,
maintenance, RPC facade, resource sessions, portable host/environment, prepared
Agent launcher, process restart, REPL keys and conversation recovery. New tests use
real HTTPS and an operator/account/user JWT-authenticated local NATS server. They
launch the generated owner package in a separate interpreter with Agent imports
forbidden, invoke its HTTP API, check authorization and actual readiness/drain
commands, and exercise logical-owner isolation, stable replay, restart recovery,
exclusive writers, credential cleanup, foreign-owner rejection and HTTPS grant
issue/renew/revoke. Hub/controller/node replies are fixtures. No enrolled Fleet,
fresh dependency installation, full Agent deployment or native Desktop packaging
acceptance is claimed by these tests.

## Private Agent settings and deferred Skills discovery

The native Agent now exposes `get_agent_settings` and `save_agent_settings`.
They operate on the App-owned configuration under its private data directory,
not project files or the host user's settings. Responses include a content
revision, the running revision, saved preference overrides and frozen effective
preferences. Saves compare the supplied revision under a cross-process file lock;
a stale view gets a conflict without overwriting another view's changes. Atomic
0600 staging and replacement preserve the old document on a failed save.

Only declared preference sections are writable through this API. Deployment
connections, service grants, environment-file paths and credential fields cannot
be changed through it. Existing unmanaged fields remain on disk and are omitted
from the response. JSON must be bounded, finite and have matching top-level
section types. This is not yet complete nested semantic validation, immutable
configuration history or P6 candidate validation. Saving does not reload the
current runtime: the next backend start loads the saved overrides.

The shared configuration panel selects the private editor when it has an owned
Agent connection. Legacy Desktop continues using its existing ConfigTab. The
editor preserves a conflicting draft, shows restart-pending state, and can reload
the saved document. This does not yet implement credential editing or restart
orchestration from the GUI.

The real browser gate exposed eager Skills filesystem discovery while merely
opening Custom/Config. TemplateDashboard now scans only when its Skills tab is
visible in management mode. Hidden installs invalidate caches without starting
RPCs; returning to Skills refreshes invalidated entries. Event listeners are
removed on unmount. Visibility defaults to true for existing Desktop callers.
Native Skills CRUD still needs an App-owned resource contract; deferring its
unrelated requests is not a claim that native Skills management is complete.

Verification: 24 runtime tests passed, including an actual native HTTP process
save/restart and two simultaneous revision-checked writers. Eleven UI tests cover
the editor, conflicts, existing Teams/Agents/Skills behavior and hidden-cache
invalidation. Full Vue type checking and production Agent build passed; scoped
lint introduced no diagnostics. The rebuilt package's real-browser gate passed
chat/replay, actual file read/edit/reopen, multi-chunk upload and private settings
save/reload without `proxy_toolset` or live `reload_settings`. File tools use a
controlled TLS dependency fixture and model replies a deterministic provider;
this is not live Fleet or packaged Desktop acceptance. No deployment occurred.

### App-owned skill authoring and legacy scope compatibility

The independent Agent GUI can now list, read, edit, create, delete and move its
own skill resources through `agent_skill_files`. The two existing scope keys
remain `project`/`global` for protocol compatibility, but the independent GUI
labels them Agent overrides/defaults and resolves both against that App's
explicit configuration directories. It never discovers the OS user's home or
requests workspace Files access to edit its own skills. Text preview uses the
same App-owned resource route, including the editor's Open as file action.

Edits carry the revision of the editor draft, independently of background tree
reads. Conflicts preserve the draft and require reopening; successful writes
return the exact saved revision. Reads are bounded to 48 KiB chunks and writes
currently accept UTF-8 text up to 64 KiB. Larger text edits and additional binary
asset/download integrations remain outstanding; this is not a claim of complete
Skills/Store authoring support.

The shared scope-move implementation now uses the supplied Settings roots instead
of `Path.home()`. It preserves legacy relative-path forms and conflict prompts,
rejects scope-root/traversal/symlink moves, stages the destination and restores
source/target on handled copy/publication failures. Recovery artifacts remain if
restoration itself fails. This is cooperative local locking and failure recovery,
not a crash journal or distributed writer fence. Admitted disk operations settle
before request cancellation releases their owner, including private settings I/O.

Original Desktop skill reads and saves now use the same project scope, preventing
an edit from being redirected into a per-chat workspace. Existing CLI/Desktop
entry points remain in place. Validation: 57 runtime tests for skill resources,
scope moves, cancellation, settings and runtime ownership; 31 UI tests; full Vue
type-check and independent production build. The real native HTTP process plus
production GUI browser gate passed skill edit/save/reopen/text preview alongside
existing file upload and settings checks. An additional 12 legacy REPL/recovery/
runtime-boundary tests passed. Targeted lint introduced no new findings.
These results do not satisfy the packaged Desktop, release migration, or P4–P7
acceptance gates below, and no live deployment was performed.

### Host navigation without embedded platform panels

The independent Agent's expanded sidebar and collapsed rail now route Fleet and
account navigation through an injected, view-local host port. Its account entry
does not instantiate the old login component or read authentication state to
decide where to navigate. The original Desktop/page entry points retain their
existing Cluster/account paths when no independent host is installed. Other
shared components still require their remaining platform/API audit.

The ordinary App SDK has `app.apps.open(appId)`, gated by the embedding shell's
advertised `appNavigation: 1` capability. Packaged windows handle requests only
from their ready iframe, resolve the current launcher-visible installed App, and
use the normal launch/focus operation. Only an App id crosses this operation;
node, release, file paths and other window options are rejected. An acknowledgement
means a window was accepted, not that its backend is healthy. Errors remain
visible; navigation is not automatically retried. Unsupported hosts reject
immediately, including the legacy sidebar host. The SDK also rejects boot and
RPC/navigation replies from sources other than its embedding parent. UI and
portable-runtime app-host assets were updated together and compare identically.

Validation: 33 UI tests across shared Agent workspace compatibility, expanded and
collapsed navigation, the actual inlined SDK, shell routing and packaged-window
handshake/source validation. Full Vue type-check and independent production build
passed with no new targeted lint findings. Three runtime/integration tests passed:
the real native Agent plus production-GUI browser workflow and portable App
packaging/HTTP asset delivery. The browser workflow clicks Fleet and Account
settings and verifies calls to an explicit host navigation fixture; actual shell
launch routing is checked separately in component tests. This is not a live Fleet
deployment or an installed Desktop end-to-end navigation claim.

Scoped notebook/cell/file intents, Evolution extraction, credential provisioning,
ordinary shipped Agent delivery and platform-independent full Desktop acceptance
remain outstanding. No live deployment or replacement of existing launchers was
performed in this step.

## Required compatibility: Pantheon CLI and Pantheon Desktop

The extraction must preserve both existing products. Their retirement is not an
objective of P7. Compatibility launchers may compose the Agent App and its local
dependencies; they must not put Agent execution back into the platform core.
The long-term implementation shares the same versioned Agent engine and GUI,
rather than maintaining a second legacy Agent implementation indefinitely.

| Entry | Compatibility contract | Target composition |
| --- | --- | --- |
| `pantheon cli` / `python -m pantheon.repl` | Interactive use, `-i` one-shot, `-r`/`--resume`, templates, workspace, model selection and existing automation remain supported | Local Agent App composition by default; remote attachment can be optional |
| `pantheon ui` / local desktop backend | Preserve local startup and existing connection arguments during transition | Local platform plus separately owned Agent App and required headless Apps |
| Native Pantheon Desktop local profile | Preserve bundled startup, project selection, connection readiness and shutdown without requiring a Hub login | Ship a tested, compatible local App set with the desktop distribution |
| Desktop connected to a remote deployment | Preserve supported connection/authentication flows and conversation access | Discover platform and Agent independently; negotiate their protocols explicitly |

Current source anchors: `pantheon/__main__.py` exposes `cli` and `ui`;
`pantheon/repl/__main__.py` constructs a local `ChatRoom`; and
`pantheon/chatroom/start.py` emits `PANTHEON_READY`. In the UI repository,
`src-tauri/tauri.local.conf.json` bundles `pantheon-backend`, and
`src-tauri/src/lib.rs` launches it and consumes readiness. These are compatibility
boundaries, not evidence that the new composition is already shipping.

Local use must not require cloud Hub credentials, platform budget, a separately
installed Fleet daemon or an external NATS deployment. A launcher may start
bundled local infrastructure. Remote model APIs still require their own network
access and credentials; offline inference requires an available local model.
Preserve BYOK and configured model routes. Do not silently replace a selected
local backend with a cloud one.

P3 must provide local and Fleet composition roots for the same Agent package.
P4 must let the native client load that package's GUI and retain local connection
support. A versioned readiness descriptor may add platform/Agent identifiers, but
must not silently change the meaning of legacy `service_id`, `ws_url` or
`tcp_url`. Keep a versioned adapter or explicit legacy launch profile until the
supported desktop clients have a tested replacement. Readiness must describe
actually usable services, not just successful process spawning.

P5 must inventory existing `.pantheon` settings, key/OAuth storage, templates,
conversation memories, project mappings and attachments for both CLI and desktop.
Provide compatible reads or an explicit backed-up migration. Fence the old
writer before activating a migrated store; never let legacy and new runtimes
write the same conversation concurrently. Rollback must reopen a compatible
store or restore the backup, not assume an older runtime can read a newer schema.

Mandatory release gates (all remain required even if platform-only tests pass):

1. With Hub/Fleet endpoints absent, start the local CLI, run a deterministic
   conversation and a real local tool call, interrupt it, restart and resume it.
   Exercise interactive and one-shot entry points, old flags, templates and exit
   behavior. Use a local model or fixture for the no-cloud test.
2. Install the built native local desktop package into a clean environment;
   start it, select/switch projects, chat, execute tools, cancel, quit and reopen.
   Test the actual packaged backend and readiness handshake, not only source
   imports or mocked connection adapters.
3. Upgrade representative existing CLI and desktop data, verify conversations,
   configs and project files, inject migration failure and exercise rollback.
4. Exercise supported old/new desktop-backend protocol combinations. Unsupported
   versions must give an actionable upgrade path, without silent fallback or an
   endless loading screen.
5. Stop/uninstall Agent and confirm the independent PantheonOS shell and other
   Apps remain usable. Launching Agent again must reconnect both supported clients.
6. Record macOS and Linux execution results separately; Windows packaging and
   real execution remain unverified until a Windows runner is available.

During migration, existing entry points and working adapters remain in place.
P7 can remove obsolete internals only after the equivalent supported product path
passes these gates. Unit/component regressions are useful evidence but cannot
mark packaged desktop, live model or data-upgrade acceptance complete.

Compatibility baseline recheck: 23 tests passed across REPL key handling,
conversation recovery, App-host Agent drain and runtime boundary suites; the UI
repository's legacy/independent connection suites passed 8 tests. These checks
cover existing components only. The packaged/local-product gates above remain
pending, and this documentation change does not deploy a new runtime.

P2 managed-provider follow-up: Go Apps now have a reusable authenticated loopback
HTTP host and start/readiness/drain entrypoint matching ordinary Fleet invocation.
The lifecycle driver supports identity-bound process component hooks without
Shell-specific dispatch. Shell can be built as an opt-in immutable native package
and run in two independent Fleet-managed deployments. Actual package integration
covers lease acquisition retry/renewal/release, retained environment isolation,
pending-output stop blocking, completion while draining, new-work rejection,
sibling survival and data retention. Control credentials are stripped from Shell
child environments. The full appsvc/lifecycle race suites passed locally; native
packages were also built/manifest-validated for macOS arm64 and Linux amd64/arm64.
Only macOS execution was exercised. This is not a live deployment or P2 completion:
Agent-instance session assembly, cross-node scoped consumer assembly,
explicit project workspace attachment, complete detached process ownership,
gateway recovery and the final Agent package remain outstanding.

P2 owner/session follow-up: the platform now journals exact-generation resource
acquisition intents separately from dependency credentials. Its owner-only
`fleet_app_resource_session` RPC acquires, inspects or releases a declared
`resource-session@1` provider. Acquisitions use stable lease IDs and opaque logical
owner IDs. The platform's independent session loop queries before renewing,
releases sessions whose consumer deployment is authoritatively stopped/replaced,
and finishes already-journaled releases without needing the consumer online.
Older/incomplete/foreign inventory and transport failures defer; a replaced
provider terminates the binding as unavailable without claiming remote cleanup.
Grant-authority delays cannot block the separate session maintenance loop.

The real local integration runs the shipping native Shell package on a Fleet
provider Manager and a separate consumer Manager, with authenticated NATS between
Python owner processes and both nodes. It verifies logical-owner cwd/environment
isolation, lost acquisition acknowledgement, fresh owner-process recovery,
actual lease renewal (only the coordinator schedule is advanced), selective
release, automatic release after consumer stop, and old-generation rejection
after provider restart. This does not run actual Agent instances or a remote HPC
node. The legacy factory indexes startup bindings by configuration ID. The new
instance factory described below consumes preassigned instance bindings, but live
dynamic assembly and delegated logical-owner termination must still be connected
before claiming per-Agent automatic Shell lifetimes. Durable gateway grants,
distributed coordinator fencing, detached process ownership and workspace
attachment also remain unfinished. No live deployment is included.

Verification for this follow-up: 141 Python tests passed across session ownership,
dependency assembly/maintenance, platform service/bootstrap/authenticated RPC,
App lifecycle, Agent tool bindings and drain. The Controller's actual
authenticated-NATS/native-App integration passed with Go's race detector,
including the new Shell owner scenario. Grant and session loops have separate
wakeups and are both cancelled/awaited on platform shutdown; regression tests
cover a grant authority that remains pending while sessions continue maintenance.

### Live logical-instance binding follow-up (P2/P3)

`LiveDependencyOwner` now composes declared dependencies for an already-running
consumer App. It validates immutable consumer/provider manifests and exact live
generations, journals the binding recipe before resource mutations, acquires or
observes generic provider sessions, and issues operation-stable gateway grants.
Its journals keep public receipts and exact policies, not bearer tokens. A lost
reply is recovered by replaying the same issue operation; a replaced/expired
resource is never silently recreated. Local maintenance renews partial or fully
delivered bindings and revokes their recorded grants when the consumer ends.
Malformed or older generation snapshots defer both live and initial-start grant
maintenance, rather than being interpreted as authoritative termination.

`ScopedDependencyBindings` is a local composition capability that pins the consumer,
provider placements, aliases, methods and bound workspace arguments. Its caller
can supply only a logical owner, a stable operation ID and approved aliases.
Resource ownership is namespaced by deployment and logical owner; changing an
Agent config revision reuses the same owner's provider session. Shared Files
bindings carry the fixed workspace argument and allocate no owned resource.
The generic owner path imports no Agent classes and contains no Shell branch.

`DependencyInstanceProvisioner` connects this capability to the durable dynamic
Agent factory. It projects approved tool/MCP profiles into fresh dependency
clients, validates delivery identity and assembles actual Agent instances. Agent
configs cannot choose endpoints/providers, pass a Fleet owner key, or broaden the
allowed aliases. Existing CLI/Desktop composition remains unchanged.

The platform's owner-only `fleet_app_bind_dependencies` RPC and separate live
binding maintenance loop are implemented. This RPC returns private credentials:
it is excluded from model tools and must never be handed directly to App-instance
callers. The scoped capability currently runs within a trusted composition; an
authenticated remote facade with durable owner-approved policy registration is
still required before an independent Agent App can use it across processes.
The final Agent package is not switched to this path yet. Explicit logical-owner
retirement after all revision Runs drain, partial-allocation retirement, history
reclamation, cross-replica fencing and live remote acceptance remain open.

Validation: 166 tests passed across live/static/dynamic instance assembly,
dependency/session ownership, platform service/bootstrap/authenticated RPC and REPL
keys. Nine additional cases for ambiguous inventory and the private owner API
then passed in the 40-case live-binding/maintenance suite (175 distinct cases).
The Go race-enabled native-App integration also exercises live issuance, lost
issue acknowledgement, TLS RPC, renewal and stop-triggered revocation across two
local Fleet Managers over authenticated NATS. Resource allocation failure tests
use a simulated provider; native Shell execution remains covered separately by
the existing owner-session scenario, not a claim of packaged Agent deployment.
No live services were updated.

### Durable dependency authority follow-up (P2/P3)

The Controller now persists issued dependency grants, renewals and revocations in
its private state directory before acknowledging them. The owner supplies stable
per-binding issue IDs; retries after lost responses return the same credential,
not a newly authorized relationship. Restart restores unexpired grants while
still checking actual consumer/provider generations. Revoked/expired operations
remain tombstoned. Corrupt or uncertain storage fails closed. Full contract,
rolling upgrade order and bounded journal limitations are in
`docs/app-dependency-rpc.md`, “Durable Controller grants and lost issuance replies”.

This removes the in-memory-only gateway recovery gap mentioned in earlier progress
notes. It does not complete the restricted dynamic provisioning broker, resource
retirement, distributed fencing, Windows Controller ACL storage or final ordinary
Agent package. The 4,096-record history limit also needs generation-fenced
reclamation before unrestricted production use. No deployment has occurred and
legacy CLI/Desktop launch paths remain intact.

Verification: 75 Python assembly/maintenance/resource-session tests passed; the
Go gateway race suite passed, including real child-process abrupt exit and grant
recovery. Hub's 35 auth/validation tests and the actual native-App/authenticated-NATS
Controller integration are tracked separately in their respective test runs.

### Instance assembly follow-up (P2/P3, opt-in)

`AgentInstanceFactory` reads `values.agent_instances` from the existing immutable,
generation-bound App runtime configuration. Entries have an instance UUID,
conversation ID, config ID, resolved config digest and exact scoped tool bindings.
Two conversations may reuse one config ID but must have distinct instance IDs and
owned credentials. The loader rejects credential aliases that cross logical
owners. Shared access explicitly declares `owner_ref: null` and gets distinct
client wrappers, so closing one instance's client cannot close another's client.
The gateway's grant-bound session remains the authorization boundary; declarations
in this loader cannot create permissions.

The domain runtime passes the conversation ID into assembly and reports the
non-secret instance/config identity through `get_agents`. Within one composition,
repeated construction returns the same Agent object, using a per-conversation
lock and a validated input snapshot. Config revision changes require new bindings;
templates cannot inject an instance ID or tool credentials. Team currently keys
members by display name, so duplicate names are rejected before resources can be
silently dropped. The old config-keyed factory remains only a compatibility path.

`AgentEnvironment.close_agents` drains the composition's clients after Agent work
and plugins, including clients whose team never finished construction. This is
local client ownership, not remote lease release. Existing team delegation reuses
the target instance with a new execution context per Run; it does not clone its
parent's bindings. Dynamic child instance provisioning is still required.

This is not a deployable Agent App or a completed P2/P3 gate. The final composition
root must wire the static or dynamic factory, provision owners without full Fleet
keys, handle termination/reconfiguration, isolate model and
plugin configuration, and package the ordinary Agent entrypoint. The startup
snapshot currently supports one member per config ID in a conversation; the final
membership model must allow multiple instances of the same config in one team.
Provider failure/restart recovery, distributed fencing and live deployment remain
separate unfinished requirements.

### Durable dynamic instance assembly (P2/P3, local integration)

`AgentInstanceStore` now reserves stable instance UUIDs and immutable configuration
revisions in a private SQLite journal before allocation. Each revision has a
durable operation UUID. A retry, including after reopening the journal, must reuse
that intent. Schema initialization and member reservation are transactional; an
unsupported schema or different data namespace fails without replacing the
database. A lifetime filesystem lock prevents a second local writer. This does
not replace deployment fencing across independent replicas or copied volumes.

`ProvisionedAgentInstanceFactory` accepts previously unknown conversations through
the existing `AgentEnvironment.create_agents` boundary. Its explicit composition
provisioner receives immutable intents and returns scoped `AgentInstanceBinding`
objects. The factory coalesces allocation by instance/revision, rather than the
entire team: reordering or adding members reuses unchanged Agent objects, editing
one member creates a new object with its existing instance UUID, and another
conversation receives a different UUID. Failed team creation retains successful
members for retry. Cancellation of a caller does not abandon accepted allocation
or a SQLite write; shutdown joins them and drains all delivered clients. Invalid
bindings close newly delivered clients without closing a borrowed sibling client;
failed cleanup prevents further allocation until recovery.

The provisioner is a trusted composition interface, not yet a production remote
allocation service. It must recover the original operation/session after lost
acknowledgement, deliver fresh client wrappers and enforce scoped access through
the platform. A revision's operation ID denotes binding assembly, not a new
resource owner: compatible Shell state remains owned by the stable instance UUID
across edits. Changed dependencies need explicit reconfiguration and drain, not
an implicit reset. No Fleet owner credentials or session/grant creation authority are
added to Agent templates or the instance journal. Client shutdown does not claim
remote sessions are released.

The integration exercises an actual `AgentRuntime` constructor, startup,
`create_chat`, persistent conversation loading, Team/Agent assembly, HTTPS tool
calls and runtime cleanup, then reopens both conversations and verifies their
identities and allocation intents. The HTTPS fixture represents already issued
grants; it is not a live Fleet allocator or a Shell process. Additional checks
cover overlapping requests, lost replies, partial team failure, configuration
edits, alias rejection, interrupted initialization and shutdown during allocation,
SQLite writes and real in-flight HTTPS calls.

Still required: wire a restricted platform provisioning service and final App
composition; support explicit membership independent of config ID; retire old
revisions/members only after their runs drain; terminate remote leases; bound
retained history/cache size; complete data migration/fencing and live acceptance.
Existing CLI/Desktop entrypoints are unchanged. This is not P2/P3 completion or
a deployed release.

Verification for the dynamic follow-up: 133 tests passed across dynamic/static
instances, plugins, runtime boundaries, App lifecycle, dependency clients, model
scope, conversation recovery and REPL keys. A subsequent separate-process journal
reopen test also passed (134 distinct tests total). This is local evidence;
packaged Desktop/CLI, production provisioning and cross-node gates remain open.

Earlier static-assembly verification: 164 tests passed across instance assembly, explicit runtime
composition, Agent/App lifecycle, dependency assembly/maintenance, resource-session
ownership and platform bootstrap/authenticated RPC. The focused Agent/team run
passed 59 tests, including the five existing deterministic delegation checks.
The new 17 instance tests use actual Agent/Team dispatch and real TLS clients:
stable identity, shared-config isolation, alias rejection, shared-service client
independence, config mutation during concurrent assembly, stopping/failed assembly
cleanup, and target-instance retention across two delegated execution contexts.
These use a deterministic HTTPS grant/provider fixture, not a real remote Shell
or model. Three legacy tests that call OpenAI directly were also attempted and
failed with 401 because this environment has no API key; their live inference
acceptance remains unverified. No deployment was performed.

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

### Explicit template configuration (P3 prerequisite)

App compositions can now give `Settings` a private user configuration directory
and pass that exact settings object to both template managers. Template discovery,
CRUD and prompt expansion no longer consult or replace the process-global settings
or prompt resolver on this explicit path. Preparation expands a copy of the team
definition and reads a fresh prompt snapshot, so one deployment cannot modify a
shared definition and later assemblies see edited/deleted prompt overrides.
Legacy convenience constructors retain their existing behavior during migration.

Verification: 97 tests passed across template isolation, runtime boundaries,
instance assembly, real App-host lifecycle, model directory and Playground.
Two excluded legacy template assertions were reproduced against the committed
pre-change implementation: missing placeholders are preserved rather than raising,
and the retired `skills` prompt is absent. No live deployment was performed.
This isolates template/settings paths, not model clients, environment credentials,
stateful plugins or the final Agent App package; those remain P3 work.

Memory/learning follow-up: registry factories now construct composition-owned
runtimes rather than process singletons. Their guidance/retrieval paths come
from the initialized runtime. Learning uses the supplied settings for user and
factory skill layers and the private environment's skill exclusions. A generic
background-plugin lifetime waits for accepted post-run work on App shutdown,
rejects late work and shields the drain from a cancelled observer. Stopping one
composition does not stop another's extraction. Legacy project switching no
longer resets process singleton variables that could affect another composition.

Verification: 326 tests passed across memory/learning, plugin registry, template
and instance isolation, runtime boundaries and the actual App-host lifecycle.
The new tests exercise real stores and registry-created runtimes; delayed post-run
work substitutes deterministic file writes for model calls. Model tier/provider
resolution, marketplace credentials, concurrent per-Run model selection, remaining
plugins and project-switch ownership still need migration. This is not complete
model/plugin isolation, a deployable Agent package or a live deployment.

Plugin startup follow-up: AgentRuntime now awaits one owned plugin composition
before ToolSet readiness. An enabled factory failure closes already-constructed
plugins in reverse order and reports an initialization error, retaining rollback
errors rather than silently serving a reduced feature set. Empty compositions and
failures are cached along with successes. Concurrent/cancelled observers cannot
restart or cancel construction, and App cleanup joins initialization/rollback
before releasing providers. The legacy synchronous factory remains for CLI/factory
callers; normal AgentRuntime and legacy project replacement use the owned factory.
Logging the memory model uses its representation rather than resolving a lazy
model tier merely to log startup configuration.

Verification: 368 tests passed across the above suites plus model directory,
Playground, project discovery and remaining Think plugin checks. Two old Think
assertions expect its retired tool/prompt to be injected; both failed identically
when the committed pre-change Think implementation was loaded. They were excluded
from the final regression run. New checks cover partial rollback failures,
cancelled startup observers, empty/failing composition caching, provider cleanup
ordering and readiness refusal. No live model inference or deployment was done.

### Explicit model-call scope (P3 prerequisite)

`ModelCallScope` carries a composition's Settings, authorized Fleet model client,
explicit OAuth managers, model-tier resolver and Responses capability cache.
Agent and its explicit instance factory accept this object from the composition,
not an Agent template or instance configuration payload. Main inference, retry
settings, configured context variables and LLM autocompaction use it. Tool-result
externalization receives the composition's data directory. Default/tag resolution
requires the supplied selector on this path; absent Fleet/OAuth capabilities fail
without constructing an ambient client or importing an OS user's CLI login.

Use `Settings(..., isolated_env=True, environment={}, user_home=...)` to begin with
no inherited process credentials. A supplied environment mapping is copied. The
old isolated constructor still snapshots the process environment for compatibility;
isolation alone does not mean an empty credential set. Private project/user
configuration files remain inputs, and the composition controls their roots.

The existing provider adapters are reused. Scoped budget calls require both proxy
endpoint and credential, including on the Responses path. Missing vendor keys do
not borrow another vendor's OpenAI key, nor can a generic proxy key silently go to
a different vendor endpoint. OpenRouter budget model IDs retain their routing
prefix; local Ollama retains keyless operation. Scoped OpenAI/Anthropic SDK calls
exclude ambient organization/project/bearer headers and close their request-owned
clients on completion, exceptions and cancellation. Responses endpoint probes are
cached per scope; only an absent API (404/405/501) before any emitted chunk can
fall back. Authentication/rate-limit/server errors and partially emitted streams
do not trigger that extra API replay. Ordinary model-request retry/fallback policy
still applies above this layer; exactly-once inference is not promised.

Verification uses real localhost HTTP/SSE endpoints and the installed SDKs, with
conflicting process credentials and ambient client constructors forbidden. It
covers concurrent budget/BYOK requests, actual Agent inference and compaction,
Anthropic auth headers, local Ollama routing, OpenRouter budget names, missing
credentials, independent probe caches, instance factory injection and SDK cleanup.
Fleet binding and OAuth absence are tested with controlled clients; these are not
live Fleet inference, OAuth provider acceptance or a running Ollama deployment.

Regression run: 620 passed, 99 skipped (optional external-service tests), one
excluded image-generation preference assertion. That assertion expects Gemini
first while the baseline selects `gpt-image-2`; it failed identically with the
committed pre-change routing/settings modules loaded. A deadline test that bypasses
Agent construction now supplies the new optional scope field explicitly; its
existing timeout/fallback assertions passed unchanged.

Remaining: final App composition/credential delivery and closing its shared Fleet
client, fully owned tier selection, memory/learning and other auxiliary model
callers, and per-Run configuration snapshots. Context-collapse state is already
owned by AgentRunContext; the outstanding issue was metadata lookup, addressed
below. The scope is a dependency
injection boundary, not an authorization grant. This is still opt-in groundwork,
not complete inference isolation or a deployable Agent App. No live rollout or
paid model call was performed.

### Owned model metadata follow-up

Context-pressure decisions, synchronous/asynchronous prompt projection,
autocompaction thresholds and message/UI token accounting now use the bound
ModelCallScope catalog. Fleet exact-model and route references read metadata from
the same authorized client used for inference, without constructing the ambient
Fleet client. Missing or invalid Fleet input limits reject context preparation;
accounting reports an unknown limit/error instead of inventing 200K or reusing an
unrelated historical limit. The App token-statistics endpoint also keeps a
UI-selected model override inside this scope.

The existing context-collapse manager was already Run-context-owned; no second
manager mechanism was introduced. Concurrent projection coverage checks distinct
Run managers and prompt overheads. Other new checks cover same-reference catalogs
with different capabilities and limits, independent collapse/autocompaction
thresholds, real Agent dispatch using bound client fixtures, invalid metadata and
UI model overrides. These are local tests with deterministic model clients, not
live Fleet model inference or final Agent App composition.

Verification: the broader regression passed 629 tests, skipped 99 optional tests
and retained the previously documented image preference exclusion. The final
UI-accounting follow-up is additionally checked with the model scope, token
optimization and App-host lifecycle suites. No deployment was performed.

### Compression caller and image-root follow-up

The compression plugin now retains its registry-supplied Settings. Each operation
captures the active Agent's model and ModelCallScope before awaiting other plugin
hooks, then passes that scope to the temporary compression Agent. Scoped pressure
checks use the current model's input limit rather than a previous model's message
metadata; first-use Fleet checks refresh the bound client's description. The
legacy direct constructor retains its optional settings fallback.

An actual temporary Agent.run exposed another ambient lookup: input and tool
image storage initialized the process-global image directory even for text-only
input. Scoped Agents now construct ImageStore with their composition's image
root on both paths. This is path ownership, not a filesystem security boundary.

Verification: 221 tests passed and 3 optional tests skipped across scoped models,
compression, owned plugins, runtime/App lifecycle, instances, deadlines and token
optimization. A further 83 image/source/adapter tests passed; 8 real-provider
checks skipped without credentials. New tests execute two real compression
Agent.run calls concurrently over local HTTP/SSE with separate credentials,
private summary-detail files and forbidden ambient settings, plus actual image
input persistence into distinct roots. No paid API or live deployment was used.

Remaining plugin work includes memory/learning callers and their background
Agent Files bindings, concurrent per-conversation plugin state, and final App
composition and lifecycle wiring. This does not complete P3.

### Auxiliary model and Files binding follow-up

Memory selection, flush and session-note calls now go through an explicit
AuxiliaryExecution. Its task-local call snapshot contains the originating model
and ModelCallScope; lazy tier names resolve through that scope. The scoped path
uses the shared provider dispatcher, including Fleet's message result shape.
Memory/learning post-run hooks capture their active Agent and a message snapshot
before scheduling tasks. The parent context is reset when the hook returns.
Queued memory/session-note drain passes retain the newest submission's model
snapshot rather than inheriting the earlier worker's model. Compression invokes
pre-compression hooks under the same captured active Agent scope.

Memory extraction, dream consolidation and skill extraction share the background
Agent runner. Scoped runs require an explicit file_manager dependency binding;
workspace_path is descriptive and does not grant filesystem authority. They
cannot construct the local FileManager fallback. The App composition owns these
provider clients; each operation borrows them and joins adopted background tool
work in finally, even when its observer is repeatedly cancelled. Composition
cleanup must release clients only after all plugin tasks drain.

AgentEnvironment can now supply a complete asynchronous plugin factory. The
owned registry accepts an explicit factory map whose closures carry model/Files
bindings; any missing enabled plugin fails readiness and rolls back prior plugins
instead of invoking the ambient factory. Legacy factories remain available only
when no explicit map is supplied. Final App assembly still needs to construct this
map and deliver/revoke its authorized bindings; this is not a deployed App.

Verification: 522 tests passed and 3 optional tests skipped across auxiliary
execution, plugins, memory/learning, compression, Agent/App lifecycle, instances,
dependency bindings, model scope and token optimization. Added evidence includes
real local HTTP/SSE for selection/flush/note, concurrent per-conversation model
credentials, pending-drain model changes, complete Agent tool loops over scoped
HTTPS provider fixtures, missing-Files rejection, repeated-cancellation drain,
explicit Runtime plugin initialization and missing-factory rollback. No paid API,
live Fleet/HPC deployment or full frontend/data migration was tested here.

Remaining: final ordinary App composition and credential delivery, plugin
management endpoints that still construct ambient Fleet/ModelServices toolsets,
per-conversation compression state, durable dynamic instance provisioning and
full P0–P7 acceptance. Legacy CLI/combined-host paths are still transitional.

### Explicit App plugin assembly (P3, opt-in)

`create_app_plugins` now constructs all seven registered built-in plugins through
an explicit factory map. Settings must belong to the supplied ModelCallScope.
Memory and learning borrow an explicit Files binding for auxiliary Agent work;
tasks require an instance-bound output metadata resolver. Enabled unknown plugins
or missing capabilities fail instead of invoking the ambient registry factories.
The existing AgentEnvironment plugin callback can run this composition; the
ordinary backend entrypoint/configuration delivery is still unfinished.

Fleet and Model Services plugins borrow the exact Agent instance's management
providers. AgentInstanceFactory exposes an object-identity checked binding lookup:
copying a public instance ID is not sufficient. These plugins do not construct
FleetToolSet/ModelServiceManager or consult process Fleet credentials. Model
selection remains local to the team and checks the target member's own model
client, ready text/tool support and positive context limit. Delayed validation
cannot overwrite a changed model/scope. Remote management grants must omit the
local `use_fleet_model` method. Prompt guidance no longer assumes the Agent's host
is the node executing its Shell binding.

Explicit task plugins keep task state in the composition's private brain
directory even when two deployments reference the same external project.
Conversation identifiers and resolved paths cannot escape that root. Output
registration uses the supplied resolver (including explicit source node), with
no process-local filesystem or global service-discovery fallback. Headless
policy reads the supplied Settings. This does not migrate historical task data
or yet provide the cross-node Files resolver in the final App bootstrap.

Explicit plugins are required during Team setup: binding failure or cancelled
setup cannot silently produce a runnable partial team. Failed setup is terminal
for that Team object, avoiding retries that append duplicate hooks. Existing
legacy plugin binding errors retain their warning behavior. Management wrappers
borrow clients; App cleanup still drains plugin work before the instance owner
closes those clients.

Verification: 567 tests passed and 3 optional tests skipped across App plugins,
owned plugin/model/auxiliary scopes, task/model management, instance bindings,
runtime boundaries, real App-host lifecycle, memory/learning and compression.
New tests exercise actual Team dispatch over local TLS dependency grants,
private task persistence, the real AgentRuntime plugin callback and cleanup,
target-scoped model selection, stale metadata, failed/cancelled setup and path
escapes. Model management/inference responses and output metadata use fixtures;
this is not a live Fleet management/GPU deployment or final App startup gate.
No deployment was performed. Dynamic resource provisioning, the final composition
root, per-conversation compression state, GUI packaging and data migration remain.

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
