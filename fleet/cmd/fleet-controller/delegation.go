package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/auth"
	"github.com/aristoteleo/pantheon-fleet/internal/proto"
	"github.com/aristoteleo/pantheon-fleet/internal/token"
)

var allocationID = regexp.MustCompile(`^[a-f0-9]{32}$`)

// delegateHandler mints a separate, narrow NATS identity for each allocation.
// The existing connector credential is never widened. Revoking the connector
// blocks renewal of every delegated identity; access creds expire normally.
func delegateHandler(authority *auth.Authority, pub ed25519.PublicKey, revoked *revokedSet) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", 405)
			return
		}
		if authority == nil {
			http.Error(w, "auth disabled", 501)
			return
		}
		var req proto.DelegateRequest
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384)).Decode(&req) != nil || !allocationID.MatchString(req.Allocation) {
			http.Error(w, "invalid allocation", 400)
			return
		}
		p, err := token.Verify(pub, req.RefreshToken)
		if err != nil || p.NodeID == "" || strings.HasPrefix(p.NodeID, "hpc_") || revoked.isRevoked(p.NodePub) {
			http.Error(w, "connector is not authorized", 401)
			return
		}
		key, e1 := base64.StdEncoding.DecodeString(p.NodePub)
		sig, e2 := base64.StdEncoding.DecodeString(req.Sig)
		skew := time.Now().Unix() - req.TS
		if e1 != nil || e2 != nil || len(key) != ed25519.PublicKeySize || skew < -120 || skew > 120 ||
			!ed25519.Verify(key, []byte(proto.DelegateChallenge(p.NodePub, p.FleetID, req.Allocation, req.TS)), sig) {
			http.Error(w, "invalid delegation proof", 401)
			return
		}
		id := proto.DelegatedNodeID(p.FleetID, p.NodeID, req.Allocation)
		creds, err := authority.MintFleetNode(p.FleetID, id)
		if err != nil {
			http.Error(w, "cannot issue delegated credential", 500)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(proto.DelegateResponse{NodeID: id, Creds: string(creds), ExpiresAt: time.Now().Add(auth.AccessTTL).Unix()})
	}
}
