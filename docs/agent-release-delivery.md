# Building and delivering Agent App releases

The Agent frontend and backend remain one ordinary versioned App. Its allocation
and Model Services access dependencies remain separate ordinary Apps. A release
set is only a local distribution index for their exact Fleet artifact digests;
it does not replace Store Git history, App manifests or deployment journals.
It contains code and relative package paths, never owner settings or credentials.

Build the GUI with the same version as the Agent release, using `build:agent-app`
in the UI repository. Build `fleet-app-transport` for each selected target OS and
architecture from this repository's `fleet` directory. Then run:

```sh
python -m pantheon.chatroom.release \
  --output /path/to/new-release-set \
  --version 0.7.0 \
  --frontend /path/to/built-agent-gui \
  --transport darwin-arm64=/path/to/mac-fleet-app-transport \
  --transport linux-amd64=/path/to/linux-fleet-app-transport \
  --dependencies /path/to/additional-app-declarations.json
```

The output contains `release-set.json` and one directory per platform, each with
`agent`, `allocator` and `model-access` App packages. Additional dependency
declarations and credential alias declarations (`--credential`, repeatable) are
passed to the existing Agent package builder unchanged. Configuration is still
provided at deployment; this command does not disable plugins or select models.
The existing single-App `pantheon.chatroom.package` command is unchanged.

Optional `--provider name=/path/to/app` includes an existing ordinary App using
the standard portable adapter or its declared native platform manifests. A
provider compiled for only one platform can only be used in a matching set.
Existing deployed Model Service Connectors need not be included or restarted.
For newly prepared Connectors, use the original `pantheon.models.connector_package`
builder and register them through the existing Model Services bootstrap.

Choose target nodes explicitly in a placement file:

```json
{
  "agent": {"node_id":"my-mac", "platform":"darwin-arm64", "scope":"agent-candidate", "generation":0},
  "allocator": {"node_id":"my-linux", "platform":"linux-amd64", "scope":"agent-allocator", "generation":0},
  "model-access": {"node_id":"my-linux", "platform":"linux-amd64", "scope":"agent-model-access", "generation":0}
}
```

In the authenticated owner platform environment, stage these releases:

```sh
python -m pantheon.apps.release_set \
  --release-set /path/to/new-release-set \
  --placements /path/to/placements.json \
  --owner YOUR_FLEET_OWNER \
  --output /path/to/new-targets.json
```

This command uses the existing Fleet connection and exact-artifact upload. It
does not install, start, stop or grant access. All selected files/digests are
validated before any network request; all node owners/platforms are checked
before upload. One verified artifact at a time is uploaded from a private
temporary spool. Changed code or a missing platform is an error, not a fallback.
Retries replay the same byte offsets. Already installed digests are skipped
using a fresh node snapshot, so delivery does not repeat installation hooks.

The private output is the existing Agent composer's `targets` map, with actual
artifact digests filled in. Feed it into `model_services_agent_preset` together
with the explicit Agent/project/tool configuration and node-vault references.
Select existing `fleet-model://` or `fleet-route://` models there, review access,
then save the resulting ordinary recipe using the startup preset API. A staged
but uninstalled release is intentionally not accepted by the read-only installed
target checker; installation is performed by the ordinary deployment coordinator.

For long-running allocator/model-access hosts, provision a dedicated revocable
platform key through [owner control credentials](owner-control-credentials.md).
The command returns existing endpoint-bound node references for the composer;
Agent itself receives only narrowed dependency grants.

To create the first preset in Fleet's **Startup apps** panel, prepare a setup
file from the delivery targets, provisioned control references and complete Agent
profile:

```sh
python -m pantheon.apps.agent_setup \
  --targets /path/to/new-targets.json \
  --profile /path/to/complete-agent-profile.json \
  --control-credentials /path/to/new-control-references.json \
  --operation-id initial-agent-review \
  --output /path/to/new-agent-setup.json
```

The profile contains the existing composer's `agent` configuration and `tools`
policies. Optional `agent_credentials`, `extra_bindings` and `provider_apps` are
preserved. Include the complete project snapshot, enabled plugin settings,
tool/MCP profiles and required dependencies; this command never generates a
reduced profile or disables a feature to make startup pass. Targets must name
`agent`, `allocator` and `model-access` exactly. Additional provider targets and
configuration belong in `provider_apps`, so they are not silently dropped.
Control references are matched to each control App's exact node.

Import this file with **Import Agent setup or preset**. The panel reads current
Fleet nodes and their installed releases. For an original prepared target whose
artifact has only been staged, use **Install prepared … release**. This explicit
action invokes ordinary Fleet installation, not startup; refresh its progress.
An installed artifact is reused. Pending/failed/interrupted operations retain
their original ledger intent; inspect recovery in Fleet rather than silently
creating a new installation attempt. A lost reply can be retried with that same
intent after checking node status.

Choose nodes, exact installed releases and candidate scopes in **App targets**.
Edited targets use generation zero and must not already exist; this is new
candidate placement, not adoption/migration of an existing App. An unchanged
prepared stopped target retains its expected generation. To place allocator or
Model Service access on another node, import the output of
`pantheon.platform.owner_credentials` with **Control references**. The UI selects
references for the exact destination. Agent profiles with their own node-local
provider keys require a newly provisioned setup before moving that Agent; the
UI will not transplant their references. Project paths, tool policies and
additional providers remain unchanged, so prepare destination data separately.

Select the published models/routes for each desired quality tier, then **Review configuration**.
The existing `model_services_agent_preset` API supplies a generic recipe and
service-wide access review. **Check deployment targets** must pass before saving
a new setup using the existing revision-checked startup API. Edits invalidate
the previous review. Initial setup import, model selection
and save do not start Apps or migrate old data; only future enabled startup uses
the saved recipe. An existing preset still uses its canonical update path.

The file preparation is currently an owner/operator step. GUI release publication
and delivery, credential acquisition, complete profile/dependency authoring and
the migration/cutover workflow still need implementation before first-run setup
is fully automatic.

Delivery alone is not readiness or first-run setup. Credential acquisition and
rotation, GUI creation of the initial configuration, Store publication, migration
and switching the default deployed Agent remain separate required work. In
particular, a short-lived Fleet session token must not be treated as a permanent
node credential. This command does not move legacy CLI/Desktop data or change
the active Atrium deployment.
