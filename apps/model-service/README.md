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

### Engine idle coordination (internal protocol; not automatically enabled)

Model-memory expiry and stopping an engine process are separate operations.
The connector now provides a durable owner-only admission handshake. Fleet's
node coordinator, registry wake-before-binding path and user policy controls
still need integration; neither instance's keep-alive is cleared by this change.

- `idle_drain(suspend_id, config_revision, idle_seconds, idle_epoch)` is available
  only for owned `on_demand` or `warm` Ollama/llmster deployments. Read `idle_epoch`
  from `status.engine_idle`; a new fence increments it. The engine deadline must
  not shorten the configured model warm TTL. Metadata polling does not extend
  the deadline. Actual call/job start and completion do extend it.
- Active or queued calls, maintenance, pending recovery, unfinished model jobs,
  loaded models (including ones absent from this connector's catalog), missing
  metadata and failed observations cannot authorize idle shutdown. The engine
  observation holds no admission/cancellation lock. Any intervening use
  invalidates it, including a call that completed before observation returned.
- A positive `safe_to_stop` means only that admission is durably fenced in
  `engine-idle.json`. It is not evidence that a process exited or memory was
  released. The owner must verify the generation-bound engine before stopping
  it and Fleet must confirm exit before releasing its reservation. Repeating
  the same operation is safe; an earlier epoch cannot shut down a later cycle.
- On connector restart, a pending fence continues rejecting inference. Routing
  preflight reports it without touching or waking the engine. Normal configure
  and resume cannot silently clear it.
- After verifying the restarted owned engine, the owner calls
  `idle_resume(suspend_id, config_revision, config, managed)`. The revision is
  the original fenced revision; only the loopback port may change, preserving
  engine, scope, recipe, context, parallelism, policy and budget. A durable wake
  intent precedes configuration replacement. The same payload resumes a crash
  before or after that write; a different target is rejected. A successful
  replay is observational and cannot undo a later Stop.
- An explicit drain racing a wake persists `stopped` and wins. Only explicit
  owner recovery may call `idle_reset(suspend_id, config_revision)` with the
  current revision, then verify/reconfigure the engine and call normal resume.
  Reset alone does not reopen admission. Automatic wake must never use reset.

The node coordinator must persist exact connector/engine bindings and immutable
operation IDs, operate without an Agent/UI connection, serialize against Stop
and upgrades, and publish a fresh binding/configuration before inference is
submitted. An uncertain binding or lost inference response must not trigger a
new generation or an automatic inference replay.

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

## Node-owned engine idle coordination

Fleet nodes advertising `model-engine-idle=1` provide an explicit management
protocol: `model_idle_register`, `model_idle_status`, `model_idle_wake` and
`model_idle_disable`. Registration binds one `model-<deployment>` connector to
its separately owned, budgeted `engine-<deployment>` instance. It requires their
exact installed revisions/generations, the matching connector configuration hash
and an idle timeout. Endpoint, credentials, launch options and memory budget are
derived from the installed engine; the caller cannot override them in the policy.
Only managed on-demand/warm Ollama and llmster are currently eligible, and an
engine timeout cannot shorten the configured warm model lifetime.

The node persists its policy, connector admission fence and immutable lifecycle
operation IDs. It observes idle state without loading/unloading models, obtains
the connector's durable `idle_drain` acknowledgement, checks direct engine use,
stops the exact process and confirms exit before releasing reservations. New
direct engine calls are excluded during stop/wake; connector status remains
available. Agent/UI disconnection does not stop the node coordinator.

A model consumer must request wake **before** resolving its frozen inference
binding, poll the same policy revision until active, and use the acknowledged new
engine generation/configuration hash. Wake reserves a one-minute admission grace
interval; it never submits inference itself. Readiness is checked before the
connector's durable `idle_resume` handshake. Lost acknowledgements observe or
continue the same operation, never mint another start or replay model generation.
Only current and eight recent successful automatic operation receipts are kept;
failed/unknown operations and ordinary owner lifecycle history are not pruned.

An explicit lifecycle action or policy disable revokes pending automatic actions.
Disable does not implicitly wake a sleeping engine or reopen a fenced connector.
A Fleet restart preserves the policy but uncertain process/operation bindings
require explicit recovery; they are not silently adopted or restarted.

The workload path is `POST /api/model-services/<deployment>/engine-idle` on Hub,
with only an action (`status` or `wake`) and the expected deployment revision.
Hub derives ownership, exact connector and policy identity from its directory;
the controller checks the ready connector and node policy before requesting
wake. The response excludes node-local endpoints, credential paths and launch
configuration. Consumers receive no general lifecycle or registration authority.

Hub persists `engine_idle` policy intent (`registering`, `enabled`, `disabling`,
`disabled`) with a node policy revision and timeout. Only an acknowledged active
wake can publish a newer engine generation/configuration through a deployment
CAS. A concurrent stop intent or directory edit rejects the late observation.
The Model Client wakes before resolving inference transport, preserves the exact
model publication and never retries inference. Alias probes observe sleeping
services without waking them; only the selected candidate requests wake. Active
loaded candidates still rank ahead of dormant ones under ready-first selection.

Owner management requires the additional `model-engine-idle-management=1`
capability. Agent saves the immutable registration request before sending it.
`model_idle_cancel` durably revokes that same request even if registration never
arrived, or its acknowledgement was lost. Late registration cannot overwrite the
cancellation. The owner waits for already executing lifecycle operations and
reconciles only their exact owned engine generation before publishing disabled.
A failed durable cancellation blocks Stop; it never assumes the node was stopped.

Services exposes a compact timeout control and read-only idle status. Status
polling does not wake engines and stops when hidden/closed. Disabling through the
owner control explicitly recovers normal engine availability; Stop instead
revokes the policy without waking. Recovery/upgrade first revoke automatic work,
drain the connector, reset its durable idle fence and verify the exact engine
before readmission. An acknowledged cancelled rebind can explain a newer config
hash even when its Hub publication was lost. It cannot justify another engine or
connector identity. Restart after Stop resets and resumes the retained fence.

Installed 0.1.9 Ollama and llmster acceptance passed idle shutdown, selected-call
wake, disable, explicit Stop and restart with retained weights. Existing user
services have not been automatically opted in. UI release and further upgrade
edge acceptance remain separate. Model status/job
history observes the retained connector without waking; explicit discovery,
publication and model actions wake before RPC. Publishing uses the returned
post-wake deployment revision, so a changed engine binding is not overwritten.

### Preloaded warm replicas (connector 0.1.11)

An owned **resident** Ollama or llmster service can persist one imported model as
its preload selection. The owner submits `models_submit` with `action=preload`,
an exact `model_id`, a unique `job_id`, and the current `pool_revision` returned in
`models_status.preload`. Selection and the load job are saved in one transaction.
Repeated job IDs return the same receipt; stale selections cannot replace newer
ones. Weights are neither downloaded nor imported by this action.

The load happens before inference, under the existing admission fence and node
resource reservation. Changing the selection unloads the previous known model
before loading the replacement. Other-model inference cannot evict a warm
replica's selected model. Use multiple explicitly budgeted services as alias
candidates to form a pool across nodes; ready-first routing already favors loaded
candidates. This does not automatically provision nodes or resize reservations.

For Ollama, explicit preload also performs one fixed public-prompt compute warmup
with at most two output tokens. This exercises prefill and decode initialization
before reporting Ready; phase `Warming inference kernels` remains observable in
the same durable job. It runs only against this owned node-local engine, does not
use user prompts or provider credentials, and discards the generated output.
The receipt includes `warmup_version=1` after confirmation. Old load-only receipts
need owner-verified resume or explicit preload before they can become ready in
the new connector. An already loaded legacy model need not be unloaded for this.

Owner-verified configure/resume restores a successfully preloaded model after
service restart. Merely reopening the UI or reading metadata never loads it.
Already-loaded, compute-ready recovery performs no new load or generation and
creates no new job. Interrupted
or failed loads stay visible and unavailable for routing until an owner explicitly
retries; recovery never replays an uncertain load. Preload records and weights
survive process stop and connector updates. Engine family, context, parallelism
and model identity remain subject to the same owned-engine checks.

`action=unpin` with the selected model and current pool revision disables future
preloading. It keeps current memory/weights; unload and service Stop are explicit.
The current preload receipt cannot be deleted until superseded or disabled.
The Models UI exposes these controls only when the connector reports protocol 1.

Ollama preloading shifts model loading and bounded compute initialization earlier;
it does not guarantee latency for every context length or kernel shape. Other
engines currently retain their native load-only preload behavior. Report cold
and warm TTFT using distinct prompts. Native memory amounts remain reservations rather than
hard OS limits. Initial automated acceptance uses HTTP engine fixtures and the
llmster driver fixture; installed multi-node pool acceptance remains required.

### Binary media artifacts (connector 0.1.12)

The connector stores input/output media separately from model weights, under its
own persistent data directory. References have the form
`fleet-artifact://<deployment-id>/<opaque-id>`; they contain no filesystem path,
provider URL or credentials. Only finalized, checksum-verified files are readable.
The existing instance-scoped Fleet workload grant and current `X-Model-Config`
are required by the data-plane path; the grant is validated by Fleet before
forwarding to the node-loopback connector.

`ModelServices.media(deployment_id, policy)` opens an exact-service session over
the existing Direct/Relay transport. `direct_only` never falls back to Relay.
The binding/configuration is frozen for that session; media is never rerouted to
a different model candidate. Reconfiguration rejects active transfers, and Stop
waits for them. No model load or inference is triggered by media operations.

```python
from pantheon.models.client import ModelServices

client = ModelServices()
try:
    async with client.media('my-service') as media:
        with open('input.wav', 'rb') as source:
            artifact = await media.upload(source, request_key='recording-001',
                                          kind='audio', mime='audio/wav')
        # Use a staging file; publish/rename only after full checksum validation.
        with open('verified.wav.partial', 'wb') as destination:
            await media.download(artifact['ref'], destination)
        await media.remove(artifact['ref'])
finally:
    await client.aclose()
```

Transfers use raw chunks of at most 1 MiB. Uploads compute SHA-256 before declaring
the file. After a lost acknowledgement, the caller can explicitly repeat the
same upload with the same request key and unchanged source; committed chunks are
not duplicated. The client never automatically replays a failed request. Changed
content with an existing key is rejected. Downloads validate byte ranges, size,
ETag and the complete checksum; redirects and compressed responses are rejected.
The source must stay unchanged during upload. A failed download must not be
published as a completed result.

The node reserves declared bytes atomically, including unfinished uploads. Limits
are 512 MiB per artifact, 2 GiB total and 256 records. Unused artifacts must be
explicitly removed; there is no implicit deletion of job-pinned media, model
weights or user files. SQLite/files are private to the connector. Raw media is
not serialized into NATS status messages, activity records or a JSON editor.

HTTP routes: `POST /media/artifacts` declares an input; `PUT /media/artifacts/<id>`
appends with `Upload-Offset`; `POST .../<id>/complete` seals; `GET .../<id>` reads
metadata; `GET .../<id>/content` requires one explicit bounded byte Range;
`DELETE .../<id>` removes an unused artifact. Generated-output declarations and
job leases are internal driver operations, not caller-controlled upload fields.

The binary storage protocol is also used by the speech job driver below. Image and video jobs use the same artifact store. Browser direct-preview
transport remains separate work.


### Durable typed inference jobs (connector 0.1.13)

Rerank is the first typed job driver. Publish operation `rerank` only for an
`api` or SGLang endpoint that actually serves a reranker. Text-only SGLang
models do not become rerankers by changing their publication. The adapter
matches the pinned SGLang 0.5.20 `/v1/rerank` text query/document contract:
https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/entrypoints/openai/serving_rerank.py
This does not yet configure managed cross-encoder launch flags. Image and video
drivers require an attached SGLang Diffusion engine rather than this text recipe. Rerank passed actual attached SGLang/L4 acceptance; owned
Docker/NVIDIA launch is a separate unverified path.

```python
async with client.inference('fleet-model://my-service/ranker', 'rerank') as jobs:
    receipt = await jobs.submit(
        {'query': 'Find relevant evidence', 'documents': ['Document A', 'Document B']},
        request_id='experiment-001', parameters={'top_n': 1})
    job_ref = receipt['ref']
# Poll the fixed job, never re-resolve an alias or resubmit an uncertain request.
receipt = await client.job_operation(job_ref, policy='direct_only')
history = await client.inference_jobs('my-service', policy='direct_only')
await client.job_operation(job_ref, action='cancel', policy='direct_only')
```

POST `/inference/jobs` returns a small durable receipt. GET the collection for
metadata, GET `/<id>` for one result, POST `/<id>/cancel` for explicit cancellation,
and DELETE `/<id>` to remove terminal history. All routes require the current
instance authorization/configuration. The service shares text inference's FIFO
admission, concurrency, drain and model lifetime machinery. Listing jobs never
loads a model or resubmits work. A reconnect retains the exact deployment; carry
forward the original transport policy when inspecting its handle.

Requests are bounded to 128 KiB and 128 documents. Only query/documents,
`top_n` and `return_documents` are accepted; per-call endpoints, credentials and
paths cannot override deployment configuration. Results are bounded and contain
validated scores/indexes; requested document text is reconstructed from input.
The private node ledger retains up to 128 jobs, including results. Query payloads
are not persisted. Explicit `return_documents` does retain those selected input
documents in the result. Remove terminal history to release its space; ordinary
request metadata has a separate retention limit.

An exact repeated job ID/request/configuration observes the same job. Changed
input with that ID is rejected. A lost acknowledgement is not retried. Queued jobs
found after connector restart are cancelled before submission; possibly submitted
jobs become Unknown and are never replayed. Each admitted job has a ten-minute
wall deadline. Cancellation closes this connector's upstream request, which does
not prove a remote provider stopped computing or billing.

Closing Playground or its observer timing out does not cancel a submitted Fleet
job. Explicit Cancel requests cancellation. Model Services Activity has a separate
job history viewer so results remain discoverable after reopening the window or
pruning ordinary text-request history. It defaults to Direct only; Relay requires
an explicit selection. Only one result is fetched at a time and released when the
view is hidden. These lifecycle guarantees are tested with real local HTTP and
Fleet QUIC fixtures. Connector 0.1.13 also passed installed Hub/Agent and actual
L4 reranker acceptance. Later drivers need their own installed acceptance.


### Speech generation (connector 0.1.14)

Attach a node-local Speaches endpoint, for example `http://127.0.0.1:8000/v1`,
using engine `speaches`. Install the model in Speaches first, discover it, then
publish its `speech` operation and confirmed compute location. This connector
does not own, install, update or stop the attached speech engine. An explicit
`api` service can use the same compatible speech endpoint with provider billing.
Ollama, LM Studio and SGLang are not advertised as speech adapters.

```python
async with client.inference('fleet-model://voice-service/my-model', 'speech') as jobs:
    receipt = await jobs.submit(
        {'text': 'Hello from this Fleet node.'}, request_id='speech-example-001',
        parameters={'voice': 'af_heart', 'response_format': 'wav', 'speed': 1.0})
    job_ref = receipt['ref']
# Observe until terminal, using the ORIGINAL transport policy. Do not resubmit.
receipt = await client.job_operation(job_ref, policy='direct_only')
if receipt['state'] == 'succeeded':
    artifact = receipt['result']['artifacts'][0]
    async with client.media('voice-service', policy='direct_only') as media:
        # Write to a staging file; publish/rename only after checksum verification.
        with open('speech.partial', 'wb') as target:
            await media.download(artifact['ref'], target)
```

Voice is required. Supported formats are WAV and MP3; speed is 0.25–4 and text
is limited to 32,768 characters. Engine-specific support can be narrower.
The adapter follows pinned Speaches0.9.0-rc.3 `/v1/audio/speech`:
https://github.com/speaches-ai/speaches/blob/v0.9.0-rc.3/src/speaches/routers/speech.py

A job reserves up to64MiB on the node before inference. Audio is written in
64KiB chunks and finalized with its actual size and SHA256. Receipts contain
opaque artifacts, never audio bytes/base64 or provider URLs. Partial, cancelled,
oversized or invalid-format outputs are discarded; uncertain inference is never
replayed. Connector restart reclaims partial outputs. Successful outputs stay
retained until terminal job history is explicitly removed; removing it also
deletes its generated files, unless another job still retains one. Interrupted
history removal is recovered after restart.

The driver passed isolated CPU Speaches/Kokoro generation, audio decoding and
checksum checks, followed by installed Agent/Hub -> joined CPU Fleet acceptance:
three real generations (including Playground), Direct-only binary downloads,
job-ID deduplication, connector restart with retained audio, and history/output
removal. The acceptance node and sandbox were revoked/terminated afterward.
This verifies an attached engine; managed Speaches and transcription remain open.

The local Playground UI implements explicit Relay audio preview using the existing
browser instance-cookie grant, bounded binary ranges and SHA256 validation. It
cancels transfers and releases the audio Blob on hide/close. Controller61eedf01
supplies the necessary CORS headers without allowing browser workload tokens.
Live browser playback acceptance and versioned UI publication are still pending.
Direct-only results do not use Relay; browser direct preview remains separate
work. No audio is sent through the old base64 media-RPC transport.


### Speech to text (connector 0.1.15)

Publish `transcription` on an attached Speaches/API service after installing and
confirming its ASR model. Upload audio to that same service's binary media API,
or use a speech output already retained on it:

```python
async with client.media('voice-service') as media:
    with open('recording.wav', 'rb') as source:
        artifact = await media.upload(source, request_key='recording-001', kind='audio', mime='audio/wav')
async with client.inference('fleet-model://voice-service/my-asr-model', 'transcription') as jobs:
    job = await jobs.submit({'audio': artifact['ref']}, request_id='transcribe-001',
                            parameters={'language': 'en'})
# Inspect the same job ref until terminal. Its result contains text and usage.
```

The adapter posts bounded multipart chunks to `/v1/audio/transcriptions`.
Supported inputs are WAV, MP3, FLAC, OGG, WebM and M4A, at most64MiB. It accepts
JSON response format, optional ISO language, prompt and temperature0–1. Other
engine-specific response formats and timestamp modes are not implied supported.
An input lease prevents deletion during a job; completing/cancelling releases
it without deleting user-owned input. Outputs used by another job cannot be
removed with their producing job's history until that consumer releases them.
Cross-service refs/URLs/paths are rejected before submission; routing never
silently copies audio. No transcript retry follows a lost/ambiguous response.

Playground accepts `parameters.audio_asset` as a Fleet artifact ref. Its file
picker uploads binary ranges to a concrete selected service via Relay. For an
alias, use an existing artifact ref from its selected authorized candidate;
foreign-node resolution fails rather than moving the audio. Direct-only browser
uploads remain unimplemented; the Python client supports direct media transfer.

Real isolated Speaches0.9.0-rc.3 CPU acceptance with pinned Kokoro and
Systran/faster-whisper-tiny.en passed two generation/transcription round trips,
job deduplication, restart and cleanup. Offline Whisper requires `refs/main` in
its private HF cache to point to the pinned downloaded commit; do not enable
online fallback or replace the revision with a moving branch. Installed Fleet
transcription rollout/acceptance is still pending.


### Asynchronous video jobs (connector 0.1.17)

Publish `video` for an attached SGLang Diffusion 0.5.20 engine that serves a video
model. The managed text-engine recipe does not launch it. This integration has
HTTP fixture/regression coverage and isolated real L4 generation, full MP4 decode,
process-kill recovery and cancellation acceptance. Installed Fleet/Hub/browser
video acceptance and rollout remain pending.

```python
async with client.inference('fleet-model://my-service/video-model', 'video') as jobs:
    receipt = await jobs.submit({'text': 'A small boat crossing a lake'},
        request_id='video-001', parameters={'size': '512x512', 'fps': 16,
        'num_frames': 17, 'num_inference_steps': 4})
# Inspect the same reference after reconnect, never resubmit with a new ID.
receipt = await client.job_operation(receipt['ref'], policy='direct_only')
```

Supported sampling parameters are `size`, `fps`, `num_frames`, `seed`,
`num_inference_steps`, `guidance_scale`, and `negative_prompt`. Output is one
bounded MP4 artifact (64 MiB maximum), validated incrementally and downloaded from
the same engine. Prompts, engine filesystem paths and returned URLs are not in
the durable job receipt. Downloads after restart verify already committed bytes.

The upstream API does not abort generation. Cancel records intent, keeps observing
the acknowledged upstream ID, and discards output after completion/failure. Closing
Playground only stops observation. Connector restart restores outstanding capacity
before admitting new work and never repeats creation. `unknown` with
`upstream_pending: true` still consumes capacity and blocks drain/history removal;
it does not mean stopped. Missing observations can be resumed explicitly with
`client.job_operation(ref, action='reconcile', policy=original_policy)` or Activity's
**Review recovery → Resume status checks**. This only observes an already
acknowledged ID; the UI does not offer it when creation's ACK was lost.

If creation's acknowledgement was lost, no upstream ID can be safely inferred.
That job remains unknown and reserved. Automatic replay, record deletion, or
killing an attached engine are not recovery. **Review recovery** lets the service
owner inspect the saved identity, check the external engine, then explicitly attest
that the generation has ended and release its reservation. An empty upstream job
list, HTTP404, disconnection or elapsed time is not evidence of GPU completion.

This administrative release uses generation-bound owner RPC, unavailable to an
inference grant. Its ticket binds the original job, configuration, journal state
and connector run. Changing cancellation intent or restarting invalidates an
uncommitted ticket. Journal and job/lease changes commit atomically before capacity
is freed; an exactly repeated committed release is idempotent. The job retains
`state: unknown`, `upstream_cancel_confirmed: false` and an `owner_release` audit
record with `verified_by_engine: false`. It never claims successful generation or
machine-verified cancellation. Only parked observers may be released. No engine
request or process termination is performed. Automatic owned-engine shutdown
proof is separate from this explicit attached-engine owner workflow.

The connector deadline records cancel intent; it cannot guarantee a GPU abort.


### Bounded status waits and request timing (connector 0.1.18)

Authenticated `GET /inference/jobs/{id}` accepts `Prefer: wait=N` for an integer
0–5 seconds. Active or unknown/outstanding work holds the response until a
terminal outcome commits or the bound expires. Completion wakes observers
immediately; the ledger lock is released while waiting. At most16 waiting
observers are admitted per connector. Further observations return current state
immediately without taking an inference slot or blocking cancellation. Configuration
identity is checked again before returning a held response. Waiting never submits,
replays, reroutes or cancels inference, and unknown upstream work stays outstanding.

Playground requests2-second waits and keeps its250ms polling floor. Older connectors
ignore the optional header and continue to work. Each Direct HTTP request still
requires its own fresh App grant; no authority is cached to reduce polling cost.

Fleet typed results report `elapsed_ms` from the Agent starting resolution through
observing the outcome, with `timings.resolve_ms`, `submit_ms`, and `observe_ms`.
`service_elapsed_ms` retains the connector's independent task duration. These clocks
must not be subtracted to infer network latency: deduplicated results can belong to
an earlier call. Browser RPC delivery, explicit media preview/download and acceptance
checks are outside request timing. Refreshing job status updates service time only;
it cannot reconstruct an earlier client request's duration.

### Named node credentials (connector 0.1.19, Fleet 0.5.0-models.5)

Attached services accept `secret_ref: node-secret://NAME`. The connector resolves
it through the selected node's Fleet binary over a private local pipe. No key is
returned by management RPC, saved in connector configuration, published to Hub,
put into an App artifact, or entered in the frontend. Each use re-reads the named
credential, so an explicit local rotation takes effect on the next request.

Provision it **on the selected node**, as the account running Fleet:

```
fleet credentials put --fleet FLEET_ID --name openrouter --endpoint https://openrouter.ai/api/v1 --file /absolute/private/key-file
fleet credentials list --fleet FLEET_ID
fleet credentials delete --fleet FLEET_ID --name openrouter
```

Use `--state-dir` if Fleet uses a custom state directory. `--stdin` can replace
`--file`; there is deliberately no command-line key argument. `put` refuses to
overwrite an existing name unless `--replace` is supplied. Commands print only
references; `list` never prints keys. Input files are not removed or modified.
The local operator provisions and revokes credentials; inference callers cannot.

The store is scoped to the node's Fleet lifecycle root, separate from App packages,
model caches and connector generations. On macOS/Linux it requires an owner-only
directory and files (0700/0600), rejects symlinks, and checks ownership when read.
On Windows the complete credential, including its endpoint, is protected with
user-bound DPAPI without machine-wide scope or plaintext fallback. This uses the
existing Fleet dependencies. Windows hardware validation remains outstanding.

The saved API base URL must match the connector endpoint, including its path.
Scheme, host, port and prefix changes fail before opening an upstream connection;
redirects are not followed. Deliberate provider/endpoint changes require explicit
local replacement. A key deletion prevents new requests but does not cancel an
already authorized stream. Missing keys, an old Fleet, a wrong endpoint and
unreadable records fail closed; there is no fallback to another provider, a legacy
file or platform budget. Public configuration contains the reference, never the key.

Legacy `credential_file` remains an explicit alternative for existing deployments;
it cannot be combined with `secret_ref` and is not imported automatically. Named
credentials apply to attached services, not owned engine idle/wake configuration.
An older Fleet is rejected by the manager before installing a named-key connector.

The store protects against other OS users and remote inference consumers. It does
not sandbox malicious native processes running as the same OS account, which can
already read that account's files or use its DPAPI identity. Platform master keys
and existing BYOK/OAuth settings are never copied into this store automatically.

### Pinned speech model preparation (development)

The owner-only `speech_models` management RPC exposes `catalog`, `prepare`,
`status`, `jobs`, `cancel` and `forget`. It prepares the immutable Kokoro ONNX and
faster-whisper tiny.en revisions in `speech-models.json`. Preparing does not start
an engine or modify an attached service's cache. The managed Speaches launch
recipe and UI are still being integrated; this preparation API alone does not
mean that Fleet-owned speech execution is available.

Downloads use the existing bounded durable job queue and verified blob cache.
Explicit resume reuses completed files and partial transfers. Job metadata names
an aggregate `hf-speech-snapshot`: its digest covers the pinned manifest, its size
is the sum of files, and its URL identifies the source revision (not an archive).
Every file has a separate exact URL, size and SHA256. Callers select a catalog ID;
they cannot substitute a model URL, revision, destination or executable.

Preparation atomically publishes a self-contained Hugging Face cache under
`cache/speech-models/MANIFEST_SHA256/hub`, with its own immutable revision and
`refs/main`. Files are regular read-only copies; there are no external symlinks.
Copying checks every hash again before publication. Reuse checks the pinned file
inventory, sizes and recorded modification/change times without network access.
Unexpected mutations require explicit repair rather than silently replacing
weights. Clearing a finished job removes history only; cached weights remain.
No Hugging Face SDK or inference package is installed into the connector.


### Managed CPU speech

The `speaches-0.9.0-rc.3-linux-amd64-cpu` recipe uses an immutable image and one
pinned Kokoro or Whisper snapshot. Fleet requires Linux read-only mounts and
owner-mapped containers (`app-owner-user=1`), preserving private cache permissions.
Docker must already be installed. The manifest sets `run_as_owner`; the runner
maps its own UID/GID, never IDs supplied by the caller.

The pinned Fleet image derives from the same upstream digest without installing
additional dependencies. It makes the image-only `/home/ubuntu` traversable by
other UIDs so Python can find its standard library. A build-time import check
runs as UID/GID 65532. Node state and model mounts keep their private permissions.

Preparation downloads and verifies model files before starting the engine. Inference
runs offline against a read-only cache. Readiness requires the actual model to be
loaded, not merely downloaded. This recipe is resident with one concurrent request;
stop the service to release memory, keeping weights for restart. CPU memory floors
are admission requirements, not measured usage. Discovery publishes only the pinned
model, excluding internal voice-activity detectors. Management/download routes are
not exposed by the owned engine.

The CPU wrapper has real non-root Modal synthesis, transcription and cached-restart
acceptance. This does not establish Fleet Docker mount or installed UI acceptance;
those paths require separate verification before calling the managed rollout complete.

### Pinned diffusion model preparation

`model_services_diffusion_models(deployment_id, action, model_id, resume)` exposes
owner-authenticated `catalog`, `status`, `prepare`, `jobs`, `cancel`, and `forget`
operations through an existing SGLang connector. Preparation is a durable download
job; it does not start an inference engine or change an attached endpoint. The
managed launch recipe below consumes the same verified snapshot.

The image catalog pins `stabilityai/sdxl-turbo` revision
`71153311d3dbb46851df1931d3ca6e939de83304` as `sdxl-turbo-71153311`. Its twenty
explicit files include the model card, license, tokenizer/configuration files and
safetensors weights; repository Python, pickle checkpoints and moving revisions
are excluded. Large-file hashes are the upstream LFS SHA256 identities; small
files were fetched at that revision and verified against their Git blob identity
before recording SHA256. Runtime preparation verifies all downloaded bytes.
The 24 GiB system-memory floor follows the existing bounded acceptance
configuration; it is not a measured minimum or a GPU reservation.

The video preparation catalog also pins `Wan-AI/Wan2.1-T2V-1.3B-Diffusers`
revision `0fad780a534b6463e45facd96134c9f345acfa5b` as
`wan2-1-t2v-1-3b-0fad780a`. Its 20 files total 28,928,905,975 bytes,
including all eight safetensors shards, both shard indexes, configuration and
tokenizer files. The exact `tokenizer/spiece.model` data file is permitted;
other `.model` files, pickle weights and repository code remain rejected.
This repository declares Apache-2.0 in its pinned README and has no separate
LICENSE.md. The 48 GiB system-memory floor follows the prior attached-engine
acceptance configuration with text-encoder CPU offload; it is not a measured
minimum. Preparation is available through the owner RPC, separately from engine
startup. This catalog entry does **not** add a managed video launch recipe or
claim installed GPU/container acceptance.

Speech and diffusion use the same resumable content-addressed blob downloader and
atomic offline HF snapshot builder. Their prepared snapshots and durable job
stores have separate namespaces. Existing speech identities/receipts are
unchanged. A cancelled or failed job retains verified blobs and never exposes a
partial snapshot. `forget` removes job history, not prepared weights. A warm
preparation checks the receipt and file metadata without HTTP or rehashing the
entire model; modified files require explicit repair rather than silent download.

The SDXL manifest totals 13,878,882,605 bytes. Preparation currently keeps both
verified blobs and a private read-only snapshot (roughly two copies); it requires
space for the snapshot plus a margin after downloading. The worker streams file
chunks rather than holding weights in process/browser memory. A future cache-space
optimization must preserve snapshot ownership and integrity.


### Managed image generation

`sglang-diffusion-0.5.20-linux-amd64` runs the pinned SDXL Turbo snapshot in the
immutable SGLang 0.5.20 image. Select Image generation in Model Services, an
explicit Linux NVIDIA node and accelerator. Creation starts durable file
preparation; Downloads shows progress/cancellation/resume. Start the service once
files are ready. The engine uses offline mode and read-only package/weight mounts;
its output/state directory is writable. Stopping retains the verified cache.

This recipe requires at least 24 GiB system memory, a 20 GiB exclusive CUDA
reservation, one resident model and one concurrent request. It publishes image
generation only, up to 512 by 512 pixels. Larger requests are rejected rather than
silently resized; attached image engines retain their independent capabilities.
Readiness checks the exact content-derived served model identity. These floors
are explicit accepted configuration bounds, not measured minimum usage; physical
GPU memory remains separately checked by the runner/entrypoint.

The production entrypoint and managed connector passed real Modal L4 startup,
two distinct image jobs with full PNG decoding/checksums, connector history
recovery, engine stop and cached restart. Engine-ready samples were 129.003 and
89.289 seconds; generation samples were 8.443 and 5.328 seconds. This is not a
latency distribution or installed Fleet Docker mount/resource-lease acceptance.
Those gates, rendered UI acceptance and deployment remain separate requirements.

### Explicit single-node tensor parallel groups

SGLang text deployments may declare `tensor_parallel_size` as 1, 2, 4 or 8.
An omitted value retains the existing single-GPU identity. The resource list
must name exactly that many distinct CUDA UUIDs on one Linux node; multiple
ranks require exclusive reservations. Select the GPUs explicitly in Model
Services. `parallel` continues to mean concurrent requests, not GPU count.
This does not distribute a model across arbitrary Fleet nodes or change an
already running deployment.

Admission checks every GPU independently. It accounts for per-rank KV cache
(including replication when there are fewer KV heads than ranks), engine
workspace and a common SGLang static-memory fraction compatible with every
selected GPU. System RAM conservatively covers each worker loading the full
source. The snapshot estimator grants projection-sharding credit only for the
pinned Qwen2 loader; embeddings, norms and unrecognized tensors remain fully
counted on each device. Other supported decoder architectures retain full-weight
per-device estimates. Unsupported attention/KV partitions fail before startup.

Preparing a snapshot records bounded safetensors-header accounting. Existing
verified snapshots gain this metadata under their preparation lock when needed;
weight files are not downloaded, rewritten or reread in full. The launcher sets
CUDA visibility to exactly the reserved UUIDs and passes the explicit rank count.

`uv run --with modal python fleet/scripts/verify-sglang-modal.py --tp 2` runs the
bounded two-L4 native-process fixture. It checks streaming inference, cancellation,
per-GPU allocation and release after stop. It is separate from installed Fleet
Docker and multi-node acceptance; a compile or unit-test pass proves neither.
