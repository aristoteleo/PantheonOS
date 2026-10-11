package hosted

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/deployments"
)

type fakeSecrets struct{ endpoint string }

func (s fakeSecrets) Get(fleet, name string) (string, deployments.SecretInfo, error) {
	if name != "owner-hub" {
		return "", deployments.SecretInfo{}, fmt.Errorf("no secret")
	}
	return "pbk_owner", deployments.SecretInfo{Name: name, Endpoint: s.endpoint}, nil
}

func TestModelAccessFiltersByPolicyAndIssuesConnectorGrants(t *testing.T) {
	binding := map[string]any{"node_id": "brain", "instance_id": "i-conn", "revision": revShell, "generation": 2.0, "component": "backend", "port": "http"}
	other := map[string]any{"node_id": "mac", "instance_id": "i-x", "revision": revShell, "generation": 1.0, "component": "backend", "port": "http"}
	rows := []map[string]any{
		{"deployment_id": "platform", "state": "ready", "revision": 3.0, "binding": binding, "models": []any{"m1"}},
		{"deployment_id": "private", "state": "ready", "revision": 1.0, "binding": other},
	}
	var calls []string
	var grantRequest map[string]any
	hub := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		if r.Header.Get("Authorization") != "Bearer pbk_owner" {
			http.Error(w, "no", 401)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/model-services":
			_ = json.NewEncoder(w).Encode(map[string]any{"deployments": rows})
		case "/api/model-services/routes":
			_ = json.NewEncoder(w).Encode(map[string]any{"routes": []any{
				map[string]any{"route_id": "tier-high", "revision": 2.0, "candidates": []any{map[string]any{"deployment_id": "platform"}}},
				map[string]any{"route_id": "mixed", "revision": 1.0, "candidates": []any{map[string]any{"deployment_id": "private"}}},
			}})
		case "/api/fleet/apps/dependency-http-grants":
			_ = json.NewDecoder(r.Body).Decode(&grantRequest)
			grantRequest["consumer"].(map[string]any)["fleet_id"] = fleetID
			grantRequest["provider"].(map[string]any)["fleet_id"] = fleetID
			sum := sha256.Sum256([]byte("i-conn:backend:http:2"))
			_ = json.NewEncoder(w).Encode(map[string]any{"origin": "https://" + hex.EncodeToString(sum[:])[:32] + ".apps.test",
				"access_token": strings.Repeat("a", 64), "grant_id": strings.Repeat("b", 64), "expires": time.Now().Unix() + 300,
				"consumer": grantRequest["consumer"].(map[string]any), "provider": grantRequest["provider"].(map[string]any)})
		default:
			http.Error(w, `{"detail":"unexpected"}`, 404)
		}
	}))
	defer hub.Close()
	policies := `{"values": {"model_services": {"protocol": 1, "policies": {
	  "agent": {"consumer": {"$app": "agent"}, "deployments": {"platform": "current"}, "routes": {"tier-high": "current"}, "allow_wake": false},
	  "files": {"consumer": {"$app": "files"}, "deployments": {}, "routes": {}, "allow_wake": false}}}},
	 "credentials": {"hub": {"$secret": "owner-hub"}}}`
	store := storeWith(t, map[string]deployments.AppSpec{
		"model-access": {Hosted: "model-services-control", Scope: "model-access", Intent: deployments.Running, Config: map[string]json.RawMessage{"backend": json.RawMessage(policies)}},
		"agent":        {Package: "agent", Scope: "agent", Intent: deployments.Running},
		"files":        {Package: "files", Scope: "files", Intent: deployments.Running},
	})
	d, _ := store.Get(fleetID, "general-team")
	_ = store.SetStatus(fleetID, "general-team", d.Revision, deployments.Status{Apps: map[string]deployments.AppStatus{
		"agent": {NodeID: "brain", InstanceID: "i-agent", Revision: revAgent, Generation: 5, State: "ready"}}})
	// Feed any extra fields the fake echoes back to the consumer checks.
	m := &ModelAccess{Store: store, Hub: &Hub{Secrets: fakeSecrets{hub.URL}, HTTP: hub.Client()}}
	h := NewHost(store)
	h.Register("model-services-control", m)
	b, _ := Binding(fleetID, "general-team", "model-access", "model-services-control")
	control := func(policy, op string, args any) map[string]any {
		t.Helper()
		status, out := rpc(t, h, b, "model_services_control", map[string]any{"policy_id": policy, "operation": op, "arguments": args})
		if status != 200 {
			t.Fatalf("%s %s: %d %v", policy, op, status, out)
		}
		return out["result"].(map[string]any)
	}
	got := control("agent", "deployments", map[string]any{})
	if got["status"] != 200.0 || len(got["result"].(map[string]any)["deployments"].([]any)) != 1 {
		t.Fatalf("deployments: %v", got)
	}
	routes := control("agent", "routes", map[string]any{})["result"].(map[string]any)["routes"].([]any)
	if len(routes) != 1 || routes[0].(map[string]any)["route_id"] != "tier-high" {
		t.Fatalf("routes: %v", routes)
	}
	conn := control("agent", "connect", map[string]any{"binding": binding})
	result := conn["result"].(map[string]any)
	if conn["status"] != 200.0 || result["access_token"] != strings.Repeat("a", 64) || len(result) != 3 {
		t.Fatalf("connect: %v", conn)
	}
	if grantRequest["app_id"] != "model-service" || !strings.HasPrefix(grantRequest["operation_id"].(string), "model-") ||
		grantRequest["consumer"].(map[string]any)["instance_id"] != "i-agent" {
		t.Fatalf("grant request: %v", grantRequest)
	}
	if denied := control("agent", "connect", map[string]any{"binding": other}); denied["status"] != 403.0 {
		t.Fatalf("an unauthorized connector was connected: %v", denied)
	}
	if denied := control("agent", "engine_idle", map[string]any{"deployment_id": "platform", "action": "wake", "revision": 3}); denied["status"] != 403.0 {
		t.Fatal("waking needs allow_wake")
	}
	if denied := control("agent", "deployments", map[string]any{"extra": 1}); denied["status"] != 403.0 {
		t.Fatal("inexact arguments accepted")
	}
	before := len(calls)
	if empty := control("files", "deployments", map[string]any{}); empty["result"].(map[string]any)["deployments"] == nil || len(calls) != before {
		t.Fatalf("an empty policy needs no Hub: %v", empty)
	}
	if status, _ := rpc(t, h, b, "model_services_control", map[string]any{"policy_id": "nobody", "operation": "deployments", "arguments": map[string]any{}}); status != 400 {
		t.Fatal("unknown policy accepted")
	}
	_ = context.Background
}
