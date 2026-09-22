package dataplane

import (
	"context"
	"fmt"
	"strings"

	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/protocol"
	"github.com/multiformats/go-multiaddr"
)

const AppProto protocol.ID = "/pantheon-fleet/app-http/1.0.0"

// NewAppClient has no inbound file-transfer or App handlers. Workloads must not
// inherit the node's file receiver merely to open an authenticated App stream.
func NewAppClient(ctx context.Context) (*Plane, error) {
	return newPlane(ctx, nil, 0, false, false)
}

func (p *Plane) HandleApp(handler network.StreamHandler) { p.host.SetStreamHandler(AppProto, handler) }

// OpenAppStream uses Circuit Relay only for peer identification and DCUtR
// rendezvous. The App protocol (including its grant) is opened only after a
// direct connection exists. The caller decides whether a failed preflight may
// fall back to the separate authenticated HTTP Relay transport. No application
// payload is sent here, and no request is retried here.
func (p *Plane) OpenAppStream(ctx context.Context, addresses []string) (network.Stream, error) {
	if len(addresses) == 0 || len(addresses) > 32 {
		return nil, fmt.Errorf("invalid App peer addresses")
	}
	var target peer.AddrInfo
	for _, raw := range addresses {
		if len(raw) > 1024 {
			return nil, fmt.Errorf("invalid App peer address")
		}
		ma, err := multiaddr.NewMultiaddr(raw)
		if err != nil {
			return nil, fmt.Errorf("invalid App peer address")
		}
		info, err := peer.AddrInfoFromP2pAddr(ma)
		if err != nil || (target.ID != "" && target.ID != info.ID) {
			return nil, fmt.Errorf("ambiguous App peer identity")
		}
		target.ID = info.ID
		if strings.Contains(raw, "/p2p-circuit") || strings.Contains(raw, "/quic-v1/") {
			target.Addrs = append(target.Addrs, info.Addrs...)
		}
	}
	if len(target.Addrs) == 0 {
		return nil, fmt.Errorf("direct App transport unavailable")
	}
	if err := p.host.Connect(ctx, target); err != nil {
		return nil, err
	}
	// Identify on an inbound circuit connection triggers the existing node's
	// hole puncher. Swarm.NewStream waits for a non-limited connection unless
	// AllowLimitedConn is set. Do not use that flag here, or open an App stream
	// on the signaling connection. NoDial prevents a fresh stream dial from
	// bypassing this rendezvous and its caller's setup deadline.
	s, err := p.host.NewStream(network.WithNoDial(ctx, "fleet-app-direct"), target.ID, AppProto)
	if err != nil {
		return nil, err
	}
	if s.Conn().Stat().Limited || strings.Contains(s.Conn().RemoteMultiaddr().String(), "/p2p-circuit") ||
		!strings.Contains(s.Conn().RemoteMultiaddr().String(), "/quic-v1") {
		_ = s.Reset()
		return nil, fmt.Errorf("direct App transport required")
	}
	return s, nil
}
