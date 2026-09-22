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

- App HTTP is direct only. Authorized Circuit Relay addresses may establish
  peer identification and DCUtR rendezvous, but no App grant or HTTP request is
  written to that connection. Stream creation waits for a direct QUIC connection;
  mixed target peer identities and actual relayed App streams are rejected.
  A relay signaling connection alone is not a successful direct preflight. If
  NAT traversal cannot establish direct connectivity within the setup deadline,
  a strict direct-only call fails without submitting inference.
  Short-lived workload peers first identify with the authorized relay and wait
  for their local DCUtR receiver to register before opening the target circuit.
  This avoids losing the node's one-time inbound hole-punch negotiation during
  public-address discovery. Bootstrap is bounded to three seconds; if it cannot
  prepare, only the target's direct addresses remain eligible.
  The model client may explicitly select its existing HTTP Relay **before**
  inference submission where route policy permits. The protocol never replays a
  submitted request, even after a network failure.
- Stale bindings fail at issuance, handshake and every subsequent HTTP request.
  A restart or replacement cannot retarget an existing connection.
- Grants and their App streams expire after at most five minutes. Reusing the
  underlying QUIC peer does not extend that authority. Lost node control
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

The Python model client uses `fleet app-session` and HTTPX's existing `httpcore`
dependency. A private stdin/stdout bootstrap announces protocol 2 and the peer
ID; bounded binary frames then carry single-use grants and HTTP bytes. Secrets
do not enter arguments, environment variables or logs. There is no localhost
HTTP proxy or new Python QUIC package. The original one-shot `app-dial` protocol
remains available for independent callers.

Eight invocations are admitted per ModelServices client. A pool of at most
sixteen helpers leaves each invocation room for a separate direct cancellation
connection if the usual control gateway is unavailable. Idle peers are reused
only for the same node, and each new App stream requires a fresh grant for the
current immutable instance binding. Reusing a QUIC peer never reuses an App token
or authorizes a replacement generation. The node's connection-rate protection
is unchanged; avoiding a new peer for each call reduces handshakes and burst
rejections on remote paths.

Frames have a one-byte type, a four-byte big-endian length and at most 64 KiB of
payload. Each direction has 256 KiB of byte credit. G supplies the grant, R/F
report authentication, D carries bytes, W returns credit, E marks stream EOF,
and X closes the stream. C is a barrier after both old stream pumps have stopped;
only then may the next grant be sent. An unread response cannot indefinitely
buffer data or prevent X from being processed. Invalid frames close the helper.
The pool reaps idle peers after 20 seconds; the helper independently exits after
30 seconds without an active stream. Parent EOF/termination and event-loop
shutdown also reap helpers. Cache eviction drains admitted requests and retires
idle peers. The helper registers no file receiver or inbound App service.

`direct_only` aliases require a successful direct handshake before HTTP is sent.
Unavailable transport fails closed. Wrong/stale/rejected authority never falls
back, even when Relay is allowed. For an explicitly opted-in direct preference,
only pre-submission availability failures may select the existing Relay; a
30-second, exact-binding negative cache prevents repeated unavailable probes.
Failures after submission never switch transport or replay inference. Cancellation
uses the existing authenticated Fleet App gateway first, even for a direct-only
invocation: this control message carries only the request ID and original config
identity, never the prompt, output or inference body. It remains bound to the
original node/instance/revision/generation; there is no directory refresh or model
reselection. This avoids requiring a fresh NAT traversal while the original stream
is still open. If that gateway is unavailable, the original direct transport can
attempt the same cancellation metadata under the shared five-second deadline.
Rejected/stale authority does not trigger that fallback. A successful cancellation
requires an explicit `cancelled: true` acknowledgement, and installed acceptance
also checks the backend request record. Failure to acknowledge is logged, then the
stream closes; releasing client resources alone is not evidence of cancellation.

Ordinary calls retain Relay by default while measurements are pending; the
`ModelServices(prefer_direct=True)` SDK option enables comparative testing of
direct preference with policy-permitted Relay fallback. This choice does not
enable cross-node/model or cloud fallback. The result records actual transport.

The cross-language suite runs Python -> built Fleet CLI -> real QUIC -> HTTP,
including tools/usage/SSE, embeddings, direct-only aliases, authorization errors,
truncated streams, real connector cancellation, large binary responses and
unread-pipe cleanup. Run `PYTHONPATH=. python -m pytest tests/test_model_direct.py`
from the runtime root with Go available. Pure Go tests also cover bootstrap
bounds, invalid grants and parent disconnects. These tests use synthetic control
records and do not replace installed Hub/Fleet or LAN/remote acceptance.

Current installed models remain on Fleet HTTP Relay. Rollout and actual
same-node/LAN/remote model measurements are still required. Loopback transport
tests are not LLM TTFT measurements or cross-platform runtime acceptance.
