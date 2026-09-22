# Model Service Connector

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
request ratios. Metadata is briefly cached; actual admission rechecks capacity,
drain and configuration. There is no admission queue yet. A request is bound once
before its inference POST and never replayed on another candidate after failure.

Current transport is authenticated Fleet Relay. A direct-only policy fails
closed until direct transport is available. Local computation does not imply
the Agent or transport remains on the same machine. Alias catalog capabilities
and context use a conservative intersection; returned results carry the actual
deployment, generation, alias revision, transport and resolution duration.
