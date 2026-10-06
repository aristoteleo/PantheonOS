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

After the candidate **completed startup and was cleanly stopped**, call
`fleet_app_upgrade_rollback_plan` with the candidate preparation ID and a new
rollback deployment ID. The owner must present these returned policies before
submitting the recipe:

- `data_policy: retained-source-data`: the old release reopens its data as it was
  before the upgrade; it does not open the candidate's potentially newer schema.
- `candidate_writes: retained-separately`: new conversations and other candidate
  writes stay in its data directory. They are neither discarded nor merged into
  the old release's data.

The plan checks that the completed candidate is the exact prepared recipe and
that the original data instance is still at its retained stopped generation.
Unchanged members of the selected group restart through the same ordinary
deployment with newly resolved bindings. Shared providers stay pinned. Planning
does not start Apps, publish a route, or update a CLI/Desktop launch profile.

## Current limits

- This primitive requires a completed source deployment and an unused target
  revision/scope. Nodes and scopes are preserved; cross-node data transfer is not
  part of this workflow.
- Contracts are checked, but there is no schema migration hook/admission yet.
  Current acceptance uses data-compatible releases.
- Failed or partial candidate startup still needs deployment recovery. The
  rollback planner refuses it rather than assuming a timeout means stopped.
- Fleet's current clone bounds are 64 MiB and 10,000 entries; links and special
  files are rejected. Large conversation stores need a larger-state design and
  acceptance before this can serve all existing users.
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
