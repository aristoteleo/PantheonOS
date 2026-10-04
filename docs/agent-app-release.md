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

The same composition is available as a file command:

```sh
python -m pantheon.chatroom.deployment --input /path/to/composition.json \
  --output /path/to/candidate-deployment.json
```

The new output is private (mode 0600) and never overwrites an existing intent.
It contains `owner`, `operation_id`, `apps`: pass those to `fleet_app_deploy` with
`action: advance`. Stage all three artifacts on their chosen nodes first.
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
actual `compose_deployment` / `AppDeployment` recipe. A fourth native process is
the existing `apps/model-service` connector, configured against a deterministic
local engine. No production node/account or existing Agent data is contacted.

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
checks four distinct native processes, exact prepared generations, idempotent
re-advancement, authorized catalog selection, a complete streamed conversation,
history persistence, unavailable-provider catalog removal, exactly one engine
call, and clean lifecycle stops with no remaining owned resources.

Authenticated NATS, native lifecycle/configuration/vault handling, dependency
issuance and per-call checks, the WebSocket byte relay, Model Services access,
Connector and Agent runtime are real. Hub's auth/directory wrapper and engine
output are fixtures. A process-local DNS/socket mapping sends `*.apps.test:443`
to a random loopback TLS port; the test does not modify hosts files, use a
privileged port or disable certificate verification. The node rendezvous handler
is a test adapter to real Manager service/use checks and `apptransport.Relay`;
it is not an enrolled production Runner. Owner packages receive an explicit
fixture trust root in prepared configuration.

This establishes the local combined startup/call path, not production Hub/Atrium
provisioning, real model quality/GPU performance, remote-node networking, full
tool/plugin composition or migration/cutover readiness.
