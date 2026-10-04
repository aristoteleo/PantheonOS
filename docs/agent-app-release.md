# Building the independent Agent App

This is the candidate release path for Agent extraction, not the default Atrium
Agent. It keeps the existing Desktop and CLI packages available during migration.
Build from the matching runtime and UI source revisions; do not install a source
checkout into the App's Python environment.

## Saved conversations moving to Model Services

An owner migration may supply `model_selection=ModelSelectionConversion(...)`
to `import_backup`. It is bound to the same verified backup digest and live
legacy writer fence. For example, after inventory, backup and an explicit model
choice for every saved member:

```python
from pantheon.chatroom.migration_models import ModelSelectionConversion

selection = ModelSelectionConversion(
    backup_directory, digest=backup_digest, fence=fence,
    owner=target_fleet_id, node_id=target_agent_node,
    dependency="model_services",
    fleet_tiers={
        "normal": "fleet-route://normal",
        "high": "fleet-route://high",
        "low": "fleet-route://low",
    },
    selections=[{
        "conversation_id": "existing-chat-id",
        "config_id": "existing-saved-member-id",
        "source": "openai/previous-model+think:high",
        "target": "fleet-route://chosen-model+think:high",
    }],
    templates=[{
        "path": "/absolute/legacy/.pantheon/agents/researcher.md",
        "config_id": "researcher",
        "source": "openai/previous-model+think:high",
        "target": "fleet-route://chosen-model+think:high",
    }],
    settings=[{
        "path": "/absolute/legacy/.pantheon/settings.json",
        "field": ["memory_system", "selection_model"],
        "source": "openai/previous-helper",
        "target": "fleet-route://memory-helper",
    }],
)
receipt = import_backup(backup_directory, digest=backup_digest, fence=fence,
                        model_selection=selection)
```

The selection list must be expanded to cover **every** saved member (an empty
list is valid for an empty history). Source values
must match the backup exactly. A fallback list requires an equally sized target
list with an explicit reference and matching reasoning effort at each position.
Only the member's model field changes; histories, instructions, tools, config IDs
and deterministic migrated instance IDs are preserved. The source remains fenced
and unchanged. Repeating the same import resumes it; changing the mapping requires
a different migration, not overwriting partial target data.

Use `selection.describe()["models"]` for the prepared Agent model configuration
and supply its ordinary Model Services dependency credential through the existing
deployment assembly. The persisted binding pins that configuration and Agent
placement. The independently packaged runtime verifies the committed binding and
the `migration-model-selections.json` digest before opening the data namespace.
The converter itself is not shipped in the Agent App.

`templates` maps scalar model declarations in imported project/global `agents`
and `teams` Markdown libraries. It uses the original absolute source path and the
actual member `id`; for an inline team member without an explicit `id`, use its
entry name. All direct model selections require a mapping. Existing Fleet
references are validated and retained; quality/capability selectors retain their
meaning through the pinned `fleet_tiers`. Empty/omitted model declarations retain
their original inheritance semantics. Stale, duplicated or unused mappings fail
before creating destination data or provisioning keys.

Only YAML model and team path-reference scalar tokens change. Prompt bodies,
comments, line endings and tool declarations remain byte-identical. Relative
library references stay byte-identical when their destination survives relocation. An
interrupted import pins these mappings in the same audit as saved conversations.
The conversion is bounded, processes one file at a time and does not execute
template instructions. Non-YAML frontmatter, aliases/merge keys, duplicate fields
and unsupported model field shapes require explicit conversion before import.
Ordinary independent Agent startup now preserves all owned template files,
including names also shipped by the factory; legacy bootstrap reclaim/retirement
is not run for `TemplateManager(seed_settings=False)`. Factory defaults remain
available through the existing layered lookup. CLI/Desktop bootstrap defaults
are unchanged.

Team `agents` path references are relocated into the imported library even when
no model-selection conversion is requested. Absolute paths point to the new
App-owned file; cross-root relative paths are recomputed. ID references and
inline definitions retain their original lookup behavior. Unbacked path references
fail preflight before destination creation; the importer never fetches additional
files just because template content names them.

For external Agent/Team Markdown libraries, add `agent_libraries` to the original
inventory/fence/backup spec **before** taking the snapshot:

```json
{"agent_libraries": ["/absolute/shared-agent-recipes"]}
```

The optional list names at most 128 existing, nonoverlapping directories outside
the other migration roots. All Markdown files under each directory are included
in the same backup, source-consistency checks and cooperative local fence. Each
library is imported under its own `configuration/.pantheon/agents/_imported/`
namespace, so equal filenames do not overwrite each other or existing project
templates. Team path references resolve to those copies. Model mappings still use
the original source path and member ID. Symlinks and non-template files need
explicit conversion; this is not an arbitrary directory copy. External writers
must still be stopped separately because cooperative fences do not exclude editors
or old binaries. Prompt-body includes and their parameter/asset paths are a
separate, unfinished migration scope; this feature closes team-to-Agent paths only.

`settings` maps explicitly configured plugin text models by the original source
file and a two-element field path. Supported fields are
`context_compression.compression_model`, `memory_system.selection_model`,
`memory_system.flush_model`, `memory_system.dream_model`, `learning_system.model`
and `learning_system.extract_model`. Project and global settings are converted
independently, preserving their layer precedence. Direct selectors require exact
mappings even when their plugin is disabled or a higher layer overrides them.
This prevents an old direct-provider selector from returning when configuration
changes. Null, empty and `auto` values retain inheritance; quality tags use the
explicit Fleet tiers. Existing Fleet references are validated and retained.
Only model fields change, including their original reasoning effort. Plugin
enablement, thresholds and all other retained preferences are unchanged. Unknown,
stale, duplicate and unconsumed mappings fail preflight before target creation or
vault writes; the mapping audit also prevents resuming with different choices.

Optional `model_credentials=ModelCredentialConversion(...)` still consumes and
provisions legacy API keys to the existing Fleet vault. With model selection
conversion, that credential descriptor is retained only as receipt provenance;
its keys/provider aliases must **not** be inserted into Agent configuration. The
owner must configure the actual Connector with those node-secret references using
ordinary Model Services. Credential and selection owners must match; the provider
node and Agent node may be separately chosen. Actual cross-node deployment is a
separate acceptance gate.

This API handles saved conversations, explicit default quality tiers and the
YAML template declarations above. It does not infer or publish an equivalent
model, validate paid-provider behavior, migrate vision/image-generation provider
preferences or arbitrary third-party plugin selectors, or rewrite prompt-body
includes and asset paths. Validate
the intended catalog/capabilities and consumer policy before live cutover. A
missing/unavailable model fails through the existing App model validation instead
of falling back to an ambient API key. Complete template/configuration migration,
budget enabled-state/OAuth conversion and distributed cutover remain pending.

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
absolute effective-key source (`settings.json`, launch dotenv or the explicit runtime
handoff below), target credential `alias`, exact API `endpoint`,
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
does not yet convert unexported runtime state, OAuth, platform-budget credentials
or arbitrary MCP configuration, nor perform production cutover.

The source inventory includes `<project_config>/../.env` by default. If user or
project settings select another `env_file`, supply its absolute path as
`environment_file` in the migration source spec. Import checks that choice against
the backed-up settings; it never reads an unrecorded file or uses the migrator's
environment to expand variables. Keys/bases retain dotenv > project > user
precedence, including empty-value fallback. Unsupported environment fields remain
an explicit conversion error. The original dotenv is retained only in the private
backup; the prepared App gets vault references. Recreate older backups that did
not inventory the launch environment file before attempting import.

For a running legacy Agent, first freeze configuration changes and invoke its
owner RPC `export_model_migration_handoff(operation_id)` with a new stable operation
ID. It writes an owner-private model environment snapshot beneath
`<user_home>/fleet-node/agent-migration/handoffs/`; the response contains only
the canonical path and source roots, never key values. Add the returned `source`
as `model_environment_file` in the migration source spec. Then stop/exclude old
writers, acquire the existing migration fence and take a new private backup.
The export is not a fence: do not change source settings between export and
backup, and re-export under a new operation ID if they change.

With this input, credential conversion uses the whole captured runtime environment
before project/user settings, including legacy key aliases. Explicitly absent or
empty fields retain Settings fallback behavior without resurrecting the launch
dotenv's old values. The migrator never reads its own environment for keys. The
handoff stays in the private backup, is not copied into the Agent App, and source
changes invalidate import. Nonempty fallback, platform-budget and local Ollama
fields are recorded but still require their own conversion; they cannot silently
be discarded by the provider-key converter. OAuth sessions and in-memory settings
changes outside this environment snapshot remain separate migration work.

To make the migrated Agent consume Model Services rather than retain direct BYOK,
also provide the existing `ModelSelectionConversion` with explicit saved-member,
template/plugin and quality-tier mappings. The combined receipt puts provider
vault bindings under `model_bindings.provisioning`; use them for the original
Model Service Connector. The Agent's `models` use published Fleet references and
its model dependency grant; provider API keys are not part of its configuration.

Preserve the provider's actual base path in these references. In particular,
an explicit root URL must not acquire an implicit `/v1` in the migrator or generic
Fleet preparation; the native adapter determines its protocol path. Updated Fleet
preparation preserves that distinction while using the same vault records. An
older node that rewrites the endpoint will fail the migrated Agent's binding
admission check and needs updating before starting that candidate.

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
