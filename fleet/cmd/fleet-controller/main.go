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
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
)

func main() {
	addr := flag.String("addr", ":8099", "HTTP listen address")
	natsURL := flag.String("nats", "nats://localhost:4222", "NATS url advertised to Nodes")
	relaysCSV := flag.String("relays", "", "comma-separated relay multiaddrs advertised to Nodes")
	enableAuth := flag.Bool("auth", true, "issue per-fleet scoped NATS credentials (decentralized JWT)")
	stateDir := flag.String("state-dir", defaultStateDir(), "where the Authority's keys are persisted")
	emitCfg := flag.String("emit-nats-config", "", "write a nats-server config for this Authority to this path, then keep serving")
	natsListen := flag.String("nats-listen", "0.0.0.0:4222", "listen address baked into --emit-nats-config")
	jsStore := flag.String("js-store-dir", "./fleet-jetstream", "JetStream store dir baked into --emit-nats-config")
	flag.Parse()

	relays := splitCSV(*relaysCSV)

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
		// TODO: validate req.Key against the PantheonOS hub. For now, derive a
		// stable Fleet id from the key.
		fid := deriveFleet(req.Key)
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

	log.Printf("fleet-controller listening on %s (nats=%s, auth=%v)", *addr, *natsURL, *enableAuth)
	log.Fatal(http.ListenAndServe(*addr, mux))
}

// deriveFleet maps a key to a stable Fleet id. Replace with hub-backed
// user lookup; one Fleet per user.
func deriveFleet(key string) string {
	h := sha256.Sum256([]byte(key))
	return "f_" + hex.EncodeToString(h[:])[:16]
}

func defaultStateDir() string {
	if d, err := os.UserConfigDir(); err == nil {
		return filepath.Join(d, "pantheon-fleet-controller")
	}
	return ".pantheon-fleet-controller"
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
