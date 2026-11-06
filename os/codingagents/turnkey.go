package codingagents

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"
)

// A Team Chat turn on PantheonOS models never holds the user's model key
// (docs/apps/team-chat.md, "Fixes after the sixth Phase 3 check"). That key
// is the user's LiteLLM virtual key: it never expires and carries the
// user's whole monthly budget, so whoever copied it out of a turn could
// spend that budget from anywhere, for as long as they liked. Instead the
// turn gets a token of its own for the node's route gateway (127.0.0.1),
// which passes the turn's model calls on to the platform's proxy with the
// node's key (billed to the user's budget, as before). The token:
//   - is made when the turn starts and revoked the moment its runtime
//     exits, and in any case after turnKeyMax;
//   - is good only on this computer's gateway, only for model calls
//     (turnModelPaths), never for the proxy's other routes;
//   - cannot be renewed or extended: nothing the gateway serves makes or
//     changes a token.

// turnKeyMax is how long a turn's model token lives at most, should its
// turn never be seen to end.
const turnKeyMax = 12 * time.Hour

// turnModelPaths are the model calls a turn's token may make: Anthropic's
// Messages (Claude Code), OpenAI's Responses (Codex) and Chat Completions
// (OpenCode, Pi), and the model list.
var turnModelPaths = map[string]string{
	"/v1/messages": http.MethodPost, "/v1/messages/count_tokens": http.MethodPost,
	"/v1/responses": http.MethodPost, "/v1/chat/completions": http.MethodPost, "/v1/models": http.MethodGet,
}

// TurnModelKey is the model service a Team Chat turn of agent is pointed at
// instead of p: the node's gateway and a new token that passes model calls
// on to p. revoke ends the token (call it when the turn ends; more than
// once is fine).
func TurnModelKey(agent string, p Platform) (turn Platform, revoke func(), err error) {
	g, err := routeGateway()
	if err != nil {
		return Platform{}, func() {}, err
	}
	token := g.grantModel(agent, p, time.Now().Add(turnKeyMax))
	var once sync.Once
	return Platform{Base: g.base, Key: token}, func() { once.Do(func() { g.revoke(token) }) }, nil
}

// RevokeLaunchTokens ends the route gateway tokens a launch's environment
// holds (a route's PANTHEON_ROUTE_TOKEN): a Team Chat turn's end with it.
func RevokeLaunchTokens(env []string) {
	for _, kv := range env {
		if token, ok := strings.CutPrefix(kv, routeTokenVar+"="); ok && token != "" {
			if g, err := routeGateway(); err == nil {
				g.revoke(token)
			}
		}
	}
}

// grantModel is a new token that passes agent's model calls on to p until
// it is revoked or until.
func (g *RouteGateway) grantModel(agent string, p Platform, until time.Time) string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	token := "ptk_" + hex.EncodeToString(b)
	g.mu.Lock()
	g.grants[sha256.Sum256([]byte(token))] = routeGrant{Agent: agent, model: &p, until: until}
	g.mu.Unlock()
	return token
}

// passModel sends one model call of a turn on to the platform's proxy, the
// turn's token replaced by the node's key, and its answer back as it comes.
func (g *RouteGateway) passModel(w http.ResponseWriter, r *http.Request, grant routeGrant) {
	path := strings.TrimSuffix(r.URL.Path, "/")
	method, ok := turnModelPaths[path]
	if !ok {
		gatewayError(w, http.StatusNotFound, "invalid_request_error", "A Team Chat turn's token is good only for model calls")
		return
	}
	if r.Method != method {
		gatewayError(w, http.StatusMethodNotAllowed, "invalid_request_error", "Use "+method+" for "+path)
		return
	}
	up, err := url.Parse(grant.model.Base)
	if err != nil {
		gatewayError(w, http.StatusBadGateway, "api_error", "The model service's address is not one")
		return
	}
	key := grant.model.Key
	proxy := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme, pr.Out.URL.Host = up.Scheme, up.Host
			pr.Out.URL.Path, pr.Out.URL.RawPath = strings.TrimRight(up.Path, "/")+path, ""
			pr.Out.URL.RawQuery = pr.In.URL.RawQuery
			pr.Out.Host = up.Host
			for _, h := range []string{"Authorization", "X-Api-Key", "Cookie", "Proxy-Authorization"} {
				pr.Out.Header.Del(h)
			}
			pr.Out.Header.Set("Authorization", "Bearer "+key)
		},
		// Streamed answers go on as they come.
		FlushInterval: -1,
		Transport:     g.client.Transport,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			if r.Context().Err() == nil {
				gatewayError(w, http.StatusBadGateway, "api_error", "The model service did not answer")
			}
		},
	}
	r.Body = http.MaxBytesReader(w, r.Body, 64<<20)
	proxy.ServeHTTP(w, r)
}
