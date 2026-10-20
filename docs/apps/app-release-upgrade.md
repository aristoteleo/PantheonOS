# Compatible App release upgrade and retained-data rollback

This is an owner-controlled candidate workflow, not an automatic updater or a
production default cutover. It uses ordinary Fleet installation, `clone_data`,
App deployment and stop operations. It contains no Agent-specific lifecycle.

## Procedure

1. Build and install exact target artifacts on the source nodes. For Agent, the
   GUI build report must match the backend version; the package builder rejects
   an old GUI paired with a new backend version.
2. Stop the selected group through `fleet_app_deployment_stop`. Include every
   deployed consumer whose bindings or configuration refer to a changed App.
   Shared providers outside that group retain their original running generation.
3. Call `fleet_app_upgrade_prepare` with the owner, original deployment ID, a new
   stable candidate operation ID, selected App names and changed revision digests.
   Resume this same operation ID after pending results, lost replies or owner
   restart. It validates installed contracts before copying any data.
4. Once the result is `prepared`, call that API with `action="recipe"`, owner and
   operation ID only. The private result is an ordinary `fleet_app_deploy` input.
   Preparation itself neither starts the candidate nor changes a default route.
5. Submit/resume that exact deployment recipe and validate the ready candidate
   before separately publishing a route or changing a saved launch profile.

The Python owner primitive is `AppUpgradePreparation`. Its checkpoint is an
immutable owner-private intent, written before sending any clone request. Fleet
operation IDs are deterministic. A lost acknowledgement observes the same ledger
operation; it cannot silently recopy data under a new ID. Preexisting candidate
data is rejected unless it is the unused copy belonging to that exact intent.
Changed source/provider generations, conflicting requests and unknown outcomes
require inspection rather than replacement operations.

## Rollback policy

After the candidate **completed startup and was cleanly stopped**, or its
**deployment abort completed**, call
`fleet_app_upgrade_rollback_plan` with the candidate preparation ID and a new
rollback deployment ID. The owner must present these returned policies before
submitting the recipe:

- `data_policy: retained-source-data`: the old release reopens its data as it was
  before the upgrade; it does not open the candidate's potentially newer schema.
- `candidate_writes: retained-separately`: new conversations and other candidate
  writes stay in its data directory. They are neither discarded nor merged into
  the old release's data.

The plan checks that the stopped/aborted candidate is the exact prepared recipe and
that the original data instance is still at its retained stopped generation.
Unchanged members of the selected group restart through the same ordinary
deployment with newly resolved bindings. Shared providers stay pinned. Planning
does not start Apps, publish a route, or update a CLI/Desktop launch profile.

## Failed or interrupted candidate startup

Call `fleet_app_deployment_abort` with the owner, candidate deployment ID as
`source_operation_id`, and a distinct stable abort `operation_id`. The original
deployment journal is durably fenced under its own owner lock before observation
or teardown. Calling `fleet_app_deploy` on that old deployment can no longer
resume its starts, even after an owner restart.

The coordinator checks the original node operation requests and waits for queued
or running installation/preparation/start operations to settle. An unknown node
outcome requires Fleet recovery; a timeout alone never establishes termination.
After the outcome is known, it checkpoints all exact target generations and
stops consumers before providers. A process-free preparation is cancelled by
Fleet's ordinary stop action, releasing its reservations. A failed started
process goes through the ordinary stop hooks and resource checks. Shared services
outside this deployment and both releases' data remain intact.

Pending drains retain their dependencies. Failed/unknown stop receipts require
inspection under the original operation rather than a replacement request.
Repeating the same abort after lost acknowledgements, cancellation or interrupted
checkpointing observes the existing node ledger. A changed generation or foreign
owner is rejected. `aborted` means every selected target has a verified stopped
generation or is still at its unused original state.

After `aborted`, use the upgrade rollback planner to restore the old release.
Alternatively, the ordinary deployment restart planner can review a **new**
deployment of the entire aborted group, using its exact resulting generations.
The aborted journal is retained and remains fenced. Partial group restart is
rejected because its internal references would be incomplete.

## Composed model startup cancellation

`ModelServiceBootstrap.abort(owner=..., source_operation_id=..., operation_id=...)`
now fences its original composition under the same journal lock used by startup.
The distinct abort ID must remain unchanged across retries. It advances the
existing generic consumer abort before the provider abort, waiting for original
node operations and exact stopped generations. It starts no Apps and performs no
model discovery or credential preparation. Missing child journals require a node
ledger check proving those child operations never ran and their targets remain
unused; missing metadata alone is not evidence of process termination.

The coordinator captures exact model publications before teardown. It also
recognizes a registration/rebind that committed but lost its acknowledgement,
using the prepared provider identity, declared configuration and model choices.
Directory changes between pending advances stop further teardown. After the
provider abort completes, a revision-checked directory write publishes only its
verified stopped generation, retaining names, selections and metadata. Repeated
observation after a lost directory reply does not write another revision.
An existing unrelated/pending model operation, changed generation or conflicting
child recipe requires inspection. Abort records contain no model credentials.

The saved-profile host now invokes this primitive on an explicit stop during
incomplete startup; native Desktop exposes the same stop through its existing
owner pipe. Once cleanup and owner exit are confirmed, an explicit reopen starts
a fresh profile cycle, or a reviewed release rollback restores retained source
data. Cleanup itself never launches a replacement. Previously prepared budget
credentials are retained rather than revoked. Distributed fencing, abrupt owner
crash and disconnected-node recovery remain outside this local guarantee.

## Durable data format admission

Apps can declare an independent durable format in `app.json`:

```json
"dataSchema": {"id": "pantheon-agent", "version": 1, "accepts": [1]}
```

`version` is the format this release writes, not the code version. `accepts`
lists formats the App can safely open, including any App-owned migration it
actually implements before serving. The list must include its own written
version. Positive integer versions, bounded unique entries and exact fields are
required. A declaration alone does not implement migration.

The portable builder carries the identical contract into `fleet.json` as
`data_schema`. Artifact construction and the node's verified-manifest query
reject inconsistent declarations. Upgrade preparation requires the same format
identity and the source written version in the target's accepted list before
copying. The node repeats this check in `clone_data`, so a direct lifecycle
request cannot bypass it. Two legacy undeclared releases retain the old copy
behavior, with no format assurance. A one-sided declaration is rejected rather
than inferring the unknown source format. Adopting such an old artifact requires
an explicit migration workflow, which is not implemented here.

Installing a declared format raises the node's durable ledger to protocol 8;
older Runners cannot reopen it and silently drop the contract. The RPC envelope
remains protocol 1. This requires updated Fleet binaries, not just an updated
owner coordinator. Nodes without support reject the new execution field.

Agent now owns `agent-data-format.json` within its `agent` data directory. Under
the admission lock it checks the format ID, version and namespace before opening
its instance database. Invalid, future, linked, nonregular and oversized markers
are refused. A new or earlier unmarked extracted-Agent directory receives format
1 only after its existing migration barrier and instance database checks pass,
while holding the instance writer lock. The marker is atomically replaced and
synced. A stamp failure releases the instance writer. This names the current
layout; it does not perform an arbitrary legacy import or downgrade. Actual
format transformations, their interrupted recovery and publication remain open.

## Current limits

- This primitive requires a completed source deployment and an unused target
  revision/scope. Nodes and scopes are preserved; cross-node data transfer is not
  part of this workflow.
- Interface and declared data-format contracts are checked by the owner and
  node, and Agent admits its on-disk format before opening its instance database.
  There is no generic schema transformation hook yet; acceptance uses the same
  Agent data format across releases.
- Terminal startup failures and cancelled preparations can be aborted and rolled
  back. Unknown node operations, failed drain hooks and disconnected nodes still
  require their original Fleet recovery before this coordinator proceeds.
- The cancellation fence coordinates owners on one local filesystem. Distributed
  replica fencing and controller/node crash recovery remain separate acceptance.
- Updated Fleet nodes copy up to 64 GiB and 1,000,000 entries by default, with an
  owner-configurable policy and a disk-space preflight. Links, special files and
  directory trees deeper than 128 levels are rejected. Older nodes retain the
  64 MiB/10,000-entry behavior; a new owner coordinator alone does not change it.
- Publication, atomic route/default cutover, saved local-profile adoption and
  GUI upgrade review are not implemented by this API. Agent self-edit and
  distributed failure acceptance remain separate outstanding requirements.

## Evidence

`tests/test_app_deployment_upgrade.py` covers immutable intent resumption after
lost replies, stale generations, incompatible App identities/contracts, target
data conflicts, owner separation and private recipe access. Combined deployment,
stop, preview and real native fixture tests pass **91 tests** in 4.26 s
(`/tmp/agent-upgrade-retained-final-20261006.log`). Additional rollback rejection
cases cover changed source, candidate and shared-provider generations, remaining
source reservations, a running candidate and a foreign owner. They exposed and
fixed a policy-only shared-provider reference missing from rollback review.
The native fixture performs actual Fleet
copy/start/stop/rollback and verifies that the source and candidate writes remain
separate, then closes the owned infrastructure.

`tests/test_agent_app_upgrade.py` builds paired Agent 0.7.0 and 0.7.1 releases and
uses real local Fleet, the original Model Service Connector and Shell. It runs
the original conversation, stops the Agent/allocator/model-access group, copies
the Agent data, deploys the new release, checks exact history and stable logical
Agent identity, makes another call, stops and rolls back to the retained release,
and verifies the original history and a further tool call. The final run passes
in **89.22 s** (`/tmp/agent-paired-upgrade-retained-final-20261006.log`), including
new real Shell results after upgrade and rollback and unchanged shared Shell
and Model Service Connector generations. This is
total test duration, not startup latency. Model replies are deterministic local
fixtures; this is backend lifecycle acceptance, not rendered GUI interaction,
all General Team plugins, live providers or a production deployment.

Partial-start coverage in `tests/test_app_deployment_abort.py` includes failed
starts before/after consuming a preparation, in-flight install/prepare/start,
lost stop replies, owner observation cancellation, checkpoint failure, unknown
operations, stale generations, pending drains, continued shared providers and
rollback after an aborted candidate. The native fixture also runs a candidate
process with intentionally failing readiness, drains it and restores the old
release while retaining both data histories. The final combined suite passes **111
tests** in 7.93 s (`/tmp/app-abort-reviewed-final-20261006.log`).

The actual Agent gate now also packages an intentionally failing readiness probe
around the real backend health check. Both the healthy and failing candidate
scenarios pass in **185.64 s** total (`/tmp/agent-release-abort-20261006.log`). The
failed Agent retains process resources until the generic abort drains it; the
test verifies empty resources/reservations, rejection of the fenced old startup,
restored original history, a new real Shell result and unchanged shared providers.
The final failed-Agent-only run passes in **102.07 s**
(`/tmp/agent-release-abort-final-20261006.log`). The identity regression also
rejects a different instance ID at the otherwise matching revision/scope.

The format-admission increment passes 139 Python regressions
(`/tmp/agent-schema-regression-final-20261006.log`) and Fleet boundary/ledger tests
(`/tmp/agent-schema-go-final2-20261006.log`). With the rebuilt schema-aware Runner,
four native scenarios pass in 192.74 s (`/tmp/agent-schema-native-final-20261006.log`),
including real Agent marker/history/tool preservation on upgrade and rollback.
These tests use compatible Agent format 1 and deterministic model responses;
they do not prove a schema transformation, cloud deployment or startup benchmark.

## Large App-owned state

Fleet's existing node-owner `resource-policy.json` (in that owner's lifecycle
root, alongside `ledger.json`) now accepts a `state_copy` section:

```json
{
  "state_copy": {
    "max_bytes": 68719476736,
    "max_entries": 1000000,
    "reserve_bytes": 1073741824
  }
}
```

These are the defaults. Omitted/zero fields use those defaults; negative values,
more than 1 PiB of bytes/reserve or 10,000,000 entries are rejected at node startup.
The policy is loaded once when the node opens and its effective values are visible
in resource status. Apps cannot override it through their manifest or clone
request. Existing resource-policy fields remain valid.

Before copying file content, Fleet scans the stopped source and checks the entry
and logical-byte limits plus free disk space with the selected reserve. This is
a preflight, not a disk reservation: other processes, filesystem metadata and
quotas can still cause a later write failure. Files are streamed through one
256 KiB buffer; directories are read in batches of 128 rather than loading an
entire conversation directory into memory. Cancellation is checked between read
chunks. Copy detects file identity/size/mtime changes and changed aggregate tree
size/count; managed source writers stay stopped under the lifecycle lock. This
is not an atomic filesystem snapshot against external writers.

The receipt is written only after copying and syncing files. The existing outer
clone operation retains the old data, removes temporary copies on ordinary errors
or cancellation, and publishes the candidate directory by rename. A hard Runner
crash/unknown operation still requires explicit recovery; this change does not
add automatic cleanup or replay of those unknown operations.

Native Go tests cover a **112 MiB** history with hash equality and about **269 KiB
total Go allocation** during the copy, **10,005** conversation files, explicit
limits before any data writes, in-file cancellation without a success receipt,
links, disk-budget checks and policy validation. Evidence:
`/tmp/app-state-copy-large-20261006.log` and
`/tmp/app-state-copy-final-unit-20261006.log` (the first log's 104 MiB label was
corrected to 112 MiB; the actual data size was unchanged).

With the rebuilt native Fleet, `tests/test_app_upgrade_native.py` retains an
**85 MiB** attachment through both healthy upgrade and failed-candidate
abort/rollback, verifying hashes in both original and candidate directories.
The combined deployment suite passes **111 tests in 9.09 s**
(`/tmp/app-large-state-native-20261006.log`). Resource-admission regression also
passes (`/tmp/app-state-copy-resources-20261006.log`). These are local macOS
results; real long conversation UI behavior, representative user-data migration,
other operating systems and production rollout remain unverified.

## Saved local product release selection

`pantheon local-release` connects the ordinary copy/retained-data workflow to a
private `launch.json` produced by `pantheon local-setup`. Cleanly stop the profile
first. Review and approval temporarily run that profile's local Fleet owner but
do not start its Apps. Approval installs the selected revisions, prepares data,
validates the next ordinary deployment, and replaces the launch description last.
The immutable decision includes the original checkpoint, both compiled profiles
and both launch choices. Credentials remain in their existing separate file.

```sh
python -m pantheon local-release --launch /private/setup/launch.json \
  --target-bundle /products/new-release
# Inspect the result, then use its exact review_id:
python -m pantheon local-release --launch /private/setup/launch.json \
  --target-bundle /products/new-release --approve REVIEW_ID
python -m pantheon cli --launch /private/setup/launch.json
```

The CLI reads the adopted bundle from the saved description on each invocation.
It refuses profile/setup/credential overrides alongside `--launch`. After taking
the profile owner lock, it checks the description again before connecting or
starting Apps. The command uses the current explicitly invoked Pantheon runtime;
the description's `launcher` is retained for native Desktop use, not executed
indirectly by the CLI.

After cleanly stopping the candidate, rollback is a separate reviewed decision:

```sh
python -m pantheon local-release --launch /private/setup/launch.json \
  --rollback-of ORIGINAL_UPGRADE_REVIEW_ID
python -m pantheon local-release --launch /private/setup/launch.json \
  --rollback-of ORIGINAL_UPGRADE_REVIEW_ID --approve ROLLBACK_REVIEW_ID
python -m pantheon cli --launch /private/setup/launch.json
```

Rollback restores the original bundle and retained source data. Candidate writes
stay in their separate directory. A lost launch-write acknowledgement can be
retried using the same command and approval ID: both pre-rename and post-rename
states are recognized, without creating another data copy. Once Apps have run a
new cycle, an old approval cannot be reused. Node operations with unknown outcomes
are not replayed as fresh operations.

This release command preserves configuration, App identities/topology and model
publications. Configuration edits use `local-update`; model-provider revision
changes require their own deployment workflow. Compatible releases must use a
Runner that understands the saved ledger even when selecting an older App
bundle. A failed candidate can now be stopped through the saved-profile host and
then reviewed for retained-source rollback. It does not implement data-schema
transformations, abrupt-owner-crash recovery or automatic rollback. The native Desktop saves its
own launch description; updating another `launch.json` does not change that copy.
The paired native Desktop now exposes **Choose new version** after a clean stop,
shows the source/target bundles and copy/retained-data policy, and requires the
reviewed decision before applying it. After the approved version has run and
stopped, **Versions available for rollback** lists matching retained releases.
Selecting one performs a fresh review; it does not immediately restore data.
Installed-product acceptance remains pending.

### Interrupted native adoption

`local-release --launch /private/launch.json --status` starts no Fleet processes.
It reads bounded private receipt history and reports completed rollback choices
and unfinished launch adoption. The owner writes an applied receipt after saving
the launch choice; a missing applied receipt keeps the exact decision pending.
The Desktop checks this before autostart and offers **Resume version change**.
It cannot discard an uncertain change or start another configuration in that
state. An approval rejected before any durable decision can still be discarded.
The ordinary CLI also checks pending decisions under the local owner lock before
starting Apps, so the terminal cannot bypass an interrupted Desktop adoption.

Completed history follows matching copies of a launch description for the same
profile. Unfinished work must resume using its original launch file; selecting
another description for that profile does not hide the pending decision. Native
approval/status commands have bounded output, do not echo arbitrary stderr or
credentials, and wait for process completion instead of killing journal writes
on a timer. Remote Agent App frames cannot invoke these owner-only controls.

Desktop and the explicitly selected Python owner runtime must both include this
release protocol. The UI does not install or rewrite the user's Python runtime.
Rendered Chromium tests use controlled native IPC; the separate Rust-owner gate
uses actual paired Agent packages, public CLI commands and native Fleet. These
are not yet an installed macOS window-click or production rollout acceptance.

### Paired Agent coordinator-exit acceptance

The real paired Agent gate now repeats healthy upgrade/rollback and
failed-readiness/abort/rollback with a fresh owner process after every accepted
stop, clone, preparation and start submission. Each process exits before the
coordinator receives its receipt. Both scenarios pass (372.44 s total,
`/tmp/agent-paired-owner-exit-20261006.log`), with 22 exits per scenario, one
submission per durable operation, original conversation/logical identity
preserved and real Shell calls after rollback. Shared providers retain their
generations until final test cleanup. This covers local owner process death;
Runner death, distributed fencing, GUI interaction during the fault and data
format transformation remain outside this gate.
