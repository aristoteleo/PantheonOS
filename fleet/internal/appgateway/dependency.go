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

// Wire argument names include legacy Python aliases such as _action/_args.
// They remain explicitly enumerated; method names retain the stricter rule.
var rpcArgument = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)
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
			if !rpcArgument.MatchString(name) || seen[name] {
				return false
			}
			seen[name] = true
		}
		for name, value := range rule.Bound {
			if !rpcArgument.MatchString(name) || seen[name] || !json.Valid(value) {
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
	g.manageAuthorizedDependency(w, r)
}

// Called only by authenticated service control or the explicit local owner
// adapter, which supplies the resolved Fleet identity itself.
func (g *Gateway) manageAuthorizedDependency(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
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
		if err := g.RevokeDependency(q.Fleet, q.ID); err != nil {
			http.Error(w, err.Error(), 503)
			return
		}
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
	if decode(&q) != nil {
		http.Error(w, "invalid dependency grant", 400)
		return
	}
	grant, err := g.IssueDependency(r.Context(), q)
	if err != nil {
		http.Error(w, err.Error(), err.(*DependencyError).Status)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(grant)
}

// DependencyError carries the HTTP status the grant API answers with.
type DependencyError struct {
	Status int
	Reason string
}

func (e *DependencyError) Error() string { return e.Reason }

func dependencyError(status int, reason string) error {
	return &DependencyError{Status: status, Reason: reason}
}

// IssueDependency mints (or, for a repeated operation ID, returns) a
// dependency grant. The HTTP API and the in-process reconciler share it, so a
// grant has the same checks and durability whichever path requested it. The
// result is what a consumer's configuration receives.
func (g *Gateway) IssueDependency(parent context.Context, q DependencyRequest) (map[string]any, error) {
	if g.consumerCheck == nil || g.dependencyInvoke == nil {
		return nil, dependencyError(503, "dependency RPC unavailable")
	}
	if !q.valid() || !g.acceptsHTTPDependency(q.HTTP) {
		return nil, dependencyError(400, "invalid dependency grant")
	}
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	if err := g.consumerCheck(ctx, q.Consumer, q.Preparation); err != nil {
		return nil, dependencyError(409, "consumer binding unavailable")
	}
	if err := g.verify(ctx, q.Provider); err != nil {
		return nil, dependencyError(409, "provider binding unavailable")
	}
	if ctx.Err() != nil || q.Expires <= time.Now().Unix() {
		return nil, dependencyError(409, "dependency authorization expired")
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.dependencyStoreFailed || q.Operation != "" && g.dependencyStore == nil {
		return nil, dependencyError(503, "durable dependency authorization unavailable")
	}
	if q.Operation != "" {
		if old, ok := g.dependencyStore.records[dependencyOperation(q)]; ok {
			if old.Policy != dependencyPolicy(q) {
				return nil, dependencyError(409, "dependency operation has a different policy")
			}
			if old.Request == nil || old.Expires <= time.Now().Unix() {
				return nil, dependencyError(410, "dependency operation revoked or expired")
			}
			return g.dependencyResult(old.Token, old.ID, *old.Request), nil
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
		return nil, dependencyError(503, "dependency grant capacity reached")
	}
	if err := g.persistGrant(q, key, id); err != nil {
		return nil, dependencyError(503, "dependency persistence unavailable")
	}
	g.dependencies[key] = &dependencyGrant{q, id}
	return g.dependencyResult(key, id, q), nil
}

func (g *Gateway) dependencyResult(key, id string, q DependencyRequest) map[string]any {
	origin := "https://" + Host(q.Provider.Instance, q.Provider.Component, q.Provider.Port, q.Provider.Generation, g.domain)
	result := map[string]any{"grant_id": id, "access_token": key, "expires": q.Expires}
	if g.localRPCOrigin != "" {
		origin = g.localRPCOrigin
		result["consumer"], result["provider"] = q.Consumer, q.Provider
	}
	if q.HTTP != nil {
		result["origin"] = origin
	} else {
		result["endpoint"] = origin + "/rpc"
	}
	return result
}

// Only the owner may extend an existing live grant. Its token, identities,
// methods, arguments and timeout remain unchanged. Expiry/revocation is final:
// renewal cannot recreate a missing grant or authorize a prepared replacement.
func (g *Gateway) renewDependency(w http.ResponseWriter, r *http.Request, fleet, id string, expires int64) {
	grant, err := g.RenewDependency(r.Context(), fleet, id, expires)
	if err != nil {
		http.Error(w, err.Error(), err.(*DependencyError).Status)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(grant)
}

// RenewDependency extends a live grant (see renewDependency); the reconciler
// calls it in-process for the grants of the Apps it started.
func (g *Gateway) RenewDependency(parent context.Context, fleet, id string, expires int64) (map[string]any, error) {
	if g.consumerCheck == nil {
		return nil, dependencyError(503, "dependency RPC unavailable")
	}
	if expires <= time.Now().Unix() || expires > time.Now().Add(15*time.Minute).Unix() {
		return nil, dependencyError(400, "invalid dependency renewal")
	}
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
	if grant == nil || !g.acceptsHTTPDependency(grant.HTTP) {
		return nil, dependencyError(410, "dependency grant unavailable")
	}
	// The HTTP upstream credential has the original expiry. Extending only the
	// gateway receipt would advertise authority the provider no longer accepts.
	if grant.HTTP != nil && !grant.HTTP.NodeBound && expires > grant.Expires {
		return nil, dependencyError(409, "HTTP dependency needs a fresh upstream credential")
	}
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	if err := g.consumerCheck(ctx, grant.Consumer, ""); err != nil {
		return nil, dependencyError(409, "consumer binding unavailable")
	}
	if err := g.verify(ctx, grant.Provider); err != nil {
		return nil, dependencyError(409, "provider binding unavailable")
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	current := g.dependencies[key]
	if current == nil || current.id != id || current.Expires <= time.Now().Unix() {
		return nil, dependencyError(410, "dependency grant revoked or expired")
	}
	if ctx.Err() != nil || expires <= time.Now().Unix() {
		return nil, dependencyError(409, "dependency renewal timed out")
	}
	// Publish an immutable replacement: in-flight reads of the original grant
	// stay race-free. Concurrent renewal/lost acknowledgements never shorten it.
	replacement := *current
	if expires > replacement.Expires {
		replacement.Expires = expires
	}
	if g.dependencyStoreFailed || g.persistGrant(replacement.DependencyRequest, key, replacement.id) != nil {
		return nil, dependencyError(503, "dependency persistence unavailable")
	}
	g.dependencies[key] = &replacement
	return map[string]any{"grant_id": id, "expires": replacement.Expires, "consumer": replacement.Consumer, "provider": replacement.Provider}, nil
}

// RevokeDependency removes a grant and cancels its in-flight HTTP streams.
func (g *Gateway) RevokeDependency(fleet, id string) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.dependencyStoreFailed || g.persistRevocation(fleet, id) != nil {
		return dependencyError(503, "dependency persistence unavailable")
	}
	for key, grant := range g.dependencies {
		if grant.id == id && grant.Consumer.Fleet == fleet {
			delete(g.dependencies, key)
			for flight := range g.dependencyHTTP[key] {
				flight.cancel()
			}
		}
	}
	return nil
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
	expectedHost := Host(grant.Provider.Instance, grant.Provider.Component, grant.Provider.Port, grant.Provider.Generation, g.domain)
	if g.localRPCOrigin != "" {
		expectedHost = strings.TrimPrefix(g.localRPCOrigin, "https://")
	}
	if grant.Expires <= time.Now().Unix() || r.Host != expectedHost || !g.acceptsHTTPDependency(grant.HTTP) {
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
