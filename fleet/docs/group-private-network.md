# Private model-group peer preflight

`pantheon.models.group_network` checks that two peers can communicate on an
explicit private address and mutually authenticate the same group launch. It
uses Python's standard library on nodes. It does not install a VPN, open a public
tunnel, request GPUs, start an engine, publish a model or modify Fleet intent.
It is not yet wired into the user-facing group creation/start path.

## Identity and trust

`PeerTopology` accepts a strict roster: protocol, Fleet owner, group ID, exact
model artifact SHA256, launch-settings SHA256, and 2–16 members. Each member pins
rank, node ID, started generation and a private control IP/port. Ranks are dense;
nodes/endpoints are distinct. Order and equivalent IP spellings canonicalize to
the same fingerprint. Changed owner, group, generation, model, settings, rank or
endpoint requires new certificates. The roster is an immutable copy.

The future authorized planner must compute `launch_sha256` from all shared engine
settings and assignments, including engine recipe, weight format/quantization,
global TP, context/concurrency, budgets and private rendezvous configuration.
This library binds that hash; it cannot verify an opaque hash describes a valid
SGLang deployment. Never build the trusted roster from unauthenticated peer
announcements. Reject changing addresses on an existing intent; explicit recovery
must create a new binding rather than silently adopt a replacement node.

Use a group-scoped CA and short-lived leaf certificates with both server/client
EKUs, issued only after authenticated Fleet ownership and assignment checks.
Each leaf has exactly one DNS SAN equal to `topology.certificate_name(rank)`.
Generate private keys on the assigned node through its authenticated credential mechanism;
never store them in Hub group records, app artifacts, logs or environment-wide
configuration. The peer transport primitive does not issue certificates. The
Fleet authority and enrollment interfaces below provide a durable node-local
issuer, local peer keys and public certificate installation. The GPU test
harness still uses ephemeral material in Modal's authenticated control path,
not the production Fleet enrollment interface.

## Node-local enrollment and public certificate delivery

Unix Runners advertise `model-group-credentials: 1`. Owner-authenticated lifecycle
RPCs `group_peer_enroll` and `group_peer_install` take `instance_id`, `revision`
and the exact **prepared** `generation`. Install additionally accepts one
`certificate_pem` and one `ca_pem`, each capped at 16 KiB. Enroll accepts neither.
`FleetLifecycle.group_peer(binding, certificate=..., authority=...)` wraps this
wire interface. These RPCs do not start an engine or provide a complete model
group creation API. Windows does not advertise support for this store.

The installed `model-service` package must contain a regular, bounded
`group-peer.json` with exactly:

```json
{"protocol":1,"rank":0,"topology":{"protocol":1,"owner":"f_0123456789abcdef","group_id":"example","model_sha256":"<64 lowercase hex>","launch_sha256":"<64 lowercase hex>","members":["<exact PeerTopology members>"]},"ca_sha256":"<SHA256 of root certificate DER>"}
```

This is a schema illustration; `members` must contain the real member objects.
The node derives owner and node ID locally, verifies the full prepared resource
hold, and checks that its assigned roster generation equals the prepared generation
plus one. Callers cannot send a replacement roster, CA pin, private key or host path.
The lifecycle mutation lock serializes enrollment against start/stop and returns
busy immediately during another lifecycle operation. Old or cancelled bindings
fail; a later preparation cannot reuse a roster pinned to an earlier generation.

Enrollment generates an ECDSA P-256 key and signed CSR, durably stores the material
under the Runner's private state directory (0700 directories/0600 files), and
returns only the public CSR, exact DNS SAN, topology fingerprint and binding.
Identical retries after a Runner restart return the same CSR. Missing/corrupt
material inside an existing attempt is an error; it never silently rotates a
key. A partially created attempt without a durable record likewise fails closed.
This is node-owner file isolation, not protection against the same OS user or a
privileged attacker deleting/replacing the complete state directory.

Certificate installation requires the pinned root DER hash, a verified chain,
the generated public key, exactly the roster DNS SAN, digital-signature usage,
both client/server EKUs and a valid leaf lifetime of at most 24 hours. Additional
SAN identities, wildcard names, bundled certificates and replacement certificates
are rejected. Retrying the same installed certificate is idempotent. Public
ledger snapshots and artifacts never contain private keys or CSRs. Enrollment
responses contain CSRs, but no private key or local credential path.

This slice is tested through real owner-scoped NATS -> Runner -> persisted
preparation -> enrollment -> certificate installation, including denial of a
different Fleet owner's publish and rejection after cancellation. Manager
restart, corrupt/missing keys, identity/CA mismatch, expiry, private permissions
and Python/Go topology-fingerprint agreement have separate regression tests.

Still required before user-facing group launch: durable public group-creation
intent and package creation, per-instance read-only credential delivery to the actual engine supervisor,
private collective-network admission, peer/process supervision and leader-only
publication. The credential capability alone must
not be treated as permission to run a multi-node engine. The existing single-node
managed package API does not expose this interface as a group launch option.

`tls_contexts(ca_file, certificate_file, key_file)` requires TLS 1.3 and mutual
certificate verification. No OS trust store changes, insecure fallback, wildcard
SANs or CN matching are supported. Every probe verifies an exact certificate
rank, topology fingerprint, sender/receiver and fresh challenge.

## Durable rank-zero certificate authority

Unix Runners also advertise `model-group-authority: 1`. The four owner-scoped
lifecycle methods `group_authority_prepare`, `group_authority_status`,
`group_authority_issue`, and `group_authority_close` use the same authenticated
Fleet control plane. `FleetLifecycle.group_authority` wraps these methods.
No Hub secret store, global CA, additional service, or node library is required.

Prepare accepts the complete `group_topology` on its original rank-zero node.
It durably creates one P-256 group CA, then returns the public CA, DER hash,
fingerprint and expiry. This happens **before** building rank packages, whose
manifests pin that hash. Store the original public topology/CA pin in durable
creation intent before making packages or advancing the group. That creation
flow is not yet implemented by the public Hub group journal.

Status, issue and close require `group_id` and `topology_sha256`; they cannot
replace the roster. Issue additionally takes one `group_claim` with exactly
rank, node_id, instance_id, revision, scope, generation, preparation_id, csr_pem.
The coordinator must derive this claim from durable target/preparation identity
and compare it with a fresh authenticated prepared-instance enrollment; a CSR
alone is not evidence of a prepared engine. The authority validates rank/node/
generation, the derived instance identity, and the CSR's P-256 signature and
single exact SAN. It binds each rank permanently to its first complete claim.
Replacement keys/instances/preparations and shared keys across ranks are refused.

Authority state, private key and the exact issued certificates are persisted
atomically in the original leader's protected Runner directory, outside the
public ledger. Responses contain only public material. Retry after a lost reply,
Agent replacement or Runner restart returns the same CA and certificate. Missing,
corrupt or ambiguous existing state fails closed. Moving to another leader or
recovering lost state requires an explicit new group, never implicit trust reset.
The CA has a 24-hour lifetime; leaves have at most 12 hours and cannot outlive it.
An expired original certificate is not renewed on retry. CA name constraints
limit issuance to the exact topology's DNS suffix, with no subordinate CA.

Close is an irreversible **issuance fence**, not certificate revocation or an
engine stop. It erases the stored signing key and keeps public audit bindings;
if received before Prepare, it writes a tombstone that prevents delayed creation.
Already issued leaves remain cryptographically valid until expiry. The lifecycle
coordinator must still fence starts, stop all original members and confirm actual
resource release. Peer/process health determines inference availability meanwhile.
Closed/uncertain attempts count toward the bounded 128-authority node store; no
automatic deletion or reuse can reopen an old group ID.

Validation uses two independent local Runner state directories, a real NATS
server, owner credentials and narrower node credentials. Both peer packages are
installed/prepared, enrolled and signed; rank-zero Runner plus owner connection
are replaced before installation; retries retain identical CA, CSR and leaf
bytes. Both certificates install, node credentials cannot command the authority,
closed issuance stays closed, and cancellation releases both preparations and
rejects late installation. Separate tests exercise actual TLS 1.3 between the
issued peers, corruption, expiry and same-key rejection. This is control-plane
recovery evidence, not deployed multi-host GPU or group-serving acceptance.

## Coordinator certificate barrier and durable public trust

`GroupJournal` and `HubGroupJournal` accept optional `peer_security` on an initial
unsubmitted lifecycle plan, after exact artifact digests are known. It contains
only the canonical `topology`, `ca_sha256`, and initially false `ready`/`closed`
acknowledgements. Hub checks exact owner/group/node/started-generation bindings,
private canonical endpoints and strict field types. Trust cannot be added,
removed or changed later, and acknowledged barriers cannot be reverted. Existing
lifecycle groups without this optional field retain their previous behavior.

For such groups, `GroupCoordinator` waits until **every** original preparation
is observed with its exact operation, instance, generation and reservations.
It then enrolls each node, checks the returned instance/revision/generation/SAN/
fingerprint, derives the signing claim from durable target and preparation IDs,
requests the original rank-zero authority, and installs the returned public leaf
and CA on that same prepared node. The installed response must confirm the same
CSR and binding. A partial result, timeout, wrong identity or lost acknowledgement
keeps starts behind the barrier. Public CSRs/certificates remain in bounded RPC
responses and node state, not Hub's lifecycle record. Only a pinned CA hash is
stored there; no private material leaves the nodes.

Once all certificates are confirmed, the coordinator persists the acknowledged
barrier and committing phase before sending any engine start on a later pass.
Still-prepared members are rechecked before their start; already-started or
uncertain members are never re-enrolled as replacements. Credential RPC retries
are idempotent against the original node records. This does not change the
existing no-replay rule for potentially accepted engine starts.

Abort closes the original signing authority and persists its acknowledgement.
An unavailable issuer does not block fencing/stopping other confirmed owned
members, but the group cannot become stopped until both the issuance fence and
all resource releases are confirmed. A competing cancellation changes the Hub
revision and prevents an older credential worker from claiming new starts.
Close is still not revocation of an existing TLS certificate.

The joint acceptance uses the actual Hub HTTP API over an ASGI test transport,
a persisted SQLite Hub database, the Runtime Hub client/coordinator, real
owner-authenticated NATS, two independent Fleet Runners and two native CPU
processes. It discards five real issuance/install/close acknowledgements,
rebuilds Hub/Agent objects three times, confirms the certificate barrier before
starting, and checks both original PID birth identities are no longer alive
following stop. Unit fault injection also races cancellation against signing and
holds the authority unavailable while independently cleaning member resources.
This is local control/process evidence, not multi-host GPU performance or an
installed user deployment.

This optional journal extension consumes already built group artifacts. It does
not yet persist a pre-package creation draft, build those artifacts, mount peer
credentials into engines, authorize collective networking or publish a leader.
The user-facing group creation API remains unavailable until those paths and
installed failure/recovery acceptance are connected.

## Bounded execution

1. Load the authorized immutable roster and node-local TLS material.
2. Use `listen(topology, rank)` to bind the exact private control address. Only
   RFC1918 IPv4 or ULA IPv6 literals are accepted; public, wildcard, loopback,
   metadata, link-local, mapped IPv4 and DNS endpoints are rejected.
3. In a caller-owned bounded worker, call `accept_peer` for incoming probes.
   Reject duplicate ranks when collecting receipts; self-probes cannot satisfy
   the check. In parallel, call `probe_peer` for each explicit other rank.
4. Close the listener and join workers on success, cancellation or failure.
   There is no hidden listener, retry, background task, lifecycle mutation or
   persistent preflight success cache in the library.

Each operation has a total deadline of at most 60 seconds, including handshake,
frame and reply. Frames are length-prefixed and capped at 4096 bytes before body
allocation; partial EOF and a stalled/slow peer fail. A success receipt proves
one control-channel exchange at that time, not continued availability, model
readiness, a distributed barrier or permission to launch a replacement.

## SGLang and network isolation

This mTLS channel does **not** encrypt NCCL/Gloo/TCPStore traffic. Private address
validation also cannot establish firewall policy or route isolation. The launch
planner must require an operator/provider-isolated network or an authenticated
private overlay for **all** engine communication, including dynamically opened
collective ports. Fleet HTTP/SSE Relay is not a drop-in NCCL transport.

Modal's region-scoped i6pn network is workspace-private and supports explicit
container address exchange. Treat the workspace as the collective-traffic trust
boundary; do not assume every private address shares a particular fixed prefix
beyond ULA. The acceptance harness opens no public tunnels. See
[Modal cluster networking](https://modal.com/docs/guide/private-networking).

## Continued cohort health

`pantheon.models.group_mesh.PeerMesh` keeps one TLS connection per rank pair
for the lifetime of one immutable attempt. The lower rank connects to the higher
rank; both sides authenticate the exact topology-bound certificates. Framed
protocol2 health messages bind sender, receiver, roster and a fresh echoed nonce.
Only waiting -> loading -> ready transitions are accepted; failure is terminal.
The number of connection workers is bounded by the explicit roster.

Use it as a context manager, call `wait_connected()` before loading an engine,
and use `transition('loading')` / `transition('ready')` only after the respective
local actions. `check(require_ready=True)` returns true only while the local
engine is marked ready and every original peer has a fresh ready observation.
Initial listener refusal may wait up to the startup deadline. Once connected,
TLS/protocol failures, stale heartbeats, state regression and disconnected peers
end the attempt. It never reconnects, replaces a rank or resumes a lost group.
Closing joins all connection workers and shuts down their sockets.

The caller must monitor its exact owned engine and local readiness, call `fail()`
on local failure, withdraw inference readiness immediately on cohort failure,
and stop only its own owned process/container through its lifecycle supervisor.
The mesh itself never kills a PID, releases Fleet reservations, publishes a
model or claims remote resources are free. Explicit Fleet cleanup still confirms
those outcomes in the durable journal. This protocol is not a globally atomic
readiness lease: failure observation is bounded by the configured peer deadline.

The GPU acceptance harness now uses this actual module through startup and
inference. After a successful response it stops only the original rank1 engine;
rank0 must withdraw readiness and exit without a controller-issued stop. Its
local watchdog observes both its original process and the live peer channel.
The real GPU result alone does not isolate NCCL's own failure propagation from
the peer monitor; the three-process CPU test isolates the latter. It records
readiness withdrawal and GPU cleanup separately. This caller integration is an acceptance harness; the installed Fleet
group package and credential-delivery path are still pending integration.

## Rank-specific engine launch

The internal `apps/model-service/sglang_group.py` rank compiler now validates a
complete pinned SGLang plan and produces each rank's command and environment.
Its launch hash covers shared settings and every node's resources, measured GPU
capacities, interface, control address/port and rendezvous port. Global TP2/4/8
is split uniformly over explicit Linux NVIDIA nodes; UUIDs must be distinct and
exclusively reserved. No model precision/context changes or node substitutions
are made to fit a budget. Memory loading overhead is charged to node-local
workers, while weights/KV estimates use global TP. One static fraction is checked
against every GPU, including heterogeneous capacities; each node revalidates its
local physical capacities before using the plan.

Commands pin node rank, node count and bracketed IPv6 or literal IPv4 rendezvous.
HTTP is loopback only; the returned `publishable` flag identifies only the leader
role and is **not** publication authorization or readiness. Collective sockets
select the exact declared interface, address family and SGLang host IP. This
initial transport mode disables RDMA selection and uses sockets; it makes no
RDMA performance claim. `environment()` removes inherited distributed overrides
and cloud model credentials before applying the compiled settings.

The compiler does not inspect a host network or reserve resources itself. Its
caller must verify that the private address belongs to the declared interface,
authenticate every original peer, enforce reservations and network isolation,
and keep observing all owned processes. Managed group packaging/start, durable
certificate delivery, complete-cohort supervision and leader-only publishing
remain to be connected to Fleet/Hub. The ordinary single-node managed API still
rejects group configuration, so incomplete orchestration cannot be enabled there.

SGLang v0.5.20's nonzero ranks have a dummy health server after scheduler
readiness; they do not expose the leader's model catalog. `ready_rank` uses
`/health` for workers and `/ready` plus exact model discovery for rank0. All local
checks and the original process identities must pass before publishing. See the
[pinned engine source](https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/entrypoints/engine.py).

## Fleet credential delivery to a declared group process

On Unix Runners advertising `model-group-runtime:1`, a model-service artifact
must declare exactly one budgeted component with `group_peer:true` and contain
its pinned `group-peer.json`. Starting this artifact requires the exact current
prepared generation and its installed, still-valid node-local certificate. A
start without these checks fails before start hooks or engine creation. Existing
ordinary model-service artifacts without either field retain their launch path.
A pinned group manifest without a declared consumer is rejected at start.

The Runner supplies `PANTHEON_GROUP_CREDENTIALS`. A native process receives an
instance/revision/started-generation-specific private directory. A Linux
container must use `run_as_owner:true` and receives that directory as a read-only
bind at `/run/pantheon/group-peer`. Manifest mounts cannot obscure that path;
caller-supplied `PANTHEON_GROUP_*` environment values cannot select the source.
Only `key.pem`, `certificate.pem`, `ca.pem`, and the public `group-peer.json` are
exported. The full enrollment record, signing key, and shared provider-credential
root are not delivered to this component. Paths and private contents are absent
from the public lifecycle ledger. Native processes share the Runner's OS user;
file permissions are not a sandbox against that same user.

Files are sealed read-only and retries compare their bytes to the original
installed identity. A partial or changed export is not repaired as if unused.
Cancelling an unconsumed preparation removes its partial export. Normal stop or
reconciliation removes a started generation's export only after every original
resource is confirmed gone. Unknown/live resources and blocked stops retain it;
Runner shutdown alone does not remove it. Cleanup never erases durable enrollment
or leader authority state. Installing a credential consumer persists ledger
protocol 3, preventing older Runners from ignoring the new field after downgrade;
public lifecycle RPC stays protocol 1.

Acceptance uses the actual Hub API/journal, Runtime coordinator, authenticated
NATS, two independent Runners and real native Python processes. Each child loads
its leaf/key into an SSL context, loads its root and verifies its manifest's
node/generation before signalling readiness. Dropped acknowledgements and
recreated Hub/Agent objects preserve the original launch; both processes and
runtime credential directories are gone after stop. Unit/race checks cover
missing credentials, partial exports, blocked stop, Runner restart, generation
isolation and rejected mount overrides. This is native delivery evidence. Actual
Docker read-only mount enforcement, full peer-mesh engine supervision, private
collective-network admission, leader-only publication and installed GPU-group
recovery remain separate acceptance gates. No NCCL encryption is implied.

## Validation

```sh
python -m pytest tests/test_model_group_network.py tests/test_model_groups.py tests/test_model_group_hub.py -q
python -m pytest tests/test_model_sglang_group.py tests/test_model_snapshots.py -q
python -m pytest tests/test_model_group_mesh.py -q
uv run --with modal --with cryptography python fleet/scripts/verify-group-private-network.py --output /absolute/new/receipt-directory
uv run --with modal --with cryptography python fleet/scripts/verify-sglang-group-modal.py --output /absolute/new/gpu-receipt-directory
```

The opt-in harness runs the **same module bytes** in two CPU Modal containers,
checks different private addresses, incoming/outgoing certificate identities,
TLS 1.3 and source hashes, and terminates the original calls. Credentials are
ephemeral and omitted from receipts. Synthetic rank/node identities are confined
to this network test; it does not register Fleet nodes or claim lifecycle proof.
Inspect `handle.json` for cleanup status before retrying a failed attempt.

Unit tests use loopback TLS with explicit test-only dial mapping. They cover
configuration/generation changes, wrong rank/CA, message identity/challenges,
rejected address classes, frame fragmentation/limits, timeouts and cleanup.
Neither these tests nor the CPU Modal check prove multi-machine GPU inference,
NCCL isolation, distributed lifecycle recovery, sustained latency or throughput.

The GPU harness uses two independent private Modal containers with one L4 each,
the pinned SGLang image and the same commit-pinned public Qwen snapshot. It
compiles and authenticates both rank plans, waits for both readiness checks,
submits inference at rank0 and stops its own process trees before terminating
the original calls. It records source hashes, GPU identities/budgets and memory
return to baseline. It does not register Fleet nodes or prove distinct physical
host placement, Docker lifecycle or complete production group orchestration.

The September 23 GPU acceptance passed with two distinct L4 GPU UUIDs and
private IPv6 addresses. Both ranks authenticated, became ready, and completed
Qwen2.5-0.5B-Instruct TP2 inference. NCCL logs confirmed `NET/Socket` on the
declared `eth1` addresses. Both original processes exited and measured GPU memory
returned to each rank's baseline; both Modal calls finished successfully and the
App stopped. This is functional evidence, not a performance acceptance: rank0
cold engine readiness took 153.2 seconds and the first 10-token response took
12.6 seconds. Warm throughput, cancellation, failure recovery and distinct
physical-host placement still need separate acceptance.

The first attempt failed before loading because the exported image's file
timestamps differed from its preparation receipt. The corrected harness hashes
every prepared file against the original receipt and records timestamp drift;
all contents matched. It does not rewrite the receipt, relax the production
snapshot fast check, or substitute weights. Receipts are under
`model-services-design-2026-09-21/acceptance-2026-09-21/sglang-group-modal-r2/`
in the development workspace. The original failed attempt is retained too.

The subsequent September 23 live-mesh run also passed inference and the
original-rank failure scenario. Rank0 exited within 5.59 seconds of its local
fault-observation barrier; rank1 exited within 0.154 seconds of its local fault
injection. These are separate local measurements, not synchronized one-way
network timings. Both GPUs returned exactly to their original 9MiB/7MiB baselines;
original calls succeeded and the Modal App stopped. The 10-token response took
6.26 seconds on this different placement and is not a matched performance
comparison. Receipts: `acceptance-2026-09-21/sglang-group-mesh-modal/`.
Distributed cancellation, isolated network-partition behavior and integration
with Fleet-owned process/container supervision remain separate acceptance gates.
