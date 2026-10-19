package dataplane

import (
	"context"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/client"
	"github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/relay"
	"github.com/libp2p/go-libp2p/p2p/protocol/holepunch"
)

func TestWorkloadPeerHasNoInboundFileReceiver(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p, err := NewAppClient(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	if protocols := p.host.Mux().Protocols(); slices.Contains(protocols, TransferProto) || slices.Contains(protocols, AppProto) {
		t.Fatal("workload helper exposed node data-plane receivers")
	}
}

func TestAppRendezvousNeverOpensProtocolOnRelay(t *testing.T) {
	for _, upgrade := range []bool{false, true} {
		name := "cancel-before-direct"
		if upgrade {
			name = "direct-upgrade"
		}
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(t.Context(), 8*time.Second)
			defer cancel()
			rh, err := libp2p.New(libp2p.ListenAddrStrings("/ip4/127.0.0.1/udp/0/quic-v1"))
			if err != nil {
				t.Fatal(err)
			}
			defer rh.Close()
			r, err := relay.New(rh)
			if err != nil {
				t.Fatal(err)
			}
			defer r.Close()
			// This peer deliberately has no automatic hole punching. Establish
			// the direct connection explicitly only after observing rendezvous.
			target, err := libp2p.New(libp2p.ListenAddrStrings("/ip4/127.0.0.1/udp/0/quic-v1"))
			if err != nil {
				t.Fatal(err)
			}
			defer target.Close()
			var appStreams atomic.Int32
			target.SetStreamHandler(AppProto, func(s network.Stream) {
				appStreams.Add(1)
				_ = s.Close()
			})
			if _, err := client.Reserve(ctx, target, peer.AddrInfo{ID: rh.ID(), Addrs: rh.Addrs()}); err != nil {
				t.Fatal(err)
			}
			p, err := NewAppClient(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			// Simulate the asynchronous DCUtR registration after the relay has
			// identified this short-lived workload peer. The target must never
			// receive a circuit connection before this receiver is installed.
			var earlyRendezvous atomic.Bool
			target.Network().Notify(&network.NotifyBundle{ConnectedF: func(_ network.Network, conn network.Conn) {
				if conn.RemotePeer() == p.host.ID() && !slices.Contains(p.host.Mux().Protocols(), holepunch.Protocol) {
					earlyRendezvous.Store(true)
				}
			}})
			address := rh.Addrs()[0].String() + "/p2p/" + rh.ID().String() + "/p2p-circuit/p2p/" + target.ID().String()
			type result struct {
				stream network.Stream
				err    error
			}
			done := make(chan result, 1)
			go func() { s, e := p.OpenAppStream(ctx, []string{address}); done <- result{s, e} }()
			for p.host.Network().Connectedness(rh.ID()) != network.Connected {
				select {
				case <-ctx.Done():
					t.Fatal("relay identification did not establish")
				case <-time.After(10 * time.Millisecond):
				}
			}
			time.Sleep(100 * time.Millisecond)
			if len(target.Network().ConnsToPeer(p.host.ID())) != 0 {
				t.Fatal("App rendezvous raced the hole-punch receiver")
			}
			p.host.SetStreamHandler(holepunch.Protocol, func(s network.Stream) { _ = s.Reset() })
			for p.host.Network().Connectedness(target.ID()) != network.Limited {
				select {
				case result := <-done:
					if result.stream != nil {
						_ = result.stream.Close()
					}
					t.Fatalf("App open ended before limited rendezvous: %v", result.err)
				case <-ctx.Done():
					t.Fatal("rendezvous did not establish")
				case <-time.After(10 * time.Millisecond):
				}
			}
			select {
			case result := <-done:
				if result.stream != nil {
					_ = result.stream.Close()
				}
				t.Fatalf("App stream did not wait for a direct connection: %v", result.err)
			case <-time.After(100 * time.Millisecond):
			}
			if appStreams.Load() != 0 {
				t.Fatal("App protocol leaked through Relay")
			}
			if earlyRendezvous.Load() {
				t.Fatal("target connected before the hole-punch receiver was ready")
			}
			if upgrade {
				if err := target.Connect(network.WithForceDirectDial(ctx, "test-reversal"), peer.AddrInfo{ID: p.host.ID(), Addrs: p.host.Addrs()}); err != nil {
					t.Fatal(err)
				}
			} else {
				cancel()
			}
			select {
			case result := <-done:
				if !upgrade {
					if result.err == nil || appStreams.Load() != 0 {
						t.Fatal("cancelled rendezvous opened an App stream")
					}
				} else {
					if result.err != nil {
						t.Fatal(result.err)
					}
					defer result.stream.Close()
					if result.stream.Conn().Stat().Limited || strings.Contains(result.stream.Conn().RemoteMultiaddr().String(), "/p2p-circuit") {
						t.Fatal("App protocol opened on Relay after rendezvous")
					}
				}
			case <-time.After(2 * time.Second):
				t.Fatal("App rendezvous did not release")
			}
		})
	}
}
