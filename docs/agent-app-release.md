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

The full owner bootstrap recipe and production cutover remain pending. Do not
replace the legacy Agent manifest or point its data store at a candidate before
the migration/rollback gates pass.

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
