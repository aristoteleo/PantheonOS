package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"
)

// Visitor grants serve a hosted App to people other than its owner
// (docs/app-hosting.md). The owner's credential never travels with them;
// only the App's declared share methods and path prefixes pass.

// VisitorGrant marks an attach request as a visitor's. The Hub sends ShareID
// and Identity; the gateway fills Share from the App's installed manifest.
type VisitorGrant struct {
	ShareID  string          `json:"share_id"`
	Identity json.RawMessage `json:"identity"`
	Share    *ShareSpec      `json:"-"`
}

// ShareSpec is a manifest's `share` declaration.
type ShareSpec struct {
	Methods []struct {
		Name      string   `json:"name"`
		Arguments []string `json:"arguments"`
	} `json:"methods"`
	Paths []string `json:"paths"`
}

// ShareResolver reads the share declaration of the exact installed revision.
type ShareResolver func(context.Context, Binding) (*ShareSpec, error)

var shareIDRE = regexp.MustCompile(`^[A-Za-z0-9_-]{8,64}$`)

// SetShareResolver enables visitor grants. Called once, before serving.
func (g *Gateway) SetShareResolver(resolve ShareResolver) { g.shareResolver = resolve }

// prepareVisitor validates a visitor attach and resolves what it may reach.
func (g *Gateway) prepareVisitor(ctx context.Context, q *AttachRequest) error {
	v := q.Visitor
	var identity struct {
		Kind     string `json:"kind"`
		ID       string `json:"id"`
		Username string `json:"username"`
	}
	if g.shareResolver == nil || q.Workload || q.URLAuth || !shareIDRE.MatchString(v.ShareID) ||
		json.Unmarshal(v.Identity, &identity) != nil || (identity.Kind != "user" && identity.Kind != "anonymous") ||
		identity.ID == "" || len(identity.ID) > 128 || len(v.Identity) > 1024 {
		return fmt.Errorf("invalid visitor grant")
	}
	spec, err := g.shareResolver(ctx, q.Binding)
	if err != nil {
		return err
	}
	if spec == nil || len(spec.Methods) == 0 {
		return errNotShared
	}
	v.Share = spec
	return nil
}

var errNotShared = fmt.Errorf("this App does not declare anything to share")

// visitorHeader is the identity the backend reads as ctx.visitor().
func visitorHeader(v *VisitorGrant) string {
	var identity map[string]any
	_ = json.Unmarshal(v.Identity, &identity)
	identity["share_id"] = v.ShareID
	raw, _ := json.Marshal(identity)
	return string(raw)
}

const maxVisitorRPC = 1 << 20

// allowVisitor enforces a visitor grant on one request. A permitted RPC body
// is re-attached for the proxy. It reports false after writing the refusal.
func allowVisitor(w http.ResponseWriter, r *http.Request, v *VisitorGrant) bool {
	path := r.URL.Path
	switch {
	case r.Method == http.MethodPost && path == "/rpc":
		body, err := io.ReadAll(io.LimitReader(r.Body, maxVisitorRPC+1))
		if err != nil || len(body) > maxVisitorRPC {
			http.Error(w, "request too large", 413)
			return false
		}
		var call struct {
			Method string                     `json:"method"`
			Args   map[string]json.RawMessage `json:"args"`
		}
		if json.Unmarshal(body, &call) != nil {
			http.Error(w, "invalid App RPC", 400)
			return false
		}
		for _, m := range v.Share.Methods {
			if m.Name != call.Method {
				continue
			}
			allowed := map[string]bool{}
			for _, a := range m.Arguments {
				allowed[a] = true
			}
			for name := range call.Args {
				if !allowed[name] {
					writeJSON(w, 403, map[string]any{"success": false, "error": "argument " + name + " is not shared"})
					return false
				}
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
			r.ContentLength = int64(len(body))
			return true
		}
		writeJSON(w, 403, map[string]any{"success": false, "error": "this method is not shared"})
		return false
	case r.Method == http.MethodGet || r.Method == http.MethodHead:
		if path == "/__fleet/status" {
			return true
		}
		for _, prefix := range v.Share.Paths {
			if strings.HasPrefix(path, prefix) {
				return true
			}
		}
		// The App's own page and package; never its data or fleet endpoints.
		if !strings.HasPrefix(path, "/_fleet/") && !strings.HasPrefix(path, "/__fleet/") && path != "/rpc" {
			return true
		}
	}
	http.Error(w, "not shared", 403)
	return false
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

// RevokeShare drops every live visitor grant of a share.
func (g *Gateway) RevokeShare(shareID string) int {
	g.mu.Lock()
	defer g.mu.Unlock()
	n := 0
	for key, v := range g.grants {
		if v.Visitor != nil && v.Visitor.ShareID == shareID {
			delete(g.grants, key)
			n++
		}
	}
	return n
}

func (g *Gateway) revokeShare(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || !g.serviceAuthorized(r) {
		http.Error(w, "unauthorized", 401)
		return
	}
	var q struct {
		ShareID string `json:"share_id"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&q) != nil || !shareIDRE.MatchString(q.ShareID) {
		http.Error(w, "invalid share", 400)
		return
	}
	writeJSON(w, 200, map[string]any{"revoked": g.RevokeShare(q.ShareID)})
}

// ShareTarget is the owner's current ready instance of a hosted App.
type ShareTarget struct {
	Binding Binding    `json:"binding"`
	Name    string     `json:"name"`
	Version string     `json:"version"`
	Share   *ShareSpec `json:"share"`
}

// ShareTargetResolver finds the owner's ready instance of app_id in scope on
// node; a stopped or replaced instance resolves to its successor.
type ShareTargetResolver func(ctx context.Context, fleet, node, appID, scope string) (*ShareTarget, error)

// SetShareTargetResolver enables /apps/share-target. Called once, before serving.
func (g *Gateway) SetShareTargetResolver(resolve ShareTargetResolver) { g.shareTarget = resolve }

var targetRE = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,128}$`)

func (g *Gateway) resolveShareTarget(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || !g.serviceAuthorized(r) {
		http.Error(w, "unauthorized", 401)
		return
	}
	var q struct {
		Fleet string `json:"fleet_id"`
		Node  string `json:"node_id"`
		AppID string `json:"app_id"`
		Scope string `json:"scope"`
	}
	if json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&q) != nil || g.shareTarget == nil ||
		!targetRE.MatchString(q.Fleet) || !targetRE.MatchString(q.Node) || !targetRE.MatchString(q.AppID) || !targetRE.MatchString(q.Scope) {
		http.Error(w, "invalid share target", 400)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	target, err := g.shareTarget(ctx, q.Fleet, q.Node, q.AppID, q.Scope)
	if err != nil {
		http.Error(w, err.Error(), 409)
		return
	}
	writeJSON(w, 200, target)
}
