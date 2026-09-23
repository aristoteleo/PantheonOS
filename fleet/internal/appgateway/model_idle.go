package appgateway

// Model consumers may observe or request wake of an owner-enabled policy. This
// path never accepts arbitrary lifecycle methods, configuration or credentials.
import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"time"
)

type ModelIdleRequest struct {
	Binding
	ID       string `json:"id"`
	Revision uint64 `json:"policy_revision"`
	Action   string `json:"action"`
}

type ModelIdleBinding struct {
	InstanceID string `json:"instance_id"`
	Revision   string `json:"revision"`
	Generation uint64 `json:"generation"`
}

// Deliberately omit the node-local endpoint, credential path and configuration.
type ModelIdleSnapshot struct {
	ID             string           `json:"id"`
	Revision       uint64           `json:"revision"`
	Enabled        bool             `json:"enabled"`
	State          string           `json:"state"`
	Connector      ModelIdleBinding `json:"connector"`
	Engine         ModelIdleBinding `json:"engine"`
	ConfigRevision string           `json:"config_revision"`
	IdleSeconds    int              `json:"idle_seconds"`
	Cycle          uint64           `json:"cycle"`
	WakeRequested  bool             `json:"wake_requested"`
}

type ModelIdleDispatch func(context.Context, ModelIdleRequest) (ModelIdleSnapshot, error)

func (g *Gateway) SetModelIdleDispatch(dispatch ModelIdleDispatch) { g.modelIdle = dispatch }

var modelIdleID = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
var modelIdleDigest = regexp.MustCompile(`^[a-f0-9]{64}$`)

func (p ModelIdleSnapshot) Matches(q ModelIdleRequest) bool {
	if p.ID != q.ID || p.Revision != q.Revision || !p.Enabled ||
		p.Connector != (ModelIdleBinding{q.Instance, q.Binding.Revision, q.Generation}) ||
		p.Engine.InstanceID == "" || !modelIdleDigest.MatchString(p.Engine.Revision) || p.Engine.Generation == 0 ||
		!modelIdleDigest.MatchString(p.ConfigRevision) || p.IdleSeconds < 1 || p.IdleSeconds > 86400 {
		return false
	}
	switch p.State {
	case "active", "fencing", "stopping", "sleeping", "waking", "rebinding", "recovery_required":
		return true
	}
	return false
}

func (g *Gateway) accessModelIdle(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+g.serviceToken)) != 1 {
		http.Error(w, "unauthorized", 401)
		return
	}
	var q ModelIdleRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8192))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&q) != nil || decoder.Decode(new(any)) != io.EOF || !q.Binding.Valid() ||
		!modelIdleID.MatchString(q.ID) || q.Revision == 0 || q.Revision > 9007199254740991 ||
		(q.Action != "status" && q.Action != "wake") || q.Component != "backend" || q.Port != "http" {
		http.Error(w, "invalid model idle request", 400)
		return
	}
	if g.modelIdle == nil {
		http.Error(w, "Model idle service unavailable", 503)
		return
	}
	select {
	case g.slots <- struct{}{}:
		defer func() { <-g.slots }()
	default:
		http.Error(w, "Model idle service busy", 503)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := g.verify(ctx, q.Binding); err != nil {
		http.Error(w, "Model connector generation unavailable", 409)
		return
	}
	p, err := g.modelIdle(ctx, q)
	if err != nil || !p.Matches(q) {
		http.Error(w, "Model idle policy unavailable or changed", 409)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(p)
}
