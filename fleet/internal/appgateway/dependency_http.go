package appgateway

// HTTP data-plane dependencies reuse the durable RPC grant identities and
// revocation journal. No browser cookies, management RPCs or arbitrary paths
// are acquired by exchanging one of these credentials.
import (
	"context"
	"net/http"
	"path"
	"strings"
	"time"
)

type HTTPRule struct {
	Method string `json:"method"`
	Path   string `json:"path"`
	Prefix bool   `json:"prefix,omitempty"`
}

type HTTPDependency struct {
	Rules      []HTTPRule        `json:"rules"`
	Headers    map[string]string `json:"headers,omitempty"`
	Credential string            `json:"credential"` // Hub-signed exact provider identity, never returned to consumer
}

type dependencyFlight struct{ cancel context.CancelFunc }

func cleanDependencyPath(value string) bool {
	return strings.HasPrefix(value, "/") && len(value) <= 1024 &&
		!strings.ContainsAny(value, "\\%?#\r\n\x00") && path.Clean(value) == value
}

func (p *HTTPDependency) valid() bool {
	if len(p.Rules) == 0 || len(p.Rules) > 64 || len(p.Headers) > 16 || len(p.Credential) < 32 || len(p.Credential) > 8192 {
		return false
	}
	seen := map[string]bool{}
	for _, rule := range p.Rules {
		if !cleanDependencyPath(rule.Path) || rule.Path == "/rpc" || strings.HasPrefix(rule.Path, "/__fleet") || strings.HasPrefix(rule.Path, "/_fleet") || rule.Prefix && rule.Path == "/" {
			return false
		}
		switch rule.Method {
		case "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE":
		default:
			return false
		}
		key := rule.Method + " " + rule.Path
		if seen[key] {
			return false
		}
		seen[key] = true
	}
	for name, value := range p.Headers {
		lower := strings.ToLower(name)
		if name == "" || name != http.CanonicalHeaderKey(name) || len(name) > 128 || len(value) > 4096 || strings.ContainsAny(value, "\r\n\x00") {
			return false
		}
		for _, c := range name {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-') {
				return false
			}
		}
		if strings.HasPrefix(lower, "x-fleet-") || strings.HasPrefix(lower, "x-pantheon-") || strings.HasPrefix(lower, "x-forwarded-") {
			return false
		}
		switch lower {
		case "authorization", "cookie", "host", "connection", "upgrade", "transfer-encoding", "content-length", "te", "trailer", "proxy-authorization", "proxy-connection", "forwarded":
			return false
		}
	}
	return true
}

func (p *HTTPDependency) permits(r *http.Request) bool {
	if !cleanDependencyPath(r.URL.Path) || r.URL.RawPath != "" || r.Header.Get("Origin") != "" || r.Header.Get("Sec-Fetch-Site") != "" || r.Header.Get("Upgrade") != "" || r.Header.Get("Content-Encoding") != "" {
		return false
	}
	for _, rule := range p.Rules {
		if rule.Method == r.Method && (rule.Path == r.URL.Path || rule.Prefix && strings.HasPrefix(r.URL.Path, rule.Path+"/")) {
			return true
		}
	}
	return false
}

func (g *Gateway) dependencyLive(key string, grant *dependencyGrant) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	current := g.dependencies[key]
	return !g.dependencyStoreFailed && current != nil && current.id == grant.id && current.Expires > time.Now().Unix()
}

func (g *Gateway) checkHTTPDependency(ctx context.Context, key string, grant *dependencyGrant) bool {
	if !g.dependencyLive(key, grant) {
		return false
	}
	check, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if g.consumerCheck(check, grant.Consumer, "") != nil || g.verify(check, grant.Provider) != nil || check.Err() != nil {
		return false
	}
	return g.dependencyLive(key, grant)
}

func (g *Gateway) serveHTTPDependency(w http.ResponseWriter, r *http.Request, key string, grant *dependencyGrant) {
	if !grant.HTTP.permits(r) {
		http.Error(w, "HTTP dependency path or method not authorized", 403)
		return
	}
	if !g.checkHTTPDependency(r.Context(), key, grant) {
		http.Error(w, "HTTP dependency no longer authorized", 409)
		return
	}
	ctx, cancel := context.WithDeadline(r.Context(), time.Unix(grant.Expires, 0))
	defer cancel()
	flight := &dependencyFlight{cancel: cancel}
	g.mu.Lock()
	current := g.dependencies[key]
	if g.dependencyStoreFailed || current == nil || current.id != grant.id || current.Expires <= time.Now().Unix() {
		g.mu.Unlock()
		http.Error(w, "HTTP dependency revoked before admission", 409)
		return
	}
	if g.dependencyHTTP[key] == nil {
		g.dependencyHTTP[key] = map[*dependencyFlight]struct{}{}
	}
	g.dependencyHTTP[key][flight] = struct{}{}
	g.mu.Unlock()
	defer func() {
		g.mu.Lock()
		delete(g.dependencyHTTP[key], flight)
		if len(g.dependencyHTTP[key]) == 0 {
			delete(g.dependencyHTTP, key)
		}
		g.mu.Unlock()
	}()
	done := make(chan struct{})
	// Re-check while headers, uploads or streamed output are pending. Failing
	// closed also handles an unreachable consumer node or a poisoned journal.
	go func() {
		defer close(done)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if !g.checkHTTPDependency(ctx, key, grant) {
					cancel()
					return
				}
			}
		}
	}()
	defer func() { cancel(); <-done }()
	request := r.Clone(ctx)
	request.Header.Del("Cookie")
	// Prevent hop-by-hop header removal from discarding owner-bound headers.
	request.Header.Del("Connection")
	for name, value := range grant.HTTP.Headers {
		request.Header.Set(name, value)
	}
	g.proxyApp(w, request, AttachRequest{Binding: grant.Provider, Credential: grant.HTTP.Credential,
		Expires: grant.Expires, Workload: true})
}
