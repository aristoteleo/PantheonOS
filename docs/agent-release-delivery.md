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

Delivery alone is not readiness or first-run setup. Credential acquisition and
renewal, GUI creation of the initial configuration, Store publication, migration
and switching the default deployed Agent remain separate required work. In
particular, a short-lived Fleet session token must not be treated as a permanent
node credential. This command does not move legacy CLI/Desktop data or change
the active Atrium deployment.
