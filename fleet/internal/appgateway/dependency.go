package appgateway

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

const maxDependencyRPC = 512 * 1024

var rpcName = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,127}$`)
var appName = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,79}$`)

type ConsumerCheck func(context.Context, apptransport.InstanceIdentity, string) error
type DependencyInvoke func(context.Context, Binding, string, json.RawMessage, int) (json.RawMessage, error)

// RPCMethod permits only enumerated caller arguments. Bound arguments are
// supplied by the owner (e.g. workspace/session identity) and cannot be replaced.
// The provider remains responsible for applying its resource boundary inside
// each method; a path string here is not itself a filesystem sandbox.
type RPCMethod struct {
	Arguments []string                   `json:"arguments"`
	Bound     map[string]json.RawMessage `json:"bound"`
}
type DependencyRequest struct {
	Operation   string                        `json:"operation_id,omitempty"`
	Consumer    apptransport.InstanceIdentity `json:"consumer"`
	Preparation string                        `json:"preparation_id,omitempty"`
	Provider    Binding                       `json:"provider"`
	AppID       string                        `json:"app_id"`
	Methods     map[string]RPCMethod          `json:"methods"`
	HTTP        *HTTPDependency               `json:"http,omitempty"`
	Expires     int64                         `json:"expires"`
	Timeout     int                           `json:"timeout_seconds"`
}
type dependencyGrant struct {
	DependencyRequest
	id string
}

// SetDependencyDispatch is called once, before serving. Invocation reuses the
// owner-scoped Fleet RPC protocol: no App receives NATS or provider credentials.
func (g *Gateway) SetDependencyDispatch(check ConsumerCheck, invoke DependencyInvoke) {
	g.consumerCheck, g.dependencyInvoke = check, invoke
	g.dependencies = map[string]*dependencyGrant{}
	g.dependencyHTTP = map[string]map[*dependencyFlight]struct{}{}
}

func (q DependencyRequest) valid() bool {
	if !q.Consumer.Valid() || !q.Provider.Valid() || q.Consumer.Fleet != q.Provider.Fleet || !appName.MatchString(q.AppID) || q.Provider.Component != "backend" || q.Provider.Port != "http" || q.Timeout < 1 || q.Timeout > 600 || q.Expires <= time.Now().Unix() || q.Expires > time.Now().Add(15*time.Minute).Unix() {
		return false
	}
	if q.HTTP != nil {
		if len(q.Methods) != 0 || !q.HTTP.Valid() {
			return false
		}
	} else if len(q.Methods) == 0 || len(q.Methods) > 64 {
		return false
	}
	if q.Operation != "" && !appName.MatchString(q.Operation) {
		return false
	}
	if q.Preparation != "" && !appName.MatchString(q.Preparation) {
		return false
	}
	for method, rule := range q.Methods {
		if !rpcName.MatchString(method) || len(rule.Arguments)+len(rule.Bound) > 64 {
			return false
		}
		seen := map[string]bool{}
		for _, name := range rule.Arguments {
			if !rpcName.MatchString(name) || seen[name] {
				return false
			}
			seen[name] = true
		}
		for name, value := range rule.Bound {
			if !rpcName.MatchString(name) || seen[name] || !json.Valid(value) {
				return false
			}
		}
	}
	return true
}

func (g *Gateway) manageDependency(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+g.serviceToken)) != 1 {
		http.Error(w, "unauthorized", 401)
		return
	}
	if g.consumerCheck == nil || g.dependencyInvoke == nil {
		http.Error(w, "dependency RPC unavailable", 503)
		return
	}
	g.mu.Lock()
	failed := g.dependencyStoreFailed
	g.mu.Unlock()
	if failed {
		http.Error(w, "dependency persistence unavailable", 503)
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64*1024))
	if err != nil || uniqueJSON(raw) != nil {
		http.Error(w, "invalid dependency grant", 400)
		return
	}
	decode := func(out any) error {
		d := json.NewDecoder(bytes.NewReader(raw))
		d.DisallowUnknownFields()
		return d.Decode(out)
	}
	if r.Method == "DELETE" {
		var q struct {
			Fleet string `json:"fleet_id"`
			ID    string `json:"grant_id"`
		}
		if decode(&q) != nil || q.Fleet == "" || len(q.ID) != 64 {
			http.Error(w, "invalid revocation", 400)
			return
		}
		g.mu.Lock()
		if g.dependencyStoreFailed || g.persistRevocation(q.Fleet, q.ID) != nil {
			g.mu.Unlock()
			http.Error(w, "dependency persistence unavailable", 503)
			return
		}
		for key, grant := range g.dependencies {
			if grant.id == q.ID && grant.Consumer.Fleet == q.Fleet {
				delete(g.dependencies, key)
				for flight := range g.dependencyHTTP[key] {
					flight.cancel()
				}
			}
		}
		g.mu.Unlock()
		w.WriteHeader(204)
		return
	}
	if r.Method == "PATCH" {
		var q struct {
			Fleet   string `json:"fleet_id"`
			ID      string `json:"grant_id"`
			Expires int64  `json:"expires"`
		}
		if decode(&q) != nil || q.Fleet == "" || len(q.ID) != 64 || q.Expires <= time.Now().Unix() || q.Expires > time.Now().Add(15*time.Minute).Unix() {
			http.Error(w, "invalid dependency renewal", 400)
			return
		}
		g.renewDependency(w, r, q.Fleet, q.ID, q.Expires)
		return
	}
	if r.Method != "POST" {
		http.Error(w, "method not allowed", 405)
		return
	}
	var q DependencyRequest
	if decode(&q) != nil || !q.valid() {
		http.Error(w, "invalid dependency grant", 400)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := g.consumerCheck(ctx, q.Consumer, q.Preparation); err != nil {
		http.Error(w, "consumer binding unavailable", 409)
		return
	}
	if err := g.verify(ctx, q.Provider); err != nil {
		http.Error(w, "provider binding unavailable", 409)
		return
	}
	if ctx.Err() != nil || q.Expires <= time.Now().Unix() {
		http.Error(w, "dependency authorization expired", 409)
		return
	}
	g.mu.Lock()
	if g.dependencyStoreFailed || q.Operation != "" && g.dependencyStore == nil {
		g.mu.Unlock()
		http.Error(w, "durable dependency authorization unavailable", 503)
		return
	}
	if q.Operation != "" {
		if old, ok := g.dependencyStore.records[dependencyOperation(q)]; ok {
			if old.Policy != dependencyPolicy(q) {
				g.mu.Unlock()
				http.Error(w, "dependency operation has a different policy", 409)
				return
			}
			if old.Request == nil || old.Expires <= time.Now().Unix() {
				g.mu.Unlock()
				http.Error(w, "dependency operation revoked or expired", 410)
				return
			}
			g.mu.Unlock()
			g.writeDependency(w, old.Token, old.ID, *old.Request)
			return
		}
	}
	key := nonce()
	sum := sha256.Sum256([]byte(key))
	id := hex.EncodeToString(sum[:])
	for key, grant := range g.dependencies {
		if grant.Expires <= time.Now().Unix() {
			delete(g.dependencies, key)
		}
	}
	if len(g.dependencies) >= 1024 {
		g.mu.Unlock()
		http.Error(w, "dependency grant capacity reached", 503)
		return
	}
	if err := g.persistGrant(q, key, id); err != nil {
		g.mu.Unlock()
		http.Error(w, "dependency persistence unavailable", 503)
		return
	}
	g.dependencies[key] = &dependencyGrant{q, id}
	g.mu.Unlock()
	g.writeDependency(w, key, id, q)
}

func (g *Gateway) writeDependency(w http.ResponseWriter, key, id string, q DependencyRequest) {
	w.Header().Set("Content-Type", "application/json")
	origin := "https://" + Host(q.Provider.Instance, q.Provider.Component, q.Provider.Port, q.Provider.Generation, g.domain)
	result := map[string]any{"grant_id": id, "access_token": key, "expires": q.Expires}
	if q.HTTP != nil {
		result["origin"] = origin
	} else {
		result["endpoint"] = origin + "/rpc"
	}
	_ = json.NewEncoder(w).Encode(result)
}

// Only the owner may extend an existing live grant. Its token, identities,
// methods, arguments and timeout remain unchanged. Expiry/revocation is final:
// renewal cannot recreate a missing grant or authorize a prepared replacement.
func (g *Gateway) renewDependency(w http.ResponseWriter, r *http.Request, fleet, id string, expires int64) {
	g.mu.Lock()
	var key string
	var grant *dependencyGrant
	for k, value := range g.dependencies {
		if value.id == id && value.Consumer.Fleet == fleet && value.Expires > time.Now().Unix() {
			key, grant = k, value
			break
		}
	}
	g.mu.Unlock()
	if grant == nil {
		http.Error(w, "dependency grant unavailable", 410)
		return
	}
	// The HTTP upstream credential has the original expiry. Extending only the
	// gateway receipt would advertise authority the provider no longer accepts.
	if grant.HTTP != nil && expires > grant.Expires {
		http.Error(w, "HTTP dependency needs a fresh upstream credential", 409)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := g.consumerCheck(ctx, grant.Consumer, ""); err != nil {
		http.Error(w, "consumer binding unavailable", 409)
		return
	}
	if err := g.verify(ctx, grant.Provider); err != nil {
		http.Error(w, "provider binding unavailable", 409)
		return
	}
	g.mu.Lock()
	current := g.dependencies[key]
	if current == nil || current.id != id || current.Expires <= time.Now().Unix() {
		g.mu.Unlock()
		http.Error(w, "dependency grant revoked or expired", 410)
		return
	}
	if ctx.Err() != nil || expires <= time.Now().Unix() {
		g.mu.Unlock()
		http.Error(w, "dependency renewal timed out", 409)
		return
	}
	// Publish an immutable replacement: in-flight reads of the original grant
	// stay race-free. Concurrent renewal/lost acknowledgements never shorten it.
	replacement := *current
	if expires > replacement.Expires {
		replacement.Expires = expires
	}
	if g.dependencyStoreFailed || g.persistGrant(replacement.DependencyRequest, key, replacement.id) != nil {
		g.mu.Unlock()
		http.Error(w, "dependency persistence unavailable", 503)
		return
	}
	g.dependencies[key] = &replacement
	g.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"grant_id": id, "expires": replacement.Expires, "consumer": replacement.Consumer, "provider": replacement.Provider})
}

// serveDependency returns true for any known dependency bearer, even if the
// requested path is forbidden, so it can never fall through to unrelated
// browser, media, direct, or management authority. HTTP grants explicitly
// authorize streamed responses only on their permitted data paths.
func (g *Gateway) serveDependency(w http.ResponseWriter, r *http.Request) bool {
	if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		return false
	}
	key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	g.mu.Lock()
	grant := g.dependencies[key]
	failed := g.dependencyStoreFailed
	g.mu.Unlock()
	if grant == nil {
		return false
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if failed {
		http.Error(w, "dependency persistence unavailable", 503)
		return true
	}
	if grant.Expires <= time.Now().Unix() || r.Host != Host(grant.Provider.Instance, grant.Provider.Component, grant.Provider.Port, grant.Provider.Generation, g.domain) {
		http.Error(w, "dependency grant expired or mismatched", 401)
		return true
	}
	if grant.HTTP != nil {
		g.serveHTTPDependency(w, r, key, grant)
		return true
	}
	if r.Method != "POST" || r.URL.Path != "/rpc" || r.URL.RawPath != "" || r.URL.RawQuery != "" || r.URL.ForceQuery || r.Header.Get("Origin") != "" || r.Header.Get("Sec-Fetch-Site") != "" || r.Header.Get("Upgrade") != "" || r.Header.Get("Content-Encoding") != "" {
		http.Error(w, "dependency RPC only", 403)
		return true
	}
	select {
	case g.slots <- struct{}{}:
		defer func() { <-g.slots }()
	default:
		http.Error(w, "dependency RPC capacity reached", 503)
		return true
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxDependencyRPC))
	if err != nil {
		http.Error(w, "invalid RPC body", 400)
		return true
	}
	payload, timeout, err := dependencyPayload(raw, grant)
	if err != nil {
		http.Error(w, "RPC method or arguments not authorized", 403)
		return true
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(timeout)*time.Second)
	defer cancel()
	// Prepared grants are unusable until that exact generation really runs.
	if err := g.consumerCheck(ctx, grant.Consumer, ""); err != nil {
		http.Error(w, "consumer binding unavailable", 409)
		return true
	}
	g.mu.Lock()
	current := g.dependencies[key]
	admitted := !g.dependencyStoreFailed && current != nil && current.id == grant.id && current.Expires > time.Now().Unix()
	g.mu.Unlock()
	if !admitted {
		http.Error(w, "dependency grant revoked or expired", 401)
		return true
	}
	// Revocation prevents new admission; an accepted mutation may still finish.
	// Never retry or replay a call whose transport outcome is unknown.
	result, err := g.dependencyInvoke(ctx, grant.Provider, grant.AppID, payload, timeout)
	if err != nil || len(result) > maxDependencyRPC || !json.Valid(result) {
		http.Error(w, "dependency call failed; outcome may be unknown", 502)
		return true
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(result)
	return true
}

func dependencyPayload(raw []byte, grant *dependencyGrant) (json.RawMessage, int, error) {
	bad := fmt.Errorf("invalid dependency RPC")
	if uniqueJSON(raw) != nil {
		return nil, 0, bad
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		return nil, 0, bad
	}
	for name := range fields {
		if name != "method" && name != "args" && name != "timeout_seconds" {
			return nil, 0, bad
		}
	}
	var method string
	if json.Unmarshal(fields["method"], &method) != nil {
		return nil, 0, bad
	}
	rule, ok := grant.Methods[method]
	if !ok {
		return nil, 0, bad
	}
	args := map[string]json.RawMessage{}
	if value, ok := fields["args"]; ok && (json.Unmarshal(value, &args) != nil || args == nil) {
		return nil, 0, bad
	}
	allowed := map[string]bool{}
	for _, name := range rule.Arguments {
		allowed[name] = true
	}
	for name := range args {
		if !allowed[name] {
			return nil, 0, bad
		}
	}
	for name, value := range rule.Bound {
		args[name] = value
	}
	timeout := grant.Timeout
	if value, ok := fields["timeout_seconds"]; ok {
		if json.Unmarshal(value, &timeout) != nil || timeout < 1 || timeout > grant.Timeout {
			return nil, 0, bad
		}
	}
	result, err := json.Marshal(map[string]any{"method": method, "args": args, "timeout_s": timeout})
	if err != nil || len(result) > maxDependencyRPC {
		return nil, 0, bad
	}
	return result, timeout, nil
}

// Reject duplicate keys (including nested arguments), excessive nesting and
// trailing JSON before interpreting the authorization envelope.
func uniqueJSON(raw []byte) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var value func(int) error
	value = func(depth int) error {
		if depth > 64 {
			return fmt.Errorf("JSON depth exceeded")
		}
		t, err := d.Token()
		if err != nil {
			return err
		}
		if delim, ok := t.(json.Delim); ok {
			switch delim {
			case '{':
				seen := map[string]bool{}
				for d.More() {
					k, e := d.Token()
					if e != nil {
						return e
					}
					key, ok := k.(string)
					if !ok || seen[key] {
						return fmt.Errorf("duplicate JSON key")
					}
					seen[key] = true
					if e = value(depth + 1); e != nil {
						return e
					}
				}
			case '[':
				for d.More() {
					if e := value(depth + 1); e != nil {
						return e
					}
				}
			default:
				return fmt.Errorf("invalid JSON")
			}
			_, err = d.Token()
			return err
		}
		return nil
	}
	if err := value(0); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}
