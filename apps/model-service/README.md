# Model Service Connector

Connector 0.1.4 keeps explicit cancellation distinct from a successful response:
closing the upstream socket to cancel JSON/embedding work cannot turn its EOF
into a `completed` activity record. Existing engine and model files are reused.

Connector 0.1.3 supports explicit managed engine updates. Prepare the selected
recipe, then choose **Update engine** in Model Services. The coordinator pins
the source, target artifact and first start operation in Hub; it drains model
calls/jobs, stops only the owned engine, and verifies the replacement before
publishing its new binding. Context, budgets, model identity and files stay in
the same node and scope. Active model memory must warm again after restart.
**Resume engine update** continues that exact operation after a lost response
or Agent restart. A failed start is not automatically replayed or switched to
another engine/cloud route. Inspect Fleet logs before recovery of a terminal
failure; abort/replan UI is not implemented yet. External attached engines are
never upgraded. Ordinary restarts reuse the installed artifact, independently
of newer Agent packaging code.

Opt-in native acceptance (verified archives/model only, isolated Fleet root):
`FLEET_TEST_UPGRADE_CACHE=/path/to/acceptance-cache FLEET_TEST_PYTHON=/path/to/python go test ./internal/lifecycle -run '^TestLiveManagedEngineUpgrade$' -count=1 -v`
from `fleet/`. The cache holds SHA256-verified Ollama 0.34.1/0.34.2 Mac archives
and the GGUF described by `qwen-source.json`. It checks real inference before/
after version change, a lost start response, unchanged weight files, an ordinary
restart, retained configuration/activity, and release of owned processes/budgets.
The test uses a local registry; live Hub/gateway acceptance remains separate.

A lightweight, node-local connector for attached Ollama, LM Studio, SGLang and
OpenAI-compatible API endpoints. Python's standard library is sufficient for
the connector. Installing it does not install an inference engine.

The Model Services frontend manages the connector through the Agent's
`model_services_*` APIs. A deployment pins its Fleet node, instance revision,
generation and configuration hash. Model calls use Fleet's HTTP data plane;
they are not sent through the management RPC bus.

## Management and inference are separate

Fleet injects `PANTHEON_APP_RPC_TOKEN` into each owned process generation and
adds `X-Fleet-RPC-Token` to owner `app_rpc` calls. `/rpc` rejects requests without
this credential. The public application gateway strips client copies of the
header. An inference grant cannot configure endpoints or download files.

The node's private `rpc-secret` seeds generation-bound credentials. It is not
included in the public lifecycle ledger. An older Fleet without `app-rpc-auth=1`
must be updated before using these management methods; do not disable the check
to accommodate it. Provider keys remain in node-local credential files and are
only read when contacting the configured endpoint.

## Artifact tasks

Authenticated RPC methods:

- `artifacts_list`: bounded job history, excluding source URLs.
- `artifacts_submit`: immutable `job_id`, plus a pinned source with `url`,
  `sha256`, exact `size`, `name`, `revision` and `format`.
- `artifacts_submit` with `job_id` and `resume: true`: explicitly resume using
  the source saved on this node. A signed source URL need not return to the UI.
- `artifacts_cancel`: retain verified partial bytes for later resume.
- `artifacts_forget`: remove a finished history record, keeping cached files.

These transfer/verify files only; they do not extract, execute, load or publish
models. Engine preparation uses separate, immutable recipes described below.

## Recovery after runner or node restart

Connector 0.1.2 and Fleet `app-recovery=1` support explicit **Check and recover**
in Model Services. Hub retains the original connector/engine bindings in a
`recovering` intent and withholds new model routes until verification completes.
Retries use that intent and the same start operation IDs.

Fleet reuses live resources only when a committed ready generation, exact
component identities, retained resource reservations and fresh readiness probes
agree. It never replays interrupted startup/stop hooks. If all owned processes
have exited, Fleet confirms their exit and releases leases; the coordinator may
start the same installed revision once. It does not install engines or download
models. A second failure or an unrelated newer generation requires inspection
in Fleet rather than an automatic restart loop. This recovery does not reopen
model weights or replay unfinished model-management jobs.

The connector drains calls before rebinding a restarted managed engine's local
port. Configuration preview, compare-and-swap and resume are owner-only RPCs;
configuration must match before admissions resume. Attached engines and API
credentials remain externally managed. Failed recovery remains visible and
unavailable for new calls until explicitly resumed.

`PANTHEON_APP_CACHE` is an App-owned directory provided by Fleet and stable
across package upgrades. Verified blobs live in `blobs/<sha256>`. Durable job
stores live in `tasks/<PANTHEON_APP_SCOPE>/jobs.sqlite3`, so deployments retain
separate histories while reusing identical bytes. Source URLs are private to
the job store. Uninstalling the App does not remove the cache or external files.

Transfers require HTTPS, validate response ranges and final SHA256, and publish
via atomic rename. A receipt prevents repeated hashing/downloading of unchanged
verified blobs. A crash between rename and receipt recovers by local hashing.
Only one writer can own a blob or task store. At most two transfers and 256
history entries are retained per deployment. Interrupted tasks require explicit
resume; reopening a window never restarts them automatically. Cancellation of
a silent network read is bounded by its 30-second socket timeout.

## Managed engines

`engines_catalog`, `engines_prepare`, `engines_jobs` and `engines_cancel` are
owner-only RPCs. The App's `engines.json` pins vendor, platform, version, size
and SHA256. A caller selects a recipe ID; it cannot supply an executable, shell
installer or engine download URL. Preparing an engine runs as a durable job,
outside Fleet's 600-second install hooks. Verified archives are extracted into
an atomic version directory with traversal/special-file/size checks; internal
library links become hardlinks. Cached versions are validated and reused.

Managed recipes include Ollama, macOS arm64 llmster and Linux amd64 SGLang.
Native macOS/Windows archive
handling uses only Python's standard library. Linux `.tar.zst` preparation needs
`zstd` on PATH or Python 3.14 zstd support and reports that prerequisite.
Managed execution currently requires an explicit Apple Metal or NVIDIA CUDA
device and a declared memory budget. Windows archive/compile checks do not
imply real Windows execution acceptance.

The Agent creates a connector scope `model-<deployment>` and a distinct owned
engine scope `engine-<deployment>`, sharing only the stable App cache. The
native engine component replaces its wrapper with the selected engine; Fleet owns the
actual process, port and reservation. Its HOME/model directory is isolated,
cloud execution is disabled, and readiness checks the exact version and port.
llmster runs in its own HOME and prepared runtime copy, with background updates,
JIT loading and plugins disabled. Imports use its pinned CLI against the owned
daemon. No user LM Studio configuration or login session is changed.
Closing the management UI does not stop either process. Stop drains the
connector before stopping the engine. An incomplete stop is published as
`stopping` and remains retryable after the connector has exited.

Managed configuration is immutable in the registry. A changed recipe, context,
budget or App engine revision needs a separate deployment until an explicit
upgrade workflow is available. Engine startup does not download model weights
automatically.

### Owned model operations

`models_status`, `models_submit` and `models_forget` are owner-only operations.
Ollama and llmster import a verified GGUF download ID, then load/unload an exact
`fleet/<sha256>:latest` identity. Jobs persist with bounded history and monotonic
elapsed times; interrupted jobs become unknown and are never blindly replayed.
Unloading releases model memory while retaining the disk cache. llmster uses
an explicit context, one concurrent load and an idle TTL; after expiry, load the
model explicitly before inference. Inference callers cannot override these
resource/lifetime settings. Stop first blocks new calls and drains current work.

### Managed SGLang

The Linux NVIDIA recipe pins the official 0.5.20 runtime image by digest. Docker
and NVIDIA container support must already be available; this does not install
drivers or enable privileged containers. Fleet exposes only the reserved GPU
UUID, enforces the container RAM limit and mounts its package and exact model
snapshot read-only. Image layers are reused by Docker. The preparation jobs and
model cache remain separate from this image.

1. Create a managed SGLang deployment with context, concurrency, RAM/VRAM budgets
   and the SHA256 of a `safetensors.tar.gz` bundle.
2. In Downloads, download the pinned bundle and prepare its snapshot. The bundle
   contains root-level config, tokenizer and safetensors files. No Python code,
   pickle weights, links, traversal paths or remote model code are accepted.
3. Start the service. Fleet checks an estimate of weights + decoder KV cache +
   workspace, starts the pinned image and waits for that exact model to be ready.
4. Discover, confirm capabilities and publish the model for Agent/Playground.
   Stopping this resident service unloads the model and retains its disk files.

The initial managed estimator supports unquantized qwen2/llama/mistral/gemma
decoder layouts. Other architectures remain available through attached engines;
they are not assigned a made-up memory estimate. The pinned recipe uses FP16,
bounded total KV tokens, and explicit context/concurrency. Native SGLang testing
inside Modal is distinct from Docker/NVIDIA runtime acceptance on a Fleet host.

## Fleet resource admission

Fleet nodes advertising `app-resources=1` expose a versioned memory/accelerator
snapshot. NVIDIA uses stable GPU UUIDs; Apple Metal aliases unified RAM; Linux
AMD reports measured PCI/VRAM information. Unknown availability is not free
memory. AMD execution is not yet enabled by these inventory changes.

A component can declare `resources.memory_bytes` and exact device budgets.
Fleet atomically persists these before starting the component. CUDA device
visibility is derived from the reserved UUIDs. CUDA containers receive specific
`--gpus` device IDs and RAM/swap limits; unsupported bindings fail rather than
falling back to all GPUs. Native process RAM budgets are cooperative admission
limits, **not OS-enforced memory limits**.

Dynamic owner lifecycle operations `resource_reserve` and `resource_release`
pin the same instance/revision/generation and an immutable lease ID. Releasing
a model claim must follow confirmed unload. Static component claims cannot be
manually released. Unknown/failed generations continue to count until Fleet
confirms their processes are gone. Stopping or uninstalling cannot silently
discard a live claim.

Admission uses fresh telemetry (45 seconds), system/device safety reserves and
all outstanding claims. Available memory and reservations are intentionally
accounted for conservatively, so externally observed allocations can reduce the
budget twice; this favors refusal over optimistic overcommit. Existing external
engines cannot be made exclusive by these claims. A node-local
`resource-policy.json` may set `system_reserve_bytes` and per-device
`device_reserve_bytes`; defaults reserve 25% of system RAM and 5% of dedicated
VRAM, with a 256 MiB floor. Unified GPU memory is included in the system budget.

## Acceptance

From the Runtime checkout:

```sh
python -m pytest tests/test_model_artifacts.py tests/test_model_services.py tests/test_model_engines.py tests/test_model_control.py tests/test_model_snapshots.py -q
cd fleet
go test -race ./internal/node ./internal/lifecycle ./internal/runner ./internal/appgateway
```

Optional local hardware sampling:

```sh
FLEET_TEST_RESOURCE_INVENTORY=1 go test ./internal/node ./internal/lifecycle -run 'TestLiveResource' -v
```

With Modal credentials configured, `fleet/scripts/verify-model-resources-modal.py`
uses a bounded, isolated T4. `fleet/scripts/verify-sglang-modal.py` builds a fixed
SGLang image/model, then exercises Fleet-owned engine and connector processes on
an isolated L4, including real inference and shutdown. Both scripts terminate
their sandboxes in `finally` and also enforce a platform timeout. No production
Fleet registration, user volumes or provider credentials are used.

These tests do not substitute for the deployed Hub/Agent/Fleet path, Windows
hardware acceptance, visual managed-engine UI acceptance, or LAN/Relay benchmarks.
# Explicit route aliases

Model Services → Routes publishes an owner-scoped `fleet-route://<id>` alias.
Candidates, node allowlists, compute location, billing and required capabilities
are explicit. Attached engines have unknown compute location until the owner
confirms it; a loopback Ollama endpoint alone does not prove local computation.
Managed engines are local, while an API connector computes at its provider.

Hub resolves authorized candidate metadata with a route revision; the client
probes each exact-generation Fleet grant without loading or invoking a model.
Selection can preserve order or prefer observed loaded models and lower active
request and queue ratios. Metadata is briefly cached; actual admission rechecks
capacity, drain and configuration. A request is bound once
before its inference POST and never replayed on another candidate after failure.

Current transport is authenticated Fleet Relay. A direct-only policy fails
closed until direct transport is available. Local computation does not imply
the Agent or transport remains on the same machine. Alias catalog capabilities
and context use a conservative intersection; returned results carry the actual
deployment, generation, alias revision, transport and resolution duration.


## Request admission and activity

The connector admits at most the managed engine's configured parallelism, or
four calls for an attached service. Up to 32 further requests wait in FIFO order
for at most 30 seconds. A managed engine never overlaps different models: a
model switch waits for running requests to finish. Full/expired queues return
429 without submitting inference. Draining rejects new admissions and cancels
queued work; running requests can finish or be cancelled explicitly.

The node-local `activity.sqlite3` retains metadata for up to 128 completed,
failed, cancelled or unknown requests, plus the bounded in-flight set. It records
request/model/configuration identity, queue/first-byte/first-token/total timing,
byte counts and numeric usage when reported. It does not retain prompts,
responses, credentials, upstream error bodies or URLs. Writes occur on state
transitions and first token, not on every token. Restarted unfinished requests
are `unknown` and are not replayed. Duplicate request IDs in the retained window
are rejected; this is not an unlimited or cross-provider exactly-once guarantee.

Management RPC `activity` reads that history; `cancel_request` takes `request_id`.
Both require the generation-bound Fleet management credential. Model consumers
retain the existing inference-grant `/cancel` endpoint. Cancellation arriving
before admission records a bounded cancellation marker. Disconnects while queued
or waiting for upstream headers cancel the call and release its admission slot.
A disconnected or truncated stream is not counted as completed.

`/route-state` reports separate `active_calls`, `queued_calls`, `capacity` and
`queue_capacity`. Alias preflight may choose a full engine with available queue
space; ready-first selection compares loaded state and running-plus-queued load.
The selected binding remains fixed after submission. The response includes
`X-Model-Queue-Ms` and the client exposes `request_id`/`queue_ms` in route metadata.
Atrium's Activity view polls only while visible and never cancels work on close.

Existing service deployments remain bound to their installed connector revision;
use Services → Update connector to move to the current Agent’s bundled revision.
A frontend refresh alone does not update a connector.


## Connector updates and interrupted operations

Update connector requires a node advertising `app-data-clone=1`. Fleet stages
and installs the new immutable package first. The Hub then persists an update
intent containing the source binding and target digest and blocks new catalog
admissions. Existing calls drain before the exact old connector generation stops.
The external or separately-owned inference engine is not restarted by this action.

Fleet’s `clone_data` lifecycle action copies configuration and request history
entirely on the selected node, between stopped revisions of the same App/scope.
It accepts only revision/generation identities, never caller-supplied paths.
Links and special files are rejected, and copies are limited to 64 MiB / 10,000
entries; large weights and independent download jobs remain in the stable App
cache. An existing destination is never overwritten. A matching import receipt
allows recovery if acknowledgement is lost after directory publication.
The original state is retained. Generation-bound RPC/control credentials are
regenerated at the new start rather than reused from copied endpoint metadata.

After startup the Agent checks the retained configuration hash and, for managed
services, the unchanged owned engine binding, then publishes the new connector.
While an update is incomplete, Services shows Resume connector update. Retrying
uses the saved target even if the Agent has since updated. Unexpected generation,
configuration or process state requires inspection in Fleet; it is not silently
adopted. Updating a stopped attached connector explicitly starts its new revision.
Managed engine recipe/configuration upgrades are a separate remaining workflow.
