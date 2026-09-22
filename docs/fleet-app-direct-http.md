# Direct workload HTTP over Fleet

The `app-direct-http: 1` node capability adds an authenticated HTTP stream over
Fleet's existing libp2p QUIC host. No additional transport dependency or public
model-server port is needed. Browser cookies and browser application origins
continue to use the existing App gateway.

## Control and data paths

1. A workload creates a `dataplane.NewAppClient` peer. This peer does **not**
   register the node's file receiver or an inbound App handler.
2. It exchanges its existing Fleet identity at Hub's
   `POST /api/fleet/apps/workload-direct-connect`, sending its `peer_id` and the
   exact `node_id`, `instance_id`, `revision`, `generation`, `component`, `port`.
3. Hub derives Fleet ownership from the authenticated user. It creates an
   instance-scoped five-minute credential and contacts Controller's
   `/apps/direct-connect` using the existing Controller service credential.
4. Controller checks the live binding and sends `app_direct_grant` to only that
   node's authenticated NATS subject. The node validates its owner and current
   generation before storing a bounded, expiring grant. The grant binds the
   caller's authenticated QUIC peer to that exact published App port.
5. The reply contains target `peer_id`, `addresses`, one-use `access_token`,
   `expires`, and `transport: fleet_direct`. Hub returns validated fields plus
   the binding, never a login token or the delegated App credential.
6. `appdirect.Dial` opens `/pantheon-fleet/app-http/1.0.0`, sends exactly 64 ASCII
   token bytes and waits for one byte: `1` accepted, `0` rejected. No HTTP request
   is sent before this acknowledgement. The node hashes pending tokens, matches
   the actual TLS-authenticated peer and atomically consumes a successful grant.
7. HTTP requests then travel over QUIC. The node rechecks the exact generation
   and holds a lifecycle use lease for each request. A reverse proxy supplies
   only the delegated App identity; client-supplied credentials, cookies and
   identity/forwarding headers are removed. Existing connector model/configuration
   checks and internal RPC authentication remain in force.

## Failure and resource semantics

- This protocol is direct only. Circuit Relay addresses are not dial candidates,
  mixed target peer identities are rejected, and the actual stream must be direct.
  The model client may explicitly select its existing HTTP Relay **before**
  inference submission where route policy permits. The protocol never replays a
  submitted request, even after a network failure.
- Stale bindings fail at issuance, handshake and every subsequent HTTP request.
  A restart or replacement cannot retarget an existing connection.
- Grants and connections expire after at most five minutes. Lost node control
  connectivity closes connections within the one-second availability check.
  Caller revocation relies on Hub authentication for new grants and expiry for
  an already-issued grant; this is not instantaneous user-token revocation.
- Upstream cancellation follows connection closure. Model clients must retain
  the existing explicit request-cancel-before-disconnect behavior (using another
  independently authorized connection) to preserve cancellation records.
- There are at most 1,024 pending grants and 64 incoming App streams per node;
  unauthenticated handshakes have a five-second deadline. HTTP headers are bounded
  at 32 KiB and idle connections at 15 seconds. Responses stream with backpressure.
- This is workload HTTP, not a generic TCP or WebSocket proxy. CONNECT, browser
  origins, Fetch Metadata and Upgrade requests are rejected. User-supplied URLs
  never select the backend destination.

## Validation and rollout status

Tests exercise real loopback QUIC sockets and HTTP: streaming/binary bodies,
identity stripping, same-token races, stolen-token peer rejection, expiry,
control disconnect, upstream cancellation, stale generations, relay refusal and
bounded pending grants. A Controller-handler-to-QUIC test verifies no Relay
dispatch occurs. Hub tests verify scope, ownership and response validation.

The protocol and Hub exchange alone do not switch existing model calls to direct
transport. The Python workload bridge and model route/client integration still
need implementation and actual same-node/LAN/remote acceptance before rollout.
Existing installed models remain on Fleet HTTP Relay. Loopback transport tests
are not LLM TTFT measurements or cross-platform runtime acceptance.
