# Encrypted model-group collective namespace

`internal/groupnetwork` is the Linux kernel network primitive for managed model
groups. It is not yet connected to the installed Fleet lifecycle. Nodes must
continue to refuse `model-group-private-network` until the integration below is
complete; passing these tests is not permission to advertise the capability.

The parent namespace creates a WireGuard interface, then moves it into a fresh,
node-generated namespace. The encrypted UDP socket retains the parent namespace;
only the inner private member addresses exist inside. Each peer gets a single
/32 or /128 allowed address and route, no subnet/default route. The kernel checks
peer keys for both source identity and traffic encryption, covering dynamically
opened TCP collective ports rather than only the mTLS control port. This follows
[WireGuard's documented namespace integration](https://www.wireguard.com/netns/).

No parent interface or route is moved or changed. `lo` and `wg0` are activated;
some Linux kernels also instantiate inert fallback tunnel devices which remain
down and address-free. Workloads must run without NET_ADMIN and NET_RAW (preferably
without any capabilities). Do not give model containers privileged access.

The node checks the original local underlay address before native commands. The
rank-sorted endpoint roster, canonical public keys, distinct private addresses,
exact local key match and original peer manifest are checked before changes.
Private keys enter `wg` through stdin, not argv, files, command logs or errors.
The kernel primitive receives the original key from the node's `OverlayStore`;
it never generates a replacement.
The primitive adds no Go module dependency. Only nodes using this group mode need
Linux WireGuard support, iproute2 and wireguard-tools plus a trusted privileged
network setup path. Single-node/attached/API services do not acquire these needs.

Each new namespace/interface has an exclusive random node-generated name. Setup
failure attempts cleanup, retaining a non-nil lease if the result is uncertain.
In particular a failed namespace-add acknowledgement retains its handle for
inspection. `Close` is idempotent after acknowledged cleanup and never frees an
uncertain lease merely because time passed. Callers must reap every joined
workload before deleting the namespace; unlinking it cannot kill live holders.
This in-memory primitive does **not** yet provide crash recovery or automatic
adoption of old namespace names.

## Durable node enrollment

`groupcredentials.OverlayStore` persists a node-generated X25519 key in a private
0600 record under a 0700 directory. The group ID, owner, node, exact manifest
(including rank, generation and CA pin), and UDP endpoint are immutable. The
directory is keyed by owner/node/group rather than the changing plan hash. Missing,
corrupt, duplicate-field or permissive records fail closed instead of regenerating
keys. The lifecycle manager serializes access under its node-state lock.

Owner-authenticated lifecycle RPCs use an explicit `group_overlay` object:

- `group_overlay_prepare`: `manifest` and private UDP `address`; returns the
  original public endpoint, including after a lost response or Runner restart.
- `group_overlay_pin`: `group_id`, `topology_sha256`, and the entire rank-sorted
  `endpoints` list. Every key/socket is unique and the local entry must match the
  stored original. Repeating the exact roster is allowed; replacing it is not.
- `group_overlay_status`: `group_id` and `topology_sha256`; public state only.
- `group_overlay_close`: the same identity; irreversibly fences enrollment and
  deletes the stored private key. It writes a tombstone even before prepare arrives.

States are `prepared`, `pinned`, and `closed`, not network readiness. Private key
material is available only to node-internal code after roster pinning; no RPC
exports it. Closing enrollment does not revoke a key already loaded into a kernel
interface, reap engines, or prove resource release. Kernel namespace ownership,
restart reconciliation and lifecycle cleanup must still be implemented separately.
The RPCs do not run network commands, reserve UDP ports, install dependencies or
advertise `model-group-private-network`. They are available on the control plane
without asserting the host can execute a Linux collective namespace.

## Acceptance

Run `go test -p 2 ./internal/groupnetwork ./internal/groupcredentials` for roster,
key, route, command-boundary and cleanup-handle checks. For actual kernel traffic:

```sh
python3 fleet/scripts/verify-group-overlay.py --output /tmp/group-overlay-check
```

The opt-in harness requires a running local Linux Docker daemon and a cached
Linux Python image. It creates one resource-bounded privileged *test container*,
mounts only a read-only test binary, installs its test tools, and removes only its
own container afterward. It never changes host routing or a Fleet deployment.
Within that container it verifies:

- Keys and the original endpoint roster survive reconstructed node stores, then
  unprivileged, zero-capability processes exchange TCP through kernel WireGuard.
- A foreign key claiming the same inner address cannot connect; the original
  authenticated peer remains usable afterward.
- No route to external IPs, host underlay IPs or undeclared members exists.
- Kernel WireGuard counters confirm encrypted traffic in both directions.
- Parent routes are unchanged and every created namespace is removed.

This is one-kernel namespace acceptance, not physical multi-host, IPv6-kernel,
SGLang GPU or installed Fleet acceptance. IPv6 route compilation has unit coverage.

## Required integration

1. Persist namespace ownership/intent before effects and implement restart-time
   reconciliation; enrollment persistence alone cannot recover kernel resources.
2. Exchange and atomically pin the node endpoint/public-key records in the creation
   journal; never use a replaced key or endpoint after a lost acknowledgement.
3. Allocate private overlay addresses and compile the launch plan with `wg0`.
4. Keep model containers network-isolated while constructing their namespace;
   join only after admission and drop all network administration/raw capabilities.
5. Connect authenticated readiness/leader inference from Fleet without adding a
   general route into the namespace; preserve credential/resource ownership.
6. Reap workloads, remove namespaces, confirm leases released on cancel/partition;
   verify on installed Linux GPU nodes before advertising the network capability.
