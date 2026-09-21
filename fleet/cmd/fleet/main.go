// Command fleet is the Pantheon-Fleet Runner: the single binary a user starts
// on any machine to join their Fleet. It detects the machine's capability,
// brings up the data plane (libp2p), registers into the Fleet's Registry over
// NATS, heartbeats, and serves the Agent's Tasks and Transfers.
//
// The Controller join (key -> fleet + scoped creds) is a separate service; in
// dev you bypass it with --nats and --fleet.
package main

import (
	"github.com/aristoteleo/pantheon-fleet/internal/nativecapture"
	"github.com/aristoteleo/pantheon-fleet/internal/streamrtc"

	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
	"github.com/aristoteleo/pantheon-fleet/internal/join"
	"github.com/aristoteleo/pantheon-fleet/internal/node"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/registry"
	"github.com/aristoteleo/pantheon-fleet/internal/runner"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
	"github.com/nats-io/nats.go"
)

const version = "0.4.0-native.8"

func main() {
	handled, code, err := appLaunchBootstrap()
	must(err)
	if handled {
		os.Exit(code)
	}
	defer finishAppLaunch(0)
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "up":
		cmdUp(os.Args[2:])
	case "capture":
		cmdCapture(os.Args[2:])
	case "prime":
		cmdPrime(os.Args[2:])
	case "version", "--version", "-v":
		fmt.Println("pantheon-fleet runner", version)
	default:
		usage()
		os.Exit(2)
	}
}

// cmdPrime is the macOS folder-permission primer. Launched via `open` (so it runs
// as a LaunchServices-registered app), it reads configured shared folders (the
// home directory by default), triggering OS permission prompts where needed.
// Once the user clicks Allow the grant sticks to the signed .app identity, so a
// later FOREGROUND `fleet up` (run directly, with live output + Ctrl-C) has access
// too — giving the same terminal experience as Linux. On later runs the folders
// are already granted, so this returns instantly. No-op off macOS. See install.sh.
func cmdPrime(args []string) {
	if runtime.GOOS != "darwin" {
		return
	}
	// Resolve the same defaults and saved choices as `up`.
	stateDir, requested, disabled, err := primeShareOptions(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return
	}
	roots, err := configureShares(stateDir, requested, disabled)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return
	}
	for _, path := range roots {
		root, err := os.OpenRoot(path)
		if err != nil {
			continue
		}
		probes := []string{"."}
		// Listing home alone does not request macOS access to its protected
		// subfolders. Probe these through os.Root so symlinks cannot escape
		// the shared boundary. No files are read or modified.
		if home, err := os.UserHomeDir(); err == nil {
			if home, err = filepath.EvalSymlinks(home); err == nil && path == home {
				probes = append(probes, "Desktop", "Documents", "Downloads")
			}
		}
		for _, probe := range probes {
			if f, err := root.Open(probe); err == nil {
				_, _ = f.Readdirnames(1)
				_ = f.Close()
			}
		}
		_ = root.Close()
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `pantheon-fleet runner

Usage:
  fleet up   [--controller <url> --join-token <token>] [--name <name>]
                          [--labels a,b] [--workdir <dir>] [--no-dataplane]
                          [--share-dir <absolute-path> ...] [--no-files] [--no-capture-setup]
  fleet capture doctor       (inspect native capture availability)
  fleet capture permissions  (open the native streaming permission guide)
  fleet version

After the first Controller join, plain fleet up resumes from the local state.
Files shares your home directory by default. Use --no-files to turn it off,
or --share-dir to share only specific folders. Choices are saved on this node.
In Phase 1 (dev) you can bypass the Controller with --nats <url> and --fleet <id>.`)
}

func cmdUp(args []string) {
	fs := flag.NewFlagSet("up", flag.ExitOnError)
	key := fs.String("key", "", "PantheonOS API key (pbk_...) — selects your Fleet")
	joinToken := fs.String("join-token", "", "single-use join token (preferred over --key; from the Cluster panel)")
	name := fs.String("name", node.DefaultName(), "friendly node name")
	labelsCSV := fs.String("labels", "", "comma-separated labels (e.g. gpu,hpc)")
	kind := fs.String("kind", envOr("FLEET_NODE_KIND", proto.KindMachine),
		"node kind: sandbox|pod|machine|frontend")
	capsCSV := fs.String("caps", os.Getenv("FLEET_NODE_CAPS"),
		"app placement capabilities (proc,fs:workspace,display,gpu,net,dom); fs:local is derived from shared folders")
	workDir := fs.String("workdir", ".", "working directory for Tasks")
	controllerURL := fs.String("controller", "", "Controller URL — resolves --key to your Fleet")
	natsURL := fs.String("nats", "", "NATS url (dev: bypass the Controller)")
	fleetID := fs.String("fleet", "", "fleet id (dev: bypass the Controller)")
	relaysCSV := fs.String("relays", "", "comma-separated relay multiaddrs")
	p2pPort := fs.Int("p2p-port", 0, "fixed UDP/QUIC port for the data plane (0 = random)")
	forceRelay := fs.Bool("force-relay", true, "reserve a relay slot so peers on other networks can reach this node (default on; direct addrs are still advertised — pass --force-relay=false only for a node with a stable public address)")
	noDataplane := fs.Bool("no-dataplane", false, "control plane only (no libp2p / Transfers)")
	stateDir := fs.String("state-dir", defaultStateDir(), "where the stable node id is kept (set per-node to run several on one host)")
	var shares sharedDirs
	fs.Var(&shares, "share-dir", "share only these folders instead of home (repeatable; use '~' for home; saved locally)")
	noFiles := fs.Bool("no-files", false, "turn off Files access and remember this choice (default: share home)")
	noCaptureSetup := fs.Bool("no-capture-setup", false, "skip the macOS streaming permission guide (headless/unattended use)")
	_ = fs.Parse(args)
	fileRoots, err := configureShares(*stateDir, shares, *noFiles)
	must(err)

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

	// Resolve the Fleet from a fresh join, persisted state, or dev arguments.
	var credsPath, refreshToken string
	var savedState fleetState
	var hasSavedState bool
	freshJoin := *controllerURL != "" && (*key != "" || *joinToken != "")
	devMode := *controllerURL == "" && *natsURL != "" && *fleetID != ""
	if !freshJoin && !devMode {
		var err error
		savedState, hasSavedState, err = loadFleetState(*stateDir)
		must(err)
	}
	var persistedState fleetState
	if freshJoin {
		asg, err := join.Join(ctx, *controllerURL, proto.JoinRequest{
			Key: *key, JoinToken: *joinToken, NodePub: nodePub, NodeID: nodeID,
		})
		must(err)
		*natsURL, *fleetID = asg.NatsURL, asg.FleetID
		refreshToken = asg.RefreshToken
		if len(asg.Relays) > 0 {
			relays = asg.Relays
		}
		if asg.Creds != "" {
			credsPath = filepath.Join(*stateDir, "fleet.creds")
			must(writePrivateFile(credsPath, []byte(asg.Creds)))
		}
		if refreshToken != "" {
			persistedState = fleetState{
				ControllerURL: *controllerURL,
				FleetID:       *fleetID,
				NatsURL:       *natsURL,
				Relays:        append([]string(nil), relays...),
				RefreshToken:  refreshToken,
			}
			must(saveFleetState(*stateDir, persistedState))
		}
		fmt.Printf("controller: key %s -> fleet %q via %s (auth=%v)\n", redact(*key), *fleetID, *natsURL, credsPath != "")
	} else if *natsURL != "" || *fleetID != "" {
		// Dev mode: both values are supplied directly and no persisted Controller
		// assignment or credentials are needed.
	} else if hasSavedState {
		if *controllerURL != "" && *controllerURL != savedState.ControllerURL {
			fatal("--controller %q does not match the saved Controller %q", *controllerURL, savedState.ControllerURL)
		}
		*controllerURL = savedState.ControllerURL
		*natsURL = savedState.NatsURL
		*fleetID = savedState.FleetID
		refreshToken = savedState.RefreshToken
		persistedState = savedState
		if len(relays) == 0 {
			relays = append([]string(nil), savedState.Relays...)
		}
		credsPath = filepath.Join(*stateDir, "fleet.creds")
		// Refresh before opening NATS: the cached access credential may have
		// expired while the node was stopped, but the node-bound refresh token
		// remains valid.
		ts := time.Now().Unix()
		sig := node.Sign(nodeKey, token.PoPChallenge(nodePub, *fleetID, ts))
		fresh, err := join.Refresh(ctx, *controllerURL, proto.TokenRequest{
			RefreshToken: refreshToken, TS: ts, Sig: sig,
		})
		must(err)
		must(writePrivateFile(credsPath, []byte(fresh.Creds)))
		if fresh.RefreshToken != "" {
			persistedState, err = persistFleetStateRefreshToken(*stateDir, persistedState, fresh.RefreshToken)
			must(err)
			refreshToken = fresh.RefreshToken
		}
	}
	if *natsURL == "" || *fleetID == "" {
		fatal("need --controller <url> --key <key> or --join-token <token>, dev --nats <url> --fleet <id>, or a prior successful join")
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
	if capa.Runtimes == nil {
		capa.Runtimes = map[string]string{}
	}
	capa.Runtimes["runner"] = version
	if *capsCSV != "" {
		capa.Caps = splitCSV(*capsCSV)
	} else {
		capa.Caps = node.DefaultCaps(*kind, capa)
	}
	// fs:local is factual, never a remote/caller claim. It does not mean workspace.
	filteredCaps := capa.Caps[:0]
	for _, cap := range capa.Caps {
		if cap != "fs:local" {
			filteredCaps = append(filteredCaps, cap)
		}
	}
	capa.Caps = filteredCaps
	if len(fileRoots) > 0 {
		capa.Caps = append(capa.Caps, "fs:local")
	}
	for _, path := range fileRoots {
		capa.FileRoots = append(capa.FileRoots, filepath.ToSlash(path))
	}
	rec := proto.Node{
		NodeID:     nodeID,
		Name:       *name,
		Kind:       *kind,
		Labels:     splitCSV(*labelsCSV),
		Capability: capa,
		State:      proto.State{Status: proto.StatusOnline, Load: node.LiveLoad()},
		Net:        netInfo,
		Version:    version,
	}

	// Authentication failures wake the credential refresh loop. Network/auth
	// recovery retains this connection so existing services keep subscriptions.
	kick := make(chan struct{}, 1)
	renewable := credsPath != "" && *controllerURL != "" && refreshToken != ""
	natsOpts := append(fleetRecoveryOptions(ctx, stop, kick, renewable), nats.Name("fleet-runner/"+nodeID))
	if credsPath != "" {
		// Scoped creds: replies/requests use a per-fleet inbox prefix so the
		// _INBOX namespace is isolated per fleet too (matches the JWT scope).
		natsOpts = append(natsOpts, nats.UserCredentials(credsPath), nats.CustomInboxPrefix("_INBOX_"+*fleetID))
	}
	nc, err := connectFleetNATS(ctx, *natsURL, natsOpts)
	must(err)
	defer nc.Drain() //nolint:errcheck

	reg, err := registry.Open(ctx, nc, *fleetID, nodeID, 30*time.Second)
	must(err)
	must(reg.Put(ctx, rec))

	r := runner.New(nc, *fleetID, nodeID, reg, dp, &rec)
	defer func() {
		shutdown, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := r.Apps().Close(shutdown); err != nil {
			fmt.Printf("App service shutdown did not finish: %v\n", err)
		}
	}()
	must(r.EnableLifecycle(filepath.Join(*stateDir, "apps", *fleetID)))
	if *controllerURL != "" {
		if err := r.EnableServices(ctx, *controllerURL); err != nil {
			fmt.Printf("App service gateway unavailable: %v\n", err)
		}
	}
	defer r.CloseLifecycle() //nolint:errcheck
	registerBuiltins(r, nc)
	registerNodeFiles(r, nc, fileRoots, nodeID)
	sub, err := r.Serve()
	must(err)
	defer sub.Unsubscribe() //nolint:errcheck

	// Runtime info file: local processes (the sandbox's Pantheon worker, the
	// prestart warmer) learn this node's coordinates from here. Written only
	// now — AFTER the cmd subscription is live — so seeing the file means
	// the node answers; writing it earlier made prestart race the subscribe
	// window and trip its breaker on a healthy node.
	if info, err := json.Marshal(map[string]string{
		"node_id": nodeID, "fleet_id": *fleetID, "nats_url": *natsURL,
	}); err == nil {
		_ = os.WriteFile(filepath.Join(*stateDir, "runtime.json"), append(info, '\n'), 0o644)
	}

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
	if len(fileRoots) > 0 {
		fmt.Printf("Files: sharing %d folder(s): %s\n", len(fileRoots), strings.Join(fileRoots, ", "))
		fmt.Println("       Use --no-files to turn off Files access, or --share-dir to limit folders.")
	} else {
		fmt.Println("Files: not shared. Restart with --share-dir <folder> to enable.")
	}

	go r.Heartbeat(ctx, 10*time.Second)
	// Permissions are optional for ordinary tasks, so onboarding never blocks
	// node registration or credential renewal. The native guide remembers Later.
	if runtime.GOOS == "darwin" && *kind == proto.KindMachine {
		go func() {
			status, err := nativecapture.Probe()
			if err != nil || !status.Available || status.Ready() {
				return
			}
			fmt.Println("Streaming: permission setup needed. Run fleet capture permissions to configure Screen Recording and Accessibility; other apps remain available.")
			ssh := os.Getenv("SSH_CONNECTION") != "" || os.Getenv("SSH_TTY") != ""
			if nativecapture.SetupNeeded(runtime.GOOS, status, ssh, *noCaptureSetup) {
				if err := nativecapture.Setup(ctx, true); err != nil && ctx.Err() == nil {
					fmt.Printf("Streaming permission guide: %v. Run fleet capture permissions to try again.\n", err)
				}
			}
		}()
	}

	// Refresh the short-lived credential before it expires. The NATS client
	// re-reads credsPath on its next reconnect (which the server triggers at
	// expiry), so a legit node stays online while a leaked cred dies fast.
	// See docs/fleet-security-model.md.
	if renewable {
		if persistedState.RefreshToken == "" {
			persistedState = fleetState{
				ControllerURL: *controllerURL,
				FleetID:       *fleetID,
				NatsURL:       *natsURL,
				Relays:        append([]string(nil), relays...),
				RefreshToken:  refreshToken,
			}
		}
		go refreshCredsLoop(ctx, stop, kick, *controllerURL, *fleetID, refreshToken, nodePub, nodeKey, credsPath, *stateDir, persistedState, func() {
			// Wake the reconnect backoff after writing a complete new credential.
			// Do not interrupt healthy requests on scheduled renewals.
			if nc.IsReconnecting() {
				_ = nc.ForceReconnect()
			}
		})
	}

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

// envOr reads an environment variable with a fallback default.
func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
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
	finishAppLaunch(1)
	os.Exit(1)
}

func cmdCapture(args []string) {
	if len(args) == 1 && args[0] == "peer" {
		if err := streamrtc.Run(os.Stdin, os.Stdout); err != nil {
			fatal("media peer: %v", err)
		}
		return
	}
	if len(args) != 1 || (args[0] != "doctor" && args[0] != "permissions") {
		fmt.Fprintln(os.Stderr, "Usage: fleet capture doctor|permissions")
		finishAppLaunch(2)
		os.Exit(2)
	}
	path := nativecapture.Path()
	if path == "" {
		fmt.Fprintln(os.Stderr, "Native capture helper not installed. Update Fleet on this Mac/Windows node.")
		finishAppLaunch(1)
		os.Exit(1)
	}
	flag := "--probe"
	if args[0] == "permissions" {
		flag = "--permissions"
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	cmd := exec.CommandContext(ctx, path, flag)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		finishAppLaunch(1)
		os.Exit(1)
	}
}
