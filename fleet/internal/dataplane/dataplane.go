// Package dataplane will embed go-libp2p (QUIC transport + DCUtR hole-punching
// + Circuit Relay v2 + Noise/TLS) to move bulk data directly between Nodes,
// with NAT traversal and a relay fallback — no external VPN.
//
// Phase 1b (next step). For now this only declares the surface the Runner will
// fill in: advertise this Node's multiaddrs into the Registry, and open/accept
// Transfer streams to/from other Nodes.
package dataplane

import "github.com/aristoteleo/pantheon-fleet/internal/proto"

// Plane is the data-plane handle a Runner will hold once libp2p is wired in.
type Plane interface {
	// Multiaddrs returns the libp2p addresses to advertise in the Registry.
	Multiaddrs() []string
	// Reachability reports whether this Node can be reached directly or only
	// via a Relay (e.g. strict-NAT / HPC nodes).
	Reachability() string
	// Send streams a file to a peer Node, reporting progress.
	Send(req proto.TransferRequest, progress func(proto.TransferProgress)) error
	// Close shuts the libp2p host down.
	Close() error
}

// TODO(phase-1b): implement Plane with a go-libp2p host:
//   - QUIC transport, Noise/TLS
//   - Circuit Relay v2 client (reserve on the Fleet's relays)
//   - DCUtR for hole punching
//   - a /pantheon-fleet/transfer/1.0.0 stream protocol (chunked, sha256, resumable)
