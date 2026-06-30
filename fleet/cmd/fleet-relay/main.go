// Command fleet-relay is a public-IP libp2p Circuit Relay v2 node. Runners that
// can't hole-punch a direct connection fall back to relaying their Transfers
// through one of these. Its printed multiaddrs are what you pass to a Runner /
// the Controller as --relays.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os/signal"
	"syscall"

	"github.com/libp2p/go-libp2p"
	"github.com/libp2p/go-libp2p/p2p/protocol/circuitv2/relay"
)

func main() {
	port := flag.Int("port", 4250, "UDP port for the QUIC listener")
	flag.Parse()

	h, err := libp2p.New(
		libp2p.ListenAddrStrings(
			fmt.Sprintf("/ip4/0.0.0.0/udp/%d/quic-v1", *port),
			fmt.Sprintf("/ip6/::/udp/%d/quic-v1", *port),
		),
	)
	if err != nil {
		log.Fatal(err)
	}
	if _, err := relay.New(h); err != nil {
		log.Fatal("enable relay service:", err)
	}

	fmt.Println("fleet-relay running — advertise these as --relays:")
	for _, a := range h.Addrs() {
		fmt.Printf("  %s/p2p/%s\n", a, h.ID())
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	_ = h.Close()
}
