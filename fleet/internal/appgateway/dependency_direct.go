package appgateway

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"reflect"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
)

func (g *Gateway) dependencyProof(id string) string {
	mac := hmac.New(sha256.New, []byte(g.serviceToken))
	mac.Write([]byte("dependency-check:" + id))
	return hex.EncodeToString(mac.Sum(nil))
}

func (g *Gateway) dependencyByID(id, fleet string) (string, *dependencyGrant) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if !g.dependencyStoreFailed {
		for key, grant := range g.dependencies {
			if grant.id == id && grant.Consumer.Fleet == fleet && grant.HTTP != nil && !grant.HTTP.NodeBound && grant.Expires > time.Now().Unix() {
				return key, grant
			}
		}
	}
	return "", nil
}

// Owner-only exchange: the resulting QUIC token remains peer-bound and single
// use. It inherits the durable HTTP grant's identity, path scope and revocation.
func (g *Gateway) attachDependencyDirect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+g.serviceToken)) != 1 {
		http.Error(w, "unauthorized", 401)
		return
	}
	var q struct {
		ID    string `json:"grant_id"`
		Fleet string `json:"fleet_id"`
		Peer  string `json:"peer_id"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 2048))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&q) != nil || decoder.Decode(new(any)) != io.EOF || !grantHex.MatchString(q.ID) || len(q.Peer) < 32 || len(q.Peer) > 128 {
		http.Error(w, "invalid direct dependency exchange", 400)
		return
	}
	if g.direct == nil {
		http.Error(w, "Direct App service unavailable", 503)
		return
	}
	key, grant := g.dependencyByID(q.ID, q.Fleet)
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if grant == nil || !g.checkHTTPDependency(ctx, key, grant) {
		http.Error(w, "dependency unavailable", 409)
		return
	}
	expires := min(grant.Expires, time.Now().Add(appdirect.MaxLifetime).Unix())
	request := appdirect.Request{Binding: grant.Provider, Peer: q.Peer, Credential: grant.HTTP.Credential, Expires: expires,
		Dependency: &appdirect.Dependency{ID: grant.id, Proof: g.dependencyProof(grant.id), Consumer: grant.Consumer, HTTP: *grant.HTTP}}
	result, err := g.direct(ctx, request)
	if err != nil {
		http.Error(w, "Direct dependency protocol unavailable", 503)
		return
	}
	if !g.dependencyLive(key, grant) {
		http.Error(w, "dependency revoked during exchange", 409)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(result)
}

// Nodes may only query their exact inherited authority. This proof cannot
// issue grants, read the directory, proxy data, or authorize a browser.
func (g *Gateway) checkDependencyDirect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" {
		http.Error(w, "unauthorized", 401)
		return
	}
	var q appdirect.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 128*1024))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&q) != nil || decoder.Decode(new(any)) != io.EOF || !q.Valid() || q.Dependency == nil || !q.Dependency.Valid(q) ||
		subtle.ConstantTimeCompare([]byte(q.Dependency.Proof), []byte(g.dependencyProof(q.Dependency.ID))) != 1 {
		http.Error(w, "unauthorized", 401)
		return
	}
	key, grant := g.dependencyByID(q.Dependency.ID, q.Fleet)
	if grant == nil || grant.Consumer != q.Dependency.Consumer || grant.Provider != q.Binding || q.Expires <= time.Now().Unix() || q.Expires > grant.Expires || !reflect.DeepEqual(*grant.HTTP, q.Dependency.HTTP) {
		http.Error(w, "dependency unavailable", 409)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	if !g.checkHTTPDependency(ctx, key, grant) {
		http.Error(w, "dependency no longer live", 409)
		return
	}
	w.WriteHeader(204)
}
