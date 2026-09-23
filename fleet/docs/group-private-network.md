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
Deliver private keys through an authenticated node-local credential mechanism;
never store them in Hub group records, app artifacts, logs or environment-wide
configuration. Certificate issuance/delivery is the caller's responsibility and
is not implemented by this primitive. The test harness uses ephemeral material
in Modal's authenticated control path, not real Fleet credentials.

`tls_contexts(ca_file, certificate_file, key_file)` requires TLS 1.3 and mutual
certificate verification. No OS trust store changes, insecure fallback, wildcard
SANs or CN matching are supported. Every probe verifies an exact certificate
rank, topology fingerprint, sender/receiver and fresh challenge.

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

SGLang node ranks, global versus node-local TP/memory budgets, private interface
selection, rendezvous ports, complete-cohort readiness and leader-only publishing
still need engine integration. SGLang v0.5.20's nonzero ranks have a dummy health
server after scheduler readiness; they do not expose the leader's model catalog.
Do not reuse the single-node `/v1/models` readiness test on workers. See the
[pinned engine source](https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/entrypoints/engine.py).

## Validation

```sh
python -m pytest tests/test_model_group_network.py tests/test_model_groups.py tests/test_model_group_hub.py -q
uv run --with modal --with cryptography python fleet/scripts/verify-group-private-network.py --output /absolute/new/receipt-directory
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
