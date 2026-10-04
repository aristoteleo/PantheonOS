package appgateway

// HTTP data-plane dependencies reuse the durable RPC grant identities and
// revocation journal. No browser cookies, management RPCs or arbitrary paths
// are acquired by exchanging one of these credentials.
import (
	"context"
	"net/http"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

type HTTPRule = apptransport.HTTPRule
type HTTPDependency = apptransport.HTTPDependency
type dependencyFlight struct{ cancel context.CancelFunc }

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
	if !grant.HTTP.Permits(r) {
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
