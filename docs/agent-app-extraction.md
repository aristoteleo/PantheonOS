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
| P0 | Classify every public ChatRoom RPC, UI dependency, durable data root; record functional and performance baseline | Ownership, RPC and data inventories exist; exhaustive caller coverage and comparative performance/memory baselines remain incomplete |
| P1 | Move platform RPCs out of ChatRoom; connect desktop independently; stop Agent and exercise Files, Terminal, Fleet, Store, Jupyter, Browser, Model Services | Production desktop with real native Fleet placement verifies Files, PTY Terminal, Fleet, model directory and window synchronization without Agent imports; combined installed-Agent install/chat/stop/uninstall/reinstall/reconnect and independent Files/model inference now pass; Store/Browser/Jupyter and default cutover remain pending |
| P2 | Generic owner references, interface bindings, grants, sessions and leases; two Agents have independent Shell state and share stateless files | Native Agent/allocator/Shell/shared-Files/migrated-MCP joint calls, logical-owner retirement, generation-bound restart and shared-provider survival verified locally; cross-replica fencing and full deployed lifetime/failure acceptance remain pending |
| P3 | Package Agent runtime, configs, instances, conversations, runs and replayable events; preserve inference routes and cancellation | Paired native package with locked dependencies, prepared configuration, owned storage, original Model Services inference, tools, history and restart verified locally, including full Agent conversation/tool recovery after a clean entire local Fleet profile restart; an opt-in local profile host now composes clean startup/reopen/shutdown, and the original General Team runs with all default plugins; the complete preset now works through CLI and native Desktop control entry points; graphical first-run setup and native macOS chat/tool/history/reopen/close flow pass with the complete preset; final installed-product capability parity remains pending |
| P4 | Package GUI; independent client/store per deployment; remove static Agent imports from Atrium; support App intents | Production packaged GUI, independent entry, attached panes, scoped Files/image previews and transient resource intents verified locally; native macOS chat/reopen/close compatibility is verified with a debug bundle; two-deployment isolation/failure coverage, distribution packaging and default production cutover remain pending |
| P5 | Inventory, backup, import and validate data; fence old writer; preserve project asset references; test failed migration recovery | Inventory, local fencing, backup/import/admission, saved-team identities, settings/credentials/budget, MCP conversion, template paths and historical images verified in focused local gates; exhaustive configuration/OAuth coverage, representative real-data upgrade, injected failure and distributed cutover/rollback remain pending |
| P6 | Publish one frontend/backend release; isolated candidate, drain, schema checks, cutover and rollback; self-edit demonstration | Paired POSIX release builder and generic compatible upgrade/data-copy/retained-source rollback verified on real native Fleet, including actual paired Agent versions, history and Shell calls; partial-start abort and rollback after terminal readiness failure also pass for an actual Agent package; declared format admission and Agent on-disk checks are implemented; Agent-authored backend/Vue build, rendered rollback and local Store API publication/review/download now pass; distributed/unknown-outcome recovery, schema transformations, remote publication/default cutover and broader self-edit acceptance remain pending |
| P7 | Replace Hub brain-specific bootstrap with generic App deployment; remove transitional paths; complete cross-node acceptance | Opt-in platform startup and owner/profile-scoped Hub recipe delivery implemented locally; production provisioning, default cutover, legacy-path removal and cross-node acceptance pending |

M1 completes P0/P1, M2 completes P2/P3/P4, M3 completes P5/P6, M4 completes P7.
No milestone is complete merely because its files or manifest exist.

### Store publication/review preserves paired releases (latest increment)

Store previously admitted mismatched Fleet versions and stale release hashes;
review also rewrote manifest formatting even when values were unchanged. Seven
new regression cases failed before the fix, including the byte-preserving merge.
The mirrored stdlib Store protocol now checks every declared native execution
variant against App identity/version/data schema. An optional generic
`execution.release_inventory: "release.json"` binds the complete published tree
by SHA-256; the Agent builder declares it. Validation streams file hashes and
never executes App code or build hooks. Source-only releases remain supported.

Review keeps original manifest bytes when merged values equal the submitted or
upstream manifest. It retains two Git parents while allowing a coherent built
candidate to keep its version. Renumbering or combining changes that invalidate
execution metadata/inventory requires a new author-built candidate; Store never
silently rehashes or rebuilds submitted code. A merge result must not mutate its
input manifest object before deciding which bytes can be preserved.

The real Agent candidate exposed bytecode caches from an interpreter probe in
its original exported Git revision. Source export now excludes generated Python
bytecode, and the release probe disables writing it. The final real native
Agent-authored Vue/backend build, rendered upgrade and rollback passes in
**147.45 s** (`/tmp/agent-store-clean-export-20261006.log`). Its source and candidate
Git revisions then pass the local Hub API publish/contribution/prepare/review/
merge/download flow in **29.29 s**
(`/tmp/paired-agent-store-api-clean-20261006.log`). Ordinary runtime encoding proves
that the downloaded merge produces exactly the same Fleet payload and digest as
the candidate just exercised on native Fleet. Old version download remains pinned.

Hub focused Store/contribution regression passes **23 tests, 1 optional actual
Agent gate skipped** in 82.38 s; that optional gate is the separately passed run
above. A separate direct-upload test passes and confirms Hub itself rejects a
stale inventory even when an author bypasses `prepare_release`. Runtime Store
regression passes **35 tests in 66.92 s**. These counts describe separate scopes,
not a combined total. Runtime and Hub protocol files remain byte-identical.

The API run uses an isolated in-memory database and fixture identities. This is
not remote catalog publication, human review, installed-product deployment or
production cutover. Live-model authoring, broken self-authored release recovery,
concurrent GUI isolation, real-data migration and cross-node acceptance remain.
P6 and P7 are still incomplete; no user-installed App was changed.

### Agent-authored Vue frontend, paired build and rendered rollback (preceding increment)

The additional `self-edit-gui` gate makes the running Agent edit actual Vue source
in an exported working copy. The owner runs the normal Vite Agent boundary build
against that copy; the Agent then pairs the successful output with its backend
change, retains editable frontend source, updates content hashes and commits/tags
the candidate. Local build dependencies are reused for validation but neither
`node_modules` nor its temporary machine-specific link enters the release.
The ordinary Store Git bundle round-trip and Fleet upgrade/rollback follow.

Each version is rendered through the production `snapshot_frontend`/`DesktopView`
adapter bound to its actual Fleet instance. Chromium verifies that the original
and restored GUI omit the candidate label, the candidate's compiled Vue renders
it visibly, history remains available, and page reload never submits `chat`.
Backend RPC behavior is checked independently, with original/candidate/restored
Shell calls and retained-source data assertions from the same lifecycle gate.
Screenshots cover all three versions. This verifies paired code and view behavior;
it is not simultaneous multi-deployment isolation or installed Cocoa acceptance.

The first complete run passes in **146.04 s**
(`/tmp/agent-self-edit-gui-20261006.log`). Screenshot review then found that the
test label consumed conversation height; the candidate now uses a non-interactive
positioned label, and the browser gate also checks that the send control stays
inside the viewport after entering an unsent draft. The final complete regression
passes in **145.94 s** (`/tmp/agent-self-edit-gui-final-20261006.log`); its three
screenshots are under that run's `source-view`, `candidate-view` and `restored-view`
directories. Candidate screenshot inspection confirms the marker does not consume
conversation height and the composer/send control remain inside the viewport.

The LLM's authoring commands and upstream responses remain deterministic fixtures.
Store publication is a local export/import through its shipping codec, not a
remote catalog upload or human review. Live-model authoring, remote publication,
broken self-authored candidate recovery, two concurrently deployed GUIs and
production cutover remain pending. No installed user App or production default
was changed, and P6/P7 remain incomplete.

### Agent-authored candidate and explicit Shell workspace (preceding increment)

The self-edit acceptance exposed a real composition gap: the managed Shell used
its instance-private workspace while Files and Agent project metadata referred
to the owner's selected workspace. A simple `printf` tool test did not reveal it.
The General Team preset now explicitly supplies the same local workspace to the
Shell provider. Go Apps can read bounded, generation-bound prepared values through
`appsvc.RuntimeValues`; malformed/stale snapshots fail without credential output
or an ambient fallback. Shell validates an existing absolute directory and borrows
it without creating or deleting it. Unconfigured standalone Shell retains its own
data workspace. New managed artifacts use the ordinary prepared-start protocol;
existing immutable packages are unchanged and need rebuilding to gain this option.
This is node-local binding, not filesystem synchronization or an OS sandbox.

Actual paired Agent self-edit acceptance passes **1 scenario in 99.91 s**
(`/tmp/agent-self-edit-bound-20261006.log`). The running Agent calls its real Shell
to fork exported source, edit the backend and paired CSS, commit two Git revisions
and tag the candidate. Ordinary Store `prepare_release`/`unpack_release` round-trip
the candidate; inventory hashes are verified before ordinary Fleet deployment.
The still-running source retains its old RPC behavior. After upgrade the new
process exposes the authored change; history and fresh Shell calls work. Retained
source rollback restores old behavior and original history without candidate
conversation writes, and shared provider generations remain unchanged.

The model's tool command is deterministic fixture output, not evidence of live
model coding ability. CSS bytes are verified in the paired artifact, not visually
accepted in the GUI. Full frontend source authoring, candidate UI testing, human
review/publication through Store, intentionally broken self-authored releases and
production default cutover remain open. This gate does not complete P6.

The Go App SDK and Shell suites pass, as do **27 preset tests in 16.32 s**.
The native managed Shell lifecycle gate passes in **11.409 s**
(`/tmp/shell-workspace-native-prepared-20261006.log`), covering both a borrowed
project and the default instance workspace, independent sessions, credential
exclusion, pending-output drain protection, sibling survival and retained project
files after stop. The full Agent/public CLI/profile reopen regression passes in
**85.99 s** (`/tmp/shell-project-reopen-20261006.log`) with the bound workspace.

### Explicit local owner-crash recovery (preceding increment)

Local infrastructure inherits a lifetime profile lock, so a killed Python owner
cannot permit a replacement to rewrite still-live Controller/broker/Runner state.
An owner-private process receipt records birth identities, command fingerprints
and original trust/coordinates; a separate management lock serializes takeover.
The opt-in `pantheon local --launch ... --recover` verifies the dead owner and all
three surviving processes, authenticates the original node, renews its owner
credential and drains the existing profile. It starts no App or replacement node.
Normal explicit reopen after a confirmed drain uses the next ordinary cycle.
An error after takeover cannot tear down undrained infrastructure. Native Desktop
exposes the same operation after an owner failure, with the existing stop/exit
receipt gates before configuration or a new start becomes available.

Native infrastructure acceptance passes **14 tests in 27.87 s**
(`/tmp/local-owner-recovery-fixed-20261006.log`), including real owner SIGKILL,
lock retention by every surviving child, recovery during startup/after readiness,
rejection of live-owner/process/trust changes and normal credential renewal.
The actual immutable paired Agent passes **1 scenario in 80.95 s**
(`/tmp/agent-owner-crash-20261006.log`): chat and real Shell, Desktop-owner startup,
owner SIGKILL, public saved-launch recovery, explicit reopen with original chat
history and a fresh Shell result. All infrastructure processes are real; model
HTTP replies remain a fixture. Test duration is not startup latency.

Rust native command-state tests pass **23 tests, 1 ignored integration gate** in
1.41 s (`/tmp/agent-owner-recovery-rust-20261006.log`). Rendered Chromium covers
the explicit recovery button and configuration remaining unavailable while it
runs; release review/rollback interaction also passes. This is not a native Cocoa
button-click gate or installed-product deployment. Partial infrastructure loss,
machine reboot, older receipts, distributed recovery, schema transformations,
self-edit/publication and default cutover remain pending. The broader local
profile/recovery/release regression passes **61 tests in 147.14 s**
(`/tmp/local-owner-recovery-regression-20261006.log`), including a failure after
takeover that preserves undrained infrastructure for a subsequent recovery.

### Local product failed-start cleanup and retained rollback (preceding increment)

The saved-profile host now durably records startup cleanup before observing or
mutating nodes, aborts the original consumer/provider deployments, and confirms
all profile instances stopped before permitting exit. A failed intent write does
not let an in-memory phase skip that durable fence on retry. Missing child
journals require unused native targets; restart validates complete model cleanup
receipts and exact stopped generations. Explicit reopen after clean owner exit
uses a fresh cycle and authority while retaining data and model choices. A failed
upgraded candidate can instead take the existing reviewed retained-source rollback.
It cannot prepare another upgrade from an incomplete candidate.

Native Desktop UI commit `8a1eb474` exposes cancellation during startup and stop
after a recoverable startup failure. The existing native control pipe is used;
settings and version changes remain locked until both stop receipt and owner exit
succeed. The host consumes stop requests between bounded lifecycle advances.
No in-flight request is cancelled and replayed as a replacement operation.

Local profile/update/release regression passes **91 tests in 197.44 s**
(`/tmp/profile-cleanup-regression-20261006.log`). It includes real native startup
failure, full-owner restart, failed candidate rollback, interrupted registration,
missing-journal admission and repeated intent-write failure. An additional real
consumer failure/reopen case rejects missing model cleanup receipts (**13.38 s**,
`/tmp/profile-cleanup-receipt-20261006.log`). Bootstrap native acceptance and
initial profile recovery pass **11 scenarios in 78.94 s**
(`/tmp/profile-abort-probe-fixed-20261006.log`). The provider failure fixture now
wraps the original argv rather than accidentally modifying its data-directory
argument. Counts overlap earlier evidence and should not be added together.

Native Rust tests pass **22 tests, 1 ignored integration gate** in 1.21 s.
Rendered Chromium tests cover cancel-startup, failed-start stop, unchanged setup
gates, release review and rollback selection with controlled native IPC. Actual
paired Agent recovery through the public Desktop owner pipe and saved-launch
release commands passes in **118.37 s**
(`/tmp/agent-failed-desktop-recovery-proof-20261006.log`): original chat and Shell,
reviewed 0.7.1 selection, a candidate that passes its real readiness check before
injected rejection, explicit stop with clean owner exit, reviewed rollback, and
restored original chat with a fresh Shell result. A durable candidate marker
proves the backend was actually ready before failure injection. Model upstream
responses remain deterministic fixtures; test duration is not startup latency.
This is isolated candidate acceptance, not installed Cocoa-window interaction or
a production rollout. Abrupt-owner-crash authority recovery, distributed fencing,
schema transformations, self-edit/publication and default cutover remain pending.
The overall extraction goal is not complete.

### Composed model startup abort (preceding increment)

`ModelServiceBootstrap.abort` now durably fences startup and delegates cleanup
to the original generic consumer/provider deployments, in that order. It waits
on original node operations instead of replacing them. Prepared-but-unused
resources and failed live backends are handled by the existing deployment abort.
Exact publication snapshots and directory CAS reconcile stopped Model Services,
including lost registration/rebind and stop-publication acknowledgements.
Model selections, names and user data survive cleanup. Changed directory entries,
foreign child recipes, unknown operations and missing journals with existing node
work reject further teardown. No new model discovery or credential preparation
runs during abort; this remains ordinary model/App composition, not Agent code.

The combined bootstrap/abort/stop regression passes **113 tests in 3.45 s**
(`/tmp/model-bootstrap-abort-final-20261006.log`). Real local Fleet acceptance
passes **4 scenarios in 25.27 s**
(`/tmp/model-bootstrap-abort-native-reviewed-20261006.log`): healthy composition,
terminal consumer readiness failure, terminal provider readiness failure, and a
committed registration with a lost reply. Controller, Runner, Connector, directory
and consumer processes are real; the upstream model endpoint is a fixture.
Assertions verify all instances stopped with no resources/reservations, retained
data, exact stopped publication generations, no duplicate lifecycle operations
or directory writes, and rejection of the fenced original startup.

The local saved-profile integration and reviewed rollback are covered by the
subsequent increment above. Authority recovery after an abrupt owner crash still
remains open; neither increment completes the overall extraction.
No user installation, production default or external model deployment changed.

### Native Desktop release selection and interrupted adoption (preceding increment)

The native Desktop now exposes reviewed bundle selection and retained-source
rollback after a clean profile stop. It calls the same public `local-release`
owner command as the CLI, verifies the returned decision against the saved launch,
and starts the approved choice only on an explicit subsequent start. An applied
receipt distinguishes completed selection from an interrupted launch replacement.
On reopen, unfinished decisions show a resume action before any Apps start;
configuration switching and discard cannot bypass that pending decision. The CLI
checks the same receipt under the profile lock. Completed history follows matching
launch-file copies, while pending work requires its original authoritative file.

The real Rust-owner integration passes **1 scenario in 128.81 s**
(`/tmp/agent-native-release-verified-20261006.log`). It reviews/applies actual
Agent 0.7.0/0.7.1 bundles and rollback through native Rust command dispatch, with
real CLI/Fleet lifetimes, original/candidate/restored history and real Shell
results. Its explicit Python import root prevents accidentally selecting another
editable checkout when Cargo runs from the UI directory. The model upstream is
a controlled fixture. Test duration is not startup latency.

Native unit tests pass **21 tests, 1 explicitly ignored integration gate** in
1.15 s; the gate above invokes that ignored test with its isolated real profile.
The release/profile status regression passes **19 tests in 85.92 s**, and rendered
Chromium interaction covers review, discard, pending resume, remembered selection
and rollback choice. These counts overlap prior gates. This is not final native
window-click, installed-product or production acceptance. User installations and
defaults remain unchanged. Failed-profile startup recovery, schema transformations,
publication, self-edit and distributed/default cutover remain pending.

### Saved local launch release upgrades (preceding increment)

The public `pantheon local-release --launch ...` command now reviews and applies
an exact consumer-App release change, then durably adopts the selected bundle in
the saved launch description. `pantheon cli --launch ...` and `pantheon local
--launch ...` read that selection on reopen. A separate reviewed rollback restores
the retained original bundle/data; candidate writes remain separate. Private
receipts precede node changes and launch replacement happens last. Retrying a
lost acknowledgement uses the same copy/approval, including a crash after the
launch file was replaced. Startup rechecks the launch choice after acquiring
the profile owner lock. Settings/topology and model-provider revision changes
retain their separate workflows.

The release/profile regression passes **43 tests in 81.64 s**
(`/tmp/local-launch-release-20261006.log`), including three real Fleet launch
adoption/rollback scenarios with pre/post launch-write failures and two ordinary
profile release scenarios. Product/setup/profile regression passes **61 tests in
61.92 s** (`/tmp/local-launch-regression-20261006.log`). An additional startup
selection-race rejection test passes in **0.24 s**. These groups overlap earlier
gates and are not a distinct aggregate test count.

The actual paired **Agent 0.7.0/0.7.1** release passes through public CLI commands
in **127.54 s** (`/tmp/agent-launch-release-20261006.log`): original chat/Shell,
review/approve, upgraded chat/Shell, another whole-profile reopen, reviewed
rollback, original-history restoration and a fresh Shell result. Both bundle
compilation and Fleet lifecycle are real; the model HTTP upstream is a fixture.
The time is total test duration, not startup latency. No installed Desktop/Fleet
or production default was updated. Native Desktop release UI, failed-profile
startup recovery, schema transformations and default cutover remain pending.
See [saved launch commands](app-release-upgrade.md#saved-local-product-release-selection).

### Generic compatible-release evidence (preceding increment)

The generic owner workflow now prepares installed App revisions from a stopped
deployment, copies changed Apps' data using Fleet, and returns ordinary deployment
recipes. A completed, stopped candidate can roll back to the original retained
data. Candidate-only writes remain separate. Both unchanged group members and
shared providers retain explicit generation checks, including policy-only
references. There is no Agent execution import in the coordinator.

The final actual paired Agent release run passed in 89.22 s. It
also asserts additional real Shell results after both upgrade and rollback and
unchanged shared Shell/Model Service Connector generations. Original conversation
history and logical Agent identity survive upgrade; rollback restores the original
history while retaining candidate data separately. Upstreams remain deterministic
local fixtures. Details and current limits are in [App release upgrade](app-release-upgrade.md).

The ordinary deployment now also has an explicit abort operation. It durably
fences its original startup, waits for in-flight node operations, cancels prepared
holds and drains started components in reverse dependency order. Interrupted
observation and checkpoint failures resume the same intent. After a completed
abort, generic restart review or retained-source upgrade rollback can proceed.
Both normal and intentionally failed Agent releases passed the real native gate
in 185.64 s total (`/tmp/agent-release-abort-20261006.log`); the failed release runs
its backend but rejects its actual readiness probe. Rollback recovers original
history and a new Shell call, leaving shared Shell/model providers running.
The final failed-Agent scenario passes again in 102.07 s
(`/tmp/agent-release-abort-final-20261006.log`). This is total test duration and
uses a deterministic upstream, not a startup benchmark or live-provider test.

This does not adopt a new release into saved CLI/Desktop profiles, publish a new
route/default, migrate schemas or validate large existing histories. Unknown
node outcomes, blocked drain hooks, replica fencing and cross-node crash recovery
remain separate acceptance. No installed user App or production default was changed.

### App data-format compatibility admission

App releases now declare an optional durable-format identity, written version
and accepted source versions. The Python artifact builder, upgrade coordinator
and Fleet node enforce the contract; native `clone_data` cannot bypass owner
admission. A declared format cannot silently become undeclared or adopt an
unknown undeclared source. Both-legacy copies retain their prior behavior.
Installation adds an on-disk ledger fence against older Runners ignoring the
new contract. Agent format 1 marks the current extracted-App layout and is
checked, together with its namespace, before its instance database opens.
Unmarked existing layout is stamped only after the existing migration/database
admission succeeds. See [format admission](app-release-upgrade.md#durable-data-format-admission).

The Python regression gate passes **139 tests in 11.90 s**, including source
retention, failed candidate abort, rejected future/invalid markers, stamp-failure
lock release, implicit-model migration and mismatched package declarations
(`/tmp/agent-schema-regression-final-20261006.log`). The preexisting migration
case expecting a missing model to fail also fails on untouched `87aee337`; it
was corrected to reject malformed models and now separately verifies that
omitted/empty/None selection survives import without an invented model.
The Fleet boundary/ledger regression passes in **5.801 s**
(`/tmp/agent-schema-go-final2-20261006.log`). The final rebuilt native Runner
(`/tmp/pantheon-schema-fleet-final-20261006`) passes **4 real lifecycle scenarios
in 192.74 s** (`/tmp/agent-schema-native-final-20261006.log`): healthy and rejected
Agent readiness, plus healthy/failed ordinary App upgrades with large attachments.
Agent assertions preserve the original/candidate format marker, historical chat,
logical identity, fresh Shell results and shared-provider generations. Processes
are real; model replies remain fixtures. This is total test time, not latency.
This is admission, not a completed schema-transformation workflow, saved-profile
upgrade or default cutover. No installed user node or App was updated.

### Large state copying for release upgrades

The Fleet candidate now streams App state with a 256 KiB buffer and bounded
directory batches. Node-owner copy limits default to 64 GiB/1,000,000 entries;
disk-space preflight retains a default 1 GiB reserve. The existing resource-policy
file configures the limits, and resource status exposes their effective values.
No new dependency is introduced. The original stopped data remains untouched;
ordinary copy errors/cancellation cannot publish a completed-copy receipt.

Go acceptance checks a 112 MiB history with hash equality and roughly 269 KiB
total allocation, plus 10,005 files and mid-file cancellation. Real Fleet upgrade
and failed-candidate rollback preserve an 85 MiB attachment on both releases;
the associated deployment suite passes 111 tests in 9.09 s. See
[large-state details and limits](app-release-upgrade.md#large-app-owned-state).
This removes the old small-copy limitation on upgraded nodes, but does not prove
real long-history UI behavior, user-data migration, cross-platform runtime or
hard-crash recovery. Installed user nodes and production defaults are unchanged.

### First-run General Team configuration (local candidate)

Paired UI commit `35273109` adds an inline native Desktop creation form for a
workspace, explicit model engine/URL/key, all three model tiers, optional image
capabilities and Store origin. It still requires an installed Python runtime and
complete App bundle. The public `pantheon local-setup` command consumes bounded
JSON on stdin, compiles the original General Team preset, writes separate private
configuration/credentials and publishes the launch receipt last. It starts no
Apps. Desktop validates that receipt and presents an explicit start action.
Cancellation and submission clear the password field; keys do not enter argv.

The new real startup gate exposed missing required Notebook settings in the
preset's optional defaults. The compiler now materializes the original 3600 s
execution timeout and enabled execution logging, preserving explicit overrides.
The generated setup retains the original Agent, Evolution and plugin defaults.

Final evidence: **64 runtime tests** pass in 25.35 s
(`/tmp/local-first-setup-regression-final.log`); **16 native Rust tests** pass
(`/tmp/local-first-setup-rust-final.log`); Chromium first-run interaction and
existing setup/update regression pass with controlled native IPC
(`/tmp/local-first-setup-ui-final.log` and
`/tmp/local-first-setup-ui-regression-final.log`). The rendered form was inspected.
The public generated setup passes the complete native General Team scenario in
**329.10 s**, including four Fleet lifetimes, authenticated model routing, real
Shell/Files work, CLI/Desktop control and retained history/memory
(`/tmp/local-first-setup-general-team-final.log`). This is total test duration,
not a startup latency measurement. Upstream model replies are deterministic.

Final installed GUI interaction, runtime installation, provider discovery/login,
existing-data migration, package upgrade/rollback, live models and default
production cutover remain open. No user installation or production deployment
was changed. Older entries below describe their historical increment boundaries.

### Native macOS General Team interaction acceptance

The isolated debug Desktop (`03b5874f`) now passes actual native UI interaction
with runtime `d97a8210`: open the first-run-generated configuration, start the
complete App product, submit a chat, stop through **Stop Apps & settings**, quit,
reopen the same saved profile, recover the chat, execute a real Shell command,
stop/start again, recover both chat and tool history, and execute another command.
Closing the running window then drains its owned Apps and exits with code 0.
The final ledger has all **13 instances stopped**, and the profile is stopped at
cycle 4. Two persisted Shell results contain `NATIVE_FULL_SETUP_OK` and success.
The model fixture observed 12 requests in the final process, zero unauthorized
requests and a real tool result returned to it. Its prompt matcher was corrected
for the GUI's existing `<USER_REQUEST>` envelope; the earlier text-only reply
was a fixture mismatch, not a product execution failure.

All **10 Python dependency sets** report `Reusing installed Python dependencies`
on reopen. The last complete product start was visibly ready within 25.919 s of
clicking Start (an observation upper bound, not precise render latency). This
still needs startup profiling/optimization. Upstream responses are a loopback
fixture; actual model providers, distribution packaging, production/default
cutover and exhaustive capability parity remain unverified.

The machine-local evidence is
`/var/folders/tq/285915z105g568z0ss3ll7_w0000gn/T/agent-full-setup-acceptance-ipmhgpho/native-gui-acceptance-summary.json`.
This run did not update the user's installed Desktop or Fleet.

### Native Python environment selection and inheritance

Real macOS first-run testing exposed two environment-selection defects. The
native file chooser resolved a virtualenv interpreter symlink to its base Python;
UI commit `03b5874f` now selects the environment directory and preserves its
lexical `bin/python` path. A regression exercises an actual interpreter symlink.
The updated Rust suite passes **17 tests** (`/tmp/local-first-setup-env-rust.log`),
and the first-run UI regression passes.

Separately, a GUI owner could launch Fleet with an unrelated system Python first
in PATH. Local Fleet now prepends the selected owner's interpreter directory and
removes inherited Python home/import/virtualenv overrides while retaining other
build tools. **10 local Fleet tests** pass in 19.05 s
(`/tmp/local-first-setup-python-fleet.log`), including a foreign-Python PATH
regression. Native startup confirms newly prepared Notebook dependencies use
Python 3.12.13 from the selected environment, rather than system Python 3.14.
Existing environments are not claimed to have been migrated by this change.

The isolated native GUI successfully creates and reopens a saved configuration.
A startup-close experiment drains the local profile normally. Full graphical
chat/reopen acceptance is still in progress. These tests use a separate debug
App bundle, private profile and local model fixture; they do not update the
user's installed Desktop/Fleet or change production defaults.

### Explicit credentials in the local product startup

The opt-in local CLI and native Desktop owner can now accept an explicit private
credential source. `--credentials` names a separate file; Desktop stores only
that optional path in its launch configuration. The source is read before Fleet
startup, matched against selected component/attached-Connector references, then
delivered through the existing owner-authenticated encrypted node vault during
staging. Repeated identical delivery is idempotent; conflicts never replace a
key. Ordinary setup, release metadata and deployment journals contain references,
not these secret values. No ambient credentials are discovered.

The reader checks the opened file's owner/permissions, rejects links, duplicate
fields and oversized inputs, and excludes workspace, uploaded package and
Desktop-served resource directories. It freezes the launch's input for retries.
Desktop's launch-details view shows the source path as text and detects a missing
source before spawning the owner. Graphical key entry, credential rotation and
the complete first-run wizard remain unfinished.

Focused source/compiler/profile tests pass **57 tests, 11 skipped** in 9.60 s
(`/tmp/local-product-credentials-unit-final.log`); the skips require native
executables not supplied to that invocation. Separately, two native profile
scenarios pass in 32.14 s (`/tmp/local-product-credentials-native.log`), including
an authenticated original Connector, actual model inference across two complete
Fleet lifetimes, immutable package reuse and absence of the supplied key in
profile JSON journals. Upstream responses are deterministic test services.

The complete configured General Team gate passes **1 scenario in 348.46 s**
(`/tmp/local-product-credentials-general-team.log`). All upstream text requests
require the selected Bearer credential, across four real Fleet lifetimes with
configuration review/approval, CLI and native Desktop control entry points,
real Shell/Files work and history/memory recovery. It checks that the key is
absent from setup and profile JSON journals. This is not paid/live-provider,
fully installed native-GUI or production deployment acceptance.

UI commit `eaf88a6d` supplies the native launch field and path-only display.
Its final **15 Rust tests** pass (`/tmp/local-product-credentials-rust-final.log`),
including literal argument forwarding, persistence and missing-file handling.
Chromium setup/update interaction tests pass with the credential path rendered
as text (`/tmp/local-product-credentials-ui-final.log`); the screenshot was
inspected. No user installation was updated, and no default cutover occurred.

### Native Desktop settings and configuration review

Paired UI commit `6b3684a9` returns the opt-in native shell to configuration after
**Stop Apps & settings**.
It requires both a clean stopped receipt and successful owner exit; late readiness,
a stop requiring attention, and uncertain exits do not reopen the App view or
permit replacing a profile. Restart clears the previous lifetime's stop flags.
The existing close-window path still drains the owner and exits.

A stopped workspace can choose a private candidate setup, see the source/target
paths and changed JSON pointers, discard the review or approve it. The shell uses
its explicit launcher and the public `pantheon local-update` entry point, delegating
checkpoint/candidate/contract validation to the existing owner command. It never
starts Apps during review/approval. Approval remembers the target for a later
explicit start; failed metadata persistence retains that approved choice for retry.
Environment configuration retains precedence. New providers, package upgrades,
values editing and a complete first-run wizard remain separate work.

The final native regression passes 14 tests, including real subprocess output/exit
handling, bounded output, rejection without echoing stderr secrets, literal argument
passing, stopped-owner gates and retention after metadata-save failure
(`/tmp/agent-desktop-update-rust.log`). Chromium interaction tests pass for selection,
cancellation, review/discard/apply states, failed review, explicit restart and the
620 x 420 layout (`/tmp/agent-desktop-update-ui.log`); a screenshot was inspected.
Those UI tests use controlled native IPC.

A separately identified macOS test client verified real stop-to-settings, the
candidate picker default directory, cancellation, same-config restart and normal
close/drain with a test owner. Automated native file-row selection did not complete;
native apply/restart with the real complete product is not claimed. The test client
was closed; the user's installed Fleet/Desktop was not changed.

Runtime/compiler regression passes 43 tests, with the native-only case omitted in
that invocation (`/tmp/agent-desktop-update-runtime.log`). Separately the complete
General Team gate passes one scenario in 364.70 s using the new public command for
actual review/approval between four native profile lifetimes. It preserves ids,
history, memory and real Shell/Files work (`/tmp/agent-desktop-update-general-team.log`).
Its model upstreams are fixtures. These checks are not full installed-GUI, migration,
release rollback, live-model or production-default acceptance. No deployment occurred.

### Reviewed local configuration updates

`python -m pantheon.platform.local_profile_update` reviews and approves an exact
candidate setup for an existing, cleanly stopped local product profile. It
retains the same App packages, scopes, names and attached model publications.
Component values, credential references and dependency bindings may change,
including selected model routes and Files model capabilities. Package/topology
and engine-publication changes remain part of the separate release update work.
See [the owner command](local-agent-general-team.md#review-and-apply-a-configuration-update).

The review binds source/target manifests to the exact stopped checkpoint. Its
output lists changed JSON pointers and digests, not configuration values or
credentials. Approval reuses the original restart planner, verifies stopped
generations and unused resources, and runs the existing installed-contract
preview for providers and consumers before writing private review receipts.
It publishes an atomic approval reference last, without rewriting the old cycle,
starting Apps or issuing App grants. The normal target launch consumes that
decision and revalidates generations; no lifecycle bypass or new start protocol
is introduced. Old checkpoints and instance data remain in place.

An interrupted receipt/reference write can be retried with the exact review id.
Changed candidates, stale checkpoints, uncertain stops and malformed contracts
are rejected. A separately reviewed reverse configuration change is supported
after clean shutdown; it is not a data rollback. Failed-start recovery, package
migration/rollback, adding/removing providers and graphical configuration values editing
remain open. The command uses the owned local Fleet infrastructure, independent
of the installed Fleet; no production default or running user profile is changed.

The local profile/update regression passed **45 tests in 35.44 s**
(`/tmp/local-profile-update-native.log`), including real native processes through
old configuration, approved new configuration and a reviewed reversal, preserving
instance data. Final focused failure/contract tests passed **38 tests**, with the
native case omitted in that separate invocation
(`/tmp/local-profile-update-failure-final.log`); they cover interrupted durable
writes, concurrent approvals, stale/corrupt/link-swapped receipts, malformed
bindings, unchanged identity constraints and import without Agent execution.
These scopes overlap.

The configured complete General Team gate passed **1 scenario in 357.48 s**
(`/tmp/local-profile-update-general-team.log`). Between its first two of four
Fleet lifetimes it runs the public review and approval command as actual
subprocesses, updates Files sampling configuration and reopens the same profile.
Stable Agent ids, original conversation/history, real Shell/Files calls, persisted
background memory, later CLI/Desktop control access and clean shutdown pass.
Model upstreams remain fixtures. This is not a GUI Settings, installed Desktop,
live model-provider, package upgrade or data rollback acceptance claim.

### Explicit deferred Files model choices

The complete General Team preset and prepared Files v0.6.15 accept an explicit
`{"state":"unconfigured"}` for image observation (`files.sampling`) and/or
image generation. This supports first-run owners with only text models, while
retaining the complete provider graph, all Agent plugins and both tool methods.
Deferred methods return `model_not_configured` and their configuration path
before reading inputs or constructing model clients. They contribute no selected
deployment/route authority. Missing values, mixed state/binding objects and
invalid configured credentials still fail startup; no ambient fallback is added.

The same immutable Files package can subsequently start with an owner-prepared
binding and perform real Model Services image job/copy/cleanup flows. Existing
local product profiles pin their full composition hash and deliberately reject
unreviewed edited setups. The configuration update above now permits selections
from existing publications, including an unconfigured-to-configured change.
Adding a new attached model provider still needs the release update flow. Simply
editing JSON and restarting is not a supported update. Do not bypass the journal
or recreate a profile to enable a capability. Graphical Settings/first-run and
full release rollback remain separate work.

Verification: the initial focused Files/preset regression passed **77 tests**
(`/tmp/agent-deferred-models-tests.log`); the final packaged transition and Files
image tests passed **24 tests in 14.78 s**
(`/tmp/agent-deferred-models-generation.log`). Product builder/compiler coverage
passed **32 tests in 21.53 s** (`/tmp/agent-deferred-models-product.log`). These
scopes overlap and must not be added as unique tests.

The native complete-team gate passed **2 scenarios in 676.44 s**
(`/tmp/agent-deferred-models-native.log`): fully configured and explicitly
unconfigured Files models, each across four complete profile lifetimes including
CLI and native Desktop control/HTTP-view entry points. Both retain the canonical
team, all plugins, real Shell/Files calls, persisted memory and history, and clean
shutdown. The unconfigured case publishes only a text model, keeps both image
tools visible, returns explicit setup errors and makes no image requests. The
model upstreams are fixtures; this is neither real-provider/installed-GUI
acceptance nor a warm-start benchmark. No production deployment occurred.

### Native Desktop configuration selection (prepared-product entry)

Paired UI commit `dd491803` adds a native file chooser and explicit pre-start
review to the opt-in Agent Desktop shell. Missing/invalid launch configuration
now leaves an actionable setup screen rather than only a failed launch. The
selected workspace/profile/bundle/launcher are previewed; selecting or cancelling
does not start Apps. Explicit start saves a private atomic launch-metadata copy
and runs the existing local composition. The owner setup, model choices,
credentials and original data are not rewritten. An explicit environment launch
configuration still overrides the remembered choice on the next process start.

The packaged main-window origin and idle setup state are required for selection
and launch. Running or uncertain profile outcomes cannot be replaced with a new
profile through this UI. Basic launcher/bundle/setup existence is checked before
spawning, so missing product inputs can be corrected without starting an owner.

Eight native tests passed (`/tmp/agent-desktop-setup-rust-final.log`), covering
persistence/readback, private permissions, link rejection, bounded launch input,
entry origin, lifecycle guards and the existing readiness contract. The Chromium
shell test passed (`/tmp/agent-desktop-setup-ui.log`) with controlled native IPC;
the small-window screenshot was inspected. An independently identified macOS
test build also verified its real native file picker, cancellation and preview
of the complete product from the previous native gate. That manual check stopped
at review because its model fixture was already terminated; the test client was
closed without launching a profile or changing the installed product.

This is a prepared-product configuration entry, not the complete graphical
model/project creation wizard. Final installed-native chat/tool/shutdown/reopen,
first-run model policy, migration and release rollback remain outstanding. No
production deployment or default switch occurred.

### Complete General Team release assembly

`python -m pantheon.apps.general_agent_release` composes the existing independent
App builders into the ordinary release-set format. The source builder includes
all eight tool providers, Agent, allocator, Agent/Files model access and explicitly
named original model Connectors. It derives complete versioned Agent dependency
contracts from the actual provider manifests, preserving Files startup access,
per-Agent Shell resource arguments and runtime tool bindings. Files includes its
sampling/image-generation clients and the explicit target transport. Agent and
Notebook frontend builds are explicit inputs; the paired Agent version/boundary
and transport architecture retain the existing release checks.

No owner configuration, credentials, model engine selection or runtime launch is
part of assembly. The output is atomically published after artifact indexing;
existing output paths are rejected and failed builds remove their staging tree.
The existing local bundle builder adds explicit Fleet executables afterward.
The sixteen-App index bound remains unchanged, allowing twelve mandatory packages
and up to four selected model aliases. See [the product build commands](local-agent-general-team.md#product-release).

The source command/artifact gate passed **13 tests in 13.46 s**
(`/tmp/general-agent-release-build.log`), using actual provider builds, the paired
GUI and native Shell/transport for the successful command. It checks exact tool
dependency versions/interfaces, release hashes, alias collisions, output reuse
and cleanup after a provider failure. The preset/product compiler regression
passed **33 tests in 18.67 s** (`/tmp/general-agent-release-compile.log`).
The complete native General Team gate now consumes this production assembler
instead of constructing its own dependency map and provider release set.
It passed **1 test in 355.64 s** (`/tmp/general-agent-release-native.log`):
four complete profile lifetimes preserve the canonical team, all default
plugins, real Shell/Files calls, persisted memory and history, including actual
CLI and native Desktop control entry points and retirement of the old HTTP view.
This is macOS arm64 evidence with fixture model upstreams, not installed graphical
Desktop acceptance or measured warm startup performance.

Graphical first-run setup, final installed Desktop, live-model/cross-node testing,
migration, release cutover/rollback and production default replacement remain
separate acceptance work. No production deployment or default switch occurred.

### Complete product preset shared by CLI and Desktop

`compose_profile` accepts an explicit `preset: general-team` owner setup. The
pure distribution preset expands all original tool providers, per-logical-Agent
Shell allocation, independent Files model access, Agent auxiliary/GUI bindings,
Evolution's Agent callback and local Model Services management. It uses the same
ordinary dependency/tool-contract compiler and immutable release declarations.
It imports no Agent runtime, discovers no credentials and performs no launch or
inference. Existing detailed setups remain supported and conflicting custom
bindings are rejected rather than discarded. See
[local-agent-general-team.md](local-agent-general-team.md) for the input contract.

The owner retains Agent settings, projects, credentials and model selections.
When Fleet tiers are selected the complete preset requires low/normal/high
explicitly, avoiding the foreground-only configuration that previously broke
memory. Files only receives selected deployment/route access. Optional cloud
management binds its own Hub authority; its absence does not affect local model
management or imply cloud resources were stopped. Every default provider must
be present; an incomplete bundle cannot silently reduce the General Team.

The compiler/product regression passed **33 tests in 20.00 s**
(`/tmp/general-agent-preset-compile-final.log`), including a fresh-process import
boundary, preservation of platform-budget/model/settings choices, two project
GUI bindings, independent Shell scope, Files model authority and invalid/missing
provider/conflicting-configuration rejection. The initial native preset gate
passed **1 test in 282.09 s** (`/tmp/general-agent-preset-native.log`), retaining
all original plugins and the existing chat/tool/history/background-note checks.
The extended gate passed **1 test in 351.60 s**
(`/tmp/general-agent-preset-clients.log`): after the first two Fleet lifetimes,
the actual `pantheon cli --bundle --setup --chat-id --stream` command resumed
the same conversation with a real Shell call, followed by the native Desktop
control entry point, its generation-bound HTTP view, restored member identities,
chat/history and stdin-close shutdown. All four cycles ended stopped; the old
Desktop view then refused connections. This gate exercises the Desktop host
protocol, not a newly rendered or installed native webview. Test scopes overlap.

The preset removes handwritten topology/grant/schema configuration. Complete
product release assembly is supplied by the source builder above. Graphical
first-run choices, final installed Desktop
acceptance, migration/rollback, cross-node and default production cutover remain
pending. No production deployment or default switch occurred.

### Original General Team on the complete local App profile

The native joint gate now runs the canonical three-member General Team with all
seven default plugins on fourteen ordinary App instances. It does not replace
the team with a test template or disable memory, learning, compression, task,
think, Fleet or Model Services plugins. The owner explicitly binds low/normal/high
model tiers; the initial normal-only fixture allowed foreground chat to succeed
but left memory extraction broken. No runtime tier fallback was introduced.

Two complete Fleet lifetimes verify provider readiness, Notebook workspace
identity, shared Files reads, Files image generation through the original model
Connector, the same management/inference directory, stable logical Agent ids,
chat with a real Shell command, conversation recovery and ordered shutdown.
Additional assertions require completed memory-extractor calls and a persisted
session note in the Agent's own data, not only the absence of error messages.
The resulting model tool menu includes the original Web, Evolution, Desktop,
image and model-management operations. App processes, grants, storage and Fleet
transport are real; text and image engine HTTP responses are deterministic
fixtures. Presence in the menu does not prove every tool's full functionality.

`tests/test_local_profile_general_team.py` plus the original native model HTTP
regression passed **2 tests in 284.61 s**
(`/tmp/general-team-shared-directory-final.log`). This time includes clean package
installation and browser downloads, not a measured user-facing warm startup.
Earlier core and auxiliary runs passed separately; their counts are not additive.
The fixture now supports a larger declared model context and one tool response
per actual user prompt, so plugin-injected reminders cannot trigger an endless
scripted tool loop. Other model HTTP tests retain their original defaults.

The first gate used a detailed setup snapshot; the product preset above now
supplies its topology. Full provider behavior, live model/cloud calls,
release/migration/rollback and default cutover remain pending. This gate is not completion of P3 or of the extraction plan.

### Shared tool schema references for complete Agent startup

The complete General Team startup exposed a real configuration limit: the
prepared Agent component was 132,398 bytes, exceeding the existing 128 KiB
configuration bound. Files schemas appeared in execution profiles, App-lifetime
auxiliary bindings and project GUI bindings. Static auxiliary/view bindings can
now name a `profile` in the same prepared snapshot instead of repeating its
functions and hidden service schemas. They must still supply their own explicit
credential; referencing a profile does not allocate an Agent session or borrow
its grant. Inline schemas remain supported. Mixing a reference with overriding
functions/service schemas, unknown profiles or cross-group references is rejected.

The complete composition now carries 96,122 bytes of Agent component configuration
with the same tool profiles and plugins. Limits and Fleet transport are unchanged.
Schema-reference, real TLS project-grant, dependency binding, launch and plugin
tests passed **64 tests in 27.72 s** (`/tmp/agent-schema-reference.log`). Independent
project credentials, denied methods, rejected injected session arguments and
shutdown remain covered for both inline and referenced schemas. The General Team
core startup/reopen gate subsequently passed; these are configuration-byte savings,
not a measured reduction of total runtime memory.

### Shared local management and inference directory (local candidate)

Prepared model management now accepts an explicit `directory_root` for the
same owner-bound local journal used by Model Services inference. It validates
an existing snapshot before opening connections and never initializes a missing
catalog or borrows an ambient login. The original Hub-backed mode remains.
Local publication and route calls stay local even when an optional Hub credential
is supplied; only the explicit Modal endpoint is forwarded to that cloud account.
The management App manifest makes Hub credentials optional for this local mode.

Without cloud credentials, overview reports `modal_available=false`, an explicit
reason and `modal_gpu=null`. It does not run expiry reconciliation using a made-up
empty inventory. Cloud start/status/stop refuse unavailable authority before
side effects. The deploy tool also requires explicit launch confirmation when
its default H100 target is selected without a node or GPU argument.

Native verification installed the management package and original Connector on
real local Fleet, repeated stop/start and manager reopen, and checked the same
journal through the independent read-only model-access control client. Closing
management left the model running. Both original Hub and local paths passed;
the local case sent no directory request to the fixture Hub. The native and
lifecycle group passed **34 tests in 38.28 s**
(`/tmp/model-management-shared-native.log`). Cloud-boundary, deployment, ownership,
recovery, group and original-plugin regression passed **70 tests, 1 skipped**
in 0.73 s (`/tmp/model-management-shared-regression-final.log`). These are overlapping
scopes; engine HTTP responses remain fixtures, not paid GPU evidence.

The complete General Team recipe now includes the same ordinary management App
and preserves every default plugin, with management defaults on its primary
member. Its joint core execution gate has passed with the limitations recorded
above. Local group journals,
automatic idle observation/wake, deployed cloud binding/renewal, migration and
release acceptance remain outstanding. No production deployment or default
cutover occurred.

### Independent Model Services management package (local candidate)

`model-services-management` v0.1.0 now packages the original nine management
operations as an ordinary headless App with the `model-management@1` interface.
It includes the original Connector, model catalogs and Fleet inventory, without
Agent, team or settings implementations. Connector resources resolve within the
immutable package instead of an ambient App checkout. Hub, Fleet and Controller
credentials and trust roots arrive through ordinary encrypted node-vault delivery;
startup failure closes created clients, and shutdown joins owned work before
closing its connections. Remote services survive closing the management App.

The real native installation gate passed **3 tests in 17.92 s**
(`/tmp/model-management-package-20261005.log`): original Connector installation,
catalog/node inspection, stop/start, management-App reopen, remote Connector
survival and private bus-credential cleanup. Fleet, App processes and Connector
RPCs are real; Hub directory and engine HTTP responses are fixtures, not cloud
GPU deployment evidence. Management/recovery/group regression passed **138 tests,
1 deselected** in 2.06 s. Prepared group-forget additionally checks explicit
Controller routing and rejects missing/negative revocation acknowledgements;
its **11-test** group passed, including legacy behavior. Test scopes overlap.

The original package gate used the Hub-backed directory; the newer shared-local
directory gate above adds explicit local authority. Complete General Team
execution, automatic credential renewal and broader release/migration gates remain pending.
No production deployment, remote push or default cutover occurred.

#### Complete-product directory integration requirement

The shared-directory work above addresses the following integration gap identified
before that change. `LocalAppProfile` published into `LocalModelDirectory`, while
the prepared management App used the Hub directory. Merely adding the
management package with a separate test Hub would leave local inference services
invisible to management and is not complete product acceptance.

The local journal previously admitted only attached publications and aliases.
It rejected managed-engine, idle, recovery/update/stop and group journals, whereas
the original management tools need those lifecycles. Do not weaken its validator,
fake an empty cloud inventory, substitute read-only management, or remove default
plugins to get a green General Team gate. The required follow-up is an explicit
directory binding shared by management and inference, preserving the existing
Hub path and supporting the original locally applicable lifecycle records and
transition invariants. Remote Modal operations still require explicit cloud
authority; its absence must not be confused with an observed empty cloud Fleet.
The existing Hub deployment/idle/operation-stop contracts and runtime group
journals are the source of those invariants, rather than new engine adapters.

#### Shared deployment lifecycle contract (local candidate)

The original Hub deployment schema and idle/update/recovery/explicit-stop rules
now live in the framework-independent `pantheon.model_contracts` modules. Hub
uses a checked copy plus a small HTTP-error adapter; regenerate or validate it
with `scripts/sync_model_contracts.py --hub PATH [--check]`. The checked copy has
a source digest manifest and an import-boundary test, so Hub does not depend on
the Agent/runtime distribution. No engine adapter or model lifecycle algorithm
was replaced. Existing HTTP status/detail behavior is retained.

`LocalModelDirectory` now admits attached and managed deployment records through
that same contract and validates transitions under its existing atomic revision
lock. It retains strict local scalar validation, owner/file protections, bounded
payloads, tombstone revisions and read-only consumers. A pending stop prevents
a late recovery/update publication from reopening the service. Validation errors
do not echo rejected input, which may contain accidentally supplied credentials.
The ordinary model-access package includes the contract and pins pydantic; the
management package already required the same pydantic version.

Validation: **12 new lifecycle tests** include the original recovery coordinator
with lost recover/configure/resume acknowledgements against a real persistent
local directory, engine-update target pinning, explicit-stop fencing, idle
registration/cancellation and competing writers. Fleet observations in those
tests are fixtures, not a newly deployed local model engine. The combined native
management/access/directory/local-profile group passed **112 tests in 69.86 s**
(`/tmp/model-directory-contract-native-diagnostic-20261005.log`). Hub's original
model/idle/stop/multimodal/routes/groups/platform-key suite plus contract integrity
passed **91 tests in 4.02 s**. Test scopes overlap.

The first native run failed on an offline node observation during installation;
runner logs did not establish its cause. That run terminated, and its one orphaned
test Connector was explicitly retired. The next run included sanitized liveness
diagnostics and passed. This is not evidence that transient startup recovery is
fixed; its product workflow remains a separate open requirement.

The later shared-directory increment above binds the management App locally.
Still pending: local group coordination/publication and automatic idle
observation/wake, deployed cloud authority for Modal, and exhaustive General Team
capability parity. Accepting idle intent in
storage does not implement its wake endpoint. No production deployment or
default cutover occurred.

### Model management ownership extraction

The nine original public Model Services management operations now live in
`pantheon.models.management_tools`, with no Agent, team or settings imports.
The legacy plugin subclasses the same implementation and retains its local
`use_fleet_model` operation. The prepared Agent plugin still switches models
locally using the selected member's own Model Services access. The release
allowlist carries the shared tool definitions needed for legacy plugin registry
imports, without adding the model deployment manager to the Agent package.

`ManagementState` gives a prepared manager its own private deployment plans,
Controller transport and separate Modal/deploy engine-task tables. Original
`model_deploy` and `modal_gpu` operations select that state explicitly; unbound
legacy callers preserve their previous paths. A prepared manager requires
explicit directory and Fleet clients (including falsey supplied client objects),
so it cannot silently rediscover process-global credentials. Plans are bounded,
atomically replaced, private and reject symlinks and identity mismatches. Close
cancels and joins its own local engine tasks even if the caller cancels shutdown;
it does not terminate durable remote models/paid nodes or claim a cancelled RPC
has a known remote outcome. A reopened owner observes original deployment status.
The future prepared host must invoke this drain before closing its clients.

Fleet and model management now share `pantheon.apps.fleet_controller`, preserving
explicit TLS/credential ownership and Fleet's seven-day HPC token policy. Model
launch/revoke requests use the supplied Controller, never a fallback environment
when that explicit connection fails. This adds no new runtime dependency.

Validation: ownership/deployment/Modal/legacy-plugin and real native Fleet
package regression passed **55 tests in 24.23 s**
(`/tmp/model-management-owned-native-20261005.log`). Plugin/HPC/update regression
passed **27 tests in 6.80 s**. The independent Agent release chat/restart/drain
gate passed **2 tests in 13.18 s**, confirming the changed import graph in the
clean packaged runtime. A separate five-test ownership run includes two equal-id
Modal launches through independent managers and checks original publication and
revoke routing; its Hub/engine responses are fixtures, not paid GPU launches.
Scopes overlap; these do not establish complete team or management App acceptance.

The package and explicit host cleanup described above now cover the installed
management App. Connecting it to the complete General Team remains required.

### Primary-member management and host-only tool methods

Deployment dependency defaults now accept `primary_toolsets`. The team assembler
applies these only to the first (primary) member before revision hashing and
instance allocation. The selected tools still require approved profiles and the
normal owner-bound grants. Canonical recipes remain unchanged; an explicit tool
selection by another member is not a new role-denial policy. Reordering the team
changes the affected config revisions while logical identity and normal reopen
behavior remain durable.

Ordinary tool contracts can explicitly select hidden `service_methods` for host
operations such as Files `stat_path`. The compiler preserves interface admission
and emits `service_functions` separately from the model-visible functions.
Static and allocated providers validate and carry both through the same grant
transport; `list_tools` exposes only the visible set. Hidden methods are never
automatically granted. This supports task-output metadata without expanding the
model's tool menu.

Focused dependency defaults/compiler/product/instance/launch regression passed
**90 tests in 33.15 s** (`/tmp/agent-role-services-20261005.log`). Coverage includes
primary-only allocation across team reorder/reopen, idempotent preflight, hidden
method invocation and schema/interface rejection. The complete General Team
recipe now selects Fleet for the primary and Files metadata for host calls, but
its end-to-end gate remains incomplete pending Model Services management integration. No
production deployment or default switch occurred.

### Prepared Fleet management App (local candidate)

Fleet v0.8.1 now has an ordinary headless management package preserving the
original reflected tool surface: node inventory, selection and execution,
transfers, updates, HPC launch, connected-cluster jobs and HPC services/files.
The package receives private bus and Controller credentials from the same
ordinary encrypted node-vault delivery used by other Apps. It includes no Agent,
settings singleton or Desktop implementation. Legacy constructor behavior remains
available when no explicit owner binding is supplied.

The prepared service uses one owned resolver for every control path; HPC join
tokens and update release lookup use its explicit Controller client. Its local
node comes from the prepared identity rather than host files or hostname guesses.
A broken connection cannot silently join the environment's Fleet. Shutdown joins
transfer workers before releasing connections. Desktop and Fleet now share the
same small `pantheon.apps.owned_bus` implementation and private credential-file
cleanup contract; this adds no new third-party runtime dependency.

Installed-package testing exposed a remaining global proxy in window inventory.
Explicit Fleet inventory now invokes each Desktop by exact node, instance,
revision and generation over its owned connection. Unavailable/stale Desktop
calls retain the node inventory and report warnings, without falling back to a
legacy service ID. Legacy callers retain their existing proxy path.

Validation: native packaged install/configure/start/RPC/stop/reopen plus focused
ownership tests passed **10 tests in 22.11 s**
(`/tmp/managed-fleet-native-20261005.log`). These include real node shell execution
and private bus credential cleanup after each stop. Routing/HPC/update regression
passed **38 tests, 1 skipped** in 0.48 s
(`/tmp/managed-fleet-routing-20261005.log`). Earlier Fleet/real Desktop bus and
package regression passed **37 tests, 1 skipped** in 21.79 s
(`/tmp/managed-fleet-final-20261005.log`); scopes overlap. HPC scheduling itself
was not exercised on Sherlock, and no machine update or cloud launch occurred.
Credential renewal and complete General Team integration remain pending. No
production deployment, default switch or remote push occurred.

### Full-team composition follow-up (not accepted yet)

The complete General Team gate keeps the canonical team and all default plugins.
The first real start exposed missing App-lifetime Files authority for memory and
learning. Its recipe now declares Files at startup and supplies independent
auxiliary/view clients, while ordinary conversation Files grants remain owned by
logical Agent instances. This configuration has not yet passed full execution.
The next attempt reached the four-minute cold-install test deadline; it did not
establish plugin startup or chat success. Emergency teardown now stops actual
instances before closing the test bus; all twelve instances in that failed run
were stopped. The full cold-install gate has a separate fifteen-minute total
budget, leaving Fleet's individual lifecycle/readiness deadlines unchanged.

Fleet management now has an independent package with its original public tools.
Primary-member grants and task-output metadata authority have focused tests;
complete team composition still requires connecting the independently tested
Model Services management package and end-to-end verification of those grants. Do not
make the team pass by disabling plugins, granting every member management tools,
or replacing it with the small Shell fixture. Production startup-failure recovery
also remains distinct from the integration test's emergency cleanup.

### Shared providers in the complete local product

Files sampling and image-generation bindings now accept explicit public
`trust_roots_pem`. Both dependency control RPC and Model Services data transport
use the same per-client TLS context, without changing process-wide certificate
variables. Invalid explicit trust fails before constructing a client. Files
v0.6.14 includes this change. Notebook v0.7.2 accepts an existing absolute
`values.notebook.workspace`, so Files, the Agent project and the Notebook kernel
can share project files while Notebook logs/context records remain private App
state. Omission preserves the host-selected workspace.

The complete composition exposed a second limitation: prepared Model Service
registration only admitted chat selections. Explicit `operations` now travel
through the original prepared registration and clean-restart path. Selected IDs
must still be discovered, selections cannot contradict reported operations, and
the original attached-directory engine/modality rules still apply. Image models
do not acquire invented text context limits. Publication does not issue inference
or change the Connector; directory conflict and uncertain-write handling remain.

Focused sampling/image/Notebook tests passed **37 with 1 skipped** in 24.38 s.
Provider/compiler/MCP regression passed **60 tests** in 28.46 s (overlapping
scopes). Prepared publication/bootstrap tests passed **107 tests** in 54.94 s,
including image selection, invalid operations and clean restart. Full General
Team joint execution remains under test; these counts do not establish it.

### Complete App tool contracts for General Team composition

The local product compiler can now expand owner-selected `tool_contracts` from
the actual bundled manifests, preserving all visible methods, parameter types,
defaults and documentation. It emits the existing Agent profiles and allocation
policies rather than a reduced test team's hand-written functions. Interface
coverage is checked before deployment, Python type expressions are parsed without
evaluation, and Shell session arguments stay owner-bound. Hidden service methods
are not implicitly granted. The canonical General Team's members and selections
remain unchanged; installing available profiles does not make every member use
every tool.

The full contract check found two real compatibility gaps. Files v0.6.13 adds a
`file-management@1` interface for previously ungrouped file operations, retaining
the original fs/outline/image interfaces. Desktop's existing `_action`/`_args`
wire parameters now survive schema conversion, Python admission and the Go
dependency gateway. Method identifiers and enumerated argument authority remain
unchanged; the HTTP gateway regression rejects undeclared keys and attempts to
overwrite owner-bound values.

All six default tool-provider schemas together exceed 64 KiB (roughly 70 KiB
before surrounding configuration). Prepared App input now has a separate 128 KiB
limit, composition a 512 KiB limit, and their private recovery journals matching
bounds. The 256 KiB resolved snapshot cap and 64 KiB grant-request bound remain.
A real lifecycle Manager dispatch test checks an 84 KiB value survives prepare,
idempotent configure and start, while oversized input is still rejected. The
product compiler test retains every provider's visible functions and validates
the resulting Agent configuration and private recipe readback. It does not start
the full team; native combined provider startup, model-assisted Files authority,
shared workspace behavior and full chat/close/reopen are the next gate. No
installed/default product, cloud deployment or remote branch is changed.

Validation: **332 Python tests passed, 11 skipped** in 24.83 s
(`/tmp/general-team-contracts-python-20261005.log`). Both Go gateway/lifecycle
race suites passed, followed by the focused configuration race suite. A separate
native Controller/NATS/Runner gate passed in 5.61 s
(`/tmp/general-team-native-config-20261005.log`): an actual process hashes the
complete generated schema configuration, the whole local profile closes and
reopens, and the new process independently produces the same receipt. This
proves configuration delivery/recovery, not full General Team execution. Skipped
tests are not counted as verified, and production defaults remain unchanged.

### Ordinary credential delivery and local Desktop composition

Owner-side credential delivery now lives in `pantheon.apps.credentials`. The
existing model classes remain HTTP-only compatibility adapters over the same
implementation; neither model API paths nor platform-budget routing are changed.
Ordinary Apps can provision NATS/TLS/WebSocket credentials through the existing
owner-authenticated encrypted node RPC. The immutable Desktop installation gate
now uses that route instead of direct node CLI writes, checking idempotent retry
and refusal to overwrite an existing key before starting the actual package.

Local profiles have opt-in `fleet_credential` and App-alias-scoped
`fleet_event_prefix` substitutions. A startup cycle freezes private signed bus
credentials before publishing a recipe. The recipe contains only references;
retry never silently changes them when the enclosing Fleet renews its own key.
A clean profile restart binds fresh authority coordinates and preserves Desktop
window documents. Modified identities, permissive snapshot modes and symlinked
snapshots are rejected. Unchanged profiles do not receive bus credentials.

The new native gate closes and reopens the entire local Controller/NATS/Runner
profile, checking real Desktop window events, Fleet calls and restored windows.
This is local composition, not complete General Team or production acceptance.
Long-lived Desktop bus renewal, remaining native-control paths, complete team
composition and all outstanding migration/release/cutover gates remain open.
Combined profile, ordinary credential, model budget/migration and prepared
Desktop regression passed **182 tests, 2 skipped, in 83.89 s**
(`/tmp/profile-app-credentials-final-20261005.log`). The final App-scoped event
namespace adjustment is covered by the separate native profile rerun in
`/tmp/profile-desktop-scoped-final-20261005.log`. Skipped cases remain unverified.
No production deployment, default entrypoint change or remote push occurred.

### Prepared independent Desktop package (candidate)

The Desktop backend now has an immutable POSIX package builder and a prepared
entrypoint. It owns separate Fleet and event-bus connections, the Store identity,
window documents and HTTP data service. No Agent implementation, global settings
or CLI login is shipped. Configuration is checked before connections are opened;
private state cannot overlap served roots, including symlinked Store locations.
Bus credentials are provisioned through the existing node vault and kept out of
App manifests. NATS/TLS/WebSocket endpoints retain their transport identity;
WebSocket route trailing slashes are preserved. Existing HTTP model normalization
is unchanged. JWT credentials use base64 only for the vault's printable format,
not as encryption. Startup failures and cancellation join owned connection cleanup.

A real local Controller/NATS/Runner installs the package and its locked Python
dependencies, supplies generation-bound configuration, invokes Fleet/catalog and
window operations, fetches authorized file bytes, stops, reconfigures, restarts
with preserved windows, and uninstalls. A separate real-bus gate checks both
loopback and prepared tunneled-data configuration, including wrong-token denial.
The tunnel gate uses a loopback public-origin fixture: it does not deploy a real
Hub/Modal tunnel. The broad Desktop/Browser regression passed **202 tests in
35.37 s** (`/tmp/managed-desktop-regression-20261005.log`). Final prepared-package,
data-ownership and runtime-configuration regression passed **53 tests in
20.05 s** (`/tmp/managed-desktop-final-20261005.log`); the groups overlap.

Go vault and lifecycle suites passed with the race detector; the final endpoint
cases were rerun after preserving WebSocket slashes. The broader Fleet command
suite is not green: two group-credential integration tests were refused by real
host memory admission. An independent live inventory measured 15,720,939,520
available bytes out of 68,719,476,736, below the default 25% reserve. No admission
policy was weakened and these failures are not counted as passes.

This remains a candidate: some native-control/local-rendering paths still use
the embedded engine. Full General Team composition, automatic profile bus
credential provisioning/renewal, remote data routing, migration, release rollback
and production cutover remain open. No installed product or default entrypoint
was changed. The implementation is isolated on `codex/agent-app-extraction`.

### Explicit Desktop routes Browser operations through ordinary Apps

A Desktop composed with an explicit Fleet binding no longer prewarms an ambient
Chromium engine. Its non-visible `browser_open(show=False)` uses the same ordinary
Browser App placement as visible browsing. Subsequent Agent operations retain
that exact node, revision and generation; an unavailable/stale target cannot
launch a replacement local browser. The existing Browser UI facade now forwards
page creation/reattachment, navigation, staging, focus, keys and close through the
same binding. Window page creation still uses the backend's operation identity
and persists the resulting Desktop page binding.

Listing and clearing browser data cover the known bound backends, preserving
partial-failure details. An empty close target is rejected before any newest-page
selection. Keyboard events can specify a page or use an explicitly focused page;
without either, multiple known backends are refused rather than guessed. The
legacy unbound Desktop construction remains available. This does not yet remove
all Desktop native-control/local-rendering uses of the embedded engine, persist
non-window page ownership across a Desktop restart, or package Desktop itself.

Routing tests use real Desktop documents and mock App placement, forbidding the
ambient Browser singleton. Combined Browser/stream/placement and Desktop-owned
Fleet regression passed **169 tests in 12.90 s**
(`/tmp/desktop-browser-fleet-final-20261005.log`), including the separate real
Chromium and real native Controller/NATS/Runner gates. This is not a real remote
Browser UI deployment. A broader legacy QuPath discovery check has two failures:
this checkout lacks its ignored `app.json`/skill payload. Running the committed
pre-change Desktop implementation reproduces both failures (12 other cases pass;
`/tmp/desktop-native-routing-baseline-20261005.log`). Full installed-catalog
acceptance must supply the actual versioned QuPath package, not assume those
ignored files exist in every source checkout.

No production deployment, default switch or remote push occurred. Immutable
Desktop packaging and complete General Team acceptance are still pending.

### Browser engine and stream adapter own shutdown

Browser engines now stop admission, join accepted calls and background workers,
close Chromium/Playwright and X display connections, stop owned display processes,
release the profile lock and join their event-loop thread. Repeated cancellation
of a close caller does not abandon cleanup. Failed cleanup retains resources for
retry. A timed-out thread start cannot create a second engine thread. The Linux
stream adapter uses this close path, registers cleanup before initialization and
retains its display reservation if cleanup fails. QuPath's normal Save/Cancel
stop guard remains in place.

Real headless Chromium tests use two independent profiles: closing one reaps its
process and loop while the sibling remains interactive; reopening the original
profile preserves its cookie. No host browser policies or native screen settings
are changed. Adapter tests use an actual child process as the display fixture,
not real Xpra capture. Combined engine, adapter, native-stream, portable-package,
window/tab/profile and snapshot regression: **135 passed in 11.15 s**
(`/tmp/browser-engine-owned-final-20261005.log`). This is lifecycle evidence, not
a measured memory reduction or a deployed Desktop/Browser acceptance test.

Explicit Desktop Browser placement, immutable Desktop packaging and full General
Team acceptance remain open. These changes are local, not a production cutover.

### Desktop Store identity separated from ambient login (current)

`DesktopStoreBinding` supplies a specific Store origin, bearer token (or explicit
anonymous access) and optional TLS trust. The ordinary Desktop Store API now uses
this configuration without importing CLI StoreAuth or consulting process Hub,
Store-token, proxy or CA-file variables. HTTPS is required except for loopback
local profiles. Redirects are reported without forwarding or retrying credentials.
The legacy entry continues to use its existing login configuration.

Real loopback HTTP tests exercise two separately authenticated Desktop instances
and an anonymous one, denied authentication, redirect refusal and request-client
cleanup. The calls forbid Agent/settings/StoreAuth imports. Existing publication
and contribution regression now runs with both legacy and explicit identities,
retaining exact source/candidate pins, private candidate checkout and publication
binding behavior. These workflows use mocked Store replies and real local Git;
no public repository or installed App is changed by these tests.

Store identity, development/publication/contribution, manager and source-serving
regression: **49 passed in 79.81 s** (`/tmp/desktop-store-owned-20261005.log`). Browser
engine ownership and prepared Desktop packaging are the next unfinished Desktop
boundaries; full General Team, migration/release and default-cutover requirements
remain open. This change is committed locally, not deployed or published.

### Desktop-owned explicit Fleet control

`DesktopFleetBinding` accepts an owner-supplied connection and concrete Fleet,
node, user and workspace coordinates. Desktop placement, lifecycle and window
usage operations use that resolver instead of the process-wide resolver. Its
connection belongs to Desktop; consumers receive ordinary tool grants, not the
Fleet credential. Disconnect fails through the explicit resolver without joining
an ambient Fleet. Cleanup retires the binding and closes its connection while
leaving independent running Apps and sibling connections intact. Failed cleanup
retains the resource for a later close attempt. Legacy construction remains
available for the current installed platform.

A real local Controller/NATS/Runner gate stages a small ordinary HTTP App through
Desktop's catalog, installs/starts it, checks exact bindings, acquires/releases a
window lease, rejects stale generations and foreign nodes, and stops/uninstalls
through a sibling Desktop after the first is closed. The calls forbid Agent,
settings, ChatRoom/factory imports and shared resolver lookup. Both connections
and the native profile's child processes are reaped. This is real control-plane
acceptance, not a prepared Desktop package or a real General Team run.

Combined owned-control, legacy placement, registry, instance-scope, HTTP lifetime
and rendered-platform regression: **50 passed, 1 native-rendered case deselected,
in 8.57 s** (`/tmp/desktop-fleet-regression-20261005.log`). The separate owned-Fleet
case does run the native binaries. The rendered case still uses fixture placement;
its screenshot was inspected (Files and model directory are visible, with its
expected unavailable-controller notice). No new production deployment or default
cutover occurred. Browser/Store control and immutable Desktop packaging remain.

### Desktop screenshot ownership and original model transport

Bound Desktop screenshots now use the explicit workspace and node identity,
returning ordinary image content blocks and node-aware file references without
importing Agent, global settings or local-node discovery. The consuming model
transport remains responsible for capability checks and image representation.
Legacy capture still uses the caller model carried by the tool execution context.
Native application exports and browser-composited captures retain distinct
provenance. Explicit destinations and symlinks cannot escape the bound workspace;
malformed/oversized payloads fail before writing. A native capture's successful
status can no longer override a failed artifact save on either local or remote
capture paths.

An isolated process denies imports of Agent, settings, ChatRoom, factory and
ambient node discovery, then returns captured pixels to the real Agent tool path.
Agent-owned image storage delivers those pixels through the original Model
Services Connector to an HTTP model fixture. The same regression exercises
Notebook images. This verifies packaging and model transport, not native screen
permissions, real provider output or an immutable Desktop release.

Focused regression: **34 passed in 11.05 s**
(`/tmp/desktop-screenshot-model-final-20261005.log`). Explicit Fleet/Browser/Store
control, prepared Desktop packaging and complete General Team acceptance remain
pending. No installed product, default entrypoint or remote branch changed.

### Explicit Desktop files and owned HTTP lifetime

`DesktopFilesBinding` supplies the workspace, ordered App catalog roots, served
data roots and owned data server. Bound catalog/supervisor, App source lookup,
batch App sync, dynamic endpoints and bespoke-module serving no longer discover
global settings. Workspace-relative App references use the explicitly selected
user App root rather than the process home. The original legacy entry remains
available; declared roots retain the existing traversal and symlink checks.
The binding does not give its consumer a new Fleet or platform-owner credential.

`DataServerConfig` explicitly chooses local/token-gated HTTP and an optional
App-owned tunnel cache. Passing this configuration bypasses ambient tunnel token
and port variables. Legacy callers still obtain their existing environment
configuration. Late-created authorized source roots remain discoverable without
restarting the listener.

Investigation also found that Desktop had no teardown for its HTTP thread. App
cleanup now drains accepted HTTP work, closes the listener, joins the server
thread/loop and releases endpoint/root references. Setup and close join their
worker even when the caller is repeatedly cancelled. Concurrent/repeated close
is serialized across caller loops; a failed drain retains its owner for retry.
Failed bind cleans up its own runner and thread without touching another
listener. Closed instances refuse restart, and a still-pending start cannot be
replaced by a second thread. These changes fix an actual lifecycle gap; they
are not a measured process-memory improvement.

Actual HTTP tests exercise source fetches, dynamic endpoint code, bespoke
modules, catalog precedence/configuration, user-root resolution, private-path
refusal, held-request drain, repeated cancellation, failed cleanup/retry and
occupied-port startup. Bound file operations forbid settings/Agent imports.
The production Desktop browser host now supplies both state and file bindings;
its unchanged rendered workflow checks Files, model directory and cross-viewport
window synchronization. Legacy endpoint fixtures now construct complete toolset
instances and reap their actual HTTP servers. One old manifest fixture used an
obsolete string entry; it now uses the current frontend-entry structure.

Combined focused and rendered regression: **67 passed, 1 native-Fleet case
deselected, in 8.39 s** (`/tmp/desktop-files-final-20261005.log`). The first
occupied-port fixture used different bind addresses; on this Mac the wildcard
listener could coexist with the loopback listener. Using the same wildcard
address verifies the intended real bind conflict. Native Fleet regression is
also complete: **1 passed, 1 local case deselected, in 15.98 s**
(`/tmp/desktop-files-native-20261005.log`). It uses the existing built native
Fleet Runner with current Python workers and the production Desktop frontend;
Files, PTY output, model directory, window synchronization and child-process
shutdown pass with Agent imports forbidden. The rendered screenshot was
inspected. That native path still uses legacy Desktop configuration; it verifies
lifecycle compatibility, not prepared-package deployment. Durations include
tests, not startup benchmarks.

Remaining: screenshot consumer/state independence, explicit Fleet/Browser/App
Store control dependencies, immutable Desktop package/configuration and complete
General Team composition. The isolated rendered test uses a fixture Hub and
local service placement; it is not installation/publication of a prepared
Desktop App. All remaining P0–P7 gates apply. No installed Fleet/Atrium, default
entrypoint or remote branch changed.

### Explicit Desktop document, presence and event ownership

Desktop's ToolSet now accepts a `DesktopSessionBinding`: an existing absolute
state root plus an owned named-event publisher. Document and presence operations
use that root directly, never their module-global stores. Multiple services for
the same desktop can read the same persisted document/leases with separate
publisher connections. Separate desktop namespaces retain separate records and
event subjects. Closing a service closes its own connection without deleting
windows or signing out the other viewports. The legacy constructor is retained.

`NamedStreamPublisher(backend=...)` accepts ownership of an explicitly supplied
transport; a closed publisher cannot recreate an ambient backend. This uses the
existing event wire protocol and Desktop reducer, not an Agent-specific bridge
or a second window implementation. The composition owner must supply matching
document and event namespaces and appropriately scoped transport credentials.

A real local NATS integration uses distinct publish-only credentials for two
desktop subjects and a subscribe-only viewport observer. It verifies document
isolation, shared-state reattachment, viewport addressing, rejection of a reply
delivered to the wrong service, continued sibling operation after close, and
closed connections. Ambient settings/Agent imports and transport discovery are
forbidden during those operations. Viewport action replies are simulated; this
gate does not exercise browser rendering or production grant issuance.

The production Desktop browser gate now separately supplies this binding to its
local Desktop worker, placing the document outside the Files workspace and
making module-global store access fail. The existing real frontend still opens
Files, creates a directory, reads Model Services and synchronizes window changes
between two viewports without Agent implementation imports. Its first pass took
7.66 s (`/tmp/desktop-binding-browser-20261005.log`); the rendered screenshot was
inspected. Placement/Hub are fixtures in this mode; the native Fleet worker mode
was not changed or rerun for this increment. This is not a newly installed
Desktop package or full General Team acceptance.

Final combined document/presence/stream/screenshot and rendered-browser
regression: **65 passed, 1 native-Fleet case deselected, in 7.43 s**
(`/tmp/desktop-binding-final-20261005.log`). The event integration closes its
connections and reaps its broker; the browser gate also joins its platform,
service and browser processes. Test durations are not startup benchmarks.

Remaining Desktop extraction includes explicit App catalog/data-server/browser/
Fleet dependencies, screenshot consumer independence, immutable packaging and
ordinary grants to the complete General Team. All outstanding P0–P7 gates still
apply. No installed Fleet/Atrium, default entrypoint or remote branch changed.

### Real Fleet Agent/Evolution composition

The public Evolution candidate now has a joint local-product gate using the
actual Controller, NATS broker, Fleet Runner, allocator, prepared App configuration
and original Model Services Connector. Both Agent and Evolution are installed as
independent packages through their normal hooks. The owner issues Evolution's
`agent-execution@1` startup grant with a bound durable consumer identity. Agent
receives its public Evolution tool through ordinary runtime allocation. Thus the
startup order remains allocator/model access, Agent, Evolution; the callback does
not require a cyclic startup dependency or another embedded Agent.

An actual chat synchronously calls `evolve`, while that independent controller
calls the same Agent's execution service for mutation. Shell edits the source,
Python starts a real kernel and returns 42, the evaluator verifies score 0.8
against the 0.1 seed, and the controller stores the winning source and report.
Original Model Services handles both the outer chat and nested execution. Only
the upstream model engine supplies scripted responses; no grant endpoint, App
runtime, tool execution or lifecycle coordinator is replaced by a fixture.

The first joint run passed in 108.20 s (`/tmp/evolution-real-fleet-20261005.log`),
including fresh package installation and two complete Fleet lifetimes. Ordered
stop leaves every admitted instance stopped with no retained resources, joins
Controller/broker/Runner processes and closes their listener ports. Reopening
preserves Agent history and Evolution results without another model request.
The strengthened gate additionally starts a fresh chat/evolution after reopening
to exercise the new generation's grants, rather than testing only saved results.
It and the existing complete local-product/CLI reopen regression passed together:
**2 tests in 199.42 s** (`/tmp/evolution-real-fleet-final-20261005.log`). The latter
finishes four profile cycles, including the public local command and interactive
CLI. These are integration-test durations including installation, not launch
benchmarks. The focused public Evolution, execution client and deployment-stop
regression passed **40 tests, 1 opt-in live Modal skip, in 7.05 s**
(`/tmp/evolution-composition-regression-20261005.log`); Modal was not rerun here.
The composition recipe and its identity/trust requirements are documented in
`apps/evolution/MANAGED.md`. Test setup now accepts additional provider packages
without changing the existing default fixture's tools or model policy.

This is one necessary General Team dependency gate, not complete team acceptance.
The full Files/Notebook/Web/Evolution/Desktop assembly, model-assisted sampling and
image authority, interrupted whole-run reconciliation, real provider calls,
cross-node execution, migration/publication/self-edit and default cutover remain
unfinished. No installed Fleet/Atrium, default entrypoint or remote branch changed.

### Public ordinary Evolution App and explicit placement credentials

The public Evolution service now accepts an explicitly injected execution binding
and deployment policy. Both single-file and codebase `evolve` calls retain the
existing background session, status/cancel, archive and HTML-report APIs, but the
prepared entry creates a private session manager and binds reasoning to the
ordinary `agent-execution@1` SDK. Legacy callers without the binding retain their
existing behavior. Single-file output now preserves the actual winning snapshot
files rather than storing the combined display text (including `# File:` markers)
as source. Additional files produced by mutations are retained too.

`apps/evolution/build_managed.py` creates a versioned Evolution v0.7.0 candidate
with an explicit Agent interface dependency, configuration/credential declarations,
portable Fleet host and hash-locked Python dependencies. The reviewed source
allowlist excludes Agent, ChatRoom and the legacy sandbox worker/launcher. Node
execution retains the native App trust boundary; isolated execution uses the
separately pinned ordinary evolution-tools image. Workspace paths and private
state are separate; public codebase/output paths are scoped to the workspace.
Web search exposes the original search method without implicitly adding a crawler.

Prepared isolated policy explicitly supplies image identity, resource limits and
a private Modal control-plane credential. A dedicated SDK client uses the fixed
Modal endpoint rather than ambient profile lookup; the same client reaches App,
image, create and recovery calls. Invalid resource policy fails before opening
that client. Credentials remain on the controller, never in workload environments
or input payloads. The ordinary before-stop hook joins Evolution tasks and confirms
placement shutdown before closing the Agent dependency and Modal client. Failed
cleanup retains those dependencies for reconciliation. Cancelled client startup
is joined before close; successful close releases its retained opening handle.

Validation through the registered public App API covers code and codebase runs
using actual isolated tool subprocesses, reports, private state restoration without
replay, scoped paths, delayed cancellation and failed cleanup. A separate packaged
controller process (Agent/provider imports forbidden) completes an actual Shell
and Python mutation through the production execution SDK and prepared HTTPS/TLS
credential. The grant endpoint and model responses are controlled fixtures; this
is not production grant issuance or paid-model acceptance.

The same independent controller package also completed a real Modal public-API
run using explicit prepared credentials. It evaluated the seed and mutated code
in separate containers; both `sb-LwLLLcHQdLu3MrgkZ863u5` and
`sb-hJZG3c4Q6Uwgo1cM3ASDww` were independently polled terminal after App shutdown.
This passed in **11.96 s** (`/tmp/evolution-managed-modal.log`), a test duration,
not a launch benchmark. It reused the reviewed image from the preceding controller
gate. The temporary credential-bearing test configuration was removed; durable
container/run receipts remain in the private test state.

The combined Evolution/controller/SDK/Modal regression passed **121 tests, 1
opt-in live test skipped, in 40.30 s** (`/tmp/evolution-managed-regression.log`).
The skipped real Modal case is the separately executed test above. Final focused
App/credential checks passed **17 tests, 1 live skip, in 5.54 s**
(`/tmp/evolution-managed-final.log`). A fresh Python environment installed only
the hash-locked release requirements and passed package startup plus actual
public evolution/tool execution (**2 tests, 4.50 s**,
`/tmp/evolution-managed-clean-env.log`). These scopes overlap.

Remaining: install/configure the candidate in the complete General Team deployment
with real owner-issued Agent execution grants, model-assisted tool sampling/image
authority, whole-run recovery and the complete P0–P7 product/migration/release gates.
Agent tool allocation occurs at runtime; the combined deployment must verify
ordering of the Agent execution provider and its Evolution consumer rather than
introducing a startup dependency cycle. No installed Fleet/Atrium, default product
entrypoint or remote branch has changed.

### Evolution controller selects the isolated ordinary App path


`EvolutionTeam(remote_execution=...)` can now combine `sandbox_mutation` with an
explicit owned `sandbox_factory`. The initial program is evaluated by its own
single-use tool App, without admitting an Agent inference. Each mutation gets a
separate placement and the existing independent Agent execution binding. The
normal controller still performs parent sampling, inspirations/history assembly,
archive admission, checkpoints and parallel-worker coordination. Unbound legacy
callers keep their original entry; the extraction does not silently choose a
Modal account, image, credentials or placement policy.

The generic `PreparedModalApp` / `ModalAppPlacement` composes the existing pinned
image, container owner and ordinary stdio transport. Its owner exists before
start; close joins late creation, confirms remote termination, and then releases
the transport. Failed stop retains ownership. Evolution registers the placement
operation before any asynchronous creation. Confirmed operation cleanup clears
its large source/request, execution result and transport references even while
the search retains an idempotent close callback. Failure retains those owners;
this is bounded per-iteration retention work, not an RSS benchmark.

Function evaluation never runs on the controller in this path. The tool App
carries explicit function-weight and evaluation-timeout policy and returns the
complete evaluation record, including diagnostics and state. Optional LLM review
uses the original feedback logic and bound external Agent, without writing or
executing source on the controller. Review results and artifacts reach the normal
program archive. Lost initial/final replies propagate a recovery-required error
through the run instead of being downgraded to a skipped mutation. Admitted runs
cannot resample/re-evaluate on reopen. Automatic whole-run reconciliation/replay
is still not implemented.

Local production-controller acceptance uses actual packaged tool subprocesses,
forbids embedded Agent construction, controller `HybridEvaluator.evaluate` and
ambient provider-environment access, and covers sequential and two-worker runs
with and without model review. Fault injection covers lost initial/final replies,
late Modal creation, readiness cancellation and unconfirmed stop. The combined
controller, package, transport, owner and existing remote-pipeline regression
passed **93 tests in 52.56 s** (`/tmp/evolution-controller-regression.log`). The
first fault-test attempt expected the wrong in-memory retry exception; the final
checks separately verify the direct team guard and reopened-run ownership fence.
The final ownership/memory-retention and worker/App lifetime group passed
**40 tests in 22.04 s** (`/tmp/evolution-controller-ownership-final.log`).

A real Modal + independent native Agent run then entered through
`EvolutionTeam.evolve`, evaluated the seed in one container, completed five
fixture-model turns with actual Shell/Python tools in another container, and
admitted the improved child (score **0.1 → 0.8**) into the actual archive.
Both `sb-FzT6bsvfYZo5RNk0vE14AZ` and `sb-3nkb9ziP6H4mpb8Z0UzVnJ` were confirmed
terminated (SDK exit 137, deliberate owner termination). The test passed in
**17.23 s** (`/tmp/evolution-controller-modal-live.log`); this is test duration,
not a product startup benchmark. Image `im-idjUw844UMdEIeWz5StQzO` pins artifact
`8fef1e25637ee08c680fee687662f2daf14d4ec9f7583d1150a95fd7bc896601`.
The preceding image/tool smoke also passed and stopped its container;
image metadata and receipts are in `/tmp/pantheon-modal-controller-tools-20261005-a`.
Model replies and native execution grants remain fixtures; this is not real
paid-model, production authorization or default product acceptance.

Remaining: compose these explicit execution/placement dependencies in the shipped
Evolution App/default Agent product, supply model-assisted tool sampling and image
authority, verify real grants and whole-run recovery, finish full CLI/Desktop
capability parity, migration/publication/cutover and cross-node P0–P7 gates.
No installed Fleet/Atrium, default entrypoint or remote branch has changed.

### Versioned isolated tools and a real Modal / Agent execution

The isolated tools now have an ordinary `evolution-tools` App package, with a
declared `isolated-mutation@1` interface and the existing portable Fleet execution
manifest. Its explicit source allowlist includes Files, Shell, Python kernels,
the evaluator and their shared runtime utilities; it does not ship an Agent,
ChatRoom, controller execution dispatcher or provider SDK. Canonical tool-schema
conversion moved into funcdesc so the provider no longer imports the Evolution
controller. Python dependencies are pinned with hashes. Source/evaluator inputs
arrive through a single-use `initialize` RPC, separately from the release.
Initialization failure cannot reset a partially admitted workspace. Shutdown
joins initialization before closing its owned tools and evaluators.

The generic Modal image builder reuses the existing deterministic Fleet artifact
and requires its reviewed digest plus an immutable base image ID. It takes the
requirements and source from the same sealed tar, installs hash-verified wheels
during image preparation, checks the tar digest in the build container and pins
the resulting image ID. No controller environment or mutable configuration is
uploaded; container startup performs no dependency installation. Build
cancellation joins the SDK operation before deleting its staged inputs. The
artifact remains an ordinary package usable with the existing Fleet adapter;
Modal preparation does not create another App version system.

`SandboxAgentExecution.run(configuration=...)` now durably admits the exact
initialization inputs before calling the tool App, and records its confirmed
response before evaluation or reasoning. Inputs are snapshotted rather than
borrowed mutable dictionaries. Lost initialization replies remain fenced across
reopen and cannot replay materialization or proceed to inference. This still
requires the deployment owner to record container creation first.

Live validation used the exact package digest
`4a537a467d0e9912b13465327326518c3fd8b5d58b2d3ed0c5b04c732e27b74a` and
image `im-ztIGxyHPbv50kRd1ZScspA` on a pinned Python 3.12 base. A real Modal
CPU container read source through Files, ran NumPy in the actual Python kernel,
edited source through Shell, evaluated it and finalized a score improvement
from **0.1 to 0.9**. Creation-to-readiness was **2.700 s** in this sample;
App drain exited zero and container `sb-7xyCpH8vzH168dljQrWA28` was confirmed
stopped. Receipts and outputs: `/tmp/pantheon-modal-tools-20261005-b` and
`/tmp/modal-mutation-tools-live-final.log`. The preceding attempt stopped cleanly
after a test assertion incorrectly expected a Shell `returncode` field; the
corrected check uses its actual success/status contract and validates final files.

A second real Modal container then completed a five-turn mutation coordinated
by the independent native Agent App: initial score **0.1**, real Shell/Python
calls, probe evaluation, explicit submission and final score **0.8**. The owner
confirmed container `sb-1EGYjkzvHvFFA4sXM46cn7` terminated before releasing the
Agent result. The controller was forbidden from constructing an embedded Agent.
The opt-in test passed in **10.19 s** (`/tmp/evolution-modal-agent-live.log`), with
durable acceptance evidence under the printed pytest receipt directory. The
native Agent engine and Modal processes are real; model responses and the local
Agent grant are fixtures. This is not paid-model or production grant acceptance.
Reproduce with `PANTHEON_TEST_MODAL_IMAGE=<prepared-image.json>` and
`tests/test_evolution_modal_live.py`; it is skipped unless explicitly enabled.

Focused package/image/tool integration checks passed **31 tests in 19.84 s**
(`/tmp/evolution-tools-image-combined.log`); initialization ownership checks
passed **11 tests in 10.51 s** (`/tmp/evolution-tools-initialization.log`). The final combined
package, image, sandbox, remote execution, transport and host suite passed **86
tests in 39.68 s** (`/tmp/evolution-modal-package-combined.log`). The general Evolution
controller still selects its legacy sandbox launcher, and its initial evaluation
must be routed through the new isolated binding. Model-assisted tool sampling
and image authority, complete deployment/grant composition, whole-run recovery,
full default product parity, migration/publication/cutover and P0–P7 acceptance
remain open. No installed Fleet/Atrium or default product entrypoint changed.

### Modal container ownership and ordinary App transport

The generic Modal owner now records an immutable image ID, explicit command,
environment digest and random container name/nonce before creating the sandbox.
It does not forward ambient provider credentials. Interrupted creation joins the
actual SDK request; a lost create response can be reconciled by the persisted
name and ownership tags without creating another container. Missing evidence is
not interpreted as successful cleanup. Stop requires a terminal SDK poll and a
durable receipt; an unconfirmed stop retains the local owner lock. Recovery can
stop the same saved container, not resume or replay its stdin.

The ordinary App stdio transport now runs over Modal's chunked streams. It
correlates concurrent requests and AppContext callbacks, serializes whole output
frames across bounded stdin writes, limits pending calls and diagnostic memory,
and rejects new admission after transport failure. Callback authority is supplied
explicitly, never inferred from controller file/model access. Observer loss does
not cancel or resend accepted effects. Shutdown requests ordinary cleanup;
container termination remains independently owned and confirmed. Local disconnect
joins outstanding writes and callbacks after their remote/backend owners stop.
The existing stdio host now permits bounded 16 MiB messages instead of the
implicit 64 KiB asyncio limit, which was too small for ordinary source payloads.

A real CPU-only Modal smoke test passed using a pinned stdlib App image and the
production ordinary host: image preparation **10.099 s**, creation-to-readiness
**2.168 s**, and a **0.858 s** request carrying 240,000 source characters, writing
a remote file, running a Python child and returning a scoped callback. The App
then drained, exited **0**, and the SDK independently confirmed terminal state.
Sandbox `sb-t6DRZ0HyzfkwXZLwo9JSIZ` is recorded stopped in the private controller
journal `/tmp/pantheon-modal-app-smoke-20261005-a`; output is in
`/tmp/modal-app-live-smoke.log`. Reproduce explicitly with
`scripts/check_modal_app_transport.py --receipt-dir <new-private-directory>`.
This is one network/CPU sample, not a production latency benchmark or a real
model/Evolution acceptance result. No GPU or model provider credentials were used.

The actual independent-process Evolution tools/Agent integration now uses this
production transport rather than its former test-only serial JSON reader.
The combined owner, transport, ordinary host and Evolution sandbox suites passed
**50 tests in 19.79 s** (`/tmp/modal-app-evolution-combined.log`), including real
Shell/Python/evaluator processes and independent Agent reasoning with fixture
model responses. Final focused checks passed **26 tests in 2.09 s**
(`/tmp/modal-app-transport-final.log`), including refusal of new callbacks after
diagnostic-stream failure. Versioned full-tool artifact/image assembly, deployment/grant
composition, selection in the Evolution controller, isolated initial evaluation,
and an end-to-end real Modal mutation still remain. The legacy sandbox launcher,
installed Fleet/Atrium and default product entrypoints are unchanged. The full
P0–P7 goal remains active.

### External Agent execution and isolated-tool result ownership

`sandbox/agent_execution.py` binds an explicitly owned tool backend to the
existing Agent execution SDK/dispatcher. The deployment owner supplies the exact
backend identity, ordinary invocation callback and confirmed termination callback;
this binding does not create containers, discover inference providers or borrow
ambient credentials. The launcher still must journal container creation before
constructing it. All code/tool/evaluator execution remains behind the backend
invocation; the controller only coordinates and persists returned data.

A private controller journal admits one immutable request before initial
evaluation or reasoning. It records the tool contract, initial evaluation,
inference outcome and final mutation response. Tools use the existing durable
claim/effect/reply dispatcher. Finalization is followed by confirmed termination
of the pinned backend, Agent result release and a completed record. Reopening a
completed identity reads the saved response without invoking either backend;
incomplete or uncertain identities require reconciliation. A lost tool reply
cannot trigger salvage, and a lost final response cannot repeat evaluation.

Observer cancellation leaves the owned execution running. Explicit close signals
inference stop even when container termination fails, joins outstanding tool and
finalization requests and only then releases local ownership. Failed termination
retains the control lock; a truthy value or acknowledgement for another backend
does not count as confirmation. Completed reasoning is not cancelled again while
its final evaluation transport drains. These are local receipt/ownership rules,
not distributed fencing or automatic crash recovery.

Validation: **146 passed, no skips, in 45.32s** in the combined sandbox tools,
Agent execution SDK/dispatcher/service/process, stdio host, remote Evolution
pipelines/feedback and Agent/Evolution lifetime suites
(`/tmp/evolution-sandbox-execution-combined.log`). Final targeted checks passed
**10 cases in 11.61s**, including the additional finalization-drain and strict
stop-receipt cases (`/tmp/evolution-sandbox-execution-final.log`). The independent
process test runs a native Agent App plus a separate ordinary tool App, five
model turns, actual Shell/Python edits, initial/probe/final evaluation and submitted
result capture. It forbids embedded Agent construction in the controller and
Agent/model SDK imports in the tool process. Model responses and grant delivery
are fixtures; process exit is the test backend's stop evidence, not Modal
termination. Further tests cover observer loss, lost tool/final replies,
completed-record reopen, explicit cancellation and failed-stop ownership.

The legacy sandbox launcher is still unchanged. Versioned artifact delivery,
Modal transport and creation receipts, deployment/grant composition, Evolution
controller selection and isolated initial evaluation, real remote termination,
sampling/image authority and full P0–P7 acceptance remain open. No installed
Fleet/Atrium, default entrypoint or remote branch changed.

### Container-side ordinary mutation tools without an embedded Agent

`sandbox/tool_backend.py` now composes the mutation workspace as an ordinary
AppContext backend, with explicit owned ToolSet instances supplied by its
deployment factory. It exposes tool descriptions/invocation, initial evaluation
and finalization. Files/Python/Shell use the existing ordinary ToolSet adapter;
framework-only caller context is rejected and an absent sampling binding cannot
fall back to an ambient embedded Agent. No Agent or provider credentials are
constructed by this backend. Its deployment owner must actually place it inside
an isolation boundary: running this module locally does not create a sandbox.

The service keeps the evaluator inside the tool process boundary, including
immutable-parent evaluation, probes, salvage comparison and submitted-child
evaluation. It preserves parent-file capture, inspirations, weighted fitness and
submitted summaries, and caches a completed finalization response without
repeating evaluation. New tool admission stops at finalization. Fresh-workspace
and input-path checks prevent accidental source reset/traversal; a single-use
marker refuses ordinary reopen. These container-local records do not replace
the external controller's trusted durable execution receipts or crash recovery.

Shutdown joins accepted evaluation/tool calls and reaps owned subprocesses and
Python kernels. Partial provider setup closes all returned providers, including
those not yet initialized. Evaluator process-cleanup failures now propagate
instead of being converted into ordinary failed metrics. The real Python
provider also exposed stdout log contamination in the stdio host: its executable
entry now reserves stdout for RPC and directs backend prints/logs to stderr.

Validation: **88 passed, no skips, in 37.69s** across sandbox tools, stdio/App
supervision, portable packaging/HTTP, remote Evolution pipelines/feedback,
Evolution worker/App lifetime and ToolSet composition
(`/tmp/evolution-sandbox-tools-final-combined.log`). This includes actual Files,
Python kernels, Shell and evaluator processes; explicit submission and salvage;
initial/finish evaluator cancellation; source validation; partial setup and
resource cleanup. A fresh ordinary App process blocks Agent, ChatRoom and model
SDK imports while running all three tool providers, initial evaluation, a Python
calculation, code mutation and final salvage. Initial salvage fixtures used
unnormalized scores which clamp to the same fitness; the corrected fixtures use
the evaluator's required 0–1 metric range. These are local process tests, not
proof of Modal isolation, external Agent reasoning or remote grant delivery.

The legacy sandbox launcher remains unchanged. Next work is its versioned tool
artifact and transport, external Agent execution/receipt composition, isolated
initial evaluation in the Evolution controller, durable container ownership and
confirmed termination. Sampling/image authority and full capability parity also
remain open. No default App manifest, installed Fleet/Atrium or remote branch
changed; the full P0–P7 plan is still active.

### Ordinary stdio App lifetime prerequisite for sandbox composition

Inspection of the existing sandbox worker confirms it still constructs an
embedded Agent and receives provider credentials. Its replacement will need an
ordinary isolated tool App and an external Agent execution binding. The existing
ordinary stdio App host was not safe to reuse unchanged: shutdown/EOF returned
immediately, detached admitted calls and never invoked AppContext cleanup.

The stdio host now closes admission on shutdown, EOF or supported process
signals, invokes an optional provider begin_shutdown hook, joins accepted calls
and then awaits cleanup. ToolSet registration exposes its existing idempotent
shutdown through that hook. Callback responses remain readable while draining;
disconnect rejects pending callbacks rather than waiting their full deadline.
Repeated stop signals do not cancel drain. Setup failures clean partially owned
resources, and failed cleanup returns a nonzero process exit. Sync methods run
off the reader loop, retaining serialization unless explicitly concurrent; state
writes and protocol output are synchronized across threads.

Validation: **43 passed, no skips, in 7.46s** across stdio lifetime, legacy App
supervisor, ToolSet backend, portable packaging/HTTP, Agent lifecycle and Evolution
lifetime (`/tmp/agent-stdio-lifetime-combined.log`). New real-process cases cover
shutdown and repeated SIGTERM, a callback completed during drain, rejection of a
late mutation, synchronous file effects surviving shutdown/EOF, disconnected
callbacks, setup/cleanup failure and an ordinary ToolSet reaping an actual child
process before host exit. This is local lifecycle evidence, not Modal isolation
or a completed sandbox mutation. The legacy supervisor's forced-stop deadline
also remains separate from a future sandbox owner's termination confirmation.

Sandbox artifact/transport composition, external Agent/model authority, isolated
initial evaluation, durable container identity, cancel-during-create recovery and
confirmed remote termination remain to implement and validate. No installed
Fleet/Atrium, default entrypoint or remote branch changed.

### Evolution analysis/mutation/summary pipeline through Agent App

The explicit remote binding now supports both existing non-sandbox Evolution
paths: single-agent coding mutations and the analyzer/mutator/summarizer pipeline.
The latter preserves generation-dependent exploration/exploitation prompts,
configured analyzer/mutator models, the low-tier summarizer, full-context mutation
without an analyzer, SEARCH/REPLACE application, function/LLM evaluation,
direction classification, saved prompts, cost extraction and archive metadata.
Custom injected agents/evaluators remain borrowed; default remote components do
not construct an embedded Agent.

Analyzer reasoning retains `think` and optional Python experiments. An explicit
analyzer tool factory supplies its owned tool instances; it does not inherit the
mutation worker's Files/Shell authority. The caller exposes the tool schemas to
the same execution dispatcher, handles effects and keeps the receipt lock until
accepted calls and kernels finish. Python sessions reset after analysis. Helpers
are reused per worker/role with fresh execution memory; exploration and
exploitation retain their distinct system prompts. Missing Python composition
is rejected rather than disabling the configured capability. Unknown analysis
or summary outcomes propagate recovery errors instead of skipping an iteration
or accepting a fallback direction.

Remote searches now acquire a durable run identity before initial evaluation,
parent selection or helper execution. Another local process cannot acquire that
identity concurrently. Reopening an admitted run cannot silently restart its
initial evaluator, even when all individual helper receipts settled before the
crash. The run lock is released only after all owned resources close; failed
kernel/tool teardown keeps the owner fenced. A completed/stopped identity remains
recorded. This is **not automatic resume**: checkpoint/archive reconciliation
and explicit recovery of existing identities still need implementation. Local
file locks are not distributed replica fencing.

Validation: **130 passed, no skips, in 41.55s** across both remote pipelines,
feedback, Evolution worker/App lifetime, Agent execution runner/client/service/
native process and Agent lifecycle (`/tmp/evolution-pipeline-combined-fixed.log`).
The independent Agent App process now runs actual Python analysis, mutation,
feedback and summary with the original engine, real evaluation and archive
updates. Other cases verify two parallel workers, configured/no-analyzer modes,
Python cancellation, retained locks after failed teardown, a second Python
process denied the active run, and no repeated initial evaluation. Model
responses and grant delivery remain fixtures. A separate fresh-process import
check blocked Agent, ChatRoom, OpenAI, Anthropic and LiteLLM imports while loading
the Evolution remote consumer modules successfully.

Sandbox composition, tool-context/image/sampling authority, whole-run recovery,
receipt retention policy, native Fleet grant delivery, production manifest/launch
composition and the open P0–P7 gates remain. No installed Fleet/Atrium, default
entrypoint or remote branch changed.

### Evolution feedback through the ordinary Agent App


The opt-in Evolution composition now creates its default LLM reviewer through
its existing Agent execution dependency. It retains the original reviewer system
prompt, `normal` model selection, full/limited code rendering, parent/current
metrics, JSON parsing, score weighting, issue/suggestion artifacts and timeout
fallback. The existing HybridEvaluator remains responsible for function
execution and combined evaluation. Parallel mutation workers borrow the shared,
concurrency-limited evaluator; only the owner shuts down its reviewer.

Each helper invocation persists its identity and request before submitting. A
successful response or confirmed terminal failure is saved before releasing the
generic Agent receipts. This clears large result bodies/task references from the
execution dispatcher while keeping the helper's own durable call record. An
unsettled helper receipt prevents a fresh evaluator subprocess or replacement
model call after reopen. These records do not implement automatic Evolution
checkpoint/archive replay. Their retention/garbage collection belongs to the
remaining whole-run archive lifecycle.

Known model failures and confirmed timeouts preserve the existing neutral-score
fallback. Transport/persistence ambiguity or failed shutdown propagates an
EvolutionCleanupError through feedback, evaluation and final-edit salvage;
it cannot be converted into a successful default 50-point evaluation. Cancelling
or stopping a helper joins its remote inference and any accepted result save or
release. Repeated cancellation cannot detach a filesystem write. Borrowed
external evaluators remain borrowed; owned evaluators reset on owner shutdown.

Validation: **121 passed, no skips, in 37.44s** across remote helper/mutation,
Evolution lifetime/worker resources, Agent execution runner/client/service/native
process and Agent lifecycle (`/tmp/evolution-feedback-combined.log`). An actual
independent Agent App process performs initial, probe and final review alongside
real Shell/Python mutation and evaluation, with local Agent construction forbidden
in Evolution. The tests verify scores, feedback and artifacts for two parallel
workers, durable helper receipts, rejected re-execution after a save failure,
confirmed failure/timeout fallback and cancellation during inference and an actual
blocked receipt-write thread. Upstream model responses and grant delivery remain
fixtures.

The production manifest/launcher remains unchanged. Analyzer/summarizer and
sandbox composition, full tool-context/image/sampling authority, interrupted
Evolution recovery, distributed fencing and all other open P0–P7 gates remain.
No installed Fleet/Atrium, default entrypoint or remote branch changed.

### Evolution single-agent mutations through the ordinary Agent App


Evolution now has an explicit `RemoteEvolutionBinding` for its existing
single-agent mutation path. It supplies a borrowed execution client, stable run
and logical binding IDs, a private receipt mount outside the workspaces and an
owned tool factory. The original sampling, prompt, evaluator, submit, best-result
salvage, warm-start and archive code remains shared with the local path. The
remote path does not create a local Agent. It sends tool schemas to the ordinary
Agent execution service while Evolution retains its Files, Python, Shell and
business callbacks. Parallel workers keep separate workspaces, kernels, action
and evaluation budgets, submissions and execution identities.

A mutation record is inserted before resetting its workspace. Budget/submission/
best-result state is saved around tool execution, and a missing journal row blocks
the effect rather than silently treating an UPDATE as successful. Existing
mutation identities require reconciliation before another parent can replace the
workspace. This is deliberate failure containment, **not automatic Evolution
checkpoint/archive recovery**. Finalization saves the iteration result and
releases the generic execution receipts after the original archive step; atomic
archive replay and recovery of interrupted iterations remain required.

The ordinary execution specification supports bounded declarative per-turn
reminders, preserving Evolution's existing wind-down prompts. `max_turns: null`
retains the legacy no-count-limit mode with a finite execution deadline. Explicit
limits count Agent history messages, not tool rounds. No executable hooks or
consumer code move into the Agent App.

Cancellation joins the caller's accepted tools, evaluator subprocesses and
Python kernels. The mutation keeps its exclusive journal lock through tool
shutdown; failed kernel/provider teardown prevents another writer taking over.
Partial setup registers all returned resources before initializing any of them.
A failed generic caller restore scan releases its unused journal lock.

Validation: **114 passed, no skips, in 33.18s** across Evolution remote execution,
worker resources and App lifetime, Agent execution runner/client/service/native
process and Agent App lifecycle (`/tmp/evolution-ordinary-agent-combined.log`).
The actual Evolution integration test uses an independent Agent App subprocess,
its original Agent/Team/model loop and the execution SDK, plus real file edits,
Shell, Python, evaluator and archive operations. It forbids local Agent
construction in Evolution and verifies wind-down messages in actual model
requests. Further cases cover parallel budget isolation, no workspace reset on
repeated identities, lost replies, deleted/failed mutation records, partial
setup, held writer locks on failed cleanup and cancellation of actual parent and
child processes. Upstream model replies and grant delivery are fixtures.

This binding is opt-in and not yet wired into the production Evolution App
manifest/launcher. Helper/analyzer/summarizer and sandbox composition (feedback is now covered above),
full tool context/image/sampling bindings, interrupted Evolution recovery and
native Fleet grant delivery remain. Unsupported modes reject the remote binding
instead of silently using an embedded Agent. Existing local defaults remain for
compatibility until full capability parity passes. All open P0–P7 gates remain;
no installed Fleet/Atrium, default entrypoint or remote branch changed.

### Durable caller-side Agent execution dispatcher


The Agent-free consumer now has `AgentExecutionRunner`, using the existing
execution SDK and a caller-owned private SQLite journal. It persists the
execution specification and stable tool claim identities, commits an execution
fence before entering a callback and stores the outcome before replying. A
recovered claim only permits execution when the exclusive local journal proves
the tool never crossed that fence. A crash after a side effect but before its
saved result leaves an unknown outcome and prevents replay/new work. Saved
results can be resent without executing the tool again.

Run observers may disconnect without detaching accepted work. Explicit stop
cancels inference and joins all caller tools; noncancellable disk operations
drain, while declared cancellable async callbacks must finish their teardown.
Exceptions, unexpected callback cancellation, changed/malformed claim receipts
and failed persistence require recovery, rather than acknowledging a clean stop.
Parallel tools progress independently. After result archival, explicit release
clears retained bodies/task references while retaining the execution identity.
The dispatcher borrows its client's authority and never discovers another Agent
or falls back to embedded inference. Local file locking is not distributed
replica fencing.

Validation: **92 passed, no skips, in 17.84s** in the combined caller, execution
SDK/service, native HTTP process, Evolution lifetime/worker resource and Agent
App lifecycle suites (`/tmp/agent-execution-caller-combined-final.log`). The new
native process case uses the real SDK and dispatcher for three model turns,
one file edit and a Python evaluation subprocess. Reopening caller and Agent
returns the saved result without repeating inference or tools. Fault cases
include an abruptly exited caller after a disk write, lost acknowledgements,
stop/claim races, parallel requests, blocked actual filesystem worker threads,
repeated cancellation, persistence failures and release observer loss. Model
responses and credential/grant delivery are controlled fixtures.

Evolution's default production path remains local. The opt-in single-agent
binding described above integrates mutation identities, counters and callbacks;
whole-run recovery, helpers, sandbox mode and production composition remain. Unknown-effect reconciliation UI, native Fleet execution grant
delivery, cross-node failure recovery and all other open P0–P7 gates remain.
No live Fleet/Atrium installation, default entrypoint or remote branch changed.

### Ordinary Agent execution dependency

The independent Agent package now declares `agent-execution@1`, with ordinary
submit/poll/claim/reply/cancel/result/release RPCs and an Agent-free consumer SDK.
The backend uses the existing Agent/Team/compression engine, fresh Memory and
the same explicit Model Services scope as chat. It does not construct consumer
Files/Shell or load consumer code. A caller's tool schemas describe requests;
the caller owns their actual authorized execution and resource teardown.

SQLite request/reply receipts deduplicate lost observations and conflicting
submissions. A claimed tool has one worker identity; recovery does not authorize
replay or stealing. Parallel queued requests remain observable while earlier
ones are claimed. Cancellation withdraws unclaimed requests but retains claimed
outcomes, explicitly distinguishing stopped inference from stopped caller tool
effects. Restart marks active runs interrupted without resuming inference or
tools. Results are paged and checksum verified. Release retains identity
tombstones. Journal/plugin cleanup failure prevents a clean App shutdown.

`execution_method_rules` projects the real RPC contract into existing generic
dependency grants, binding the consumer identity rather than letting callers
select another namespace. The prepared package includes the engine/service and
declares their interface. No new Model Services implementation or provider SDK
is introduced in the consumer. Per-run image resolution cannot read another
chat's private image directory.

Validation: **65 passed, no skips, in 57.15s** in the combined execution service,
consumer SDK, native HTTP process, Agent App lifetime, dependency binding and
clean-release model/chat/restart gates (`/tmp/agent-execution-combined.log`). The
native process performs three actual Agent model turns with caller-side file
editing and evaluation. A built release with no source checkout calls the
original Model Service Connector, recovers results after restart without another
inference call and refuses a revoked grant. Upstream model responses and grant
issuance are fixtures. Two focused engine image-ownership/plugin-failure tests
also passed after making cleanup exceptions explicit; these overlap the combined
coverage and should not be added as a new total.

Evolution has **not yet switched** to this dependency. Its durable caller-side
tool dispatcher, budgets/wind-down, evaluator/archive callbacks, helpers and
sandbox mode must all be composed before the local Agent constructors can be
removed. Exact native Fleet grant delivery, consumer crash recovery, image
artifact retention/export and full General Team acceptance remain. See
`docs/agent-execution-service.md` for ownership, limits and the request protocol.
All open P0–P7 gates remain; nothing was pushed or deployed to live Fleet/Atrium.

### Evolution worker isolation and internal tool teardown

The next default-team audit found that parallel Evolution workers shared the
same mutation directory, Agent/Python tools, submission slot and action/evaluation
budgets. They now each own a separate worker composition and working directory,
while borrowing the common archive and concurrency-limited evaluator. Custom
injected agents/evaluators remain caller-owned. Worker shutdown failures reach
the collector promptly and prevent another run on an owner requiring recovery.

Local Files/Python/Shell invocations are now retained independently of their
observers. A cancelled Files observer cannot detach an accepted disk operation;
worker settlement waits for its actual completion. Shell commands use the same
shielded spawn and POSIX process-group reaping as evaluators. Python toolset
cleanup delegates to its owned kernel service, and Evolution uses strict kernel
shutdown. Mutation iterations settle accepted background and foreground tool
work before reading results or reusing the working copy. Owned kernels reset
between mutation iterations; each new Agent iteration still has fresh Memory.
The legacy analyzer path also settles tool calls and kernels on cancellation.
Partial tool attachment and shutdown attempt all resources, retaining cleanup
failures instead of acknowledging a clean App stop.

Agent foreground-tool cancellation now joins its child tool's asynchronous
teardown, including repeated cancellation. Adopted background tools retain their
existing separate ownership. Evolution's optional web search uses owned threaded
I/O so cancellation waits for that operation rather than blocking the event loop
or abandoning a thread. This does not add another Agent execution implementation
or a provider SDK, and does not change the selected model routing.

Validation: **101 passed, no skips, in 22.67s** in the combined Evolution lifetime/resource,
Agent App lifecycle, background-task and LocalProvider suites
(`/tmp/agent-evolution-final-owner.log`). Two parallel coding workers use actual
Agent tool dispatch/hooks, real Python kernels, Shell and Python evaluators; the
test checks separate budgets, files, memories and submissions in a shared archive.
Other cases exercise actual AppContext stop, kernel/parent/child process exit,
initialization/directory failure, legacy analyzer cancellation, accepted background edits,
a blocked synchronous filesystem write, repeated stop and propagated cleanup
failure. Mutation decisions and feedback are controlled fixtures, not paid model
calls or full General Team acceptance.

Evolution still constructs local Agent workers and borrowed/local tool bindings;
this is preparation for their explicit ordinary-App service composition, not a
completed standalone Evolution release. Remote Agent execution, sandbox model
binding, desktop-tool composition, full default-team/CLI/Desktop acceptance and
all open P0–P7 gates remain. Nothing was deployed to live Fleet/Atrium or pushed.

### Evolution execution ownership and stop prerequisite


Audit of the default General Team's remaining dependencies found that Evolution
uses a process-global session manager, synchronous timeout cancels and restarts
the entire run, and collector cancellation can leave parallel workers or Python
evaluators running. An ordinary App cannot safely report stopped in that state.

EvolutionToolSet now accepts an explicit directory-owned EvolutionManager while
preserving legacy constructor selection. Owned managers reject another root,
symlink session directories and malformed records; a previously active record
is marked failed on fresh process restore without replaying its work. Loading
records into a live manager does not replace running task handles. Session saves
use private staged files, flush/fsync and atomic replacement, preserving the old
record on serialization or replacement failure.

Both synchronous and asynchronous entrypoints create one tracked execution.
Synchronous timeout continues that task, and caller cancellation does not launch
another run or cancel the accepted mutation. Explicit cancellation joins owned
work and persists its terminal status before acknowledging. Ordinary ToolSet-host
shutdown stops admission, cancels all of the instance's runs concurrently and
joins them; persistence errors fail shutdown without preventing other runs from
being cancelled. It does not stop another instance's runs. The parallel evolution
collector now joins workers on cancellation as well as normal exit. Evaluator
subprocess teardown covers cancellation during process creation, timeout, normal
exit and repeated cancellation. On POSIX the evaluator owns a new process group
and terminates descendants; Windows descendant handling remains unverified.

Validation: **18 passed, no skips, in 1.17s**
(`/tmp/agent-evolution-final.log`). Tests exercise ordinary AppContext stop,
two-instance isolation, synchronous timeouts, disconnected callers, immediate
cancellation, persistence failure, interrupted restore and cancellation during
real process creation. Actual evaluator parent/child processes are checked after
timeout/cancel on macOS. Sequential and parallel EvolutionTeam runs execute real
Python evaluators, apply controlled mutation text, populate the archive and save
checkpoints. Delayed execution/worker fixtures expose cancellation ordering;
mutation responses are fixtures, not external model inference.

This is not a prepared Evolution release or complete General Team acceptance.
EvolutionTeam still constructs internal Agent workers, analyzer/summarizer and
optional sandbox/tool dependencies. Those require explicit composition; their
full cleanup and cross-node acceptance remain. Desktop tool adaptation, default
product composition and the outstanding P0–P7 gates above also remain. No live
deployment, installed application, default entry or remote branch was changed.

### Ordinary Files image generation through original Model Services

The prepared Files builder now has an explicit `--image-generation` variant
(v0.6.12) exposing the original `generate_image` tool and `image-generation@1`.
It declares the existing `model-inference@1` dependency; configuration supplies
the Files-owned credential, default Fleet model/route, allowed selector aliases
and timeout. Optional image observation remains independently configurable.
The package includes the canonical Model Services client and no Agent, global
settings discovery or provider SDK. Shared Files model authority belongs to the
Files deployment, independently of individual Agent instances.

Reference images are verified and frozen from the Files workspace before model
resolution. The output directory is checked before submitting paid inference
and again before publication. One resolved service handles uploads, submission,
polling, cancellation and checksum-verified download. Results are atomically
saved under `generated-images` with the ordinary bounded preview; successful
remote jobs and temporary inputs are cleaned up. Shutdown drains accepted calls
and closes the owned client. A private journal records job/upload identities
before mutations, and retains ambiguous failures or incomplete cleanup across
Files restart without prompts or credentials. Restart never replays generation.
Recovery currently uses the retained identity and existing Model Services APIs;
there is no automatic Files reconciliation UI in this increment.

Prepared Connector v0.1.25 adds OpenAI-compatible Images generation/editing to the
existing typed job/media infrastructure for `api` engines. Reference artifacts
must belong to the pinned deployment; multipart uploads retain their order.
Output supports PNG/JPEG/WebP with bounded base64, checksums and container checks.
Returned URLs are never fetched. Local and Hub publication now accept attached
Images APIs without enabling unsupported engines or weakening local-only route
policy. Provider compute/billing must be explicitly allowed on such routes.
Hub commit `a584869` contains publication validation and UI commit `ec3a8d47`
removes the SGLang-only label from image operation selectors.

Validation: **224 passed, 1 skipped, in 59.54s**
(`/tmp/agent-image-services-final.log`). The skipped prepared Connector/node-vault
case was then run with the real isolated Fleet binary and **passed in 1.16s**
(`/tmp/agent-image-node-vault.log`). Tests exercise real Connector HTTP/media
transport and scoped dependency authority, PNG/JPEG/WebP outputs, ordered edits,
revocation, invalid/foreign inputs, cancellation, ambiguous submission, retained
recovery identities, cleanup failure, output preflight, package boundaries and
existing speech/transcription/diffusion/MCP sampling paths. A built Files package
runs in a separate process with Agent/settings/provider-SDK imports forbidden.
Hub tests passed **34** and the two affected Model Services UI suites passed
**30**; counts across focused earlier runs overlap.

The upstream Images API is controlled test data, not a paid provider. This is
not complete General Team acceptance: native Gemini image calls, legacy image
selector/config migration, vision-description fallback, real-provider behavior
and full product dependency composition remain. No installed Fleet, live Atrium,
default entrypoint or remote branch was changed. All outstanding P0–P7 gates
above still apply.

### Rebuilt Notebook GUI and widget acceptance

The isolated UI baseline lacked the widget renderer, widget dependencies, lazy
editor loading and active-kernel adoption already present in the runtime's
checked-in Notebook assets. Rebuilding that baseline would silently remove these
capabilities. The reviewed Notebook-only changes from UI commits `11d65dbe`
through `604feea6` are now present in the extraction UI branch at `8e8e727c`. Existing Agent
owner-aware notebook navigation and newer terminal/deployment dependencies are
preserved. Installation uses an independent node_modules directory, not the
original working checkout's dependency symlink.

The UI build emits the complete lazy module/font/style graph. Its default output
is now local `dist-notebook-package/index.js`, not an implicit sibling repository.
The ordinary Notebook packager accepts `--frontend` to pair this newly built
viewer with its runtime-owned window action adapter; callers without an explicit
frontend retain the existing checked-in artifact. No generated viewer bytes were
replaced in the live product or the runtime's legacy frontend directory.

Rendered acceptance uses the actual `public/app-host.html` SDK in an opaque-origin
sandbox iframe, the freshly rebuilt viewer, and a separate authenticated ordinary
Notebook backend process. An import guard forbids Agent/settings/ChatRoom/factory
imports in that backend. The test parent forwards SDK messages through a loopback
fixture; this replaces placement, not the SDK, renderer, kernel or widget comms.
No backend credential reaches either browser document. All external resource
requests are blocked and cause test failure.

The gate validates real Button/Slider/Output behavior, Canvas pixels and pointer
input, kernel-side background updates, a visible add-and-execute window action,
page reopen, two simultaneous viewers, bounded replay overflow recovery, kernel
restart diagnostics, and rerunning the original widget cell through the window
action. The actual screenshot was inspected. This gate initially passed in
39.39 seconds. The final combined run passed **48 tests, no skips, in 72.86s**
(`/tmp/agent-notebook-gui-final.log`), including ordinary native Fleet Notebook
start/stop/reopen, image-to-original-Model-Services, existing widget/backend and
product composition checks. The UI's **42 Notebook unit tests**, complete
TypeScript build and production Desktop source/output boundary checks also
passed (7 eager Desktop chunks, no Agent implementation). This is rendered ordinary-App/Notebook evidence, not complete Atrium
placement or full General Team acceptance. Source reproducibility is restored;
real model-provider media acceptance and remaining P0–P7 work are still pending.

Reproduce with explicit isolated output directories:

```sh
# In the paired UI checkout:
NOTEBOOK_APP_BUILD_DIR=/tmp/agent-extraction-notebook-build \
  node scripts/build-notebook-app.mjs /tmp/agent-extraction-notebook-frontend/index.js

# In the runtime checkout, using a Python with Notebook + Playwright dependencies:
PANTHEON_TEST_NOTEBOOK_UI=/absolute/path/to/paired/ui \
PANTHEON_TEST_NOTEBOOK_FRONTEND=/tmp/agent-extraction-notebook-frontend \
  python -m pytest -q tests/test_notebook_gui.py

python -m pantheon.apps.builtin.notebook.build_managed \
  --output /tmp/paired-notebook-app --platform darwin-arm64 \
  --frontend /tmp/agent-extraction-notebook-frontend
```

### Notebook image results through original Model Services

Notebook output no longer imports `pantheon.agent` or detects a consumer's model
inside its backend. It produces standard `content_blocks` alongside its existing
output/URI fields. Add-and-execute and update-and-execute promote those blocks to
the top-level tool result, matching direct execution. List-form notebook MIME
values are joined, and SVG XML is correctly encoded as a data URI rather than
mislabelled raw XML as base64. Notebook requires no model credential for this.

The existing Model Services client previously stripped all tool-role images at
its Chat Completions boundary, even for a published vision model. The shared
message module now preserves them in an explicitly labelled tool-output image
attachment message, after the complete contiguous tool-result group. Original
tool identities and text results remain in place. Conversion is request-local,
idempotent and does not mutate stored history, choose another model, or read
unresolved local paths. Existing publication/route vision checks still run before
submission; an unconfirmed vision capability is rejected without inference or
cloud fallback. Legacy provider-specific adapters retain their native handling.

Agent-owned image storage and the bound resolver carry the returned bytes between
calls. Tool summary truncation now also receives the Agent's owned temporary
output directory; the end-to-end gate exposed and removed a remaining ambient
settings lookup there. The default path for unscoped legacy callers is unchanged.

Validation: **182 passed, 1 deselected, in 47.74s**
(`/tmp/notebook-images-final.log`). A separate packaged Notebook HTTP process with
Agent/settings imports forbidden executes actual ipykernel code. The actual
Agent tool dispatcher captures its PNG, stores it in its own image directory,
expands it through its bound resolver, and sends it through the original Model
Services client and a real local Connector HTTP server. The upstream fixture
observes image dimensions/pixels, matching tool identity and no local file URI.
All three execution forms pass; saved history is unchanged. Focused checks cover
parallel-call ordering, repeat normalization, missing identity, unresolved paths,
unconfirmed vision refusal, valid SVG output and scoped large-output storage.
The same run includes the real native Fleet Notebook start/stop/reopen gate and
model routing/dependency, image-resource and legacy image-adapter regressions.
The deselected case requires an operator-provided real Ollama endpoint; the
upstream response in this increment is controlled test data, not model-quality
or real-provider acceptance. The earlier focused group passed 107 cases (counts
overlap). SVG was validated as Notebook output, not as provider-supported vision
input; model-specific media acceptance remains to be verified.

Rendered Notebook GUI/widget acceptance is addressed above. Full General Team provider composition,
real external inference and the outstanding P0–P7 gates remain incomplete. No
live Atrium, installed Fleet, default entrypoint or remote branch was changed.

### Prepared ordinary Notebook dependency

`apps/notebook/build_managed.py` builds `integrated-notebook` v0.7.1 as an
ordinary process App with the existing DOM frontend and every original engine
method, plus the hidden execution-host descriptor. It retains the original App
identity and declares `notebook@1`; required method parameters are derived from
the source signatures rather than the old `not_defined` defaults. The package
vendors the canonical ToolSet primitives and notebook engine, not the Agent or
settings package. Pinned Python requirements use the generic portable installer.
The legacy Notebook manifest/entry remains unchanged.

Each prepared start supplies `values.notebook.execution_timeout` and
`execution_logging`. Workspace comes from the ordinary host. Selected kernels
remain workspace-owned; contexts and optional execution logs belong to the App
data directory. GUI and Agent calls canonicalize the same notebook path and use
the same workspace session. Caller-supplied framework context is rejected by the
generic authenticated ToolSet host. Notebook execution does not require a model
credential or per-Agent Shell resource. The product compiler gate preserves the
Notebook provider configuration alongside original Model Services and Shell.

Managed lifetimes close RPC admission, wait for accepted executions and saved
outputs, then close widget channels/kernels. Persistence and kernel shutdown
failures now fail the stop hook rather than silently claiming success. Cleanup
attempts other resources even after an earlier failure. Corrupt context metadata
refuses startup and is not overwritten by setup-failure cleanup. Legacy callers
retain the default non-strict error-reporting policy.

Validation on macOS: **67 passed, no skips, in 39.41s**
(`/tmp/managed-notebook-final.log`). A packaged isolated HTTP process runs two
lifetimes with actual ipykernel, widget comm messages and Python-side callbacks,
concurrent interrupt, relative/absolute path identity, authenticated admission,
execution logging and retained notebook files. A held real cell verifies that
stop waits, refuses new execution, preserves its output and only then reports
safe-to-stop; kernel PIDs are gone afterward. A separate real Controller/NATS/
Runner gate installs the artifact, delivers generation-bound configuration,
executes with the independently installed Python, stops and prepares/reopens the
same instance. It verifies new kernel PIDs, saved documents, cached dependency
reuse and final process shutdown. The suite includes existing portable Notebook,
concurrency, widget, completion, environment-selection, rename, generic ToolSet
and local product/compiler regressions. Initial direct and native subgroups
passed 49 and 29 tests respectively; counts overlap.

This is not complete Notebook/default-team acceptance. The newly rebuilt frontend is exercised visually with the managed package in the follow-up above. Notebook image transport is addressed by the follow-up above; rendered media
and model-specific format acceptance still need validation.
The default General Team still needs full provider composition and real GUI/Agent
joint usage. Cross-node/Linux/Windows validation, complete capability parity and
all outstanding P0–P7 gates remain. No installed Fleet, live Atrium, default entry
or remote branch was changed.

### Ordinary Web dependency and browser resource preparation

`apps/web/build_managed.py` now builds the existing Web toolset as an independent
headless App (`web` v0.6.6), exposing the reviewed `web-search@1` and `web-crawl@1`
interfaces. Both original operations remain: DDGS search with its argument/result
contract and Crawl4AI browser-rendered Markdown, including ordered multiple URLs
and the legacy single-string input. The package carries the canonical ToolSet
primitives, not the Agent, settings discovery or legacy global RPC bus. Ordinary
App RPC authentication/admission/drain owns accepted calls. Synchronous search
runs outside the shared event loop and cancellation joins its worker; crawling
closes its browser context and App shutdown closes the App-owned database pool.
Crawl4AI's import-time database directory and per-crawler caches both belong to
the App data directory, with TLS verification enabled.

The standard portable installer accepts an optional versioned
`runtime-resources.json` declaration for Playwright engines. This participates
in environment identity and locking; browser preparation and headless launch
validation precede publishing a completed environment. Code-only revisions reuse
it. Workspace snapshots include browser resources, and launch binds the same
browser path rather than inheriting another process's cache. Missing system
libraries fail with a dependency log instead of invoking elevated package setup.
Existing Apps without this declaration retain their original cache identity.
The streamed Browser adapter is unchanged by this increment.

Final focused validation: **59 passed, no skips, in 42.59s**
(`/tmp/agent-web-final.log`). Actual Web Python/browser dependencies were first
installed in an isolated cache with the package hook; the gate reuses that cache
and exercises the packaged HTTP host against locally served pages
that require JavaScript execution. It verifies ordered Markdown, single-URL
compatibility, authentication, health during a held crawl, stop admission/drain,
and local cache ownership. A separate actual Controller/NATS/Runner gate stages
the unchanged package, calls its RPC via Fleet, then stops and reopens the same
instance with current generations. Both native lifetimes render the page and
reuse dependencies; profile processes are confirmed stopped. The final suite
also covers resource validation, concurrent cache reuse, resource-failure
publication, durable snapshots, standard portable Apps, and generic Agent product
composition with shared Web alongside Shell and original Model Services.

Search-provider results are stubbed in the contract/cancellation test; live DDGS
engine availability was not established. These tests do not prove a full General
Team conversation or cross-machine/Linux/Windows operation. Web can now be staged
as an ordinary shared provider without model privileges or per-Agent sessions;
the default product still needs complete Files/Notebook/Evolution/Desktop
composition and the remaining parity, migration, publication and P0–P7 gates.
No installed Fleet, live Atrium, default entrypoint or remote branch was changed.

### Native Desktop candidate and default-team admission

The Tauri candidate now owns the same ordinary local product composition as the
CLI. It negotiates a versioned readiness/control pipe, pins the Agent deployment
identity, and embeds the paired production GUI through a capability-scoped
loopback view. The view never receives Fleet credentials or native IPC. The
native shell does not launch the legacy embedded runtime or installed Fleet.
Window close/quit and parent-pipe EOF request normal profile drain. Asset snapshot
cancellation joins the worker even after repeated cancellation; malformed native
readiness drains the child stdout without logging capabilities or blocking exit.
The original CLI/Desktop defaults remain unchanged.

Actual macOS testing exposed two gaps not covered by earlier prepared-chat tests.
The model availability banner ignored authorized Fleet models; it now counts only
ready, usable Fleet catalog entries. The shipped General Team includes members
with an unspecified model, which the new execution-recipe validator rejected.
Empty/None scalar models now preserve the original scoped default selection and
run-inheritance semantics, without rewriting them to an explicit quality tier.
The complete shipped team and its declared tools remain intact. Missing App
bindings are reported by name before allocation.

Validation: 31 local Desktop view, native-profile/paired-GUI and profile-lifecycle
tests passed in 114.37s (`/tmp/agent-local-desktop-fixed.log`). The integration gate
uses two complete native Fleet lifetimes, real Shell execution through original
Model Services, an unspecified model recipe, history restoration, no prompt
replay on reload, and stdin-close shutdown. Upstream model responses are fixtures;
its explicitly prepared team remains shell-only. Factory/launch/provisioned
instance tests passed 57 in 21.69s; the added missing-default-bindings and snapshot
cancellation group passed 20 in 4.12s (overlapping scopes). UI model-status tests
passed 6, TypeScript checking passed, and Rust host tests passed 7. Both the paired
GUI and macOS `.app` build succeeded. A manual close of the actual native window
showed the saving/stopping state and exited zero with a durable stopped profile.
That first native interaction did not complete inference. After the fixes, a
second actual macOS candidate session completed a model reply and real
`printf NATIVE_DESKTOP_TOOL_OK` Shell call in the native webview. Closing its
window exited zero with profile phase `stopped`; the isolated model fixture
observed the tool result (4 model requests, including auxiliary GUI requests).
The prepared shell-only test team was applied through the App RPC after the
unchanged General Team correctly failed missing-dependency admission. This is
native transport/tool/close evidence, not acceptance of the default product.
Both native test owners are confirmed exited.

Remaining: the minimal test product lacks the General Team's Files, Notebook,
Web, Evolution and Desktop bindings. A complete product composition must supply
them; substituting the shell-only fixture is not capability parity. Fresh default
conversation acceptance, persistent first-send failure feedback, full native
capability parity, first-run setup, packaged
Python, multi-project/App navigation, installed-product deployment/upgrades and
all other P0–P7 gates remain incomplete. This candidate is local and opt-in;
no installed/default environment or remote branch has been changed.

### Bundled local Agent composition through the existing CLI entry

The local product builder now packages an existing ordinary release-set variant
with the supplied Controller, NATS and Runner executables. Binary SHA-256 and
host platform are pinned; selected App artifacts retain their existing IDs,
versions and digests. Mutable `.env`, cache and VCS content is excluded, the
destination is published only after verification, and existing products are not
overwritten. Multi-platform App sources carry their explicit artifact platform
through local staging. This is an immutable distribution envelope, not another
App version or lifecycle system.

`pantheon cli --bundle ... --setup ... --profile ... --workspace ...` now selects
the independent Agent frontend explicitly; the original CLI default is retained.
The private setup preserves Agent settings/projects, declared tool and MCP
policies, model routes, attached Model Services and additional bindings. The
canonical Agent deployment composer generates its broker dependencies, then the
existing LocalAppProfile supplies real node/owner identities, TLS, credential
references and restart generations. Users of this entry no longer construct a
deployment manifest or pass separate executable paths. Missing configured Apps,
tool-policy mismatches or foreign local trust are rejected instead of disabling
features. Bundle loading does not import the Agent implementation or start the
legacy setup wizard. First-time user configuration capture/UI is still pending;
this compiler consumes an explicit prepared setup, not ambient preferences.

The complete native gate now uses the product compiler, rather than constructing
the Agent recipe inside the test. It verifies four Fleet lifetimes, preserved
conversation/logical Agent identity, the original Connector inference route,
real Shell results, and the actual CLI bundle entry with two consecutive turns
and clean shutdown. The upstream inference response remains a fixture. Native
Desktop still needs to use this composition via a versioned readiness handshake,
package the Python launcher/runtime, and pass installed-app acceptance. New
external credentials, representative migration, product publication/upgrades,
full CLI parity and all other P0–P7 gates remain outstanding. No installed Fleet,
live Atrium, default startup path or remote branch has changed.

Final macOS validation: 57 product/compiler, native profile, profile lifecycle
and release-set tests passed in 135.33s (`/tmp/local-agent-product-cli-final.log`),
with all native prerequisites supplied and no skips. This includes the actual
`pantheon cli --bundle` subprocess, package integrity/platform/path rejection,
no Agent import at product dispatch, retained settings/dependencies, excluded
private/cache files and canonical multi-platform provider artifacts. The earlier
37-test local-entry group passed in 119.91s; scopes overlap. Durations include
builds and multiple native process lifetimes, not product startup benchmarks.

### Streaming and interactive terminal through the installed Agent App

The explicit local profile entry now accepts `--agent ALIAS` without `-i` for
interactive use, or `--agent ALIAS -i PROMPT --stream` for JSON event/reset/result
lines. Both use the same exact-generation App RPC binding as the single-turn
frontend. Basic conversation, history, model and active-Agent commands remain
frontend calls; there is no legacy REPL/runtime import or second backend writer.
TTY line editing, UTF-8 pipes and bounded redirected-file input are supported.
The profile owns final shutdown; Ctrl-C/SIGTERM drains it rather than yet matching
every legacy REPL interrupt shortcut.

The backend advertises event cursor protocol 1 and provides a lightweight
read-only cursor anchor before submission. Ordinary turns no longer need a full
history copy just to start observation. The frontend validates epoch, sequence,
conversation identity, fragment order and bounded payloads before publishing
complete events. Retention gaps reload a checked history snapshot and its active
prefixes; completed-message deltas are suppressed. Recovery never resubmits the
prompt. Broken event transport/renderer failure requests stop for a pending call;
an uncertain stop response is not treated as a confirmed drain. The App/profile
owner remains responsible for accepted work and saved data.

Validation on macOS: the final native profile group passed 24 tests in 117.21s
(`/tmp/agent-terminal-native-final.log`). Its complete Agent gate builds and
installs the paired release, runs four entire Fleet lifetimes, then exercises an
actual interactive subprocess with two consecutive turns in the restored chat.
Ten model requests, successful real Shell output, four generation-specific Shell
sessions, session reuse within the interactive lifetime and final stopped state
are checked. The preceding isolated four-lifetime gate passed in 90.71s
(`/tmp/agent-terminal-interactive-native.log`). These are build/test durations,
not startup benchmarks; upstream model answers remain deterministic fixtures.

Client/replay/terminal/event-store/native HTTP/profile checks passed 60 tests with
8 native prerequisites skipped in 4.62s (`/tmp/agent-terminal-final.log`); those
profile prerequisites were supplied in the 24-test native run above. Counts
overlap. Coverage includes fragmented Unicode, retention reset, malformed
message identity, cancellation with a lost stop response, no backend imports,
command routing, redirected input, and real native protocol negotiation/restart.

This does not complete CLI parity or P3/P4: rich reasoning/media rendering,
template editing, image input, full interrupt/command behavior, automatic product
composition and the built native Desktop are still pending. Inspection confirms
the native Desktop launcher still starts the legacy combined backend and consumes
its legacy readiness payload; a versioned local composition handshake and actual
packaged acceptance are required before switching that path. No live Fleet,
Atrium, default entrypoint or remote branch was changed.

### Terminal frontend through the running Agent App

The opt-in local host accepts `--agent ALIAS -i PROMPT`, plus exact chat/resume
selection, a private JSON team template for a new chat and an explicit model
selection. It starts the ordinary composition, binds the chosen App's exact
prepared generation and runs a frontend client through native App RPC. The
terminal client does not instantiate ConfiguredAgentApplication, open the Agent
data lock, read backend memory objects or discover another runtime. Its import
boundary is checked in a clean subprocess. Original interactive/prepared CLI
entrypoints and defaults remain unchanged.

The client negotiates native protocols and selects App-owned conversations.
One-shot preflight reads live metadata, without downloading the full history for
every prompt. An explicit history reader verifies fragmented snapshots with
byte count/digest checks and bounded memory for later interactive restoration;
snapshots are released on success and failure.
It rejects known running conversations and reports concurrent queue admission as
incomplete. An uncertain RPC never repeats creation, inference or model changes.
The generic host owns one foreground task: completion/failure requests ordered
profile stop, interruption joins the frontend and drains the backend, and an
unsuccessful frontend exits nonzero only after confirmed shutdown. SIGUSR1 cannot
replay the foreground. Cancelling an in-flight terminal turn explicitly requests
Agent stop; the App host still owns the actual accepted call and durable saves.

Validation: after the metadata-only preflight change, the expanded full native
profile gate passed in 76.31s (`/tmp/agent-terminal-client-native-final.log`). After two independent Fleet lifetimes
with retained Agent/Shell history, the real `python -m pantheon local` command
opens a third lifetime, resumes the same conversation, executes another real
Shell call through original Model Services, prints its response and closes the
profile cleanly. Three distinct Shell sessions and six upstream model requests
are checked. Engine replies are fixtures. This duration includes release building
and three lifetimes, not a startup benchmark. Client/profile checks passed 36
tests in 25.49s (`/tmp/agent-terminal-profile-regression.log`), covering real host
frontend success/failure/interruption/self-cancellation, protocol/large-history
validation, unknown outcomes and explicit stop. The final client-only suite
passed 17 tests in 0.25s (`/tmp/agent-terminal-client-final.log`), adding metadata-only
status and unavailable-status rejection; it overlaps the previous client group.
Other prior regression scopes are unchanged; these counts are not a full P0–P7 acceptance claim.

This is a working single-turn frontend and resume path, not full interactive
REPL parity. Streaming rendering, slash commands, template editing, image input,
automatic Agent profile construction and native Desktop packaging remain pending.
No deployed/default environment was changed. See [local profile usage](local-app-profile.md).

### Opt-in local App profile host

`pantheon local` now dispatches before the legacy Agent/UI setup paths to a
platform-only profile owner. An explicit private manifest pins built packages and
ordinary App bindings; typed local references provide fresh loopback coordinates,
profile trust and endpoint-scoped vault references. The host runs the original
AppDeployment/ModelServiceBootstrap machinery, persists the whole composition,
reuses installed immutable artifacts, and cleanly stops consumers before original
Model Services providers and infrastructure. Reopening an acknowledged stopped
profile verifies original generations and model publications before rebinding.
Incomplete previous lifetimes, changed manifests/workspaces/trust or uncertain
model identities do not silently create new instances.

The host maintains dependency grants independently of Agent activity. Storage
observation failures are sanitized, reported and retried against the original
receipts; recovery is reported without replaying starts or calls. Healthy Fleet
is retained after uncertain startup/drain outcomes. SIGUSR1 retries the same
operation; SIGINT/SIGTERM request orderly shutdown. Embedded callers use a
command queue and do not acquire process signal handlers. Connections and
supervision tasks are closed even if profile construction fails.

The full packaged Agent profile gate passed in 71.35s
(`/tmp/agent-local-profile-full-agent.log`): two entire native Fleet lifetimes,
the same conversation and logical Agent identity, real Shell output in retained
history, and inference through the original Connector using distinct resource
sessions after restart. Upstream model replies are fixtures; native processes,
packages, transport, storage and Shell execution are real. This is a test duration,
not a startup benchmark. Final local-profile checks passed 19 tests in 19.64s
(`/tmp/agent-local-profile-final.log`), including real command/SIGINT shutdown,
installed-package reuse, lost-start-reply recovery on healthy Fleet, maintenance
failure/recovery, and connection/child cleanup when profile construction fails.
Related dependency maintenance, deployment stop, model bootstrap, preset,
composition/restart and platform regressions passed 187 tests in 3.91s
(`/tmp/agent-local-profile-regression.log`). The command's `--help` path was also
checked. These suites cover different scopes and are not end-to-end product
migration or performance acceptance.

See [Local App profiles](local-app-profile.md) for the explicit command, manifest,
status and recovery contract. This is an opt-in composition host, not yet an
automatic Agent preset, REPL client or native Desktop launcher. Interrupted-start
rollback, abrupt crash recovery, managed engines, product packaging, migration,
publication and default cutover remain outstanding. No installed Fleet, live
Atrium or production defaults changed.

### Durable ordinary deployment stop for profile shutdown

`AppDeploymentStop` and the platform-only `fleet_app_deployment_stop` API now
advance an explicit stop intent against the original completed deployment.
The owner selects Apps and supplies a new immutable stop operation ID. The
coordinator verifies the source owner/journal, includes consumers of selected
providers, checks all selected live identities before any stop, and traverses
startup dependencies in reverse order. Existing node-side stop hooks drain the
actual App; this layer does not kill processes, uninstall packages or delete data.
Unselected shared Apps remain outside its stop intent.

The private journal retains the source fingerprint, order and completed prefix;
node stop IDs are deterministic and checkpointed before submission. Pending
operations, lost acknowledgements, observer cancellation and interrupted receipt
writes resume the same intent. A blocked/failed drain prevents subsequent provider
stops. Changed instances/generations, conflicting ledger entries, remaining
resources/reservations or changed source recipes require explicit recovery.
An already stopped exact generation can be acknowledged without sending another
stop. Inspection reports only the last checkpoint; it does not establish live
termination or authorize shutting down local Fleet infrastructure.

The full native Agent gate now calls this production coordinator to stop Agent
and its brokers instead of issuing individual stop commands. It confirms Agent
stops first, the shared Shell and Model Service remain ready, repeated completed
stop observation adds no action, and the existing generation/whole-profile
restart flows preserve conversation history and execute new real Shell sessions.
Both native cases passed in 121.03s (`/tmp/agent-deployment-stop-native.log`).
Deployment/restart/model-bootstrap regression passed 123 tests in 2.96s
(`/tmp/agent-deployment-stop-regression.log`); expanded stop/API/platform checks
passed 66 tests in 1.34s (`/tmp/agent-deployment-stop-api.log`). Suites overlap;
these durations are not startup benchmarks.

This currently drains completed ordinary deployments. Interrupted startup,
model-directory state transitions, coordinated profile-level shutdown, and the
shipped automatic CLI/Desktop launcher remain separate outstanding work. No
installed Fleet, live Atrium, production defaults or user data were changed.

### Journaled model rebind before restarting ordinary consumers

The existing ModelServiceBootstrap accepts an explicit `restart_from` stopped
attached-model publication in each provider entry. This is a clean restart intent,
not discovery or crash recovery. It preserves the same model deployment, name,
node, artifact, instance, selected model IDs/context limits and configuration
revision. Shared input validation rejects changed endpoints/engines/credential
references before lifecycle work. The immutable owner-private startup recipe
captures the stopped publication; public progress still contains no configuration
or publication snapshot.

Before credential preparation or provider advancement, the coordinator reads all
unregistered restart publications. They must match either the exact stopped
snapshot or its exact acknowledged ready successor. It then uses the ordinary
AppDeployment prepare/configure/start ledger and original ModelServiceManager
rebind operation, checkpoints the receipt, checks publication/admission, and only
then advances consumer Apps with resolved model bindings. An uncertain directory
save or receipt write is resumed using the same startup/child operation IDs;
changed/deleted publications require explicit review instead of overwrite.

The real local Model Services gate now uses this production coordinator after
closing and reopening the entire Controller/NATS/Runner profile on a new port.
It injects a receipt-checkpoint failure after the real directory rebind, verifies
both consumers remain stopped, constructs a fresh coordinator, and resumes the
original intent. The ordinary model-control and minimal consumer Apps start with
new identities; the original HTTP/SSE model client performs inference through the
preserved route, and previous-generation calls/grants remain denied. Provider
responses are fixtures; the Fleet, prepared Apps, directory, checkpoint recovery,
permissions and inference transport are real.

Validation: 99 bootstrap/registration tests passed in 47.96s
(`/tmp/agent-bootstrap-rebind.log`), 106 existing preset/composition/restart/
deployment tests passed in 1.43s (`/tmp/agent-bootstrap-regression.log`), and the
native recovery gate passed in 13.74s (`/tmp/agent-bootstrap-native.log`). These
are test durations, not application startup benchmarks.

This removes separate manual provider-start/rebind/consumer-start steps from the
recovery caller. The CLI/Desktop profile owner still needs to persist the overall
profile intent, choose the clean restart recipe, deliver endpoint-scoped vault
credentials, and manage startup/drain/shutdown and abrupt interruption recovery.
Managed-engine recovery, native Desktop packaging, live migration, publication
and default cutover remain pending. No installed or production environment changed.

### Complete Agent recovery across a clean local Fleet profile restart

`plan_local_agent_restart` reopens the original completed AppDeployment journal
and delegates exact stopped-generation checks to the generic restart planner.
Only a losslessly reconstructed canonical Agent composition can proceed. The
same profile CA and model-directory path must be retained; the caller supplies
an explicit new loopback endpoint and endpoint-scoped node-vault reference for
the two owner brokers. Agent BYOK references, data paths, model tiers, extra
grants and ordinary provider Apps are preserved. App references resolve together
against their new generations in the original prepare/configure/start path.
Model selection is read from the original directory: route revisions, selected
deployments and provider artifact identities cannot silently change. A local
tool pinned outside the original graph, or an additional App with embedded local
authority inputs, requires its own explicit restart composition. No fallback to
ambient Fleet credentials or an alternate node is introduced. Planning issues
no grants, changes no directory entries and starts no processes; once the new
operation exists it must be resumed, not replanned.

The full native Agent gate now covers two cases: Agent/broker generation restart
with shared providers retained, and shutdown of all Apps followed by exit of the
whole Controller/NATS/Runner profile. In the second case it holds the old port,
opens the same profile on a new authority port, verifies stable Fleet/node/CA,
rebinds the original Connector publication, and uses the production planner to
restore Agent, both brokers and the ordinary Shell provider. It resumes the same
conversation and logical Agent identity, retains the first conversation turn,
executes a real Shell command in a new resource session, and delivers the result
back through the original Model Services alias. Calls to the old Agent generation
are rejected. Only upstream model responses are fixtures; App release building,
installation, dependency configuration, lifecycle, data, transport and Shell
execution are real. Both cases passed in 114.75s including build/installation
(`/tmp/agent-whole-profile-native.log`), not a startup performance measurement.

Restart/composition/deployment regression passed 89 tests in 1.13s
(`/tmp/agent-local-restart-regression.log`); subsequent focused coverage also
checks changed route policy and local providers outside the graph. Existing
model-selection editing uses the same canonical extraction and remains covered.

This proves a clean whole-profile Agent recovery, not abrupt crash recovery or
a shipped automatic launcher. Model provider restart, directory rebind and vault
delivery still need to be orchestrated by the product CLI/Desktop profile owner,
including durable interruption recovery. Native Desktop packaging, real-user
migration, publication/cutover/rollback and the other P0–P7 requirements remain.
No installed Fleet, live Atrium or production defaults changed.

### Generic preparation and model rebinding after local profile restart

Ordinary Apps without configuration declarations or resource budgets now reserve
an exact prepared instance identity through the same Fleet lifecycle as configured
Apps. They do not invent a resource reservation or configuration file. The
coordinator accepts an empty component map only after checking the immutable
manifest and its complete startup dependency contract; declared configuration and
credential inputs cannot be bypassed. Generation/preparation CAS, cancellation,
lost-start acknowledgement handling and persisted restart validation still apply.
The complete local Agent gate now includes the original managed Shell as an
ordinary provider in its deployment recipe instead of starting it separately.
Restarting only the Agent and its brokers retains the shared Shell provider while
issuing distinct logical resource sessions.

ModelServiceManager.rebind_prepared explicitly advances an acknowledged stopped
attached Connector publication to the same instance/artifact/node after one
prepare/start cycle. The original stop manager persists the stopped generation,
so the target is that generation plus two. Original registration verification
checks live Fleet identity, configuration, admission and discovery again. The
owner's model selection, context limits, capabilities and routes are preserved;
changed configuration, pending management operations, different instances or
concurrent directory edits require explicit management. Directory revision CAS
prevents overwrite, and a lost save reply can be retried against the exact saved
result. This helper starts no processes and does not extend managed-engine or
crash-recovery semantics.

The native model gate now stops all Apps and exits the entire local
Controller/NATS/Runner profile, reserves the previous Controller port, and opens
the same durable profile on a different port. It checks stable owner/node/CA,
reopens the original local directory, starts the same prepared Connector and
rebinds its publication without modifying route policy. A fresh consumer and
original model-control App receive new generation-bound policies and an explicit
endpoint-scoped vault reference; the prior vault entry is not overwritten.
Acceptance includes inference through the original scoped model client after
restart and rejection of the old Connector generation and previous RPC grant.
Upstream model responses remain deterministic fixtures; the profile, Apps,
publication, credentials, transport and inference path are real.

Focused deployment/configuration regression passed 102 tests; registration,
bootstrap and local-directory regression passed 99 tests. The Fleet lifecycle
package and targeted race suite passed. The full native Agent gate and the
whole-profile model recovery gate are recorded separately in
`/tmp/agent-unconfigured-native-final.log` and `/tmp/agent-model-rebind-native.log`.
These are integration checks, not launch-time or memory benchmarks.

Whole-profile recovery of the complete Agent conversation/tool composition,
automatic endpoint/credential rebasing in the shipped coordinator, CLI/native
Desktop launch, unexpected-crash recovery and the remaining P0–P7 acceptance are
still incomplete. No installed Fleet, live Atrium or production defaults changed.

### Full Agent on the independent local Fleet profile

The original Agent deployment composer now accepts explicit same-host local
transport settings: the exact loopback authority, public TLS CA and local model
directory. It emits ordinary prepared configuration for Agent, dependency
allocator and model access Apps. Owner credentials remain node-vault references
on the two trusted brokers. Model selection review/edit preserves this local
configuration; inconsistent endpoints, target nodes, certificates and supplied
Agent overrides are rejected. Cloud recipes are unchanged.

Agent receives its dependency trust in the immutable prepared snapshot rather
than relying on SSL_CERT_FILE. The trust is passed to model, tool, view and
allocator clients without modifying global environment or OS certificate stores.
Dynamic tool delivery also pins the explicit local RPC issuer. This fixes a
previous gap where a local model call could work but the Agent provisioner would
reject local Shell grants under cloud-only endpoint validation. Missing trust,
a different allocator address and grants from another loopback port fail.

AppInstanceResolver can now use an explicitly supplied live owner connection with
exact Fleet/node coordinates. The resolver closes that connection on shutdown;
the caller owns reconnect/renewal. A disconnect or closed resolver never falls
through to ambient Fleet credentials, installed daemons or node discovery. The
local model gate no longer assigns the resolver's private transport fields.

The new native gate builds and installs the full paired Agent release, allocator,
model access App, original Connector and ordinary Shell on real bundled
Controller/NATS/Runner processes. Original prepared registration publishes live
Connector discovery into LocalModelDirectory. Model selection, deployment and
restart use the production composers, AppDeployment and plan_restart. A real
conversation calls the model alias, receives a tool-call response, executes an
actual Shell command and sends its successful output back through Model Services.
After draining/stopping Agent and its two brokers, a fresh coordinator reopens
the journals, prepares new generations and resumes the same conversation and
logical Agent identity. The shared Connector and Shell provider stay running;
the resumed generation acquires a distinct Shell session. Old Agent generation
RPCs are refused. The sole external-service fixture is the upstream model's
deterministic response; no paid inference, Hub or live cloud deployment is used.

Source/configuration/dependency/placement regression passed 118 tests in 3.32s
(`/tmp/local-agent-regression.log`). The final native Agent/model, real release
deployment and prepared CLI group passed 32 tests in 98.63s
(`/tmp/local-agent-native-final.log`). The native test checks successful completed
Shell output, distinct generation-bound Shell sessions and inference history,
not merely the presence of a command string. Counts overlap prior gates; these
are test durations including installation, not product launch benchmarks.

This verifies complete Agent composition and App-generation restart within a
running local profile. It does not yet prove shutdown/restart of the entire
Controller/Runner profile, provider re-registration after changing generations
or endpoints, automatic CLI/native-Desktop launch, final product packaging,
production deployment, or the remaining P0–P7 requirements. No installed Fleet,
live Atrium, production defaults or remote branch was changed.

### Local prepared model directory and real scoped control composition

`LocalModelDirectory` supplies owner-private, durable publications and model
aliases for the bundled local profile. It stores no endpoint, engine config or
API credential. Original `ModelServiceManager.register_prepared` still verifies
the exact live Fleet instance, checks Connector configuration/admission, discovers
models and publishes reported capabilities with the owner's context limits.
Repeated registration reopens the journal and matches the prior publication
instead of rewriting it. The original manager also performs an explicit stop:
persist stopping, drain the Connector, stop through Fleet, persist stopped.

Writes compare revisions under a stable local file lock, replace/fsync one
bounded snapshot and finish before a cancelled caller returns. Readers see an
atomic snapshot. Owner mismatch, symlink/public files, corrupt data and a missing
existing catalog fail rather than silently resetting the directory. Deleted
identities retain revision tombstones so recreating an alias cannot reactivate an
old consumer policy. This is local filesystem coordination, not distributed
replica fencing. Unsupported managed-engine/group/recovery intents are rejected;
they cannot be flattened into an attached service and lose their lifecycle.

The original versioned `model-services-control` App accepts an explicit local
directory path only with the paired loopback issuer and private TLS trust. It
opens that directory read-only and keeps the owner credential in its prepared
configuration, delivered through the original encrypted node vault. Consumers
still use their ordinary scoped RPC grant and `DependencyModelServices`.
Catalog reads, alias selection and HTTP/SSE inference have no Hub dependency or
ambient credential fallback. The local directory adds no Python dependencies;
its wire output and attached-service/alias policies are checked against the
actual paired Hub router and SQLite persistence, including multimodal adapter
constraints, compute/billing restrictions and conservative capabilities.

The real native local model gate now replaces its frozen directory and custom
ModelServices subclass with live prepared registration, the packaged access App,
real RPC/HTTP grants and the production dependency client. It verifies direct
model and alias calls, denial after a pinned alias changes, stream retirement
after consumer stop, survival of the shared Connector and a subsequent explicit
owner stop with durable directory state. Controller, NATS, registry lookup,
Runner, credential delivery, Connector discovery and access App are real. The
minimal consumer is not the full Agent; upstream model output remains a fixture.

Validation: directory/owner/consumer/prepared-registration/bootstrap/deployment
regressions passed 189 cases in 39.64s (`/tmp/local-model-directory-python-final.log`).
The real local model/RPC/Fleet/TLS group passed 17 cases in 32.08s
(`/tmp/local-model-directory-native-final.log`); after adding original-manager
stop acceptance, the final native model gate passed in 10.19s
(`/tmp/local-model-directory-stop.log`). The real Hub contract comparison passed
in 0.94s (`/tmp/local-model-directory-contract.log`). Counts overlap; these are
test durations, not product startup benchmarks.

This is the prepared local Connector/catalog/control path, not the completed
standalone product. Automatic CLI/native-Desktop orchestration, full Agent
integration with this local directory, provider restart/rebinding, local managed
engine/group lifecycle, product packaging and all remaining P0–P7 gates remain.
No installed Fleet, live Atrium deployment or default startup was changed.

### Local Model Services HTTP transport and explicit trust

The bundled local Controller now separately opts into server-to-server HTTP
App dependencies, in addition to its RPC authority. The owner-facing
`/api/fleet/apps/dependency-http-grants` contract uses the existing durable grant
store, exact consumer/provider generations, path/method restrictions, expiry,
renewal and revocation. Its authenticated node tunnel targets only the published
port of the selected App. Local grants explicitly carry node-bound authority;
no fabricated Hub JWT, owner key or RPC token is forwarded to the provider.
Cloud gateways and direct grants reject this local authority. RPC-only local
Controllers still expose no HTTP data/tunnel routes. Browser authentication and
shared-origin browser Apps are not enabled by this change.

The original ModelDependencyControl accepts a local receipt only when its
prepared configuration explicitly pins that exact HTTPS loopback origin and
supplies verified TLS trust. It cannot infer local authority from a returned URL
or silently enable local direct transport. ModelServices' control, inference and
cancellation pools now accept the consumer's explicit TLS context, without
ambient proxy/CA discovery in that mode. DependencyModelServices carries its
existing dependency client's trust into these pools. Legacy callers without an
explicit context retain their transport defaults.

The real native gate installs the original prepared Model Service Connector and
a small live consumer App with Controller/NATS/Runner. The owner obtains an
actual local grant, and the production ModelServices client receives Connector
SSE through the real outbound WebSocket tunnel. Wrong paths/browser requests,
stale connector configuration and untrusted TLS clients fail. A second inference
produces its first output while the engine is still streaming; stopping the real
consumer aborts that request, closes the upstream stream and releases the
Connector call while the shared Connector stays ready. Revocation and no replay
are checked. The directory publication and engine replies are deterministic test
fixtures; this is not yet a full Agent, CLI, local directory or paid-model gate.

This gate exposed a real transport failure: Controller REST transport had added
HTTP/2 ALPN to the shared private TLS configuration, while Gorilla sent an
HTTP/1.1 WebSocket upgrade. The Controller rejected the tunnel and inference
returned 502. Runner now clones the configuration and restricts tunnel ALPN to
HTTP/1.1, preserving the original REST settings. Native and bridged tunnels use
that setting. The delegated HPC test now enables real HTTP/2 at its TLS Controller
and checks both streaming transport and the unchanged REST configuration; SSH
and Slurm remain local fixtures, with no Sherlock allocation.

Validation: the real local model/RPC/Fleet/TLS group passed 17 cases in 26.22s
(`/tmp/local-model-native-final.log`); model dependency/owner/pool regressions
passed 61 cases in 7.88s (`/tmp/local-model-python-final.log`). Gateway, transport,
direct-service and Controller regressions passed (`/tmp/local-model-go-final.log`),
including a golden legacy HTTP policy encoding check so existing grant journals
remain readable. Local/HTTP gateway tests passed under the race detector in
6.878s (`/tmp/local-model-race.log`). The delegated-node integration passed in
13.266s (`/tmp/local-model-hpc.log`). These are test durations, not launch timings.

This completes the local HTTP transport prerequisite. Local directory/control
composition, automatic CLI/native-Desktop launch, product bundle delivery and
all remaining P0–P7 acceptance still apply. No installed Fleet was replaced, no
live deployment or production default was changed, and no branch was pushed.

### Local owner-issued App RPC dependencies

The bundled local Controller now explicitly opts into an RPC-only dependency
authority on its private HTTPS loopback origin. It resolves the owner from its
local key allowlist and delegates issuance, durable operation replay, renewal
and revocation to the existing scoped gateway. It does not require a Hub, accept
an asserted Fleet identity, expose browser/tunnel routes or issue HTTP model
grants on that shared origin. Consumers receive only their scoped capability;
owner and service credentials stay with the local coordinator.

The Runner, prepared dependency owner and Python assembly must independently
opt into the exact local origin. Ordinary cloud validation remains unchanged;
there is no implicit localhost exception in App manifests. Method restrictions,
fixed session arguments, consumer/provider generation checks, short expiration
and revocation apply to local calls. Runner opt-in survives its saved assignment
and requires explicit private Controller trust before joining.

The actual native acceptance installs an ordinary Shell package and a minimal
test consumer App through real Controller/NATS/Runner binaries. The original
DependencyStarter issues/configures its grant, and the consumer process uses the
packaged dependency SDK to execute a Shell command. Attempts to override the
bound Shell session fail; renewal succeeds; revoked credentials fail; and a
separate valid grant becomes unusable after the consumer stops while Shell
remains alive. Apps are stopped before local infrastructure exits. No Hub,
authorization, node lifecycle or Shell execution fixture is used in this gate;
the consumer is a small test App, not the complete Agent or CLI.

This gate found a real certificate compatibility bug: Python 3.14 strict TLS
rejected the local leaf certificate's missing Authority Key Identifier. Local
certificates now carry the required key identifiers and key usage, and the
profile client also enables strict X.509 validation. An early issuer missing
Subject Key Identifier is re-signed with that metadata while preserving its
validated private key, subject, serial, validity and constraints. Corrupt issuers
still fail closed; neither global trust nor disabled verification is used.

Validation: the real local App/infrastructure/TLS group passed 16 cases in
20.68s (`/tmp/local-rpc-native-final.log`); these are total test durations, not
startup timings. Dependency assembly, owner host, live bindings, renewal and
retirement regressions passed 85 cases in 9.79s
(`/tmp/local-rpc-python-final.log`). Targeted gateway/lifecycle/Controller/Runner
regressions passed, as did gateway/lifecycle dependency tests under the Go race
detector (`/tmp/local-rpc-go-regression.log`, `/tmp/local-rpc-race.log`). The TLS
tests exercise strict handshakes for both new and upgraded issuers.

This completes the local RPC transport prerequisite, not the automatic CLI or
native Desktop composition. Local model-directory/control composition and HTTP
model transport, bundled product delivery, default entrypoint wiring and the
remaining P0–P7 acceptance still apply. No installed Fleet or live deployment was
updated, no branch was pushed, and no production default was switched.

### Profile-owned TLS for independent local composition

The bundled local Fleet Controller now serves HTTPS with a profile-owned CA.
The durable private issuer survives profile restarts; only the short-lived server
key/certificate rotates. Its certificate is valid for the loopback IP, and its
public CA is explicitly supplied to clients. No OS trust-store installation,
ambient CA environment variable, hostname override or disabled TLS validation is
used. A damaged/private-key-permission-invalid issuer fails startup rather than
silently changing the profile's identity. The Python implementation uses the
existing cryptography dependency.

The Runner's `--controller-ca` is saved alongside its Controller assignment and
used before a resumed `/token` request. Its dedicated client limits requests to
that Controller origin, disables proxy discovery and refuses redirects; private
trust does not affect global HTTP clients. Initial join, credential renewal and
delegation use this client. The same explicit trust reaches native and bridged
App WebSocket tunnels and direct-dependency liveness checks. Existing public
Controller clients and external-TLS-proxy deployments retain their old defaults.

The original OwnerDependencyLifecycle can now join the real local Controller
with its supplied SSL context and query the actual node, with no fake Hub. The
real local Fleet group and issuer tests passed 13 cases in 18.73s
(`/tmp/local-fleet-tls.log`), covering expiry/renewal, scoped revocation, profile
isolation, Shell installation/execution, untrusted-client rejection and damaged
issuer rejection. A separate real Runner restart, with no Controller/CA/key
arguments, passed in 2.10s using its saved assignment
(`/tmp/local-fleet-tls-resume.log`). Go private-client tests verify independent
CAs, cross-origin rejection, no global trust leakage and no redirect of join
credentials. The Controller-outage/credential-recovery regression also passes.
These are test-suite durations, not startup latency measurements.

The delegated-node gateway test now exercises both HTTP and private HTTPS with
real NATS and a real outbound WebSocket, including a file response and denial
after the service stops (`/tmp/local-fleet-tls-tunnel.log`, 13.602s). Its SSH/Slurm
boundary remains a local fixture; it did not contact Sherlock or allocate a job.
The full direct-transport group passed in 40.402s
(`/tmp/local-fleet-tls-direct.log`), including a real QUIC model request whose
authorization check uses private HTTPS. Revocation denies the next connection
before another model request reaches the provider. The Controller and model
responses in that direct test are fixtures; no cloud inference was purchased.

This is the TLS transport prerequisite, not automatic CLI/Desktop composition.
The follow-up above adds local dependency issuance across the gateway, Python
assembly and Runner while preserving owner authentication, exact generations,
fixed arguments and durable grant revocation. Browser/HTTP model origins need
their own complete isolation/transport design and acceptance. Default-entrypoint
wiring, model-directory composition and bundled native Desktop remain pending.
No live deployment, remote push or default switch occurred in this increment.

### Bundled local Fleet prerequisite for CLI/Desktop composition

`pantheon.platform.local_fleet.LocalFleet` now owns an isolated local profile
using the shipping Controller, NATS and Runner executables supplied by a product
bundle. The profile has its own stable identity, authenticated broker, loopback
control endpoints, explicit workspace share, owner credential renewal and
exclusive lifetime lock. Startup checks a freshly registered node, rather than
trusting an old runtime file. The launcher can watch sidecar failure; it does not
silently attach another Fleet or restart failed work. Shutdown reaps only its
owned processes. Accepted process creation and cleanup survive repeated
cancellation. Callers must drain their Apps before shutting down infrastructure.

Fleet `up --key-file` avoids placing the bootstrap key in process arguments.
The local Controller uses an exact broker PID file for revocation reload, avoiding
the legacy process-name broadcast to every NATS on the host. Existing remote
Controller behavior without that new flag is unchanged.

Seven real macOS integration tests passed in 16.13s
(`/tmp/local-fleet-test-7.log`): authenticated lifecycle calls, stable restart,
duplicate-owner exclusion, cancellation, failed broker startup, expiry/renewal,
sidecar failure, profile coexistence and scoped revocation, plus installation,
session acquisition, command execution, release and stop of the ordinary native
Shell App. This is the whole suite duration, not startup latency. A four-second
credential TTL exercises actual broker expiry/reconnect; read-only observation
retries tolerate that connection transition, without replaying mutations.
The test builds the real Fleet and Shell binaries and uses local NATS; no Hub or
model fixture is needed for this infrastructure/tool gate.

The real command exposed loss of output without a trailing newline: Shell removed
the whole line containing its completion marker. It now retains the prefix before
the marker in both normal and close/drain paths. Full Shell tests passed in
35.041s, including repeated/multiline/Unicode `printf` output. Runner private-key
and targeted Controller regressions also passed.

This does not yet deliver an automatic local Agent composition: trusted local
dependency issuance/gateway, model directory/control composition, bundle delivery,
CLI/Desktop entrypoint wiring and real-data upgrade remain. No system daemon was
installed, production Fleet restarted, remote branch pushed or default switched.
Linux runtime acceptance and native Desktop packaging remain open.

### Prepared and packaged CLI compatibility

Both `pantheon cli --app-data PATH` and `python -m pantheon.repl --app-data
PATH` now use the same ConfiguredAgentApplication as Fleet. The launcher must
supply its generation-bound prepared configuration. The paired release also
contains `cli.py` and the REPL, with pinned Fire/prompt-toolkit dependencies;
it can run with its own vendor tree in a clean Python environment without an
installed Pantheon package. This is an opt-in compatibility path, not automatic
local infrastructure or the default CLI cutover.

The adapter preserves explicit templates, one-shot input, recent/index/name/ID
resume and model selection. History/logs belong to the App mount. Project
selection must match the prepared binding, MCP management uses the declared
view service, and `/keys` explains the App bindings without writing global
terminal credentials. Images use the selected App project rather than ambient
settings. An explicit legacy memory directory requires migration instead of
being silently adopted. Failure results in a one-shot call propagate to the
process exit status. Legacy entrypoints remain available without `--app-data`.

The original Model Services Connector now also has a packaged CLI gate: direct
model and route calls, process exit/resume with retained context and revocation
without ambient-key fallback. It runs real subprocesses, TLS dependency RPC and
Connector HTTP/SSE; directory/authorization issuance and engine replies are
fixtures. The combined CLI group passed 18 cases in 29.51s
(`/tmp/prepared-cli-model-services.log`), including the isolated release. The
broader preceding regression group passed 69 cases with one rendered-GUI gate
skipped; final source CLI/lifetime changes passed 26 cases with the separately
executed clean-release gate skipped. Counts overlap. No live deployment or push.

Remaining compatibility work: automatically compose bundled local dependencies
without Hub login/external Fleet, preserve full CLI command/tool/image behavior,
migrate representative existing data, and install/verify native Desktop. The
release CLI hosts the same runtime and honors its exclusive data lock; it is not
a remote attachment to an already-running GUI backend.

### CLI readiness and owned runtime shutdown prerequisite

The REPL can import and receive the same Agent runtime without importing the
combined ChatRoom or platform implementation. Legacy construction remains lazy
for existing entrypoints. Supplying a runtime no longer reads global settings
just to calculate an unused memory directory; file logging uses that runtime's
settings. The prepared compatibility path above now scopes `/keys`, history,
setup/resume and launch configuration. Automatic default local composition and
exhaustive command compatibility remain required.

The CLI now joins required runtime setup before creating a conversation or
assembling its team. It displays its greeting first, but accepts input only when
setup and assembly succeed. Setup failures are visible, rather than logged in an
untracked background task. One-shot execution previously returned before the
interactive cleanup block; both paths now join setup/cache tasks, drain the
runtime and report cleanup errors. Repeated cancellation cannot detach admitted
setup or drain. Headless mode restores the caller's environment on exit.

Validation: 60 CLI/runtime/application/launch/lifecycle/recovery tests passed in
10.66s (`/tmp/repl-app-regressions.log`). They include setup/team/execution/save
failure, repeated interruption, interactive EOF and two actual App conversations
through local HTTP/SSE followed by reopening the same data mount and retained
history. A subprocess rejects any platform/combined-service import while loading
the CLI. Upstream responses and dependency allocation are fixtures; this is not
the packaged CLI, a live local tool acceptance, or native Desktop acceptance.
No default entrypoint or production deployment is switched by this increment.

### Ordinary headless tool sampling without an Agent implementation

Follow-up: ordinary consumers now select existing Model Services deployments or
routes with `spec.model_consumers`. Composition validates unused credential and
policy slots and rejects direct or transitive references to Agent-owned generations
before reading the directory. Each shared consumer gets its own policy and grant;
owner credentials remain on its prepared model-control App. Editing Agent model
choices preserves the other Apps' bindings. The startup preset importer and review
UI show each consumer's selection and the full Connector authorization scope;
missing, unexpected or mismatched reviews fail validation. A dedicated visual
model picker for every provider is not implemented yet.

The production browser/native integration now installs eight Apps, including an
independent Files model-control instance. It observes an actual raster image with
Files while Agent is running, after Agent model access stops, and after Agent is
uninstalled. Stopping Files model access rejects further sampling. The original
chat, migration, restart/reinstall, retained-data, grant and cleanup assertions
remain. `/tmp/native-files-sampling.log` passes in 172.338s; this is the whole test
duration, not App startup latency. Fleet, NATS, the scoped gateway and installed
GUI are real; Hub identity/directory and model responses remain fixtures.

Selection/composition regressions passed 95 cases with two release-dependent
cases skipped; those two then passed with the built release in 14.06s
(`/tmp/shared-model-release-composition.log`). UI review tests passed 30 cases;
type checks and targeted ESLint passed. This remains local work, not a production
deployment. CLI/native-Desktop composition, full tool capability parity and the
other P0–P7 acceptance items above remain required.

The portable ToolSet host now accepts an App-owned Model Services sampler. Each
admitted RPC receives a separate bounded callback; the callback expires when that
RPC returns, cannot replace the prepared model/route, and cannot read another
Agent's history. Unconfigured portable tools return an explicit missing-dependency
result rather than importing the legacy Agent sampler. Legacy combined ToolContext
behavior remains available outside the portable host for CLI compatibility.

The sampler and MCP share dependency validation, the original
DependencyModelServices client and cancellation-resistant cleanup. No provider SDK,
Agent run loop, ambient API key or new model backend is introduced. Request budgets
are per call, pending inference is bounded, failures are not replayed, and shutdown
waits for admitted calls before closing the owned model client. Prepared immutable
mapping configuration is accepted (the earlier MCP-only constructor required a
mutable dict).

Files has an explicit `--model-sampling` package variant declaring
`model-inference@1` and exporting workspace-local `observe_images` through that
ordinary dependency. Its required prepared values are `files` and `sampling`;
credential slot `models` must be issued for the Files consumer, not copied from
Agent. The sampling object contains exactly `credential`, `model`, `max_tokens`
and `max_requests_per_call`; `model` is an owner-selected Fleet model or route.
The base filesystem package still needs no model dependency. Both retain the
same App identity and immutable-artifact lifecycle; there is no Files-specific
model service. This increment covers bounded workspace raster observation, not
legacy cross-node image references, PDF observation or image generation parity.

The normal deployment coordinator can bind Files `models` to
`model-services-control.model_services_control`, with arguments `operation` and
`arguments`, and `policy_id` fixed by the grant. The control policy's consumer
must be the Files App identity/generation and its selected deployments/routes
must come from the existing Model Services directory. The follow-up above adds
selection to the prepared Agent/provider composition and exercises it in the
native eight-App gate; default production startup remains pending. A
shared Files provider needs a model-control lifetime independent of any one Agent;
reusing an Agent-owned control instance would break sampling on Agent uninstall.

Validation: the first combined sampler/Files/MCP/host group passed 40 cases.
The expanded group passed 118 cases with three clean-release-dependent cases
skipped (`/tmp/headless-sampling-regressions.log`). It includes real local TLS,
original Connector HTTP/SSE, exact model and route selection, revoked grants,
per-call concurrency budgets, expired callbacks with unused budget, cancellation,
setup failure and cleanup. A separately built Files package runs in an isolated
subprocess with Agent/settings/provider SDK imports blocked and sends a real
image request through its prepared dependency. Hub authority and model output
are fixtures, not a live owner deployment. The separate clean release/MCP group passed ten cases; three migration cases
needed the native credential-vault fixture and passed when rerun with the built
Fleet CLI (`/tmp/headless-sampling-migration.log`, 33.11s). The unconfigured GUI
case was not exercised in that group. All eight final targeted sampling cases
passed, including artifact validation (`/tmp/tool-model-sampling-final.log`).
`git diff --check` passed. No deployment or remote push has been performed.

### Owned tool and context-injector sampling callbacks

Local tools and context injectors now pass the owning Agent's explicit model
scope to their temporary sampler Agent. Quality tags resolve with that scope;
explicit Fleet placement and reasoning effort survive resolution. Delayed
callbacks ignore another active Agent's selected model and credentials. Reserved
context-injector callback/model fields cannot be replaced by supplied context.
Legacy unscoped selection remains supported.

Validation: seven targeted sampler cases pass using real localhost HTTP/SSE,
including concurrent App credentials, tools and injectors, implicit/low models,
delayed callbacks under another Agent context, missing-binding denial, and
reasoning/placement preservation. The initial sampler plus legacy affinity group
passed nine cases. The related model/helper/tool-binding/token suites passed 166
cases (`/tmp/agent-sampler-regressions.log`), and both clean release model/restart/
drain variants passed in 12.89s (`/tmp/agent-sampler-release.log`). These are scoped
callback and release checks, not live production deployment.

Portable ToolSet sampling is addressed above through an explicit ordinary model
dependency. The legacy combined ToolContext fallback still imports Agent for
compatibility. Migrating every model-assisted tool and removing that transitional
path require capability-parity and installed CLI/Desktop acceptance.

### App-owned chat helpers and stateless requests

AgentApplication now supplies its model scope to AgentEnvironment. Chat title
and suggestion generation use that scope, and delegation summaries borrow the
parent Agent's scope and current model. Helper caches live on ModelCallScope;
they cannot borrow another App's selector, credentials or Model Services client.
Explicit Fleet placement is preserved. Unscoped legacy callers retain their
existing provider selection. Delegation's fork-context policy likewise reads
the parent App's settings rather than ambient process settings.

A regression test exposed another real issue: all three cached helpers retained
previous calls in Agent memory, so a different conversation's next helper request
included the previous conversation. Summary, suggestions and title calls now
explicitly disable reading and updating helper memory. User conversation memory
is unchanged. The three regression cases failed before the fix and pass after.

Validation:
- 171 tests passed, two optional cases skipped across helpers, title lifecycle,
  token optimization, scoped inference/plugins, App composition and launch
  (`/tmp/chat-helper-scope-tests-4.log`). After the final delegation-policy change,
  84 targeted cases passed; the parameterized legacy/scoped asynchronous title
  lifecycle passed four cases. Four legacy delegation cases also passed.
- Two clean release model/restart/drain variants passed in 12.87s.
- The final production browser + native seven-App gate passed in 164.922s
  (`/tmp/combined-agent-helpers-final.log`). It checks exactly 33 main inference
  rounds and three bound suggestion calls, displays suggestions before stopping
  and after reinstall/reconnect, and retains all data/authorization/provider
  assertions. Hub identity/directory and model output remain controlled fixtures.
- Browser script ESLint and `git diff --check` passed.

This closes the observed chat-helper gap, not exhaustive model-call parity.
Local tool/context-injector `_call_agent` callbacks are addressed above; remote
headless tool-service sampling remains under review;
installed CLI/Desktop, migration failure/cutover/rollback, publication/self-edit
and production default topology remain outstanding. No live deployment or push.

### Declared Fleet RPC for the installed Agent GUI

The combined browser gate exposed a real transport mismatch: the installed Agent
correctly requires a node-local RPC credential, while its GUI used the HTTP App
gateway, which intentionally does not forward that privileged credential. The
GUI loaded but initialization returned 403. Managed Apps can now explicitly
select `execution.rpc_transport: "fleet"`; the generic packaged host uses Desktop
`app_call` with its trusted, exact instance binding. Fleet checks App identity,
digest and generation and supplies the node-local credential. The Agent release
declares this transport. Undeclared Apps retain their existing gateway path.
Denied, stale or disconnected calls are not replayed or routed elsewhere. Node
selection rejects an explicit Fleet RPC App on nodes missing `app-rpc-auth`.

Validation: 33 packaged-host tests passed (including success, denial, disconnect
and hostile iframe binding data), 20 manifest/registry tests passed, and the
Desktop placement suite passed. Type checking, targeted lint, production Hub-mode
Desktop build and both isolated Agent release variants passed. The complete combined browser/native gate now passes: a production desktop opens
the installed Agent, chats, observes backend retirement, continues Files and
independent model inference after uninstall, then reconnects the same window and
conversation after reinstall and chats again. The release declares `chatId` as
persisted view state; runtime bindings and credentials remain transient.

`/tmp/combined-agent-desktop-13.log` records both subtests passing in 167.418s,
with seven native Apps and 33 controlled upstream inference rounds. The browser
log is `/tmp/native-agent-desktop.log`. Real Fleet managers, install hooks,
authenticated NATS and the scoped HTTP gateway are exercised; Hub identity and
directory and upstream model output remain fixtures. Existing assertions still
cover old binding rejection, retained data, shared provider survival and cleanup.
This does not validate the real Store/Browser/Jupyter Apps or live Hub deployment.

The initial gate exposed a feature gap, addressed by the follow-up above: chat
title, suggestion and delegation summary helpers used ambient model configuration. Main conversation calls
use the bound Model Services correctly. These helpers now have explicit scope and isolation coverage; other auxiliary
callers still require capability-parity review.
No live deployment, remote push or full migration completion is claimed.

### Agent release includes the generic frame host

The real Fleet browser gate reached the installed Agent backend, then remained
at “Loading App module”. The paired Agent builder copied the portable Python
host but omitted its `assets` directory; `/app-host.html` therefore returned 404.
The release now includes the same generic App host, snapshot helper and vendored
image renderer used by other portable Apps. They are covered by the immutable
release inventory, with no Agent-specific iframe or alternate transport.

Both isolated release model/restart/drain variants passed in 13.65s, now asserting
the three assets are included in the inventory and fetching their exact bytes
from the running installed host (`/tmp/agent-release-assets-tests.log`). Complete
browser lifecycle acceptance remains pending; this is not a live deployment.

### Independent Platform readiness and configuration contention

The combined native deployment/browser gate exposed two concrete startup issues.
An authenticated independent Platform answered pings, but Atrium still required a
single live runner with `proc`, `fs:workspace` and `display`, leaving headless
Fleet deployments at “Starting your workspace”. Independent Platform connections
now require their authenticated ping and actual Desktop session, while App
placement checks each App's capabilities. Legacy combined runtime readiness
retains its existing runner requirement. Losing a display node no longer blocks
the entire independent desktop.

The node's `ConfigureApp` uses a nonblocking lifecycle lock and explicitly rejects
configuration before writing when that lock is busy. The Python lifecycle client
now distinguishes that exact protocol-v1 rejection from ambiguous failures.
AppDeployment returns its existing pending checkpoint; the next bounded advance
uses the same starter journal, configuration, grants and operation IDs. Timeout,
lost acknowledgement and all other errors retain the explicit recovery path.

Validation: 90 lifecycle/deployment/preset tests passed, including repeated busy
rejections with unchanged grants and exactly six original lifecycle operations.
21 workspace UI tests passed, including independent headless readiness, required
Desktop attachment failure and unchanged legacy readiness. TypeScript, targeted
ESLint and a fresh Hub-mode production build passed. Both production desktop
browser variants passed in 20.37s, including native Fleet Files/PTY operations.
The desktop fixture now declares its empty Modal GPU service list explicitly.
Logs: `/tmp/agent-config-busy-tests.log`, `/tmp/platform-headless-readiness.log`,
`/tmp/headless-desktop-{types,lint}.log`, `/tmp/platform-headless-browser-2.log`.

The combined installed-Agent GUI lifecycle gate is still under development;
these results do not complete it or the P0–P7 migration. No user deployment or
remote push was performed.

### Open node-installed packages without a Desktop Store checkout

Native deployment recipes install releases directly on Fleet. Desktop previously
required its local `node-artifacts` Store index to describe the bound package,
and its window-open intent performed another Store lookup. Both paths now accept
an exact authenticated Fleet binding: missing local provenance is resolved with
`app_manifest` for the installed artifact digest. App identity and generation are
checked; the artifact digest is never fabricated into a Git/Store revision.

Fleet's Open action routes packaged modules through `pkg:` and the generic
package host, including Agent when an older built-in launch alias is present.
The host resolves an explicit binding before catalog/Store discovery. Saved
bindings reconnect only to the same node, instance and artifact at a newer ready
generation, verified again through placement. They no longer silently start the
default `app` scope, which would discard prepared deployment configuration and
dependency ownership. Stopped/missing deployments require restoration in Fleet.
This also applies when a saved window carries Store provenance. Native-stream
windows retain their streaming host.

Validation:

- 79 UI tests passed across package hosting, Fleet lifecycle, window creation,
  closing and rendering. Includes absent Store checkout with/without provenance,
  prepared-generation recovery, incompatible/stopped generation rejection,
  original view-state retention, built-in alias routing and native-stream hosting.
- TypeScript passed; targeted ESLint passed for six changed component/test files.
  The two remaining changed shell/store files have the same six lint findings as
  their HEAD versions (five existing `any` declarations and a Vue template type
  assertion misread as a filter). They are not reported as clean lint.
- 55 Python placement/session/registry tests passed. The older registry test
  fixture now supplies the existing stage snapshot and resolved revision, and
  checks the immutable revision passed to staging.
- The real seven-App native Fleet gate passed in 159.87s. It additionally reads
  the Agent manifest through production AppPlacement without a Store index,
  repeats this after reinstall and rejects the old generation. Existing inference,
  data retention, provider survival and cleanup checks still run. Its Hub and
  upstream-model fixtures remain as previously documented.
- The freshly built production desktop passed both existing Chromium variants
  in 20.22s. These are still Agent-free desktop regressions, not the combined
  installed-Agent GUI uninstall/reinstall browser acceptance.

Evidence logs: `/tmp/native-package-open-{ui,runtime,fleet,desktop,types,lint}.log`.
The WindowFrame lazy-component test fixture was corrected to expose an ES module
and await loading. No live user deployment or remote push was performed. Full
combined GUI lifecycle acceptance, compatibility, migration/cutover/rollback and
P7 default switch remain outstanding.

### Desktop feedback when a bound App backend retires

Window leases previously retried stale bindings indefinitely with only a
console warning. The generic packaged-App host now receives exact-binding
retirement notifications after a failed lease renewal and a successful Fleet
lifecycle observation. Absent installation, stopped/failed instance, or a newer
instance generation produces an actionable Fleet/reconnect message. Timeout,
offline node, missing instance, older generation and a different node do not
prove retirement and leave the window intact. Retired leases stop renewing.

The existing frame and persisted view state remain available, but the frame
becomes inert and its RPC/files/navigation requests are rejected. Late startup
or diagnostic messages cannot revive it. Focusing the window does not restart
an explicitly stopped App; reconnect is a user action using the normal
placement/startup path. Reconnect reuses saved state after obtaining the current
binding. The behavior applies to ordinary packaged Apps and embedded views,
without an Agent-only lifecycle mechanism.

Validation: 56 UI tests cover usage, stale observations, disposal races, packaged
window reconnection/initial state, embedded panes and managed startup
(`/tmp/agent-backend-retirement-tests.log`); TypeScript checking and targeted
ESLint passed. The production desktop build and both Chromium desktop variants
passed (2 tests, 25.68s; `/tmp/agent-backend-retirement-desktop.log`), including
native Fleet/Files/PTY operations without Agent implementation. Those browser
gates verify desktop regressions; the new retirement/reconnection transitions
are covered at component level. A single browser gate combining the installed
Agent package, uninstall/reinstall and other desktop Apps is still required.
No live user deployment or remote push was performed.

### Native Agent uninstall/reinstall and independent model inference

The seven-App native release gate now proceeds beyond stop/restart. It stops
the Agent and its generation-bound allocator/model-access Apps, uninstalls the
paired Agent package through Fleet, and verifies both the absent installation
directory and byte-for-byte retention of the Agent's durable files.

While Agent is uninstalled, a fresh instance of the original `ModelServices`
client performs inference through workload connection issuance, the real Fleet
HTTP gateway, the running Connector and SSE. Files remains readable. The Hub
test wrapper now implements workload-connect by forwarding to the real gateway;
it does not return a fabricated transport grant or inference response. Hub
identity/directory and the upstream model's output remain fixtures.

Reinstallation uses `plan_restart` and the ordinary durable `AppDeployment`
journal with fresh prepared generations. The gate verifies that only Agent is
installed again; shared providers retain their process resources/generations.
The restored Agent preserves the exact conversation and logical instance IDs,
serves the managed Files image preview, and successfully sends the retained
remote/App-owned images to the model (the fixture checks their pixels). Final
stop retires the new grants, and the existing cleanup assertions still apply.

Validation: `TestDependencyRPCOverAuthenticatedNATSAndNativeApps` passed with
both subtests in 159.238s (`/tmp/agent-uninstall-native.log`). It observes 31
controlled upstream inference rounds, including three image checks and one
independent call with Agent uninstalled. The GUI was freshly built with
`AGENT_APP_VERSION=0.7.0`, `AGENT_APP_BUILD_DIR=/tmp/pantheon-agent-uninstall-frontend`
and `pnpm build:agent-app`; a development-version build is correctly rejected
by release validation. This is native macOS process acceptance, not a full
desktop uninstall/reopen test, actual Hub deployment or real upstream-model
acceptance. No live user deployment or remote push was performed.

### Native Fleet desktop placement without Agent

The production-browser gate now also runs against a freshly built `fleet up`
process. Controller join and Hub discovery/model-directory responses remain
local fixtures; JWT/NKey NATS authentication, JetStream node registration,
AppInstanceResolver placement, Runner supervision and the App services are real.
The Runner is isolated with its own state/workspace, no shared home directories,
no native-capture setup, no auto-update and no libp2p data plane. It does not join
or modify the user's Fleet.

Desktop, file-manager and file-transfer execute in separate Runner-owned Python
processes. Each process installs an import blocker before loading Pantheon;
the gate checks actual descendant PIDs against the guard audit and rejects even
caught attempts to import Agent implementation. The platform host uses the
production resolver without replacing placement, registry or App startup.

In the real production desktop, with Agent implementation absent, this gate:

- reads the Model Services directory and synchronizes windows across viewports;
- creates a directory through Files;
- opens Terminal, waits for a real PTY connection, executes a command that writes
  a proof file, receives the shell's output, then reads that file through Files;
- opens Fleet and observes the actual Runner node;
- checks supervised App process ownership and child cleanup at shutdown.

Enable the native variant with `PANTHEON_TEST_DESKTOP_FLEET` pointing to a fresh
`go build ./cmd/fleet` binary, plus the production desktop build/script variables
documented below. The existing local-worker variant remains a faster isolation
check. Native placement uses the currently shipped bus-App supervisor; this
does not claim that all builtins have migrated to immutable versioned packages.

Validation: both browser variants plus authenticated owner-host, platform RPC
and Desktop stream regressions passed (19 tests, 33.11s;
`/tmp/platform-native-fleet-regressions.log`). After adding an explicit assertion
that Runner shutdown leaves no child processes, both browser variants passed
again (2 tests, 19.42s; `/tmp/platform-native-fleet-cleanup.log`). The screenshot
at `/tmp/platform-desktop-gate.png` was inspected with the PTY-generated file
visible in Files.

This extends P1 evidence, not completion: a deployed versioned Agent must still
be stopped/uninstalled while the whole desktop remains usable. Real model
inference in that same deployment, Store/Browser/Jupyter, cross-node transport,
default Hub cutover, release/rollback and self-edit acceptance remain required.
No live deployment or remote push was performed.

### Production desktop gate without Agent implementation

A real Chromium gate now opens the production `desktop.html` build against an
independent Platform service over JWT/NKey-authenticated local WebSocket NATS.
An import blocker refuses Agent/ChatRoom/team/factory/memory implementation in
the host, and the browser refuses all built chunks containing legacy Agent GUI
implementation. The legacy service advertised alongside the platform has no
responder. Hub discovery/model-directory records and App placement are fixtures;
Platform RPC, Desktop session/presence, Files methods, transport and GUI are real.
The three App service workers and platform share an isolated test process, so
this is not a native Fleet process-placement or lifecycle gate.

The first run exposed a production dependency missed by static frontend checks:
Desktop window and presence broadcasts imported `pantheon.chatroom.stream`.
The named event publisher now lives in `pantheon.remote.streams`; the legacy
Agent adapter retains its chat hooks and wire format through this shared base.
Desktop closes its owned event transport even if supervisor cleanup fails.

The gate proves startup with no Agent implementation, the explicit install/restore
screen when Agent is absent, Model Services directory retrieval through platform
RPC and the original model client, opening the same desktop in a second viewport,
closing a window there and observing its removal in the first viewport, reopening
Model Services, and creating an actual directory through Files. It refuses
external browser HTTP requests and checks that model directory reads carry the
fixture owner's credential. A rendered screenshot was inspected at
`/tmp/platform-desktop-gate.png`.

- Build: UI `scripts/build-platform-desktop.mjs`, with `VITE_APP_MODE=hub`, empty
  `VITE_API_BASE_URL`/`VITE_PANTHEON_HUB_URL`, and an isolated
  `PLATFORM_DESKTOP_BUILD_DIR` (`/tmp/platform-desktop-build.log`).
- Run: runtime `tests/test_platform_desktop.py`, setting
  `PANTHEON_TEST_PLATFORM_DESKTOP` to UI `scripts/test-platform-desktop.mjs` and
  `PLATFORM_DESKTOP_BUILD_DIR` to that output. With platform RPC/service and Agent
  stream/lifecycle checks: 38 passed in 14.28s
  (`/tmp/platform-desktop-and-stream-gates.log`).
- Authenticated owner-host and Desktop session/presence/broadcast regressions:
  67 passed in 9.47s (`/tmp/platform-desktop-broadcast-regressions.log`). These
  groups overlap. The legacy final-save fixture was also updated to supply the
  explicit image-preview environment when bypassing the runtime constructor.

Still required: actual Fleet-managed Agent stop/uninstall with all graphical Apps,
real model inference in that desktop deployment, Terminal/Browser/Jupyter
acceptance, cross-node paths, default Hub cutover, release/migration/rollback and
self-edit acceptance. The fixture's missing Fleet controller is shown honestly
in the Files machine inventory; directory operations still execute locally.
No live user deployment or remote push was performed.

### Independent launch aliases and transient App reference delivery

The built-in Agent launch alias now loads the embedded GUI only after an
explicit legacy connection contract. On an independent platform, ordinary
installed-package resolution is the launch path; an absent package displays an
install/restore action rather than importing the old Agent runtime/UI. The old
CLI and legacy Desktop implementations remain available.

Files/desktop “Chat with Agent” and explicit drag targets now use a generic,
viewport-local App event channel. The packaged host subscribes after its iframe
acknowledges initialization; the Agent frontend installs its event listener before
`ready()`, retaining references while connection/composer initialization finishes.
The old Agent view uses a small adapter for the same event. Pending events never
enter shared window arguments or replay on reopening. Queues are bounded and
expire after two minutes; window removal and workspace reconnection forget
unconsumed events. A bare-id saved Agent window shadowed by the installed package
is reused instead of creating a duplicate window.

Verification:

- 113 UI regressions passed, including explicit legacy/native launch selection,
  queued and live SDK events, no event persistence/replay, exact drag targets,
  close cleanup, startup queues, private Agent clients and legacy compatibility
  (`/tmp/agent-entry-intents-regressions-final.log`).
- TypeScript, the production Agent build and the real Desktop build source/chunk
  boundary passed. No Agent implementation is in the eager Desktop closure
  (`/tmp/agent-entry-intents-boundary.log`).
- Two production-GUI Chromium/native-Agent gates passed in 10.66s with controlled
  local model/provider endpoints. They include references arriving before the
  composer exists and after it becomes usable, plus chat/replay, Files, Skills,
  settings and compact/full layouts (`/tmp/agent-entry-intents-gui.log`).
- 23 platform bootstrap/service/RPC checks passed in 5.05s. The real authenticated
  local NATS subprocess gate blocks imports of Agent implementation, serves
  platform RPCs, and remains responsive after its synthetic child exits
  (`/tmp/agent-entry-platform-gates.log`). This proves transport/process isolation;
  it does not prove running all graphical Apps after a deployed Agent shutdown.

Cross-node resource mapping, App-specific resource resolution beyond references,
full deployed Desktop acceptance with Agent stopped/uninstalled, default Hub
provisioning, release/cutover/rollback and self-edit remain unfinished. No live
user deployment or remote branch was modified.

### Attached chat panes host the ordinary Agent App

Atrium's window chat pane now selects the same installed DOM Agent package as
its launcher. A generic `EmbeddedApp` adapter gives that view a separate bridge
identity, nested durable state, immutable revision and backend binding, plus its
own ordinary usage lease. App title/menu/close commands cannot mutate or close
the containing App window. Detaching flushes coalesced durable state before
opening the same Agent backend/revision/conversation in a full window. New chat
clears the pane conversation while retaining its backend. Packaged Agent windows
cannot recursively open another Agent pane.

Only an explicitly legacy platform connection may load the preserved lazy
`LegacyWindowChatPane`. An independent platform without an Agent package shows
an install/restore state; a selected package disappearing does not switch the
conversation into the embedded legacy runtime. This is an entry-point change,
not proof that every default deployment has already been switched.

The Agent frontend accepts a compact presentation and transient host references
through ordinary initial state. State can arrive after module setup. References
are consumed once, and the shared conversation composer retains early references
until its input mounts after history/team loading. Queued references are checked
against conversation identity before insertion. None of this context grants an
App extra tool permissions or supplies a model credential.

Verification in the isolated UI/runtime worktrees:

- 87 unit regressions passed across Agent client ownership/replay/navigation,
  legacy/native pane selection, generic embedded host commands and leases,
  durable-state flushing, platform discovery and the legacy connection adapter
  (`/tmp/agent-embedded-regressions-final.log`). A stale legacy test fixture was
  updated to include the existing UI `chatroom.mode` field and now asserts cloud
  compatibility selection explicitly.
- TypeScript checking and production Agent build passed. The real Desktop build
  source/output boundary passed with no eagerly imported Agent implementation
  (`/tmp/agent-embedded-boundary.log`). The legacy pane remains a lazy fallback.
- The production Agent GUI ran in Chromium against a real isolated native Agent
  process and local model fixture: prompt/replay/reopen, delayed initial state,
  narrow pane, one-time host reference and full-layout switch passed in 4.60s
  (`/tmp/agent-embedded-gui-final.log`). An initial failure exposed the early
  reference loss described above and was fixed before the successful gate.
- The real scoped Files browser gate also passed in 6.46s, including editing,
  upload, preview and App-owned Skills/settings
  (`/tmp/agent-embedded-files-gui.log`).

The GUI gate tests the production Agent artifact; the containing Atrium pane is
covered by component/bridge tests, not a live deployed Desktop. Full platform
stop/uninstall acceptance, default launcher/bootstrap cutover, remaining resource
intents and UI preferences, release publication, migration/rollback/self-edit and
Linux/HPC acceptance remain open. No live deployment or remote push occurred.

### Captured legacy ImageStore files migrate with typed history references

The ordinary inventory now includes the `images` subtree of every declared
project/global configuration root as Agent-owned data. Each original store keeps
its own stable hashed directory under the new App image store, so identical
chat IDs or filenames from different projects cannot overwrite one another.
The existing private backup, source fencing, digest verification, resumable copy
and receipt paths also cover these bytes. Original stores remain untouched.

Import rewrites only typed `image_url` references in message `content` and
`_llm_content`, for both JSON and JSONL history. Known store references must
resolve to a captured file; missing images and lexical traversal out of a store
block admission before populating the target. Source symlinks remain rejected.
Reference processing never discovers or reads files named by message prose.
Text, tool arguments, external workspace paths and HTTP/asset URLs are retained.
JSONL conversion hashes and processes one bounded message at a time, rather than
loading a whole conversation or keeping rewritten histories in the import plan.

The native Fleet acceptance now starts with a green image in both legacy history
formats. After the normal backup/import, both histories point inside the new
App's image store. The original Model Service Connector receives and decodes
that captured image together with a red Files-provider image and a new blue
upload, and receives them again after packaged Agent restart. The gate passed
in 124.267 seconds (`/tmp/agent-native-image-migration.log`). Fleet/App processes
and transport are real on this Mac; Hub authorization/directory and inference
business output remain fixtures.

Validation also passed all 384 migration, credential/MCP/model conversion,
image-resolution and writer-fence checks with the native Fleet credential CLI
and release runtime supplied (`/tmp/agent-image-migration-regressions-native.log`,
171.11 seconds). The final metadata guard passed 24 image/import checks in
4.76 seconds (`/tmp/agent-image-migration-final.log`), including unchanged JSONL
metadata fields, three colliding image-store names, missing/escaping references,
symlink rejection, interrupted-import resume, and history exceeding 16 MiB.

This closes captured ImageStore relocation, not arbitrary workspace/remote asset
mapping or GUI resource-intent delivery. A backup taken before image stores were
classified must be recaptured under the fence; changed inventory is not silently
accepted. Distributed exclusion, post-cutover rollback, release publication,
default deployment switching and Linux/HPC acceptance remain open. No live user
data, deployment or remote branch changed.

### Model inputs resolve through owned storage and the bound Files App

Explicit Agent compositions now preserve file references in conversation history
and resolve them for each model round. Uploaded images belong to the supplied
App-private image store; workspace paths use the exact bound Files provider's
`fetch_image_base64` capability. Missing/escaping owned images do not fall through
to Files, and a missing/revoked Files binding does not read the Agent host's
same-named file. Provider failures stop before model retry/fallback. Returned
images must be bounded base64 data URIs; returned file/HTTP URLs cannot trigger
adapter-side local or network reads. User, tool and steered message images use
the same resolver. Bytes are deduplicated only within a request, so later rounds
recheck provider access and contents.

For non-Fleet compatibility models, the vision-description helper now uses the
originating ModelCallScope for model selection, credentials and its bounded
description cache. It no longer uses another App's global settings/cache.
Fleet model references retain their existing capability policy without an
implicit companion-model substitution. Legacy unscoped CLI/Desktop calls keep
their existing local-file behavior.

Validation: 176 model, factory, application, launch, tool-vision and attachment
checks passed in 32.72 seconds (`/tmp/agent-image-resources-complete.log`). The
authenticated NATS/native Fleet gate passed in 122.909 seconds
(`/tmp/agent-native-image-resources-third.log`). Its original Model Service
Connector receives and decodes both a red Files-provider image and a blue
App-owned upload, including restoration from stored history after packaged Agent
restart. The fixture checks dimensions/pixels and counts 29 inference rounds.
Real Fleet/App processes, dependency gateways and Connector transport run on this
Mac; Hub authorization/directory and model output remain fixtures, not evidence
of live model understanding or physical multi-host acceptance.

An earlier attempt stopped before image execution because node configuration
reported lifecycle busy; no fix for that contention is claimed. A subsequent
attempt reached the image check but exposed a test-only `/var` versus
`/private/var` path comparison; the assertion now compares canonical paths.

The follow-up above relocates captured old ImageStore references. General
resource intents and provider-owned generated-image scanning remain incomplete.
Model startup presets, publication, default cutover and Linux/HPC acceptance
remain open. No live user deployment or remote branch changed.

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

This closes the GUI's missing image-preview provider capability. The follow-up
above adds explicit model-input resolution and captured ImageStore relocation;
generated-image scanning and general resource mapping still need work before
cross-node attachment cutover.
Full transfer/document helper delivery, migration, publication, default cutover
and real Linux/HPC acceptance are still open. No live deployment changed.

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
