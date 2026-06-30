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
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
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
	name := fs.String("name", node.DefaultName(), "friendly node name")
	labelsCSV := fs.String("labels", "", "comma-separated labels (e.g. gpu,hpc)")
	workDir := fs.String("workdir", ".", "working directory for Tasks")
	natsURL := fs.String("nats", "", "NATS url (dev: bypass the Controller)")
	fleetID := fs.String("fleet", "", "fleet id (dev: bypass the Controller)")
	relaysCSV := fs.String("relays", "", "comma-separated relay multiaddrs")
	noDataplane := fs.Bool("no-dataplane", false, "control plane only (no libp2p / Transfers)")
	stateDir := fs.String("state-dir", defaultStateDir(), "where the stable node id is kept (set per-node to run several on one host)")
	_ = fs.Parse(args)

	nodeID, err := node.Identity(*stateDir)
	must(err)

	if *natsURL == "" || *fleetID == "" {
		fatal("dev mode needs --nats <url> and --fleet <id> (Controller join is separate; key=%q)", redact(*key))
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Data plane (libp2p) — advertise its addresses in the Node record.
	var dp *dataplane.Plane
	netInfo := proto.Net{}
	if !*noDataplane {
		dp, err = dataplane.New(ctx, splitCSV(*relaysCSV))
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

	nc, err := nats.Connect(*natsURL, nats.Name("fleet-runner/"+nodeID))
	must(err)
	defer nc.Drain() //nolint:errcheck

	reg, err := registry.Open(ctx, nc, *fleetID, nodeID, 30*time.Second)
	must(err)
	must(reg.Put(ctx, rec))

	r := runner.New(nc, *fleetID, nodeID, reg, dp, &rec)
	sub, err := r.Serve()
	must(err)
	defer sub.Unsubscribe() //nolint:errcheck

	fmt.Printf("node %q (%s) joined fleet %q — %d cores, %.0f GB RAM, gpu=%q, dataplane=%v\n",
		rec.Name, nodeID, *fleetID, capa.CPUCores, capa.RAMGB, capa.GPU, dp != nil)
	if dp != nil {
		fmt.Printf("  peer %s, %d addr(s)\n", dp.ID(), len(netInfo.Multiaddrs))
	}
	printJSON(rec)
	fmt.Println("serving tasks & transfers; Ctrl-C to leave the fleet…")

	go r.Heartbeat(ctx, 10*time.Second)

	<-ctx.Done()
	fmt.Println("\nleaving fleet…")
	c2, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	_ = reg.Delete(c2)
	cancel()
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

func printJSON(v any) {
	b, _ := json.MarshalIndent(v, "", "  ")
	fmt.Println(string(b))
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
