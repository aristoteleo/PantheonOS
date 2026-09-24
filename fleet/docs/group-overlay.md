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
The caller owns private-key persistence; no API here generates replacements.
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

- Unprivileged, zero-capability processes exchange TCP through kernel WireGuard.
- A foreign key claiming the same inner address cannot connect; the original
  authenticated peer remains usable afterward.
- No route to external IPs, host underlay IPs or undeclared members exists.
- Kernel WireGuard counters confirm encrypted traffic in both directions.
- Parent routes are unchanged and every created namespace is removed.

This is one-kernel namespace acceptance, not physical multi-host, IPv6-kernel,
SGLang GPU or installed Fleet acceptance. IPv6 route compilation has unit coverage.

## Required integration

1. Persist node-owned key and namespace intent before effects, with original
   owner/node/group/generation, closed fences and restart-time reconciliation.
2. Exchange and atomically pin endpoint/public-key records in the creation
   journal; never use a replaced key or endpoint after a lost acknowledgement.
3. Allocate private overlay addresses and compile the launch plan with `wg0`.
4. Keep model containers network-isolated while constructing their namespace;
   join only after admission and drop all network administration/raw capabilities.
5. Connect authenticated readiness/leader inference from Fleet without adding a
   general route into the namespace; preserve credential/resource ownership.
6. Reap workloads, remove namespaces, confirm leases released on cancel/partition;
   verify on installed Linux GPU nodes before advertising the network capability.
