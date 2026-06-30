// Command fleet is the Pantheon-Fleet Runner: the single binary a user starts
// on any machine to join their Fleet. It detects the machine's capability,
// registers into the Fleet's Registry over NATS, heartbeats, and serves the
// Agent's Tasks.
//
// Phase 1a: control plane + execution. The data plane (libp2p transfers) and
// the Controller join (key -> fleet + scoped creds) land in following steps;
// in dev you bypass the Controller with --nats and --fleet.
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

	"github.com/aristoteleo/pantheon-fleet/internal/control"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
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
                          [--nats <url>] [--fleet <id>]
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
	_ = fs.Parse(args)

	nodeID, err := node.Identity(defaultStateDir())
	must(err)

	// Phase 1 dev path: --nats + --fleet are used directly. The Controller
	// join (key -> fleet id + scoped NATS creds + relay list) is wired next.
	if *natsURL == "" || *fleetID == "" {
		fatal("dev mode needs --nats <url> and --fleet <id> (Controller join is TODO; key=%q)", redact(*key))
	}

	capa := node.DetectCapability(*workDir)
	rec := proto.Node{
		NodeID:     nodeID,
		Name:       *name,
		Labels:     splitCSV(*labelsCSV),
		Capability: capa,
		State:      proto.State{Status: proto.StatusOnline, Load: node.LiveLoad()},
		Net:        proto.Net{Reachability: proto.ReachDirect}, // data plane: TODO (phase 1b)
		Version:    version,
	}

	nc, err := nats.Connect(*natsURL, nats.Name("fleet-runner/"+nodeID))
	must(err)
	defer nc.Drain() //nolint:errcheck

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	reg, err := registry.Open(ctx, nc, *fleetID, nodeID, 30*time.Second)
	must(err)
	must(reg.Put(ctx, rec))

	conn := control.New(nc, *fleetID, nodeID)
	sub, err := conn.ServeCommands(ctx)
	must(err)
	defer sub.Unsubscribe() //nolint:errcheck

	fmt.Printf("node %q (%s) joined fleet %q — %d cores, %.0f GB RAM, gpu=%q\n",
		rec.Name, nodeID, *fleetID, capa.CPUCores, capa.RAMGB, capa.GPU)
	printJSON(rec)
	fmt.Println("serving tasks; Ctrl-C to leave the fleet…")

	tick := time.NewTicker(10 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			fmt.Println("\nleaving fleet…")
			c2, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			_ = reg.Delete(c2)
			cancel()
			return
		case <-tick.C:
			rec.State.Load = node.LiveLoad()
			if err := reg.Put(ctx, rec); err != nil {
				fmt.Fprintln(os.Stderr, "heartbeat failed:", err)
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
