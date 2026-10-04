# Building the independent Agent App

This is the candidate release path for Agent extraction, not the default Atrium
Agent. It keeps the existing Desktop and CLI packages available during migration.
Build from the matching runtime and UI source revisions; do not install a source
checkout into the App's Python environment.

## Build inputs

In the UI repository, build the ordinary App frontend with an explicit version:

```sh
AGENT_APP_VERSION=0.7.0 AGENT_APP_REVISION=$(git rev-parse HEAD) \
  AGENT_APP_BUILD_DIR=/tmp/agent-frontend pnpm build:agent-app
```

In the runtime repository's `fleet` directory, build the workload-only transport
for the selected node platform. This uses the same implementation as Fleet's
`app-dial` and `app-session`; it has no node management commands:

```sh
CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -trimpath -ldflags='-s -w' \
  -o /tmp/fleet-app-transport ./cmd/fleet-app-transport
```

Use `linux`/`darwin` and `amd64`/`arm64` for another target. The release builder
checks the executable's actual target and the GUI report's version. Windows is
not supported by the Agent's durable owner locks yet.

From the runtime repository, using its development Python environment:

```sh
python -m pantheon.chatroom.package \
  --output /tmp/agent-0.7.0 --platform darwin-arm64 --version 0.7.0 \
  --frontend /tmp/agent-frontend --transport /tmp/fleet-app-transport
```

The destination must not exist. `release.json` inventories frontend, backend,
host, requirements and transport hashes. Actual deployment uses the immutable
artifact digest returned by `pantheon.apps.lifecycle.build_artifact`, not the
directory name or `release.json` alone. The builder checks that the output can be
encoded by that ordinary App protocol before publishing its destination.

Pass `--dependencies /path/to/dependencies.json` for the App interfaces this
release can consume beyond the two required startup services. For example:

```json
{
  "shell": {"range": "^0.6.0", "uses": ["shell@1"], "binding": "runtime"}
}
```

Runtime declarations allow per-Agent-instance allocation; they do not share one
Shell session across conversations. GUI/plugin services needed at startup use
ordinary startup declarations and matching declared credential aliases. Provider
code is never copied into the Agent package. The installed manifest remains the
authority for interface/version validation, not a mutable configuration profile.

Python requirements ship from `pantheon/chatroom/package-requirements.lock`,
including transitive pins and hashes. To deliberately update that lock:

```sh
uv pip compile pantheon/chatroom/package-requirements.txt --universal \
  --python-version 3.11 --generate-hashes --no-header \
  --output-file pantheon/chatroom/package-requirements.lock
```

The generic Fleet installer creates/caches the environment; do not copy a venv
from the build machine. Code-only upgrades retain the same requirements cache.

## Configuration and delivery

The manifest declares `dependency-binding@1` and `model-inference@1`. The backend
requires the prepared `agent` value and scoped `allocator`/`model_services`
credentials. Optional default aliases are `platform_budget`, `provider` and
`files`; repeat `--credential` to supply the complete alias set for another
composition (including the two required aliases). Values carry credential
aliases, never provider keys. Consumer generations and bound policy IDs must
come from the generic dependency deployment coordinator.

Use the ordinary `build_artifact` and `FleetLifecycle.stage_exact` workflow.
Large releases use deterministic gzip with a 32 MiB wire limit and 128 MiB
decoded-stream limit. Fleet must report `artifact_compression: gzip-v1`; old
nodes get an explicit upgrade error before upload. No App-specific node loader
or relaxation of archive path/identity validation is introduced.

The production owner bootstrap integration and cutover remain pending. Do not
replace the legacy Agent manifest or point its data store at a candidate before
the migration/rollback gates pass.

For the candidate's data migration, use the Agent-owned directory beneath the
host state root (`<state_dir>/agent`), with the exact namespace in the prepared
`agent` value. The parent state directory also holds the generic host's endpoint
and workspace bookkeeping. Importing into that parent does not migrate the
Agent's histories. The importer and runtime share a startup admission barrier:
the native package cannot open an importing/aborted or malformed migration.
See [validated import](agent-app-extraction.md#validated-data-import-and-startup-admission)
for the supported source data and remaining cutover requirements.

For explicitly inventoried model API keys, the owner-side migration can supply a
`ModelCredentialConversion` to `import_backup`. Each binding names `provider`,
absolute source `settings.json`, target credential `alias`, exact API `endpoint`,
and `node-secret://` `ref`. A `LocalModelCredentialVault` selects the local Fleet
executable, state directory, owner and persisted node ID. The CLI must include
`credentials ensure` from this revision; older binaries fail without importing
the data. Do not pass key values in command arguments or deployment recipes.

After conversion, use `receipt['model_bindings']['models']` as the prepared Agent
model configuration and `receipt['model_bindings']['credentials']` as its
endpoint-paired vault references. Ordinary Fleet preparation resolves those
references into the private runtime snapshot. Startup checks the migration's
owner/node and provider/alias/endpoint mapping. The independent package contains
only this admission check, not the backup reader or credential converter. This
does not yet convert environment overrides, OAuth, platform-budget credentials
or arbitrary MCP configuration, nor perform production cutover.

## Compose a candidate deployment

`pantheon.chatroom.deployment.compose_deployment` is an owner-side preset for
the existing generic `fleet_app_deploy` API. It does not add an Agent-specific
node command or start another lifecycle loop. It takes these explicit inputs:

| Input | Contents |
| --- | --- |
| `owner`, `operation_id` | Fleet owner and stable deployment operation ID |
| `targets` | `agent`, `allocator`, `model-access`, each with exact `node_id`, staged artifact `revision`, `scope`, and stopped `generation` (zero for a new candidate) |
| `agent` | Prepared Agent value: protocol, private namespace, projects, settings, model sources, dependency profiles and optional auxiliary/view bindings |
| `tools` | Approved alias-to-provider policies for runtime tool allocation, including resource-session policy when needed |
| `models` | Authorized connector bindings, route revisions and `allow_wake`; these refer to the existing Model Services directory |
| `credentials` | Node-vault references by target: allocator needs Hub and Controller; model-access needs Hub; Agent gets only its explicit provider/budget credentials |
| `extra_bindings` | Optional startup GUI/plugin dependency grants with exact provider bindings |

The Agent's `dependencies.allocator` must be `allocator`; its profile aliases
must match `tools`. The preset supplies `models.model_services` and binds the
two gateway policy IDs. Empty tool/model policies are supported for initial
BYOK/budget-only setups and authorize no tool allocation or Fleet inference.

For a Model Services-based Agent, also specify the quality tiers used by normal
templates and scoped auxiliary calls in the prepared `agent.models` value:

```json
{
  "model_services": "model_services",
  "fleet_tiers": {
    "normal": "fleet-route://agent-default",
    "high": "fleet-route://agent-high",
    "low": "fleet-model://local-small/example%3A8b"
  }
}
```

These are explicit owner choices, not rankings inferred from catalog order. The
corresponding connectors/routes must also be authorized by the `models` policy.
A tier resolves only after its model appears as usable in the authorized catalog;
missing tiers, unavailable models and unsupported capability combinations (for
example `normal,vision`) fail rather than falling through to BYOK or a different
node. Route fallback stays in Model Services. With no `fleet_tiers`, the existing
local provider/tag selection remains unchanged. Explicit BYOK model IDs remain
available when separately bound. `list_available_models` includes `fleet_tiers`
so a client can inspect the effective deployment mapping.

The same composition is available as a file command:

```sh
python -m pantheon.chatroom.deployment --input /path/to/composition.json \
  --output /path/to/candidate-deployment.json
```

The new output is private (mode 0600) and never overwrites an existing intent.
It contains `owner`, `operation_id`, `apps`: pass those to `fleet_app_deploy` with
`action: advance`. Stage all three artifacts on their chosen nodes first.

For explicit platform startup, the same recipe can be supplied with
`python -m pantheon.platform --deployment-id USER --app-preset /private/startup.json`
or deployment-injected `PANTHEON_APP_PRESET`. This is opt-in; no default recipe,
Agent child or node is inferred. The platform starts serving independently while
the existing coordinator advances the preset in the background. The recipe file
must be private to its OS owner, regular, bounded to 64 KiB and not a symlink.
Artifacts, node-vault references, Fleet credentials and persistent owner journal
storage must be provisioned before launch. Do not put provider keys in the file.

`platform_app_preset_status` reports a redacted last-checkpoint state separately
from platform readiness and live App health. Startup is bounded to 30 minutes
between advancement calls. Failure or an unknown outcome stops automatic
advancement; use `fleet_app_deploy` with the original operation ID to inspect and
recover. Platform restart validates the same recipe against its existing journal.
An App deliberately stopped afterward is not automatically respawned. Shutdown
drains the accepted advancement before the final platform snapshot; it does not
stop the deployed Apps. Durable storage and the existing grant maintenance policy
still apply. Production provisioning, artifact staging, data migration and the
default Atrium cutover remain pending.

Hub-managed startup can instead use `--app-preset-url` or
`PANTHEON_APP_PRESET_URL=https://HUB/api/fleet/apps/startup/PROFILE`. This requires
the existing `PANTHEON_HUB_URL`, `FLEET_KEY` and user identity (`USER_ID`, otherwise
`--deployment-id`). The URL must belong to the paired HTTPS Hub; responses cannot
redirect the credential. Only one bounded startup read is performed. The returned
owner must match the platform's derived Fleet owner. Null recipes disable startup;
failed reads leave platform RPCs available with a redacted `preset_unavailable`
status. File and URL sources cannot be combined.

In Hub, enable `PANTHEON_APP_PRESETS_ENABLED=true` and use an independent
`platform` topology to inject this URL into new K8s/Modal hosts. Save the ordinary
recipe with an authenticated owner `PUT /api/fleet/apps/startup/PROFILE` containing
`{"revision": CURRENT_REVISION, "recipe": RECIPE}`. Read the current revision with
GET first (an absent row has revision 0). Concurrent saves yield one winner and
409 for stale updates. Use a new deployment operation ID for changed intent.
`recipe: null` disables future startup without stopping current Apps. Do not put
secrets in component values; credential slots accept node-vault references only.
This API does not stage artifacts, import Agent data or restart existing hosts.

Subsequent advances omit `apps` and retain the same operation ID. The generic
coordinator installs/prepares the Apps, resolves policies to the Agent's exact
upcoming generation, starts allocator/model-access, then starts Agent. It never
copies owner credentials into consumer grants. Deployment readiness alone does
not establish that every selected external model/tool provider is available.

The preset emits a candidate recipe only. Automatic Hub/Atrium provisioning,
full configured plugin coverage and real-node cutover acceptance remain open.

## Local release acceptance

Create a clean environment with only the release requirements:

```sh
uv venv --python 3.12 /tmp/agent-release-python
uv pip sync --python /tmp/agent-release-python/bin/python --require-hashes \
  pantheon/chatroom/package-requirements.lock
AGENT_RELEASE_PYTHON=/tmp/agent-release-python/bin/python \
AGENT_RELEASE_TRANSPORT=/tmp/fleet-app-transport \
AGENT_APP_BUILD_DIR=/tmp/agent-frontend \
PANTHEON_AGENT_FRONTEND_SCRIPT=/absolute/path/to/ui/scripts/test-agent-frontend.mjs \
  python -m pytest tests/test_agent_release.py -q
```

The parent pytest process uses the development environment; child App processes
use only the clean environment and their shipped files. The test rejects an
installed Pantheon package and guards against importing the legacy ChatRoom,
platform host or NATS. GUI acceptance is skipped if the script is not supplied;
that skip does not establish a GUI release gate.

To test actual Fleet installation, save `build_artifact`'s bytes to an artifact
file and run from `fleet`:

```sh
FLEET_TEST_AGENT_ARTIFACT=/absolute/path/to/agent-artifact \
  go test ./internal/lifecycle -run '^TestInstallAgentReleaseArtifact$' -count=1 -v
```

This uses an isolated node and the production native driver. An optional
`FLEET_TEST_AGENT_CACHE` must be an absolute, private, user-owned directory.
This gate covers staged upload, extraction, real install hooks and immutable
manifest lookup. It does not claim production Agent/Hub bootstrap, GPU inference,
Linux/Windows runtime acceptance, migration or deployed Desktop compatibility.


## Joint native deployment acceptance

The opt-in Controller gate builds the paired release and both owner-service
packages, stages them on two isolated native Fleet Managers, and advances the
actual `compose_deployment` / `AppDeployment` recipe. Two additional native
processes are the existing `apps/model-service` connector, configured against a
deterministic local engine, and the managed Shell App. No production node/account
or existing Agent data is contacted.

From `fleet`, with `nats-server`, Go, Python and the release build inputs available:

```sh
FLEET_TEST_AGENT_DEPLOYMENT=1 \
FLEET_TEST_PYTHON=/absolute/path/to/development/python \
AGENT_RELEASE_TRANSPORT=/absolute/path/to/fleet-app-transport \
AGENT_APP_BUILD_DIR=/absolute/path/to/agent-frontend \
  go test -race ./cmd/fleet-controller \
  -run '^TestDependencyRPCOverAuthenticatedNATSAndNativeApps$' -count=1 -v -timeout=10m
```

`FLEET_TEST_PYTHON` runs the owner-side coordinator; each packaged App uses the
ordinary install hook and its own requirements environment. An optional
`FLEET_TEST_AGENT_CACHE` selects a private persistent dependency cache. The gate
checks six distinct native processes, exact prepared generations, idempotent
re-advancement, authorized catalog selection, a complete streamed conversation,
history persistence and unavailable-provider catalog removal. Two conversations
instantiate separate logical Agents through the same allocator and Shell App.
The fixture model emits real tool calls: Agent A sets an environment variable,
Agent B cannot see it, and Agent A reads it back on a later turn. Agent A also
writes a project file through the shared Files provider; B reads the same file
before and after A is deleted. All fifteen
inference rounds pass through the existing Connector; final tool outputs are
checked from complete, digest-verified history snapshots rather than searching
for a value that might exist only in older history.

The gate deletes Agent A's conversation while the deployment remains active,
checks its provider session is released, rejects new turns for the deleted chat,
and verifies Agent B can still call Shell and Files. Repeated deletion is idempotent. After
stopping the Agent App, it keeps the allocator, shared Shell and Files alive and checks
both grant revocation and the remaining provider's released-session receipt
before stopping those services. This verifies logical-owner retirement as well
as whole-consumer cleanup independently of provider shutdown. Files still serves
the same project content after Agent shutdown; only Shell has per-owner sessions.

The allocator package is v0.1.1; the Agent requires that version for the scoped
`retire_dependencies` method. Deletion closes admission, drains accepted work,
revokes all revision grants and releases the owner's sessions before removing
history. Pending release retains history for retry; terminal provider loss is
reported separately from confirmed cleanup. Instance schema 2 persists the
retirement fence across restarts and migrates existing schema 1 identities.

Authenticated NATS, native lifecycle/configuration/vault handling, dependency
issuance and per-call checks, the WebSocket byte relay, Model Services access,
Connector and Agent runtime are real. Startup fetches the composed recipe through
the paired TLS Hub fixture before launching Apps. Hub's auth/directory wrapper and engine
output are fixtures. A process-local DNS/socket mapping sends `*.apps.test:443`
to a random loopback TLS port; the test does not modify hosts files, use a
privileged port or disable certificate verification. The node rendezvous handler
is a test adapter to real Manager service/use checks and `apptransport.Relay`;
it is not an enrolled production Runner. Owner packages receive an explicit
fixture trust root in prepared configuration.

This establishes the local combined startup/call path, not production Hub/Atrium
provisioning, real model quality/GPU performance, remote-node networking, full
tool/plugin composition or migration/cutover readiness.

### Optional shared provider installation

The owner-side `compose_deployment` input accepts `provider_apps`, a map of ordinary
staged deployment targets (`node_id`, `revision`, `scope`, `generation`,
`components`, `bindings`). These cannot replace `agent`, `allocator` or
`model-access`. Policies can refer to them with the existing `$app` binding form;
the generic coordinator prepares and resolves exact generations. This is used by
the six-process gate to supply `file-manager` from `apps/file/build_managed.py`.
See that App's README for its filesystem-only candidate surface and configuration.
Legacy preview, transfer, document and model-assisted tools are not yet included
in this candidate and remain a required gate before replacing the shipped Files
service. The native test uses a filename-bound read/write grant, not an unrestricted
workspace path grant.
