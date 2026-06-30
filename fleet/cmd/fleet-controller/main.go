// Command fleet-controller is the Fleet's gatekeeper. A Runner POSTs its API
// key to /join; the Controller resolves the key to the user's Fleet and returns
// how to reach the control plane, the data-plane relays, and — when auth is on —
// a NATS credential scoped to just that Fleet.
//
// Key validation here is still a stub (it derives a Fleet id from the key hash);
// the real path validates the key against the PantheonOS hub. The NATS security,
// however, is real: with --auth the Controller is the decentralized-JWT Authority
// and mints per-fleet, subject-scoped credentials (see internal/auth).
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

var httpClient = &http.Client{Timeout: 10 * time.Second}

func main() {
	addr := flag.String("addr", ":8099", "HTTP listen address")
	natsURL := flag.String("nats", "nats://localhost:4222", "NATS url advertised to Nodes")
	relaysCSV := flag.String("relays", "", "comma-separated relay multiaddrs advertised to Nodes")
	enableAuth := flag.Bool("auth", true, "issue per-fleet scoped NATS credentials (decentralized JWT)")
	allowedKeysCSV := flag.String("allowed-keys", "", "comma-separated keys allowed to /join (empty = OPEN; gate before exposing publicly)")
	allowedKeysFile := flag.String("allowed-keys-file", "", "file of allowed keys, one per line (# comments); preferred over --allowed-keys for secrets")
	hubURL := flag.String("hub-url", "", "PantheonOS hub base URL — validate keys via its platform-key API (preferred over --allowed-keys)")
	hubToken := flag.String("hub-token", os.Getenv("FLEET_CONTROLLER_SERVICE_TOKEN"), "service token for hub key validation (or env FLEET_CONTROLLER_SERVICE_TOKEN)")
	stateDir := flag.String("state-dir", defaultStateDir(), "where the Authority's keys are persisted")
	emitCfg := flag.String("emit-nats-config", "", "write a nats-server config for this Authority to this path, then keep serving")
	natsListen := flag.String("nats-listen", "0.0.0.0:4222", "listen address baked into --emit-nats-config")
	jsStore := flag.String("js-store-dir", "./fleet-jetstream", "JetStream store dir baked into --emit-nats-config")
	flag.Parse()

	relays := splitCSV(*relaysCSV)

	// Access gate. Preferred: validate keys against the hub (--hub-url). Interim:
	// a static allowlist. Neither set = OPEN (dev only).
	allowed, err := loadAllowedKeys(*allowedKeysCSV, *allowedKeysFile)
	if err != nil {
		log.Fatalf("allowed keys: %v", err)
	}
	switch {
	case *hubURL != "":
		log.Printf("gate: hub validation via %s", *hubURL)
		if *hubToken == "" {
			log.Printf("  WARNING: no --hub-token / FLEET_CONTROLLER_SERVICE_TOKEN — validation will fail")
		}
	case len(allowed) > 0:
		log.Printf("gate: %d key(s) allowed to /join (interim allowlist)", len(allowed))
	default:
		log.Printf("gate: OPEN — any key creates a fleet. Set --hub-url or --allowed-keys(-file) before exposing publicly.")
	}

	var authority *auth.Authority
	if *enableAuth {
		var err error
		if authority, err = auth.Bootstrap(*stateDir); err != nil {
			log.Fatalf("auth bootstrap: %v", err)
		}
		log.Printf("auth: ON — FLEET account %s (keys in %s)", authority.AccountPubKey(), *stateDir)
		if *emitCfg != "" {
			cfg := authority.ServerConfig(*natsListen, *jsStore)
			if err := os.WriteFile(*emitCfg, []byte(cfg), 0o600); err != nil {
				log.Fatalf("emit nats config: %v", err)
			}
			log.Printf("wrote nats-server config to %s — start it with: nats-server -c %s", *emitCfg, *emitCfg)
		}
	} else {
		log.Printf("auth: OFF — Nodes connect to %s without credentials (dev only)", *natsURL)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok")) //nolint:errcheck
	})
	mux.HandleFunc("/join", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req proto.JoinRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		if strings.TrimSpace(req.Key) == "" {
			http.Error(w, "missing key", http.StatusUnauthorized)
			return
		}
		var fid string
		if *hubURL != "" {
			vfid, ok, err := validateViaHub(*hubURL, *hubToken, req.Key)
			if err != nil {
				http.Error(w, "hub validation unavailable: "+err.Error(), http.StatusBadGateway)
				return
			}
			if !ok {
				http.Error(w, "key not allowed", http.StatusForbidden)
				return
			}
			fid = vfid // the hub maps the key to the user's fleet
		} else {
			if len(allowed) > 0 && !allowed[req.Key] {
				http.Error(w, "key not allowed", http.StatusForbidden)
				return
			}
			fid = deriveFleet(req.Key)
		}
		resp := proto.JoinResponse{
			FleetID: fid,
			NatsURL: *natsURL,
			Relays:  relays,
		}
		if authority != nil {
			creds, err := authority.MintFleetUser(fid)
			if err != nil {
				http.Error(w, "mint credentials: "+err.Error(), http.StatusInternalServerError)
				return
			}
			resp.Creds = string(creds)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp) //nolint:errcheck
	})

	// /nodes lists a Fleet's registered Nodes for the hub's Cluster panel. The hub
	// (the only holder of the service token) passes the caller's own fleet id; the
	// Controller mints a short-lived scoped credential and reads the registry KV.
	mux.HandleFunc("/nodes", func(w http.ResponseWriter, r *http.Request) {
		if *hubToken == "" || r.Header.Get("Authorization") != "Bearer "+*hubToken {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		fid := strings.TrimSpace(r.URL.Query().Get("fleet"))
		if fid == "" {
			http.Error(w, "missing fleet", http.StatusBadRequest)
			return
		}
		if authority == nil {
			http.Error(w, "auth disabled — cannot read registry", http.StatusServiceUnavailable)
			return
		}
		nodes, err := readFleetNodes(*natsURL, fid, authority)
		if err != nil {
			http.Error(w, "registry read: "+err.Error(), http.StatusBadGateway)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{ //nolint:errcheck
			"fleet_id": fid,
			"count":    len(nodes),
			"nodes":    nodes,
		})
	})

	log.Printf("fleet-controller listening on %s (nats=%s, auth=%v)", *addr, *natsURL, *enableAuth)
	log.Fatal(http.ListenAndServe(*addr, mux))
}

// deriveFleet maps a key to a stable Fleet id (used in interim allowlist mode).
// With --hub-url the fleet id comes from the hub instead (keyed to the user).
func deriveFleet(key string) string {
	h := sha256.Sum256([]byte(key))
	return "f_" + hex.EncodeToString(h[:])[:16]
}

// validateViaHub asks the PantheonOS hub to validate a platform key, returning
// the user's fleet id. ok=false means the key is unknown/revoked (deny the
// join); a non-nil error means the hub itself was unreachable/misconfigured.
func validateViaHub(hubURL, token, key string) (fleetID string, ok bool, err error) {
	body, _ := json.Marshal(map[string]string{"key": key})
	req, err := http.NewRequest(http.MethodPost, strings.TrimRight(hubURL, "/")+"/api/platform-keys/validate", bytes.NewReader(body))
	if err != nil {
		return "", false, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := httpClient.Do(req)
	if err != nil {
		return "", false, err
	}
	defer resp.Body.Close() //nolint:errcheck
	if resp.StatusCode != http.StatusOK {
		// 401/403 = our service token is wrong; 5xx = hub problem. Either way the
		// hub couldn't authoritatively validate, so surface it (don't silently deny).
		return "", false, fmt.Errorf("hub returned %d", resp.StatusCode)
	}
	var out struct {
		Valid   bool   `json:"valid"`
		FleetID string `json:"fleet_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", false, err
	}
	return out.FleetID, out.Valid, nil
}

// readFleetNodes mints a short-lived fleet-scoped credential (the Controller is
// the Authority, so it can mint for any fleet), connects to NATS, and reads the
// Fleet's Node records from its registry KV bucket FLEET_<fid>_NODES.
func readFleetNodes(natsURL, fid string, authority *auth.Authority) ([]proto.Node, error) {
	creds, err := authority.MintFleetUser(fid)
	if err != nil {
		return nil, fmt.Errorf("mint creds: %w", err)
	}
	f, err := os.CreateTemp("", "fleet-*.creds")
	if err != nil {
		return nil, err
	}
	defer os.Remove(f.Name()) //nolint:errcheck
	if _, err := f.Write(creds); err != nil {
		f.Close() //nolint:errcheck
		return nil, err
	}
	f.Close() //nolint:errcheck

	nc, err := nats.Connect(natsURL,
		nats.Name("fleet-controller-registry"),
		nats.UserCredentials(f.Name()),
		nats.CustomInboxPrefix("_INBOX_"+fid),
		nats.Timeout(5*time.Second),
	)
	if err != nil {
		return nil, fmt.Errorf("nats connect: %w", err)
	}
	defer nc.Drain() //nolint:errcheck
	js, err := jetstream.New(nc)
	if err != nil {
		return nil, err
	}
	return readNodes(js, fid), nil
}

// readNodes reads every Node record from a Fleet's registry KV. A missing bucket
// (no Node has joined yet) is not an error — it returns an empty slice.
func readNodes(js jetstream.JetStream, fleet string) []proto.Node {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	kv, err := js.KeyValue(ctx, proto.RegistryBucket(fleet))
	if err != nil {
		return []proto.Node{}
	}
	keys, err := kv.Keys(ctx)
	if err != nil {
		return []proto.Node{}
	}
	out := make([]proto.Node, 0, len(keys))
	for _, k := range keys {
		e, err := kv.Get(ctx, k)
		if err != nil {
			continue
		}
		var n proto.Node
		if json.Unmarshal(e.Value(), &n) == nil {
			out = append(out, n)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

func defaultStateDir() string {
	if d, err := os.UserConfigDir(); err == nil {
		return filepath.Join(d, "pantheon-fleet-controller")
	}
	return ".pantheon-fleet-controller"
}

// loadAllowedKeys builds the /join allowlist from a CSV flag and/or a file
// (one key per line, # comments and blanks ignored).
func loadAllowedKeys(csv, file string) (map[string]bool, error) {
	allowed := map[string]bool{}
	for _, k := range splitCSV(csv) {
		allowed[k] = true
	}
	if file != "" {
		b, err := os.ReadFile(file)
		if err != nil {
			return nil, err
		}
		for _, line := range strings.Split(string(b), "\n") {
			line = strings.TrimSpace(line)
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			allowed[line] = true
		}
	}
	return allowed, nil
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
