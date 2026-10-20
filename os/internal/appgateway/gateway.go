// Package appgateway exposes isolated App origins through outbound node tunnels.
// It accepts grants only from Hub; browser login credentials never reach an App.
package appgateway

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/gorilla/websocket"
)

type Binding = apptransport.Binding
type AttachRequest struct {
	Binding
	Credential string `json:"credential"` // Hub-signed, instance-scoped, never a login token
	Expires    int64  `json:"expires"`
	UIOrigin   string `json:"ui_origin"`
	Workload   bool   `json:"workload,omitempty"` // server-to-server; no cookies/CORS
	// URLAuth: the service authenticates every request by a capability in its
	// URL (Desktop's data server: /d/<token>/...), for subresources a sandboxed
	// App frame loads without the instance cookie. While the connected grant
	// lives, the gateway forwards cookieless GET/HEAD requests to it, without
	// the owner's credential and readable by any origin, as the service's own
	// CORS policy intends.
	URLAuth bool `json:"url_auth,omitempty"`
	// Visitor: a hosted App's visitor (docs/apps/app-hosting.md). Credential is
	// then the visitor's, and only the App's declared share passes.
	Visitor *VisitorGrant `json:"visitor,omitempty"`
}
type Dispatch func(context.Context, Binding, string, string) error
type Verify func(context.Context, Binding) error
type grant struct {
	AttachRequest
	ticket, cookie string
	ticketExpiry   time.Time
}
type pending struct {
	secret   string
	accepted bool
	conn     chan net.Conn
}
type Gateway struct {
	domain, serviceToken  string
	localRPCOrigin        string
	localHTTP             bool
	origins               map[string]bool
	dispatch              Dispatch
	verify                Verify
	direct                DirectDispatch
	media                 MediaDispatch
	modelIdle             ModelIdleDispatch
	consumerCheck         ConsumerCheck
	dependencyInvoke      DependencyInvoke
	dependencies          map[string]*dependencyGrant
	dependencyHTTP        map[string]map[*dependencyFlight]struct{}
	dependencyStore       *dependencyStore
	dependencyStoreFailed bool
	mu                    sync.Mutex
	grants                map[string]*grant // ticket and cookie share one opaque value
	pending               map[string]*pending
	slots                 chan struct{}
	rpcToken              RPCTokenDispatch
	rpcTokens             map[Binding]string
	hostedNode            string
	hosted                func(http.ResponseWriter, *http.Request, Binding)
	shareResolver         ShareResolver
	shareTarget           ShareTargetResolver
}

// SetHosted serves Apps the controller hosts itself (bindings on node) in
// process rather than through a node tunnel. Called once, before serving.
func (g *Gateway) SetHosted(node string, serve func(http.ResponseWriter, *http.Request, Binding)) {
	g.hostedNode, g.hosted = node, serve
}

// RPCTokenDispatch reads an instance's Runner-issued RPC credential. The
// gateway presents it on /rpc for callers it already authorized for that
// exact instance (an owner's browser window or workload grant), so ToolSet
// Apps serve them without a platform relay. The credential never leaves the
// controller.
type RPCTokenDispatch func(context.Context, Binding) (string, error)

// SetRPCTokenDispatch is called once, before serving.
func (g *Gateway) SetRPCTokenDispatch(read RPCTokenDispatch) {
	g.rpcToken, g.rpcTokens = read, map[Binding]string{}
}

func (g *Gateway) instanceRPCToken(ctx context.Context, b Binding) string {
	if g.rpcToken == nil {
		return ""
	}
	g.mu.Lock()
	token, ok := g.rpcTokens[b]
	g.mu.Unlock()
	if ok {
		return token
	}
	c, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	token, err := g.rpcToken(c, b)
	if err != nil || token == "" {
		return ""
	}
	g.mu.Lock()
	if len(g.rpcTokens) > 4096 {
		clear(g.rpcTokens) // a generation is immutable; dropping the cache only costs a lookup
	}
	g.rpcTokens[b] = token
	g.mu.Unlock()
	return token
}

var domainName = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$`)

// cookieName is the gateway cookie of one Atrium origin. Atrium origins on one
// site (aliases of a Hub) share a cookie partition, so with one name for all,
// connecting the same App from one replaced the other's connection.
func cookieName(uiOrigin string) string {
	sum := sha256.Sum256([]byte(uiOrigin))
	return "__Host-fleetapp-" + hex.EncodeToString(sum[:6])
}

func gatewayCookie(name string) bool {
	return name == "__Host-fleetapp" || strings.HasPrefix(name, "__Host-fleetapp-")
}

func Host(instance, component, port string, generation uint64, domain string) string {
	sum := sha256.Sum256([]byte(instance + ":" + component + ":" + port + ":" + fmt.Sprint(generation)))
	return hex.EncodeToString(sum[:16]) + "." + domain
}
func nonce() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func nativeUIOrigin(origin string) bool {
	return origin == "tauri://localhost" || origin == "http://tauri.localhost" || origin == "https://tauri.localhost"
}
func New(domain, serviceToken string, origins []string, dispatch Dispatch, verify Verify) (*Gateway, error) {
	if !domainName.MatchString(domain) || !strings.Contains(domain, ".") || strings.Contains(domain, "..") || len(domain) > 190 || len(serviceToken) < 24 || dispatch == nil || verify == nil {
		return nil, fmt.Errorf("App gateway requires an isolated wildcard domain, service token and node transport")
	}
	g := &Gateway{domain: domain, serviceToken: serviceToken, origins: map[string]bool{}, dispatch: dispatch, verify: verify, grants: map[string]*grant{}, pending: map[string]*pending{}, slots: make(chan struct{}, 256)}
	for _, s := range origins {
		u, e := url.Parse(s)
		// Tauri's bundled WebView uses these exact local origins. They still
		// require explicit operator allowlisting and an instance-bound grant.
		native := nativeUIOrigin(s)
		if e != nil || u.Host == "" || u.Path != "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (!native && u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1"))) {
			return nil, fmt.Errorf("invalid Atrium origin")
		}
		g.origins[s] = true
	}
	if len(g.origins) == 0 {
		return nil, fmt.Errorf("App gateway needs at least one Atrium origin")
	}
	return g, nil
}

// Register mounts the controller-only control routes. AppHost is installed as
// an outer host router so App traffic can never reach Controller management APIs.
func (g *Gateway) Register(mux *http.ServeMux) {
	if g.localRPCOrigin != "" {
		mux.HandleFunc("/apps/dependencies", g.manageDependency)
		if g.localHTTP {
			mux.HandleFunc("/apps/tunnel/", g.tunnel)
		}
		return
	}
	mux.HandleFunc("/apps/connect", g.attach)
	mux.HandleFunc("/apps/revoke-share", g.revokeShare)
	mux.HandleFunc("/apps/share-target", g.resolveShareTarget)
	mux.HandleFunc("/apps/direct-connect", g.attachDirect)
	mux.HandleFunc("/apps/model-idle", g.accessModelIdle)
	mux.HandleFunc("/apps/dependencies", g.manageDependency)
	mux.HandleFunc("/apps/dependencies/direct", g.attachDependencyDirect)
	mux.HandleFunc("/apps/dependencies/check", g.checkDependencyDirect)
	mux.HandleFunc("/apps/tunnel/", g.tunnel)
}
func (g *Gateway) Handler(controller http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if g.localRPCOrigin != "" {
			if "https://"+r.Host != g.localRPCOrigin || r.TLS == nil {
				http.Error(w, "local dependency origin required", 403)
				return
			}
			if g.serveDependency(w, r) {
				return
			}
			if r.URL.Path == "/rpc" {
				http.Error(w, "dependency authorization required", 401)
				return
			}
			controller.ServeHTTP(w, r)
			return
		}
		host := strings.ToLower(r.Host)
		if strings.HasSuffix(host, "."+g.domain) {
			g.serveApp(w, r)
			return
		}
		controller.ServeHTTP(w, r)
	})
}
func (g *Gateway) attach(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || !g.serviceAuthorized(r) {
		http.Error(w, "unauthorized", 401)
		return
	}
	var q AttachRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16384))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&q) != nil || !q.Valid() || (!q.Workload && !g.origins[q.UIOrigin]) || (q.Workload && (q.UIOrigin != "" || q.URLAuth)) || len(q.Credential) < 32 || len(q.Credential) > 8192 || q.Expires <= time.Now().Unix() || q.Expires > time.Now().Add(12*time.Hour).Unix() {
		http.Error(w, "invalid instance grant", 400)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	if err := g.verify(ctx, q.Binding); err != nil {
		http.Error(w, "App is not ready on the requested node/version", 409)
		return
	}
	if q.Visitor != nil {
		if err := g.prepareVisitor(ctx, &q); err != nil {
			status := 400
			if err == errNotShared {
				status = 403
			}
			http.Error(w, err.Error(), status)
			return
		}
	}
	g.mu.Lock()
	for key, v := range g.grants {
		if v.Expires <= time.Now().Unix() || (v.cookie == "" && v.ticketExpiry.Before(time.Now())) {
			delete(g.grants, key)
		}
	}
	if len(g.grants) >= 1024 {
		g.mu.Unlock()
		http.Error(w, "App gateway capacity reached", 503)
		return
	}
	ticket := nonce()
	v := &grant{AttachRequest: q, ticket: ticket, ticketExpiry: time.Now().Add(time.Minute)}
	if q.Workload {
		v.cookie = ticket // already activated; never exchanged for a browser cookie
	}
	g.grants[ticket] = v
	g.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	result := map[string]any{"origin": "https://" + Host(q.Instance, q.Component, q.Port, q.Generation, g.domain), "expires": q.Expires}
	if q.Workload {
		result["access_token"] = ticket
	} else {
		result["ticket"] = ticket
	}
	_ = json.NewEncoder(w).Encode(result)
}
func (g *Gateway) serviceAuthorized(r *http.Request) bool {
	return subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+g.serviceToken)) == 1
}

func (g *Gateway) serveApp(w http.ResponseWriter, r *http.Request) {
	if g.serveDependency(w, r) {
		return
	}
	w.Header().Set("Referrer-Policy", "no-referrer")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	origin := r.Header.Get("Origin")
	if g.origins[origin] {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Access-Control-Allow-Credentials", "true")
		// Binary model artifacts use bounded ranges and a frozen configuration.
		// Browser access still requires the instance cookie below; workload
		// bearer grants remain unusable by Origin-bearing browser requests.
		w.Header().Set("Access-Control-Expose-Headers", "ETag, Content-Range")
		w.Header().Set("Vary", "Origin")
		if r.Method == "OPTIONS" {
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-Model-Config, Range, Upload-Offset")
			w.WriteHeader(204)
			return
		}
	}
	if r.URL.Path == "/__fleet/connect" {
		g.connect(w, r)
		return
	}
	workload := strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") && origin == "" && r.Header.Get("Sec-Fetch-Site") == ""
	var keys []string
	if workload {
		keys = []string{strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")}
	} else {
		for _, cookie := range r.Cookies() {
			if gatewayCookie(cookie.Name) && cookie.Value != "" {
				keys = append(keys, cookie.Value)
			}
		}
	}
	if len(keys) == 0 {
		if access, ok := g.urlAuthGrant(r); ok {
			w.Header().Set("Access-Control-Allow-Origin", "*")
			w.Header().Del("Access-Control-Allow-Credentials")
			g.proxyApp(w, r, access)
			return
		}
		http.Error(w, "Connect this App through Atrium", 401)
		return
	}
	access, ok := g.requestGrant(r, keys, workload, origin)
	if !ok {
		http.Error(w, "App connection expired; reconnect through Atrium", 401)
		return
	}
	// WKWebView omits Referer when navigating from a custom local scheme to
	// HTTPS. For native grants, enforce the complete ancestor chain instead.
	// This is additional to the instance cookie, host/generation and Origin
	// checks; an unrelated web page still cannot embed this authenticated App.
	if nativeUIOrigin(access.UIOrigin) {
		w.Header().Add("Content-Security-Policy", "frame-ancestors 'self' "+access.UIOrigin)
	}
	// Cookies are partitioned under Atrium. Requests from an unrelated website
	// or another App origin cannot operate this instance, even with a cookie.
	if origin != "" && origin != access.UIOrigin && origin != "https://"+r.Host {
		http.Error(w, "origin not allowed", 403)
		return
	}
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" && origin == "" && r.Header.Get("Sec-Fetch-Mode") == "navigate" {
		// The native editor is an iframe created by Atrium: unlike fetch, this
		// navigation carries Referer, not Origin. A controlling service worker
		// re-fetches that navigation with destination "empty" on reload. Both
		// forms still require this grant's exact Atrium referrer; top-level
		// document navigations and unrelated embedding remain denied.
		referrer, err := url.Parse(r.Referer())
		destination := r.Header.Get("Sec-Fetch-Dest")
		nativeFrame := nativeUIOrigin(access.UIOrigin) && r.Referer() == "" && destination == "iframe"
		if !nativeFrame && (err != nil || referrer.Scheme+"://"+referrer.Host != access.UIOrigin || (destination != "iframe" && destination != "empty")) {
			http.Error(w, "open this App through Atrium", 403)
			return
		}
	}
	if access.Visitor != nil && !allowVisitor(w, r, access.Visitor) {
		return
	}
	if r.URL.Path == "/__fleet/status" && r.Method == "GET" {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"connected": true, "instance_id": access.Instance, "generation": access.Generation})
		return
	}
	if r.URL.Path == "/__fleet/model-media" {
		g.mediaOffer(w, r, access)
		return
	}
	g.proxyApp(w, r, access)
}

// requestGrant is the live connection this request uses among its gateway
// cookies for this host. A request from an Atrium uses that Atrium's own
// connection (none: it reconnects); any other request prefers the connection
// of the Atrium its Referer names. The origin checks in serveApp still apply.
func (g *Gateway) requestGrant(r *http.Request, keys []string, workload bool, origin string) (AttachRequest, bool) {
	referrer := ""
	if u, err := url.Parse(r.Referer()); err == nil && u.Host != "" {
		referrer = u.Scheme + "://" + u.Host
	}
	atrium := origin != "" && g.origins[origin]
	now := time.Now().Unix()
	g.mu.Lock()
	defer g.mu.Unlock()
	var found AttachRequest
	ok := false
	for _, key := range keys {
		v := g.grants[key]
		if v == nil || v.cookie != key || v.Workload != workload || v.Expires <= now ||
			r.Host != Host(v.Instance, v.Component, v.Port, v.Generation, g.domain) {
			continue
		}
		if v.UIOrigin == origin || (!atrium && referrer != "" && v.UIOrigin == referrer) {
			return v.AttachRequest, true
		}
		if !atrium && !ok {
			found, ok = v.AttachRequest, true
		}
	}
	return found, ok
}

// urlAuthGrant is the live, connected URL-capability grant for this host,
// stripped of the owner's credential: the service decides each request.
func (g *Gateway) urlAuthGrant(r *http.Request) (AttachRequest, bool) {
	if r.Method != "GET" && r.Method != "HEAD" {
		return AttachRequest{}, false
	}
	now := time.Now().Unix()
	g.mu.Lock()
	defer g.mu.Unlock()
	for key, v := range g.grants {
		if v.URLAuth && !v.Workload && v.cookie == key && v.Expires > now &&
			r.Host == Host(v.Instance, v.Component, v.Port, v.Generation, g.domain) {
			access := v.AttachRequest
			access.Credential = ""
			return access, true
		}
	}
	return AttachRequest{}, false
}

func (g *Gateway) proxyApp(w http.ResponseWriter, r *http.Request, access AttachRequest) {
	if g.hosted != nil && access.Node == g.hostedNode {
		g.hosted(w, r, access.Binding)
		return
	}
	rpcToken := ""
	if r.Method == "POST" && r.URL.Path == "/rpc" {
		rpcToken = g.instanceRPCToken(r.Context(), access.Binding)
	}
	// An App RPC runs up to 600 s (timeout_s) and the App ends it then; the
	// gateway waits a little longer so a long call is not cut at the proxy.
	transport := &http.Transport{DisableKeepAlives: true, ResponseHeaderTimeout: 660 * time.Second,
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) { return g.dial(ctx, access) }}
	defer transport.CloseIdleConnections()
	proxy := &httputil.ReverseProxy{Transport: transport, FlushInterval: -1,
		Rewrite: func(p *httputil.ProxyRequest) {
			p.Out.URL.Scheme = "http"
			p.Out.URL.Host = "app.local"
			p.Out.Host = p.In.Host
			p.Out.Header.Del("Cookie")
			p.Out.Header.Del("X-Fleet-RPC-Token")
			if rpcToken != "" {
				p.Out.Header.Set("X-Fleet-RPC-Token", rpcToken)
			}
			for _, cookie := range p.In.Cookies() {
				if !gatewayCookie(cookie.Name) {
					p.Out.AddCookie(cookie)
				}
			}
			p.Out.Header.Del("X-Pantheon-Visitor")
			if access.Visitor != nil {
				p.Out.Header.Set("X-Pantheon-Visitor", visitorHeader(access.Visitor))
			}
			p.Out.Header.Del("X-Pantheon-App-Token")
			if access.Credential != "" {
				p.Out.Header.Set("X-Pantheon-App-Token", access.Credential)
			}
			if access.Workload {
				p.Out.Header.Del("Authorization")
			}
			p.Out.Header.Set("X-Forwarded-Host", p.In.Host)
			p.Out.Header.Set("X-Forwarded-Proto", "https")
		},
		ModifyResponse: func(res *http.Response) error {
			cookies := res.Cookies()
			res.Header.Del("Set-Cookie")
			for _, cookie := range cookies {
				if access.Workload || gatewayCookie(cookie.Name) {
					continue
				}
				cookie.Domain = "" // never let an App set cookies on sibling origins
				cookie.Secure = true
				cookie.SameSite = http.SameSiteNoneMode
				cookie.Partitioned = true
				res.Header.Add("Set-Cookie", cookie.String())
			}
			res.Header.Del("Access-Control-Allow-Origin")
			res.Header.Del("Access-Control-Allow-Credentials")
			res.Header.Set("Referrer-Policy", "no-referrer")
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, _ *http.Request, err error) {
			// Do not log the request URL, cookies or instance credential.
			log.Printf("App gateway transport failed: instance=%s component=%s error=%v", access.Instance, access.Component, err)
			http.Error(w, "App connection unavailable; reconnect through Atrium", 502)
		},
	}
	proxy.ServeHTTP(w, r)
}

func (g *Gateway) connect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if r.Method != "POST" || !g.origins[r.Header.Get("Origin")] {
		http.Error(w, "connect through Atrium", 403)
		return
	}
	var q struct {
		Ticket string `json:"ticket"`
	}
	if json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024)).Decode(&q) != nil {
		http.Error(w, "bad ticket", 400)
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	v := g.grants[q.Ticket]
	if v == nil || v.ticketExpiry.Before(time.Now()) || v.cookie != "" || v.UIOrigin != r.Header.Get("Origin") || r.Host != Host(v.Instance, v.Component, v.Port, v.Generation, g.domain) {
		http.Error(w, "invalid connection ticket", 401)
		return
	}
	delete(g.grants, q.Ticket)
	v.cookie = nonce()
	v.ticket = ""
	g.grants[v.cookie] = v
	http.SetCookie(w, &http.Cookie{Name: cookieName(v.UIOrigin), Value: v.cookie, Path: "/", Secure: true, HttpOnly: true, SameSite: http.SameSiteNoneMode, Partitioned: true, MaxAge: int(v.Expires - time.Now().Unix())})
	w.WriteHeader(204)
}

func (g *Gateway) dial(ctx context.Context, a AttachRequest) (net.Conn, error) {
	select {
	case g.slots <- struct{}{}:
	default:
		return nil, fmt.Errorf("connection capacity reached")
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	id, secret := nonce(), nonce()
	p := &pending{secret: secret, conn: make(chan net.Conn)}
	g.mu.Lock()
	g.pending[id] = p
	g.mu.Unlock()
	defer func() { g.mu.Lock(); delete(g.pending, id); g.mu.Unlock() }()
	errors := make(chan error, 1)
	go func() { errors <- g.dispatch(ctx, a.Binding, id, secret) }()
	for {
		select {
		case err := <-errors:
			if err != nil {
				<-g.slots
				return nil, err
			}
			errors = nil
		case conn := <-p.conn:
			// Expiry also closes WebSocket upgrades, which outlive the HTTP request.
			wrapped := &limitedConn{Conn: conn, release: func() { <-g.slots }}
			wrapped.mu.Lock()
			wrapped.timer = time.AfterFunc(time.Until(time.Unix(a.Expires, 0)), func() { _ = wrapped.Close() })
			wrapped.mu.Unlock()
			return wrapped, nil
		case <-ctx.Done():
			<-g.slots
			return nil, ctx.Err()
		}
	}
}

type limitedConn struct {
	net.Conn
	mu      sync.Mutex
	once    sync.Once
	release func()
	timer   *time.Timer
}

func (c *limitedConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(func() {
		c.mu.Lock()
		defer c.mu.Unlock()
		if c.timer != nil {
			c.timer.Stop()
		}
		c.release()
	})
	return err
}

func (g *Gateway) tunnel(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/apps/tunnel/")
	g.mu.Lock()
	p := g.pending[id]
	if p == nil || p.accepted || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+p.secret)) != 1 {
		g.mu.Unlock()
		http.Error(w, "invalid tunnel", 401)
		return
	}
	p.accepted = true
	g.mu.Unlock()
	ws, err := (&websocket.Upgrader{ReadBufferSize: 32768, WriteBufferSize: 32768, CheckOrigin: func(r *http.Request) bool { return r.Header.Get("Origin") == "" }}).Upgrade(w, r, nil)
	if err != nil {
		return
	}
	conn := apptransport.New(ws)
	select {
	case p.conn <- conn:
	case <-time.After(15 * time.Second):
		_ = conn.Close()
	}
}
