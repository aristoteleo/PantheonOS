// Command fleet is the Pantheon-Fleet Runner: the single binary a user starts
// on any machine to join their Fleet. It detects the machine's capability,
// brings up the data plane (libp2p), registers into the Fleet's Registry over
// NATS, heartbeats, and serves the Agent's Tasks and Transfers.
//
// The Controller join (key -> fleet + scoped creds) is a separate service; in
// dev you bypass it with --nats and --fleet.
package main

import (
	"context"
	"crypto/ed25519"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
	"github.com/aristoteleo/pantheon-fleet/internal/join"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
	"github.com/nats-io/nats.go"
)

const version = "0.1.0"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "up":
		cmdUp(os.Args[2:])
	case "version", "--version", "-v":
		fmt.Println("pantheon-fleet runner", version)
	default:
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `pantheon-fleet runner

Usage:
  fleet up   --key <key> [--name <name>] [--labels a,b] [--workdir <dir>]
                          [--nats <url>] [--fleet <id>] [--no-dataplane]
  fleet version

In Phase 1 (dev) you can bypass the Controller with --nats <url> and --fleet <id>.`)
}

func cmdUp(args []string) {
	fs := flag.NewFlagSet("up", flag.ExitOnError)
	key := fs.String("key", "", "PantheonOS API key (pbk_...) — selects your Fleet")
	joinToken := fs.String("join-token", "", "single-use join token (preferred over --key; from the Cluster panel)")
	name := fs.String("name", node.DefaultName(), "friendly node name")
	labelsCSV := fs.String("labels", "", "comma-separated labels (e.g. gpu,hpc)")
	workDir := fs.String("workdir", ".", "working directory for Tasks")
	controllerURL := fs.String("controller", "", "Controller URL — resolves --key to your Fleet")
	natsURL := fs.String("nats", "", "NATS url (dev: bypass the Controller)")
	fleetID := fs.String("fleet", "", "fleet id (dev: bypass the Controller)")
	relaysCSV := fs.String("relays", "", "comma-separated relay multiaddrs")
	p2pPort := fs.Int("p2p-port", 0, "fixed UDP/QUIC port for the data plane (0 = random)")
	forceRelay := fs.Bool("force-relay", false, "force this Node to reserve a relay slot (strict-NAT nodes)")
	noDataplane := fs.Bool("no-dataplane", false, "control plane only (no libp2p / Transfers)")
	stateDir := fs.String("state-dir", defaultStateDir(), "where the stable node id is kept (set per-node to run several on one host)")
	_ = fs.Parse(args)

	nodeID, err := node.Identity(*stateDir)
	must(err)
	// The node's Ed25519 key (private key never leaves this machine) proves
	// possession when refreshing credentials. See docs/fleet-security-model.md.
	nodeKey, err := node.LoadOrCreateKey(*stateDir)
	must(err)
	nodePub := node.PubB64(nodeKey)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	relays := splitCSV(*relaysCSV)

	// Resolve the Fleet from the API key via the Controller. Dev mode bypasses
	// it with --nats + --fleet.
	var credsPath, refreshToken string
	if *controllerURL != "" {
		asg, err := join.Join(ctx, *controllerURL, proto.JoinRequest{
			Key: *key, JoinToken: *joinToken, NodePub: nodePub,
		})
		must(err)
		*natsURL, *fleetID = asg.NatsURL, asg.FleetID
		refreshToken = asg.RefreshToken
		if len(asg.Relays) > 0 {
			relays = asg.Relays
		}
		if asg.Creds != "" {
			credsPath = filepath.Join(*stateDir, "fleet.creds")
			must(os.WriteFile(credsPath, []byte(asg.Creds), 0o600))
		}
		fmt.Printf("controller: key %s -> fleet %q via %s (auth=%v)\n", redact(*key), *fleetID, *natsURL, credsPath != "")
	}
	if *natsURL == "" || *fleetID == "" {
		fatal("need --controller <url> --key <key>, or dev --nats <url> --fleet <id>")
	}

	// Data plane (libp2p) — advertise its addresses in the Node record.
	var dp *dataplane.Plane
	netInfo := proto.Net{}
	if !*noDataplane {
		dp, err = dataplane.New(ctx, relays, *p2pPort, *forceRelay)
		must(err)
		defer dp.Close() //nolint:errcheck
		netInfo.Multiaddrs = dp.Multiaddrs()
		netInfo.Reachability = dp.Reachability()
	}

	capa := node.DetectCapability(*workDir)
	rec := proto.Node{
		NodeID:     nodeID,
		Name:       *name,
		Labels:     splitCSV(*labelsCSV),
		Capability: capa,
		State:      proto.State{Status: proto.StatusOnline, Load: node.LiveLoad()},
		Net:        netInfo,
		Version:    version,
	}

	natsOpts := []nats.Option{nats.Name("fleet-runner/" + nodeID)}
	if credsPath != "" {
		// Scoped creds: replies/requests use a per-fleet inbox prefix so the
		// _INBOX namespace is isolated per fleet too (matches the JWT scope).
		natsOpts = append(natsOpts, nats.UserCredentials(credsPath), nats.CustomInboxPrefix("_INBOX_"+*fleetID))
	}
	nc, err := nats.Connect(*natsURL, natsOpts...)
	must(err)
	defer nc.Drain() //nolint:errcheck

	reg, err := registry.Open(ctx, nc, *fleetID, nodeID, 30*time.Second)
	must(err)
	must(reg.Put(ctx, rec))

	r := runner.New(nc, *fleetID, nodeID, reg, dp, &rec)
	sub, err := r.Serve()
	must(err)
	defer sub.Unsubscribe() //nolint:errcheck

	gpu := capa.GPU
	if gpu == "" {
		gpu = "none"
	}
	reach := netInfo.Reachability
	if reach == "" {
		reach = "control-plane only"
	}
	fmt.Printf("\n  \x1b[32m●\x1b[0m %s is online in fleet %s\n", rec.Name, *fleetID)
	fmt.Printf("    %s/%s · %d cores · %.0f GB RAM · GPU: %s · %s\n",
		capa.OS, capa.Arch, capa.CPUCores, capa.RAMGB, gpu, reach)
	fmt.Println("serving tasks & transfers; Ctrl-C to leave the fleet…")

	go r.Heartbeat(ctx, 10*time.Second)

	// Refresh the short-lived credential before it expires. The NATS client
	// re-reads credsPath on its next reconnect (which the server triggers at
	// expiry), so a legit node stays online while a leaked cred dies fast.
	// See docs/fleet-security-model.md.
	if credsPath != "" && *controllerURL != "" && refreshToken != "" {
		go refreshCredsLoop(ctx, *controllerURL, *fleetID, refreshToken, nodePub, nodeKey, credsPath)
	}

	<-ctx.Done()
	fmt.Println("\nleaving fleet…")
	c2, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	_ = reg.Delete(c2)
	cancel()
}

// refreshCredsLoop re-mints the node's short-lived credential before it expires
// and rewrites credsPath. The NATS client picks up the new creds on its next
// (re)connect. See docs/fleet-security-model.md.
func refreshCredsLoop(ctx context.Context, controllerURL, fleetID, refreshToken, nodePub string, nodeKey ed25519.PrivateKey, credsPath string) {
	// Renew at ~75% of the access TTL so a valid cred is always on disk. A small
	// absolute floor avoids pathologically tight loops; it must stay well below
	// the TTL (a 1-minute floor would exceed a short TTL and refresh too late).
	interval := auth.AccessTTL * 3 / 4
	if interval < 5*time.Second {
		interval = 5 * time.Second
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			// Prove possession of the node key over a fresh challenge, then swap
			// the refresh token for a new short-lived credential (no API key).
			ts := time.Now().Unix()
			sig := node.Sign(nodeKey, token.PoPChallenge(nodePub, fleetID, ts))
			out, err := join.Refresh(ctx, controllerURL, proto.TokenRequest{
				RefreshToken: refreshToken, TS: ts, Sig: sig,
			})
			if err != nil {
				fmt.Printf("cred refresh failed (will retry): %v\n", err)
				continue
			}
			if err := os.WriteFile(credsPath, []byte(out.Creds), 0o600); err != nil {
				fmt.Printf("cred refresh write failed: %v\n", err)
			}
		}
	}
}

func defaultStateDir() string {
	if d, err := os.UserConfigDir(); err == nil {
		return filepath.Join(d, "pantheon-fleet")
	}
	return ".pantheon-fleet"
}

func splitCSV(s string) []string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func redact(key string) string {
	if len(key) <= 8 {
		return "***"
	}
	return key[:6] + "…"
}

func must(err error) {
	if err != nil {
		fatal("%v", err)
	}
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "error: "+format+"\n", a...)
	os.Exit(1)
}
