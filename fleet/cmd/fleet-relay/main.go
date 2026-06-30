// Command fleet-relay is a public-IP libp2p Circuit Relay v2 node. Runners that
// can't hole-punch a direct connection fall back to relaying their Transfers
// through one of these. Its printed multiaddrs are what you pass to a Runner /
// the Controller as --relays.
//
// For a real deployment it needs a STABLE identity (so the advertised peer id
// survives restarts) and must ANNOUNCE its public address (a cloud VM's listen
// address is usually a private IP). Both are handled below.
package main

import (
	"context"
	"crypto/rand"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/relay"
	"github.com/multiformats/go-multiaddr"
)

func main() {
	port := flag.Int("port", 4250, "UDP port for the QUIC listener")
	identityPath := flag.String("identity", defaultIdentityPath(), "path to the relay's persistent libp2p key (stable peer id)")
	announce := flag.String("announce", "", "public IP/host to announce so peers can dial this relay (required in production)")
	limitMB := flag.Int("limit-mb", 0, "per-relayed-connection data cap in MB, each direction (0 = unlimited)")
	limitMin := flag.Int("limit-min", 0, "per-relayed-connection duration cap in minutes (0 = unlimited)")
	flag.Parse()

	priv, err := loadOrCreateIdentity(*identityPath)
	if err != nil {
		log.Fatal(err)
	}

	opts := []libp2p.Option{
		libp2p.Identity(priv),
		libp2p.ListenAddrStrings(
			fmt.Sprintf("/ip4/0.0.0.0/udp/%d/quic-v1", *port),
			fmt.Sprintf("/ip6/::/udp/%d/quic-v1", *port),
		),
		libp2p.ForceReachabilityPublic(),
	}
	if *announce != "" {
		pub, err := multiaddr.NewMultiaddr(fmt.Sprintf("/ip4/%s/udp/%d/quic-v1", *announce, *port))
		if err != nil {
			log.Fatal("bad --announce: ", err)
		}
		// Advertise the public address first, then the discovered ones.
		opts = append(opts, libp2p.AddrsFactory(func(addrs []multiaddr.Multiaddr) []multiaddr.Multiaddr {
			return append([]multiaddr.Multiaddr{pub}, addrs...)
		}))
	}

	h, err := libp2p.New(opts...)
	if err != nil {
		log.Fatal(err)
	}

	// The Circuit Relay v2 default caps each relayed connection at 128KB / 2min,
	// which silently truncates any real Transfer. A Fleet relay exists to carry
	// bulk data, so default to *unlimited* and let an operator opt into caps for
	// anti-abuse. WithLimit(nil) => no data/duration limit.
	var relayLimit *relay.RelayLimit // nil = unlimited
	if *limitMB > 0 || *limitMin > 0 {
		relayLimit = &relay.RelayLimit{Data: int64(*limitMB) << 20, Duration: time.Duration(*limitMin) * time.Minute}
		if *limitMB == 0 {
			relayLimit.Data = 1 << 60 // effectively unlimited
		}
		if *limitMin == 0 {
			relayLimit.Duration = 24 * time.Hour // effectively unlimited
		}
	}
	if _, err := relay.New(h, relay.WithLimit(relayLimit)); err != nil {
		log.Fatal("enable relay service: ", err)
	}
	if relayLimit == nil {
		fmt.Println("relay limits: unlimited (set --limit-mb / --limit-min to cap)")
	} else {
		fmt.Printf("relay limits: %d MB/dir, %s/conn\n", relayLimit.Data>>20, relayLimit.Duration)
	}

	fmt.Printf("fleet-relay  peer=%s\n", h.ID())
	fmt.Println("advertise these as --relays:")
	for _, a := range h.Addrs() {
		fmt.Printf("  %s/p2p/%s\n", a, h.ID())
	}
	if *announce == "" {
		fmt.Fprintln(os.Stderr, "warning: no --announce set; only listen (likely private) addresses are advertised")
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	_ = h.Close()
}

func defaultIdentityPath() string {
	if d, err := os.UserConfigDir(); err == nil {
		return filepath.Join(d, "pantheon-fleet", "relay.key")
	}
	return "relay.key"
}

// loadOrCreateIdentity persists the relay's libp2p key so its peer id is stable.
func loadOrCreateIdentity(path string) (crypto.PrivKey, error) {
	if b, err := os.ReadFile(path); err == nil {
		return crypto.UnmarshalPrivateKey(b)
	}
	priv, _, err := crypto.GenerateEd25519Key(rand.Reader)
	if err != nil {
		return nil, err
	}
	b, err := crypto.MarshalPrivateKey(priv)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	if err := os.WriteFile(path, b, 0o600); err != nil {
		return nil, err
	}
	return priv, nil
}
