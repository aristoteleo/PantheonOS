package dataplane

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/libp2p/go-libp2p/core/event"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/protocol"
	"github.com/libp2p/go-libp2p/p2p/protocol/holepunch"
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
	var relays []peer.AddrInfo
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
		if strings.Contains(raw, "/p2p-circuit") {
			prefix, _ := multiaddr.SplitFunc(ma, func(c multiaddr.Component) bool { return c.Protocol().Code == multiaddr.P_CIRCUIT })
			relay, err := peer.AddrInfoFromP2pAddr(prefix)
			if err != nil {
				return nil, fmt.Errorf("invalid App rendezvous address")
			}
			relays = append(relays, *relay)
		}
		if strings.Contains(raw, "/p2p-circuit") || strings.Contains(raw, "/quic-v1/") {
			target.Addrs = append(target.Addrs, info.Addrs...)
		}
	}
	if len(target.Addrs) == 0 {
		return nil, fmt.Errorf("direct App transport unavailable")
	}
	if len(relays) > 0 && p.host.Network().Connectedness(target.ID) != network.Connected && !p.prepareAppRendezvous(ctx, relays) {
		// A public/LAN target may still be directly reachable. Do not open its
		// circuit before our DCUtR receiver is ready: the node initiates hole
		// punching once at inbound Identify, so an early negotiation is lost.
		target.Addrs = slices.DeleteFunc(target.Addrs, func(a multiaddr.Multiaddr) bool {
			return strings.Contains(a.String(), "/p2p-circuit")
		})
		if len(target.Addrs) == 0 {
			return nil, fmt.Errorf("App rendezvous unavailable")
		}
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

// Identify with the authorized relay discovers our observed public address.
// libp2p installs the DCUtR receiver asynchronously after that discovery. Wait
// for the actual local protocol registration before connecting through the
// circuit to the App peer; elapsed time alone is not proof of readiness.
func (p *Plane) prepareAppRendezvous(ctx context.Context, relays []peer.AddrInfo) bool {
	ready := func() bool { return slices.Contains(p.host.Mux().Protocols(), holepunch.Protocol) }
	if ready() {
		return true
	}
	updates, err := p.host.EventBus().Subscribe(new(event.EvtLocalProtocolsUpdated))
	if err != nil {
		return false
	}
	defer updates.Close()
	bootstrap, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	for _, relay := range relays {
		if p.host.Connect(bootstrap, relay) == nil {
			break
		}
	}
	for !ready() {
		select {
		case <-bootstrap.Done():
			return false
		case _, ok := <-updates.Out():
			if !ok {
				return false
			}
		}
	}
	return true
}
