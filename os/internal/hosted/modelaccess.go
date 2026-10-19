package hosted

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"reflect"
	"regexp"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/deployments"
)

// ModelAccess is the consumer-scoped Model Services control service
// (formerly the model-services-control App: pantheon/models/access_host.py,
// dependency_service.py, access_control.py). A consumer (the Agent, Files)
// reads the model directory and routes its policy authorizes, and receives
// short-lived inference grants for them; the gateway binds policy_id.
type ModelAccess struct {
	Store *deployments.Store
	Hub   *Hub
	Now   func() time.Time
}

var (
	policyIDRE = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
	peerRE     = regexp.MustCompile(`^[1-9A-HJ-NP-Za-km-z]{32,128}$`)
)

type accessPolicy struct {
	Consumer    json.RawMessage            `json:"consumer"`
	Deployments map[string]json.RawMessage `json:"deployments"`
	Routes      map[string]json.RawMessage `json:"routes"`
	AllowWake   bool                       `json:"allow_wake"`
}

type accessConfig struct {
	policies map[string]accessPolicy
	secret   string
}

func (m *ModelAccess) now() time.Time {
	if m.Now != nil {
		return m.Now()
	}
	return time.Now()
}

// config reads the App's policies and its Hub credential's secret name.
func (m *ModelAccess) config(c *Call) (accessConfig, error) {
	var backend struct {
		Values struct {
			ModelServices struct {
				Protocol int                     `json:"protocol"`
				Policies map[string]accessPolicy `json:"policies"`
			} `json:"model_services"`
		} `json:"values"`
		Credentials struct {
			Hub struct {
				Secret string `json:"$secret"`
			} `json:"hub"`
		} `json:"credentials"`
	}
	if err := json.Unmarshal(c.Spec.Config["backend"], &backend); err != nil {
		return accessConfig{}, err
	}
	spec := backend.Values.ModelServices
	if spec.Protocol != 1 || len(spec.Policies) == 0 || len(spec.Policies) > 64 || backend.Credentials.Hub.Secret == "" {
		return accessConfig{}, fmt.Errorf("supply explicit model consumer policies and a Hub credential")
	}
	for name, p := range spec.Policies {
		if !policyIDRE.MatchString(name) || len(p.Deployments) > 64 || len(p.Routes) > 128 {
			return accessConfig{}, fmt.Errorf("invalid model consumer policy")
		}
		for id, pin := range p.Deployments {
			if !policyIDRE.MatchString(id) {
				return accessConfig{}, fmt.Errorf("pin an exact model connector binding")
			}
			if string(pin) != `"current"` {
				var b identity
				if json.Unmarshal(pin, &b) != nil || !b.valid(true) {
					return accessConfig{}, fmt.Errorf("pin an exact model connector binding")
				}
			}
		}
		for id, pin := range p.Routes {
			var revision int64
			if (id != "*" && !policyIDRE.MatchString(id)) || (string(pin) != `"current"` && (id == "*" || json.Unmarshal(pin, &revision) != nil || revision < 1)) {
				return accessConfig{}, fmt.Errorf("pin model route revisions")
			}
		}
	}
	return accessConfig{policies: spec.Policies, secret: backend.Credentials.Hub.Secret}, nil
}

func (m *ModelAccess) Call(ctx context.Context, c *Call) (any, error) {
	if c.Method != "model_services_control" {
		return nil, &ArgumentError{"Method " + c.Method + " not found on service model-services-control"}
	}
	var args struct {
		PolicyID  string          `json:"policy_id"`
		Operation string          `json:"operation"`
		Arguments json.RawMessage `json:"arguments"`
	}
	if err := c.Decode(&args); err != nil {
		return nil, err
	}
	cfg, err := m.config(c)
	if err != nil {
		return nil, err
	}
	p, ok := cfg.policies[args.PolicyID]
	if !ok {
		return nil, fmt.Errorf("Unknown model dependency policy")
	}
	status, result := 200, map[string]any{}
	if out, err := m.request(ctx, c, cfg.secret, p, args.Operation, args.Arguments); err != nil {
		status = statusOf(err)
	} else {
		result = out
	}
	return map[string]any{"protocol": 1, "operation": args.Operation, "status": status, "result": result}, nil
}

var accessShapes = map[string][]string{"deployments": {}, "routes": {}, "resolve": {"requirements", "route_id"},
	"connect": {"binding"}, "direct_connect": {"binding", "peer_id"}, "engine_idle": {"action", "deployment_id", "revision"}}

func (m *ModelAccess) request(ctx context.Context, c *Call, secret string, p accessPolicy, operation string, raw json.RawMessage) (map[string]any, error) {
	shape, ok := accessShapes[operation]
	var args map[string]json.RawMessage
	if !ok || json.Unmarshal(raw, &args) != nil || args == nil || len(args) != len(shape) {
		return nil, &StatusError{Status: 403}
	}
	for _, k := range shape {
		if _, present := args[k]; !present {
			return nil, &StatusError{Status: 403}
		}
	}
	if len(p.Deployments) == 0 {
		// An explicitly empty policy needs no Hub: it permits a budget-only Agent.
		if operation == "deployments" || operation == "routes" {
			return map[string]any{operation: []any{}}, nil
		}
		return nil, &StatusError{Status: 403}
	}
	var directory struct {
		Deployments []map[string]any `json:"deployments"`
	}
	if err := m.Hub.Request(ctx, c.Fleet, secret, "GET", "/api/model-services", nil, &directory); err != nil {
		return nil, err
	}
	rows, order := map[string]map[string]any{}, []any{}
	for _, row := range directory.Deployments {
		id := str(row["deployment_id"])
		pin, allowed := p.Deployments[id]
		if !allowed {
			continue
		}
		if string(pin) != `"current"` {
			var want any
			_ = json.Unmarshal(pin, &want)
			if !reflect.DeepEqual(want, row["binding"]) {
				continue
			}
		}
		rows[id] = row
		order = append(order, row)
	}
	switch operation {
	case "deployments":
		return map[string]any{"deployments": order}, nil
	case "routes", "resolve":
		var listed struct {
			Routes []map[string]any `json:"routes"`
		}
		if err := m.Hub.Request(ctx, c.Fleet, secret, "GET", "/api/model-services/routes", nil, &listed); err != nil {
			return nil, err
		}
		routes := []any{}
		var chosen map[string]any
		var wanted string
		if operation == "resolve" {
			_ = json.Unmarshal(args["route_id"], &wanted)
		}
		for _, r := range listed.Routes {
			if !p.routeAllowed(r) || !candidatesWithin(r, rows) {
				continue
			}
			routes = append(routes, r)
			if r["route_id"] == wanted {
				chosen = r
			}
		}
		if operation == "routes" {
			return map[string]any{"routes": routes}, nil
		}
		if chosen == nil || !policyIDRE.MatchString(wanted) {
			return nil, &StatusError{Status: 403}
		}
		var result map[string]any
		if err := m.Hub.Request(ctx, c.Fleet, secret, "POST", "/api/model-services/routes/"+wanted+"/resolve", args["requirements"], &result); err != nil {
			return nil, err
		}
		// A concurrent directory or route update must not expand the grant.
		if !reflect.DeepEqual(result["route"], chosen) {
			return nil, &StatusError{Status: 409}
		}
		for _, item := range orList(result["candidates"]) {
			candidate, _ := item.(map[string]any)
			deployment, _ := candidate["deployment"].(map[string]any)
			row := rows[str(deployment["deployment_id"])]
			expected := map[string]any{}
			for k, v := range row {
				expected[k] = v
			}
			expected["models"] = []any{candidate["model"]}
			if row == nil || !reflect.DeepEqual(roundTrip(deployment), roundTrip(expected)) {
				return nil, &StatusError{Status: 409}
			}
		}
		return result, nil
	case "connect", "direct_connect":
		var binding any
		_ = json.Unmarshal(args["binding"], &binding)
		var row map[string]any
		for _, r := range rows {
			if reflect.DeepEqual(r["binding"], binding) && r["state"] == "ready" {
				row = r
			}
		}
		if row == nil {
			return nil, &StatusError{Status: 403}
		}
		peer := ""
		if operation == "direct_connect" {
			if json.Unmarshal(args["peer_id"], &peer) != nil || !peerRE.MatchString(peer) {
				return nil, &StatusError{Status: 400}
			}
		}
		consumer, err := (&Allocator{Store: m.Store}).resolveIdentity(c.Fleet, c.Deployment, p.Consumer, false)
		if err != nil {
			return nil, &StatusError{Status: 503}
		}
		return m.issueConnection(ctx, c.Fleet, secret, consumer, row, peer)
	}
	// engine_idle
	var id, action string
	var revision any
	_ = json.Unmarshal(args["deployment_id"], &id)
	_ = json.Unmarshal(args["action"], &action)
	_ = json.Unmarshal(args["revision"], &revision)
	row := rows[id]
	if row == nil || !reflect.DeepEqual(row["revision"], revision) || (action != "status" && action != "wake") || !p.AllowWake {
		return nil, &StatusError{Status: 403}
	}
	var result map[string]any
	if err := m.Hub.Request(ctx, c.Fleet, secret, "POST", "/api/model-services/"+id+"/engine-idle",
		map[string]any{"action": action, "revision": revision}, &result); err != nil {
		return nil, err
	}
	deployment, _ := result["deployment"].(map[string]any)
	if deployment == nil || !reflect.DeepEqual(deployment["binding"], row["binding"]) {
		return nil, &StatusError{Status: 409}
	}
	return result, nil
}

func roundTrip(v any) any {
	raw, _ := json.Marshal(v)
	var out any
	_ = json.Unmarshal(raw, &out)
	return out
}

func (p accessPolicy) routeAllowed(route map[string]any) bool {
	matches := func(pin json.RawMessage) bool {
		if string(pin) == `"current"` {
			return true
		}
		var revision float64
		return json.Unmarshal(pin, &revision) == nil && revision == num(route["revision"])
	}
	if pin, ok := p.Routes[str(route["route_id"])]; ok && matches(pin) {
		return true
	}
	return string(p.Routes["*"]) == `"current"`
}

func candidatesWithin(route map[string]any, rows map[string]map[string]any) bool {
	for _, item := range orList(route["candidates"]) {
		candidate, _ := item.(map[string]any)
		if rows[str(candidate["deployment_id"])] == nil {
			return false
		}
	}
	return true
}

// inferenceRules is a published connector's full inference authority; model
// and engine management and /rpc are deliberately absent.
func inferenceRules() []map[string]any {
	var rules []map[string]any
	for _, r := range []struct {
		path    string
		methods []string
		prefix  bool
	}{
		{"/v1/chat/completions", []string{"POST"}, false},
		{"/v1/embeddings", []string{"POST"}, false},
		{"/cancel", []string{"POST"}, false},
		{"/route-state", []string{"GET"}, false},
		{"/inference/jobs", []string{"GET", "POST", "DELETE"}, true},
		{"/media/artifacts", []string{"GET", "POST", "PUT", "DELETE"}, true},
	} {
		for _, method := range r.methods {
			rules = append(rules, map[string]any{"method": method, "path": r.path, "prefix": r.prefix})
		}
	}
	return rules
}

// issueConnection obtains a short-lived inference grant for the consumer on
// the connector, through the Hub (which signs the provider credential).
func (m *ModelAccess) issueConnection(ctx context.Context, fleet, secret string, consumer identity, row map[string]any, peer string) (map[string]any, error) {
	var provider identity
	if json.Unmarshal(mustJSON(row["binding"]), &provider) != nil || !provider.valid(true) || !consumer.valid(false) {
		return nil, &StatusError{Status: 403}
	}
	request := map[string]any{"consumer": consumer, "provider": provider, "app_id": "model-service",
		"rules": inferenceRules(), "ttl_seconds": 300}
	// Retries within a short window reuse the receipt.
	window := map[string]any{"owner": fleet, "window": m.now().Unix() / 30}
	for k, v := range request {
		window[k] = v
	}
	sum := sha256.Sum256(mustJSON(window))
	request["operation_id"] = "model-" + hex.EncodeToString(sum[:])
	path := "/api/fleet/apps/dependency-http-grants"
	if peer != "" {
		request["peer_id"] = peer
		path = "/api/fleet/apps/dependency-direct-grants"
	}
	var grant map[string]any
	if err := m.Hub.Request(ctx, fleet, secret, "POST", path, request, &grant); err != nil {
		return nil, err
	}
	bad := &StatusError{Status: 502}
	wantConsumer, wantProvider := consumer.public(fleet), provider.public(fleet)
	key := "provider"
	if peer != "" {
		key = "binding"
	}
	now := m.now().Unix()
	expires := num(grant["expires"])
	token, id := str(grant["access_token"]), str(grant["grant_id"])
	if !reflect.DeepEqual(roundTrip(grant["consumer"]), roundTrip(wantConsumer)) || !reflect.DeepEqual(roundTrip(grant[key]), roundTrip(wantProvider)) ||
		expires != float64(int64(expires)) || !(float64(now+30) < expires && expires <= float64(now+300)) ||
		!digestRE.MatchString(token) || !digestRE.MatchString(id) || token == id {
		return nil, bad
	}
	if peer != "" {
		nodePeer := str(grant["peer_id"])
		addresses := orList(grant["addresses"])
		if grant["transport"] != "fleet_direct" || !peerRE.MatchString(nodePeer) || len(addresses) < 1 || len(addresses) > 32 {
			return nil, bad
		}
		for _, a := range addresses {
			s := str(a)
			if len(s) > 1024 || !strings.HasPrefix(s, "/") || strings.Contains(s, "/p2p-circuit") || !strings.HasSuffix(s, "/p2p/"+nodePeer) {
				return nil, bad
			}
		}
		out := map[string]any{}
		for _, k := range []string{"binding", "peer_id", "addresses", "access_token", "expires", "transport"} {
			out[k] = grant[k]
		}
		return out, nil
	}
	origin, err := url.Parse(str(grant["origin"]))
	prefix := sha256.Sum256([]byte(fmt.Sprintf("%s:backend:http:%d", provider.InstanceID, provider.Generation)))
	if err != nil || origin.Scheme != "https" || !strings.HasPrefix(origin.Hostname(), hex.EncodeToString(prefix[:])[:32]+".") ||
		origin.Port() != "" || origin.User != nil || origin.Path != "" || origin.RawQuery != "" || origin.Fragment != "" ||
		strings.ContainsAny(str(grant["origin"]), "?#") {
		return nil, bad
	}
	return map[string]any{"origin": grant["origin"], "access_token": token, "expires": grant["expires"]}, nil
}

func mustJSON(v any) []byte {
	raw, _ := json.Marshal(v)
	return raw
}
