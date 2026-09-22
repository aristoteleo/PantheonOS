package appgateway

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
)

type DirectDispatch func(context.Context, appdirect.Request) (appdirect.Grant, error)

// SetDirectDispatch must run before serving requests. Direct grants do not
// activate a browser cookie or register a reusable gateway bearer grant.
func (g *Gateway) SetDirectDispatch(dispatch DirectDispatch) { g.direct = dispatch }

func (g *Gateway) attachDirect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+g.serviceToken)) != 1 {
		http.Error(w, "unauthorized", 401)
		return
	}
	if g.direct == nil {
		http.Error(w, "Direct App service unavailable", 503)
		return
	}
	var q appdirect.Request
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384))
	decoder.DisallowUnknownFields()
	now := time.Now()
	if decoder.Decode(&q) != nil || decoder.Decode(new(any)) != io.EOF || !q.Valid() || len(q.Peer) < 32 || len(q.Peer) > 128 || len(q.Credential) < 32 || len(q.Credential) > 8192 || q.Expires <= now.Unix() || q.Expires > now.Add(appdirect.MaxLifetime).Unix() {
		http.Error(w, "invalid direct App grant", 400)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := g.verify(ctx, q.Binding); err != nil {
		http.Error(w, "App generation unavailable", 409)
		return
	}
	grant, err := g.direct(ctx, q)
	if err != nil {
		http.Error(w, "Direct App service unavailable", 503)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(grant)
}
