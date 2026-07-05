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
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
	"github.com/nats-io/jwt/v2"
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

	// Refresh-token signing key (Ed25519), persisted with the Authority keys.
	// Issues/verifies refresh tokens; the node key provides proof-of-possession
	// at /token, so a leaked refresh token is useless. See docs/fleet-security-model.md.
	refreshPriv, err := loadOrCreateRefreshKey(*stateDir)
	if err != nil {
		log.Fatalf("refresh key: %v", err)
	}
	refreshPub := refreshPriv.Public().(ed25519.PublicKey)
	const refreshTTL = 30 * 24 * time.Hour
	const joinTTL = 15 * time.Minute
	consumed := newJTISet()          // single-use enforcement for join tokens
	revoked := loadRevoked(*stateDir)                                    // node revocation list
	nodePubs := loadNodePubs(filepath.Join(*stateDir, "nodepubs.json")) // node_id -> node_pub
	userPubs := loadNodePubs(filepath.Join(*stateDir, "userpubs.json")) // node_id -> current user cred pubkey

	mux := http.NewServeMux()

	// resolveFleet maps a presented key to its fleet id, accepting EITHER a
	// hub-validated credential (a session-derived fleet JWT, or a pbk_ key the hub
	// knows) OR a locally-allowlisted pbk_ key. Hub validation is tried first when
	// configured; a credential the hub doesn't recognise — or an unreachable hub —
	// falls back to the local allowlist. This lets one controller accept
	// session-derived creds and static keys simultaneously, which is the migration
	// path off static bearer keys. ok=false = neither path accepts it.
	resolveFleet := func(key string) (fid string, ok bool) {
		key = strings.TrimSpace(key)
		if key == "" {
			return "", false
		}
		if *hubURL != "" {
			if vfid, vok, err := validateViaHub(*hubURL, *hubToken, key); err == nil && vok {
				return vfid, true
			}
		}
		if len(allowed) > 0 && !allowed[key] {
			return "", false
		}
		return deriveFleet(key), true
	}

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
		var fid string
		viaJoinToken := false
		switch {
		case strings.TrimSpace(req.JoinToken) != "":
			// Single-use join token (preferred): verify signature + expiry, then
			// consume its jti so it cannot be replayed to add another node.
			jp, err := token.VerifyJoin(refreshPub, req.JoinToken)
			if err != nil {
				http.Error(w, "invalid join token: "+err.Error(), http.StatusUnauthorized)
				return
			}
			if !consumed.consume(jp.JTI, jp.Exp) {
				http.Error(w, "join token already used", http.StatusForbidden)
				return
			}
			fid = jp.FleetID
			viaJoinToken = true // an owner-minted grant: may reinstate a revoked node
		case strings.TrimSpace(req.Key) != "":
			vfid, ok := resolveFleet(req.Key)
			if !ok {
				http.Error(w, "key not allowed", http.StatusForbidden)
				return
			}
			fid = vfid
		default:
			http.Error(w, "missing key or join token", http.StatusUnauthorized)
			return
		}
		// A revoked node stays out even if it re-joins: revocation is durable by
		// node key (nodePub), enforced at BOTH /join and /token. Without this a
		// kicked node that still holds the API key would just re-join and mint a
		// fresh credential — the account-level kick only drops the live connection,
		// so this is what actually keeps it out.
		//
		// A valid single-use join token is the exception: only the fleet owner can
		// mint one, so presenting it is an explicit re-authorization — reinstate the
		// node and let it back in (this is how you bring back a node you revoked). A
		// bare --key re-join of a revoked node stays refused.
		if req.NodePub != "" && revoked.isRevoked(req.NodePub) {
			if !viaJoinToken {
				http.Error(w, "node revoked", http.StatusUnauthorized)
				return
			}
			revoked.unrevoke(req.NodePub)
			log.Printf("[join] reinstated revoked node %q (fleet %s) via join token", req.NodeID, fid)
		}
		// Bind node_id → node_pub on first claim (TOFU): a node_id is scoped into the
		// node's NATS credential (its cmd subject), so a second key must not be able to
		// claim an existing node_id and receive that node's commands.
		if !nodePubs.claim(req.NodeID, req.NodePub) {
			http.Error(w, "node_id already bound to a different node key", http.StatusConflict)
			return
		}
		resp := proto.JoinResponse{
			FleetID: fid,
			NatsURL: *natsURL,
			Relays:  relays,
		}
		if authority != nil {
			// A node (sends node_id) gets a NARROW per-node credential that cannot
			// command its peers; the agent/commander (no node_id) gets the broad one.
			var creds []byte
			var err error
			if req.NodeID != "" {
				creds, err = authority.MintFleetNode(fid, req.NodeID)
			} else {
				creds, err = authority.MintFleetUser(fid)
			}
			if err != nil {
				http.Error(w, "mint credentials: "+err.Error(), http.StatusInternalServerError)
				return
			}
			resp.Creds = string(creds)
			if req.NodeID != "" {
				userPubs.record(req.NodeID, userPubFromCreds(creds)) // for the account-level kick
			}
		}
		// Bind a refresh token to the node's public key (when provided) so it can
		// refresh via /token later without re-presenting the API key.
		if req.NodePub != "" {
			nodePubs.record(req.NodeID, req.NodePub) // remember for revoke-by-node-id
			rt, err := token.Sign(refreshPriv, token.Payload{
				FleetID: fid,
				NodePub: req.NodePub,
				NodeID:  req.NodeID, // carried so /token re-mints the same narrow scope
				Exp:     time.Now().Add(refreshTTL).Unix(),
			})
			if err != nil {
				http.Error(w, "issue refresh token: "+err.Error(), http.StatusInternalServerError)
				return
			}
			resp.RefreshToken = rt
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp) //nolint:errcheck
	})

	// /token refreshes a Node's short-lived credential. Auth = refresh token
	// (Controller-signed) + proof-of-possession (a node-key signature over a
	// recent challenge). No API key needed; a stolen refresh token is useless
	// without the node key. See docs/fleet-security-model.md.
	mux.HandleFunc("/token", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		if authority == nil {
			http.Error(w, "auth disabled", http.StatusNotImplemented)
			return
		}
		var req proto.TokenRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		p, err := token.Verify(refreshPub, req.RefreshToken)
		if err != nil {
			http.Error(w, "invalid refresh token: "+err.Error(), http.StatusUnauthorized)
			return
		}
		if err := token.VerifyPoP(p, req.TS, req.Sig); err != nil {
			http.Error(w, "proof-of-possession failed: "+err.Error(), http.StatusUnauthorized)
			return
		}
		if revoked.isRevoked(p.NodePub) {
			http.Error(w, "node revoked", http.StatusUnauthorized)
			return
		}
		nodePubs.record(p.NodeID, p.NodePub) // keep the map fresh so existing nodes stay revocable
		// Re-mint at the SAME scope the join used: a node (node_id in the refresh
		// token) stays narrow; a legacy token without one falls back to broad.
		var creds []byte
		if p.NodeID != "" {
			creds, err = authority.MintFleetNode(p.FleetID, p.NodeID)
		} else {
			creds, err = authority.MintFleetUser(p.FleetID)
		}
		if err != nil {
			http.Error(w, "mint credentials: "+err.Error(), http.StatusInternalServerError)
			return
		}
		if p.NodeID != "" {
			userPubs.record(p.NodeID, userPubFromCreds(creds)) // track the LATEST cred for the kick
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(proto.TokenResponse{Creds: string(creds)}) //nolint:errcheck
	})

	// /join-tokens mints a single-use, short-lived token to add ONE machine. In
	// P0 it's authorized like /join (the key resolves the fleet); Increment D
	// moves it behind the platform session. See docs/fleet-security-model.md.
	mux.HandleFunc("/join-tokens", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req proto.JoinTokenRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		if strings.TrimSpace(req.Key) == "" {
			http.Error(w, "missing key", http.StatusUnauthorized)
			return
		}
		fid, ok := resolveFleet(req.Key)
		if !ok {
			http.Error(w, "key not allowed", http.StatusForbidden)
			return
		}
		exp := time.Now().Add(joinTTL).Unix()
		jt, err := token.SignJoin(refreshPriv, token.JoinPayload{FleetID: fid, JTI: randID(), Exp: exp})
		if err != nil {
			http.Error(w, "issue join token: "+err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(proto.JoinTokenResponse{JoinToken: jt, ExpiresAt: exp}) //nolint:errcheck
	})

	// /revoke adds a node's public key to the revocation list; /token then refuses
	// it and its short-lived credential expires within the TTL. Authorized by the
	// hub service token (open in dev, like /join). See docs/fleet-security-model.md.
	mux.HandleFunc("/revoke", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req proto.RevokeRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		// Authorized by EITHER the hub service token OR a valid fleet key, so the
		// fleet owner can revoke their own node from the Cluster panel without the
		// service token. Dev (no service token configured) stays open. When a key is
		// presented we also capture the fleet id so we can drop the node's registry
		// record (below) for an immediate disappearance from the panel.
		authed := *hubToken == "" || r.Header.Get("Authorization") == "Bearer "+*hubToken
		fid := ""
		if k := strings.TrimSpace(req.Key); k != "" {
			if vfid, ok := resolveFleet(k); ok {
				authed = true
				fid = vfid
			}
		}
		if !authed {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		// Target by node_pub directly, or resolve it from a known node_id.
		nodePub := strings.TrimSpace(req.NodePub)
		if nodePub == "" {
			nodePub = nodePubs.lookup(strings.TrimSpace(req.NodeID))
		}
		if nodePub == "" {
			http.Error(w, "provide node_pub or a known node_id", http.StatusBadRequest)
			return
		}
		revoked.revoke(nodePub)
		// Immediate, server-enforced kick: revoke the node's CURRENT user credential
		// at the account level + reload nats, so it's disconnected now and cannot
		// reconnect (not merely blocked from refreshing). Needs the node_id -> user
		// cred mapping; without it, we fall back to the eventual refresh-blocked path.
		kicked := false
		if authority != nil {
			if uPub := userPubs.lookup(strings.TrimSpace(req.NodeID)); uPub != "" {
				if err := authority.RevokeUser(uPub); err == nil && *emitCfg != "" {
					_ = os.WriteFile(*emitCfg, []byte(authority.ServerConfig(*natsListen, *jsStore)), 0o600)
					_ = exec.Command("pkill", "-HUP", "-x", "nats-server").Run()
					kicked = true
				}
			}
		}
		// Drop the node's live registry record NOW so the Cluster panel shows it gone
		// immediately, rather than waiting out its 30s heartbeat TTL (which looks like
		// the kick failed). Best-effort — the kick above is the security-relevant part.
		if fid != "" && authority != nil {
			if nid := strings.TrimSpace(req.NodeID); nid != "" {
				if err := deleteFleetNode(*natsURL, fid, nid, authority); err != nil {
					log.Printf("[revoke] node %q kicked; registry record will age out (delete failed: %v)", nid, err)
				}
			}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "node_pub": nodePub, "kicked": kicked}) //nolint:errcheck
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

// deleteFleetNode removes a Node's live record from the Fleet registry KV so a
// revoked node disappears from the Cluster panel immediately, instead of
// lingering until its 30s heartbeat TTL lapses (which reads as "kick failed").
// Best-effort — the caller has already kicked the node; a missing bucket or a
// transient NATS error just leaves the record to age out on its own.
func deleteFleetNode(natsURL, fid, nodeID string, authority *auth.Authority) error {
	creds, err := authority.MintFleetUser(fid)
	if err != nil {
		return fmt.Errorf("mint creds: %w", err)
	}
	f, err := os.CreateTemp("", "fleet-*.creds")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name()) //nolint:errcheck
	if _, err := f.Write(creds); err != nil {
		f.Close() //nolint:errcheck
		return err
	}
	f.Close() //nolint:errcheck

	nc, err := nats.Connect(natsURL,
		nats.Name("fleet-controller-revoke"),
		nats.UserCredentials(f.Name()),
		nats.CustomInboxPrefix("_INBOX_"+fid),
		nats.Timeout(5*time.Second),
	)
	if err != nil {
		return fmt.Errorf("nats connect: %w", err)
	}
	defer nc.Drain() //nolint:errcheck
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	kv, err := js.KeyValue(ctx, proto.RegistryBucket(fid))
	if err != nil {
		return nil // no bucket = nothing to remove
	}
	return kv.Delete(ctx, nodeID)
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

// loadOrCreateRefreshKey loads (or creates + persists) the Ed25519 key the
// Controller uses to sign refresh tokens, kept in stateDir/refresh.seed (0600).
func loadOrCreateRefreshKey(stateDir string) (ed25519.PrivateKey, error) {
	path := filepath.Join(stateDir, "refresh.seed")
	if b, err := os.ReadFile(path); err == nil {
		if seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(b))); err == nil && len(seed) == ed25519.SeedSize {
			return ed25519.NewKeyFromSeed(seed), nil
		}
	}
	_, priv, err := ed25519.GenerateKey(nil)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(stateDir, 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(path, []byte(base64.StdEncoding.EncodeToString(priv.Seed())+"\n"), 0o600); err != nil {
		return nil, err
	}
	return priv, nil
}

// jtiSet enforces single-use of join tokens by tracking consumed token ids until
// they expire (in-memory; sufficient for a single controller instance).
type jtiSet struct {
	mu   sync.Mutex
	seen map[string]int64 // jti -> exp
}

func newJTISet() *jtiSet { return &jtiSet{seen: map[string]int64{}} }

// consume records jti as used and returns false if it was already used.
func (s *jtiSet) consume(jti string, exp int64) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now().Unix()
	for k, e := range s.seen { // opportunistic cleanup of expired ids
		if e < now {
			delete(s.seen, k)
		}
	}
	if _, used := s.seen[jti]; used {
		return false
	}
	s.seen[jti] = exp
	return true
}

// randID returns a random hex id (a join token's jti).
func randID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// revokedSet is the node revocation list (by node public key), persisted so
// revocations survive a Controller restart within the refresh-token lifetime.
type revokedSet struct {
	mu   sync.Mutex
	path string
	set  map[string]bool
}

func loadRevoked(stateDir string) *revokedSet {
	r := &revokedSet{path: filepath.Join(stateDir, "revoked.json"), set: map[string]bool{}}
	if b, err := os.ReadFile(r.path); err == nil {
		var list []string
		if json.Unmarshal(b, &list) == nil {
			for _, p := range list {
				r.set[p] = true
			}
		}
	}
	return r
}

func (r *revokedSet) isRevoked(nodePub string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.set[nodePub]
}

func (r *revokedSet) revoke(nodePub string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.set[nodePub] = true
	r.persistLocked()
}

// unrevoke reinstates a node (removes it from the revocation list). Used when
// the fleet owner re-authorizes a previously-kicked node with a fresh join
// token. No-op if the node isn't revoked.
func (r *revokedSet) unrevoke(nodePub string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.set[nodePub] {
		return
	}
	delete(r.set, nodePub)
	r.persistLocked()
}

// persistLocked writes the set to disk; caller must hold r.mu.
func (r *revokedSet) persistLocked() {
	list := make([]string, 0, len(r.set))
	for p := range r.set {
		list = append(list, p)
	}
	b, _ := json.Marshal(list)
	_ = os.WriteFile(r.path, b, 0o600)
}

// nodePubMap remembers each node's public key by its node id (last write wins) so
// /revoke can accept the human-friendly node_id from the Cluster panel and revoke
// the matching node_pub. Persisted alongside the revocation list.
type nodePubMap struct {
	mu   sync.Mutex
	path string
	m    map[string]string
}

func loadNodePubs(path string) *nodePubMap {
	n := &nodePubMap{path: path, m: map[string]string{}}
	if b, err := os.ReadFile(n.path); err == nil {
		_ = json.Unmarshal(b, &n.m)
	}
	return n
}

// userPubFromCreds extracts the user public key (JWT subject) from a decorated
// .creds blob so the Controller can revoke that exact credential at the account
// level when kicking the node.
func userPubFromCreds(creds []byte) string {
	j, err := jwt.ParseDecoratedJWT(creds)
	if err != nil {
		return ""
	}
	uc, err := jwt.DecodeUserClaims(j)
	if err != nil {
		return ""
	}
	return uc.Subject
}

func (n *nodePubMap) record(nodeID, nodePub string) {
	if nodeID == "" || nodePub == "" {
		return
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.m[nodeID] == nodePub {
		return
	}
	n.m[nodeID] = nodePub
	if b, err := json.Marshal(n.m); err == nil {
		_ = os.WriteFile(n.path, b, 0o600)
	}
}

// claim binds a node_id to the FIRST node key that presents it (trust-on-first-use)
// and returns false if a DIFFERENT key later claims the same node_id. A node_id is
// baked into the node's NATS credential (its `node.<id>.cmd` subject), so letting a
// second key claim an existing node_id would let it receive the first node's
// commands. node_ids are random UUIDs a peer can't predict before a node joins, so
// first-claim-wins can't be abused to pre-empt a legitimate node. The original
// claimant re-joining with the same key always passes.
func (n *nodePubMap) claim(nodeID, nodePub string) bool {
	if nodeID == "" || nodePub == "" {
		return true // nothing to bind (legacy/keyless join) — no impersonation possible
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if cur, ok := n.m[nodeID]; ok && cur != nodePub {
		return false // already bound to a different key
	}
	if n.m[nodeID] != nodePub {
		n.m[nodeID] = nodePub
		if b, err := json.Marshal(n.m); err == nil {
			_ = os.WriteFile(n.path, b, 0o600)
		}
	}
	return true
}

func (n *nodePubMap) lookup(nodeID string) string {
	if nodeID == "" {
		return ""
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	return n.m[nodeID]
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
