package codingagents

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
)

// A route of Model Services (fleet-route://<id>: a name over models tried in
// order) can be an agent's model. Pi and OpenCode reach it through the
// node's route gateway: an OpenAI Chat Completions endpoint on 127.0.0.1
// that takes the route's id as the model, resolves the route through the
// Hub with the node's PantheonOS key, and sends the request to the route's
// models in order. It moves to the next only before the first byte of an
// answer; a generation once started is never sent again. Platform models
// go to the platform's proxy with the node's platform key (billed there);
// models served by the user's own computers are skipped for now.
//
// Each launch gets a token of its own for one route; neither key reaches
// the agent.

// RouteRef is how a route is named as a model.
const RouteRef = "fleet-route://"

// RouteAgents are the agents that can run a route.
var RouteAgents = []string{"opencode", "pi"}

// RouteID is the route a model names ("fleet-route://tier-normal" is
// "tier-normal"); false when it names none.
func RouteID(model string) (string, bool) {
	id, ok := strings.CutPrefix(model, RouteRef)
	return id, ok && routeIDOK(id)
}

func routeIDOK(id string) bool {
	if id == "" || len(id) > 64 {
		return false
	}
	for i, r := range id {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || i > 0 && (r == '_' || r == '-')) {
			return false
		}
	}
	return true
}

// platformDeployment is the platform's own service in every user's model
// directory: its models are the proxy's.
const platformDeployment = "platform"

// routeProvider is the provider name Pi and OpenCode know the gateway by.
const routeProvider = "pantheon-routes"

// routeTokenVar carries a launch's gateway token to the agent.
const routeTokenVar = "PANTHEON_ROUTE_TOKEN"

// RouteSkipped says why a route's models on the user's own computers are left out.
const RouteSkipped = "models on your own computers are not reachable from coding agents yet"

// Route is a route as the desktop offers it: its reference, name and the
// models a coding agent can reach, in the order tried.
type Route struct {
	Ref    string   `json:"ref"`
	Name   string   `json:"name"`
	Models []string `json:"models"`
	// Note says what the agent leaves out of it.
	Note string `json:"note,omitempty"`
}

// hubOf is the Hub a node reaches as the user: its PantheonOS key (access).
func hubOf(s Settings) (base, key string, ok bool) {
	base, key = strings.TrimRight(s.Access.HubURL, "/"), s.Access.Key
	if key == "" || placeholderKey(key) || strings.ContainsAny(key, " \t\r\n") {
		return "", "", false
	}
	if u, err := url.Parse(base); err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return "", "", false
	}
	return base, key, true
}

const (
	routesTTL   = time.Minute
	resolveTTL  = 30 * time.Second
	routesWait  = 8 * time.Second
	routesLimit = 4 << 20
)

// hubJSON does one Hub request as the user; the key goes in a header only.
func hubJSON(ctx context.Context, method, base, key, path string, body, out any) error {
	ctx, cancel := context.WithTimeout(ctx, routesWait)
	defer cancel()
	var data io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		data = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, data)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		var e struct {
			Detail any `json:"detail"`
		}
		_ = json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(&e)
		if d, ok := e.Detail.(string); ok && d != "" {
			return errors.New(d)
		}
		return &modelsError{resp.StatusCode}
	}
	return json.NewDecoder(io.LimitReader(resp.Body, routesLimit)).Decode(out)
}

type hubRoute struct {
	RouteID    string `json:"route_id"`
	Name       string `json:"name"`
	Candidates []struct {
		DeploymentID string `json:"deployment_id"`
		ModelID      string `json:"model_id"`
	} `json:"candidates"`
	Requires struct {
		Operation string `json:"operation"`
	} `json:"requires"`
}

var routesMemo struct {
	sync.Mutex
	base, key string
	routes    []Route
	at        time.Time
}

// PlatformRoutes are the user's text routes, for the agents that can run
// them (RouteAgents); read from the Hub at most every minute, the last list
// kept when it does not answer. None without a PantheonOS key or the
// platform's models.
func PlatformRoutes(s Settings) []Route {
	base, key, ok := hubOf(s)
	if _, platformOK := s.PlatformOn(); !ok || !platformOK {
		return nil
	}
	m := &routesMemo
	m.Lock()
	defer m.Unlock()
	if m.base != base || m.key != key {
		m.base, m.key, m.routes, m.at = base, key, nil, time.Time{}
	}
	if !m.at.IsZero() && time.Since(m.at) < routesTTL {
		return m.routes
	}
	var doc struct {
		Routes []hubRoute `json:"routes"`
	}
	if err := hubJSON(context.Background(), http.MethodGet, base, key, "/api/model-services/routes", nil, &doc); err == nil {
		m.routes = routesOf(doc.Routes)
	}
	m.at = time.Now()
	return m.routes
}

// routesOf are the routes an agent can run, by name: the text ones, each
// with its platform models (the others left out, and said so).
func routesOf(list []hubRoute) []Route {
	out := []Route{}
	for _, r := range list {
		if !routeIDOK(r.RouteID) || (r.Requires.Operation != "" && r.Requires.Operation != "text") {
			continue
		}
		route := Route{Ref: RouteRef + r.RouteID, Name: cmpOr(r.Name, r.RouteID), Models: []string{}}
		for _, c := range r.Candidates {
			if c.DeploymentID == platformDeployment {
				route.Models = append(route.Models, c.ModelID)
			} else {
				route.Note = RouteSkipped
			}
		}
		out = append(out, route)
	}
	slices.SortStableFunc(out, func(a, b Route) int { return strings.Compare(strings.ToLower(a.Name), strings.ToLower(b.Name)) })
	return out
}

func cmpOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// routeName is a route's name as last listed; its id when not listed.
func routeName(id string) string {
	routesMemo.Lock()
	defer routesMemo.Unlock()
	for _, r := range routesMemo.routes {
		if r.Ref == RouteRef+id {
			return r.Name
		}
	}
	return id
}

// ---- resolution -------------------------------------------------------------

// routePlan is a route resolved: the models to try, in order.
type routePlan struct {
	Candidates []routeCandidate
	// Skipped and Excluded say why models of the route are not tried.
	Skipped  []string
	Excluded []string
}

type routeCandidate struct{ Deployment, Model string }

var resolveMemo struct {
	sync.Mutex
	plans map[string]resolved
}

type resolved struct {
	plan routePlan
	at   time.Time
}

// resolveRoute asks the Hub which of route id's models may run a request
// now (it applies the route's policy and fallback rule), for a few seconds
// at most once.
func resolveRoute(ctx context.Context, base, key, id string) (routePlan, error) {
	memo := key + "\x00" + base + "\x00" + id
	resolveMemo.Lock()
	if r, ok := resolveMemo.plans[memo]; ok && time.Since(r.at) < resolveTTL {
		resolveMemo.Unlock()
		return r.plan, nil
	}
	resolveMemo.Unlock()
	var doc struct {
		Candidates []struct {
			Deployment struct {
				DeploymentID string `json:"deployment_id"`
			} `json:"deployment"`
			Model struct {
				ID string `json:"id"`
			} `json:"model"`
		} `json:"candidates"`
		Excluded []struct {
			ModelID string `json:"model_id"`
			Reason  string `json:"reason"`
		} `json:"excluded"`
	}
	// The route's own requirements apply; an agent's tools are the
	// platform's to serve or refuse, which moves the request on.
	if err := hubJSON(ctx, http.MethodPost, base, key, "/api/model-services/routes/"+id+"/resolve",
		map[string]any{"operation": "text"}, &doc); err != nil {
		return routePlan{}, err
	}
	var plan routePlan
	for _, c := range doc.Candidates {
		if c.Deployment.DeploymentID != platformDeployment {
			plan.Skipped = append(plan.Skipped, c.Model.ID)
			continue
		}
		plan.Candidates = append(plan.Candidates, routeCandidate{c.Deployment.DeploymentID, c.Model.ID})
	}
	for _, e := range doc.Excluded {
		plan.Excluded = append(plan.Excluded, e.ModelID+" ("+e.Reason+")")
	}
	resolveMemo.Lock()
	if resolveMemo.plans == nil {
		resolveMemo.plans = map[string]resolved{}
	}
	resolveMemo.plans[memo] = resolved{plan, time.Now()}
	resolveMemo.Unlock()
	return plan, nil
}

// ---- the gateway ------------------------------------------------------------

// routeGrant is what one launch's token may do: run one route, or (model
// set: a Team Chat turn's, turnkey.go) pass model calls on to the platform
// proxy with the node's key, until it is revoked or until.
type routeGrant struct {
	Agent, Route string
	model        *Platform
	until        time.Time
}

// RouteGateway is the node's route gateway.
type RouteGateway struct {
	base     string
	mu       sync.Mutex
	grants   map[[32]byte]routeGrant
	settings func() Settings
	client   *http.Client
}

var (
	gatewayOnce sync.Once
	gateway     *RouteGateway
	gatewayErr  error
)

// routeGateway is the node's route gateway, started on first use.
func routeGateway() (*RouteGateway, error) {
	gatewayOnce.Do(func() {
		ln, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			gatewayErr = err
			return
		}
		gateway = newRouteGateway("http://"+ln.Addr().String(), LoadSettings)
		go func() { _ = (&http.Server{Handler: gateway, ReadHeaderTimeout: 10 * time.Second}).Serve(ln) }()
	})
	return gateway, gatewayErr
}

func newRouteGateway(base string, settings func() Settings) *RouteGateway {
	// No overall timeout: an answer streams for as long as it takes.
	return &RouteGateway{base: base, grants: map[[32]byte]routeGrant{}, settings: settings,
		client: &http.Client{Transport: http.DefaultTransport}}
}

// grant is a new token for agent to run route id.
func (g *RouteGateway) grant(agent, id string) string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	token := "prt_" + hex.EncodeToString(b)
	g.mu.Lock()
	g.grants[sha256.Sum256([]byte(token))] = routeGrant{Agent: agent, Route: id}
	g.mu.Unlock()
	return token
}

// revoke ends a token: the gateway refuses it from now on.
func (g *RouteGateway) revoke(token string) {
	g.mu.Lock()
	delete(g.grants, sha256.Sum256([]byte(token)))
	g.mu.Unlock()
}

func (g *RouteGateway) grantOf(r *http.Request) (routeGrant, bool) {
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || token == "" {
		// Anthropic's clients with an API key send it so.
		token = r.Header.Get("X-Api-Key")
	}
	if token == "" {
		return routeGrant{}, false
	}
	sum := sha256.Sum256([]byte(token))
	g.mu.Lock()
	defer g.mu.Unlock()
	grant, ok := g.grants[sum]
	if ok && !grant.until.IsZero() && time.Now().After(grant.until) {
		delete(g.grants, sum)
		return routeGrant{}, false
	}
	return grant, ok
}

func gatewayError(w http.ResponseWriter, status int, kind, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": message, "type": kind}})
}

func (g *RouteGateway) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	grant, ok := g.grantOf(r)
	if !ok {
		gatewayError(w, http.StatusUnauthorized, "authentication_error", "This route gateway needs the token of a launch")
		return
	}
	if grant.model != nil {
		g.passModel(w, r, grant)
		return
	}
	switch strings.TrimSuffix(r.URL.Path, "/") {
	case "/v1/models":
		_ = json.NewEncoder(w).Encode(map[string]any{"object": "list",
			"data": []any{map[string]string{"id": grant.Route, "object": "model", "owned_by": routeProvider}}})
	case "/v1/chat/completions":
		if r.Method != http.MethodPost {
			gatewayError(w, http.StatusMethodNotAllowed, "invalid_request_error", "POST a chat completion")
			return
		}
		g.complete(w, r, grant)
	default:
		gatewayError(w, http.StatusNotFound, "invalid_request_error", "This gateway serves /v1/chat/completions")
	}
}

// complete sends a chat completion to the route's models in order: the next
// only when one answers with an error, before any of its answer is passed
// on; once an answer starts, it is the one.
func (g *RouteGateway) complete(w http.ResponseWriter, r *http.Request, grant routeGrant) {
	var body map[string]json.RawMessage
	if err := json.NewDecoder(io.LimitReader(r.Body, 64<<20)).Decode(&body); err != nil {
		gatewayError(w, http.StatusBadRequest, "invalid_request_error", "The request is not JSON")
		return
	}
	var model string
	_ = json.Unmarshal(body["model"], &model)
	if id, ok := RouteID(model); ok {
		model = id
	}
	if model != grant.Route {
		gatewayError(w, http.StatusForbidden, "permission_error", "This launch runs the route "+grant.Route)
		return
	}
	s := g.settings()
	base, key, ok := hubOf(s)
	p, platformOK := s.PlatformOn()
	if !ok || !platformOK {
		gatewayError(w, http.StatusServiceUnavailable, "route_unavailable", "Routes need PantheonOS on and PantheonOS models on this computer")
		return
	}
	plan, err := resolveRoute(r.Context(), base, key, grant.Route)
	if err != nil {
		gatewayError(w, http.StatusBadGateway, "route_unavailable", "Could not resolve the route "+grant.Route+": "+err.Error())
		return
	}
	var failures []string
	for _, c := range plan.Candidates {
		body["model"], _ = json.Marshal(c.Model)
		data, _ := json.Marshal(body)
		req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, p.Base+"/v1/chat/completions", bytes.NewReader(data))
		if err != nil {
			failures = append(failures, c.Model+": "+err.Error())
			continue
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+p.Key)
		if a := r.Header.Get("Accept"); a != "" {
			req.Header.Set("Accept", a)
		}
		resp, err := g.client.Do(req)
		if err != nil {
			if r.Context().Err() != nil {
				return // the agent gave up
			}
			failures = append(failures, c.Model+": "+err.Error())
			continue
		}
		if resp.StatusCode >= 400 {
			failures = append(failures, c.Model+": "+upstreamError(resp))
			resp.Body.Close()
			continue
		}
		passOn(w, resp)
		return
	}
	msg := "No model of the route " + grant.Route + " answered"
	if len(failures) > 0 {
		msg += ": " + strings.Join(failures, "; ")
	}
	if len(plan.Excluded) > 0 {
		msg += ". Not allowed now: " + strings.Join(plan.Excluded, ", ")
	}
	if len(plan.Skipped) > 0 {
		msg += ". Skipped (" + RouteSkipped + "): " + strings.Join(plan.Skipped, ", ")
	}
	gatewayError(w, http.StatusBadGateway, "route_unavailable", msg)
}

// upstreamError is the gist of a model's error answer.
func upstreamError(resp *http.Response) string {
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<10))
	var e struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	text := strings.TrimSpace(string(b))
	if json.Unmarshal(b, &e) == nil && e.Error.Message != "" {
		text = e.Error.Message
	}
	if len(text) > 200 {
		text = text[:200] + "…"
	}
	return resp.Status + " " + text
}

// passOn copies an answer as it comes, a streamed one chunk by chunk.
func passOn(w http.ResponseWriter, resp *http.Response) {
	defer resp.Body.Close()
	for _, h := range []string{"Content-Type", "Cache-Control"} {
		if v := resp.Header.Get(h); v != "" {
			w.Header().Set(h, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	flusher, _ := w.(http.Flusher)
	buf := make([]byte, 32<<10)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := w.Write(buf[:n]); werr != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
		if err != nil {
			return
		}
	}
}

// ---- launching an agent on a route ------------------------------------------

// RouteLaunch is how agent runs model when model names a route: the
// arguments that point it at the route gateway (instead of PlatformArgs),
// the variables to set over the launch's (its token; the platform key
// blanked, which it does not need) and its model argument. ok is false when
// model is no route; err when the agent or node cannot run one.
func RouteLaunch(agent, model string, s Settings) (args, env []string, modelArg string, ok bool, err error) {
	id, isRoute := RouteID(model)
	if !isRoute {
		return nil, nil, "", false, nil
	}
	if !slices.Contains(RouteAgents, agent) {
		return nil, nil, "", true, errors.New("only Pi and OpenCode run Model Services routes for now")
	}
	if _, _, hubOK := hubOf(s); !hubOK {
		return nil, nil, "", true, errors.New("routes need PantheonOS on for this computer's agents")
	}
	if _, platformOK := s.PlatformOn(); !platformOK {
		return nil, nil, "", true, errors.New(PlatformMissing)
	}
	g, err := routeGateway()
	if err != nil {
		return nil, nil, "", true, err
	}
	return g.launch(agent, id, routeName(id))
}

func (g *RouteGateway) launch(agent, id, name string) (args, env []string, modelArg string, ok bool, err error) {
	env = []string{routeTokenVar + "=" + g.grant(agent, id), platformKeyVar + "="}
	switch agent {
	case "pi":
		path, err := writeRouteExtension(g.base, id, name)
		if err != nil {
			return nil, nil, "", true, err
		}
		return []string{"-e", path, "--provider", routeProvider}, env, id, true, nil
	case "opencode":
		cfg, _ := json.Marshal(map[string]any{"provider": map[string]any{routeProvider: map[string]any{
			"npm": "@ai-sdk/openai-compatible", "name": "PantheonOS routes",
			"options": map[string]string{"baseURL": g.base + "/v1", "apiKey": "{env:" + routeTokenVar + "}"},
			"models":  map[string]any{id: map[string]string{"name": name}}}},
			"model": routeProvider + "/" + id, "small_model": routeProvider + "/" + id})
		return nil, append(env, "OPENCODE_CONFIG_CONTENT="+string(cfg)), routeProvider + "/" + id, true, nil
	}
	return nil, nil, "", true, errors.New("only Pi and OpenCode run Model Services routes for now")
}

// routeExtension is a Pi extension that adds the route gateway as a
// provider with one model, the route; the token comes from the environment.
func routeExtension(base, id, name string) []byte {
	gw, _ := json.Marshal(base + "/v1")
	model, _ := json.Marshal([]map[string]any{{"id": id, "name": name, "reasoning": false, "input": []string{"text"},
		"cost": map[string]float64{"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "contextWindow": 128000, "maxTokens": 16384}})
	return []byte("// Written by PantheonOS: a Model Services route for Pi, through this computer's route gateway.\n" +
		"export default function (pi) {\n" +
		"  pi.registerProvider(\"" + routeProvider + "\", {\n" +
		"    name: \"PantheonOS routes\", baseUrl: " + string(gw) + ", api: \"openai-completions\",\n" +
		"    apiKey: process.env." + routeTokenVar + ",\n" +
		"    models: " + string(model) + ",\n  })\n}\n")
}

// writeRouteExtension keeps route id's Pi extension in the node's state
// folder, and returns its path.
func writeRouteExtension(base, id, name string) (string, error) {
	dir := filepath.Join(Root(), "platform")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(dir, "pi-route-"+id+".js")
	tmp, err := os.CreateTemp(dir, "pi-route-*.tmp")
	if err != nil {
		return "", err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(routeExtension(base, id, name)); err != nil {
		tmp.Close()
		return "", err
	}
	if err := tmp.Close(); err != nil {
		return "", err
	}
	return path, os.Rename(tmp.Name(), path)
}

// SetEnv is env with each KEY=VALUE of extra set over it.
func SetEnv(env []string, extra ...string) []string { return mergeEnv(env, extra...) }
