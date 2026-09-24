# Browser model artifacts over Direct transport

The App gateway accepts a bounded SDP offer plus one immutable artifact receipt at
`POST /__fleet/model-media`. It requires the exact Atrium Origin and an existing
instance-scoped browser cookie. The gateway supplies the binding and workload
credential over the owner's Fleet command channel; they are never browser inputs
or part of the answer. The node rechecks generation/use leases and stops transfers
when the node control connection or original generation is lost.

A peer carries one ordered reliable `fleet-model-media-v1` data channel. After
verifying a non-relayed selected ICE pair, the browser sends `start`. The node reads
fixed 1 MiB ranges from the bound literal-loopback backend, validating headers and
streaming 16 KiB chunks with backpressure. Both endpoints verify the final SHA-256
and size before the browser creates an output Blob. Signaling never carries media
bytes. There are at most 16 live sessions, each limited to 64 MiB and 180 seconds.

UDP sockets belong to the node, rather than individual previews. Sessions reuse a
Pion UDP multiplexer while retaining separate ICE/DTLS identities. Network-address
changes create a new socket generation; existing peers keep their original one.
Up to four generations may exist, including any still physically closing. The
last peer releases a retired generation, and node shutdown waits for socket close.
This preserves IPv4 and IPv6 while avoiding repeated macOS UDP close stalls on
short-lived previews. No RTP codec/interceptor work is installed for these
artifact-only peers.

Current gathering uses host candidates (loopback and reachable LAN interfaces)
with mDNS resolution. No STUN/TURN servers are configured. Unreachable Direct
connections fail explicitly; this feature does not imply general WAN NAT
traversal and never silently retries through Relay. Existing Relay previews retain
their separate HTTPS range path.

Tests include real Pion byte transfer/error/cancel, repeated session cleanup,
network-generation ownership, cookie/Origin/authority fencing and browser
cancellation. `testdata/browserfixture` serves a disposable loopback fixture for
manual Chrome verification using the actual built TypeScript client. It does not
replace installed Fleet/Hub/Atrium or matched cross-node performance acceptance.
