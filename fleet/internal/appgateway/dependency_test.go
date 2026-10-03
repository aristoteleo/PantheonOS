package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

func TestDependencyGrantAdmissionRevocationAndIsolation(t *testing.T) {
	const serviceToken = "controller-key-only-owner-can-issue"
	provider := Binding{Fleet: "owner", Node: "provider-node", Instance: "provider", Revision: strings.Repeat("b", 64), Generation: 3, Component: "backend", Port: "http"}
	consumer := apptransport.InstanceIdentity{Fleet: "owner", Node: "consumer-node", Instance: "consumer", Revision: strings.Repeat("a", 64), Generation: 2}
	var running atomic.Bool
	var calls atomic.Int32
	started, release := make(chan struct{}, 1), make(chan struct{})
	var slow atomic.Bool
	g, err := New("apps.test", serviceToken, []string{"https://atrium.test"}, func(context.Context, Binding, string, string) error {
		t.Error("dependency escaped into arbitrary HTTP tunnel")
		return fmt.Errorf("forbidden")
	}, func(_ context.Context, b Binding) error {
		if b != provider {
			return fmt.Errorf("wrong binding")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	g.SetDependencyDispatch(func(_ context.Context, got apptransport.InstanceIdentity, preparation string) error {
		if got != consumer || (!running.Load() && preparation != "prepare-2") {
			return fmt.Errorf("not ready")
		}
		return nil
	}, func(_ context.Context, b Binding, app string, payload json.RawMessage, timeout int) (json.RawMessage, error) {
		calls.Add(1)
		if b != provider || app != "files" || timeout != 5 {
			t.Error("binding changed")
		}
		var q struct {
			Method  string         `json:"method"`
			Args    map[string]any `json:"args"`
			Timeout int            `json:"timeout_s"`
		}
		if json.Unmarshal(payload, &q) != nil || q.Method != "read_file" || q.Args["workspace_id"] != "workspace-a" || q.Args["path"] != "data.csv" || len(q.Args) != 2 || q.Timeout != 5 {
			t.Error("scope was not injected", string(payload))
		}
		if slow.Load() {
			started <- struct{}{}
			<-release
		}
		return json.RawMessage(`{"success":true,"result":"file contents"}`), nil
	})
	mux := http.NewServeMux()
	g.Register(mux)
	server := httptest.NewServer(g.Handler(mux))
	defer server.Close()
	do := func(method, path, host string, body []byte, headers http.Header) (int, []byte) {
		t.Helper()
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(body))
		req.Host = host
		req.Header = headers
		res, e := server.Client().Do(req)
		if e != nil {
			t.Fatal(e)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(res.Body)
		return res.StatusCode, raw
	}
	q := DependencyRequest{Consumer: consumer, Provider: provider, AppID: "files", Preparation: "prepare-2", Expires: time.Now().Add(time.Minute).Unix(), Timeout: 5, Methods: map[string]RPCMethod{"read_file": {Arguments: []string{"path"}, Bound: map[string]json.RawMessage{"workspace_id": json.RawMessage(`"workspace-a"`)}}}}
	body, _ := json.Marshal(q)
	owner := http.Header{"Authorization": {"Bearer " + serviceToken}}
	if code, _ := do("POST", "/apps/dependencies", "controller.test", body, nil); code != 401 {
		t.Fatal(code)
	}
	code, raw := do("POST", "/apps/dependencies", "controller.test", body, owner)
	if code != 200 {
		t.Fatal(code, string(raw))
	}
	var grant struct {
		ID       string `json:"grant_id"`
		Token    string `json:"access_token"`
		Endpoint string `json:"endpoint"`
	}
	if json.Unmarshal(raw, &grant) != nil || grant.Token == "" || grant.ID == grant.Token || strings.Contains(string(raw), serviceToken) {
		t.Fatal("bad grant")
	}
	host := Host(provider.Instance, provider.Component, provider.Port, provider.Generation, "apps.test")
	auth := http.Header{"Authorization": {"Bearer " + grant.Token}}
	valid := []byte(`{"method":"read_file","args":{"path":"data.csv"}}`)
	if code, _ := do("POST", "/rpc", host, valid, auth); code != 409 {
		t.Fatal("prepared consumer invoked", code)
	}
	running.Store(true)
	if code, _ := do("POST", "/rpc", host, valid, auth); code != 200 {
		t.Fatal(code)
	}
	for _, payload := range []string{
		`{"method":"delete_file","args":{}}`,
		`{"method":"read_file","args":{"path":"data.csv","workspace_id":"other"}}`,
		`{"method":"read_file","args":{"path":"data.csv","unexpected":true}}`,
		`{"Method":"read_file","args":{"path":"data.csv"}}`,
		`{"method":"read_file","method":"delete_file"}`,
		`{"method":"read_file","args":{"path":"data.csv","path":"secret"}}`,
		`{"method":"read_file","args":{"path":"data.csv"},"timeout_seconds":6}`,
		`{"method":"read_file","args":{"path":"data.csv"},"headers":{}}`,
		`{"method":"read_file"} {}`,
	} {
		if code, _ := do("POST", "/rpc", host, []byte(payload), auth); code != 403 {
			t.Fatal(code, payload)
		}
	}
	for _, path := range []string{"/", "/rpc?bypass=1", "/__fleet/status", "/__fleet/model-media", "/__fleet/connect", "/apps/dependencies", "/%72pc"} {
		if code, _ := do("POST", path, host, valid, auth); code != 403 {
			t.Fatal(code, path)
		}
	}
	for _, headers := range []http.Header{
		{"Authorization": {grant.Token}},
		{"Authorization": {"Bearer " + grant.ID}},
		{"Cookie": {"__Host-fleetapp=" + grant.Token}},
		{"Authorization": {"Bearer " + grant.Token}, "Origin": {"https://atrium.test"}},
		{"Authorization": {"Bearer " + grant.Token}, "Sec-Fetch-Site": {"same-origin"}},
		{"Authorization": {"Bearer " + grant.Token}, "Upgrade": {"websocket"}},
	} {
		if code, _ := do("POST", "/rpc", host, valid, headers); code != 401 && code != 403 {
			t.Fatal(code)
		}
	}
	if code, _ := do("POST", "/rpc", "other.apps.test", valid, auth); code != 401 {
		t.Fatal(code)
	}
	if calls.Load() != 1 {
		t.Fatal("forbidden request reached provider", calls.Load())
	}
	running.Store(false)
	if code, _ := do("POST", "/rpc", host, valid, auth); code != 409 {
		t.Fatal(code)
	}
	running.Store(true)
	// Revocation linearizes at admission: an accepted write finishes once,
	// later calls fail, and deleting from another fleet cannot revoke it.
	revoke := func(fleet string) {
		b, _ := json.Marshal(map[string]string{"fleet_id": fleet, "grant_id": grant.ID})
		if code, _ := do("DELETE", "/apps/dependencies", "controller.test", b, owner); code != 204 {
			t.Fatal(code)
		}
	}
	revoke("other-owner")
	slow.Store(true)
	done := make(chan int, 1)
	go func() { code, _ := do("POST", "/rpc", host, valid, auth); done <- code }()
	<-started
	revoke("owner")
	if code, _ := do("POST", "/rpc", host, valid, auth); code != 401 {
		t.Fatal(code)
	}
	close(release)
	if <-done != 200 {
		t.Fatal("accepted call lost")
	}
	if calls.Load() != 2 {
		t.Fatal("call replayed", calls.Load())
	}
	// A new Gateway has no surviving grants; possession alone is insufficient.
	g.mu.Lock()
	g.dependencies = map[string]*dependencyGrant{}
	g.mu.Unlock()
	if code, _ := do("POST", "/rpc", host, valid, auth); code != 401 {
		t.Fatal(code)
	}
}

func TestDependencyGrantValidationAndBoundedJSON(t *testing.T) {
	q := DependencyRequest{Consumer: apptransport.InstanceIdentity{Fleet: "owner", Node: "node", Instance: "consumer", Revision: strings.Repeat("a", 64), Generation: 1}, Provider: Binding{Fleet: "owner", Node: "node", Instance: "provider", Revision: strings.Repeat("b", 64), Generation: 1, Component: "backend", Port: "http"}, AppID: "files", Timeout: 5, Expires: time.Now().Add(time.Minute).Unix(), Methods: map[string]RPCMethod{"read_file": {Arguments: []string{"path"}}}}
	if !q.valid() {
		t.Fatal("invalid fixture")
	}
	for _, change := range []func(*DependencyRequest){
		func(q *DependencyRequest) { q.Consumer.Fleet = "other" }, func(q *DependencyRequest) { q.Expires = time.Now().Add(time.Hour).Unix() }, func(q *DependencyRequest) { q.Expires = 1 }, func(q *DependencyRequest) { q.Provider.Port = "admin" }, func(q *DependencyRequest) { q.Timeout = 601 }, func(q *DependencyRequest) { q.Methods = map[string]RPCMethod{"*": {}} }, func(q *DependencyRequest) {
			q.Methods = map[string]RPCMethod{"read_file": {Arguments: []string{"workspace"}, Bound: map[string]json.RawMessage{"workspace": json.RawMessage(`"a"`)}}}
		},
	} {
		copy := q
		change(&copy)
		if copy.valid() {
			t.Fatal("invalid grant accepted")
		}
	}
	for _, raw := range []string{`{"a":1,"a":2}`, `{"a":{"b":1,"b":2}}`, `[] []`, strings.Repeat("[", 70) + "0" + strings.Repeat("]", 70)} {
		if uniqueJSON([]byte(raw)) == nil {
			t.Fatal("ambiguous JSON accepted", raw)
		}
	}
}

func TestDependencyRevokedDuringConsumerCheckCannotBeAdmitted(t *testing.T) {
	provider := Binding{Fleet: "owner", Node: "node", Instance: "provider", Revision: strings.Repeat("a", 64), Generation: 1, Component: "backend", Port: "http"}
	checking, release := make(chan struct{}), make(chan struct{})
	g, err := New("apps.test", "controller-secret-long-enough", []string{"https://atrium.test"}, func(context.Context, Binding, string, string) error { return nil }, func(context.Context, Binding) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	g.SetDependencyDispatch(func(context.Context, apptransport.InstanceIdentity, string) error {
		close(checking)
		<-release
		return nil
	}, func(context.Context, Binding, string, json.RawMessage, int) (json.RawMessage, error) {
		t.Error("revoked grant reached provider")
		return nil, nil
	})
	key := nonce()
	g.dependencies[key] = &dependencyGrant{DependencyRequest: DependencyRequest{Provider: provider, Timeout: 5, Expires: time.Now().Add(time.Minute).Unix(), Methods: map[string]RPCMethod{"read_file": {}}}}
	req := httptest.NewRequest("POST", "https://"+Host(provider.Instance, provider.Component, provider.Port, 1, "apps.test")+"/rpc", strings.NewReader(`{"method":"read_file"}`))
	req.Header.Set("Authorization", "Bearer "+key)
	response := httptest.NewRecorder()
	done := make(chan struct{})
	go func() { g.serveDependency(response, req); close(done) }()
	<-checking
	g.mu.Lock()
	delete(g.dependencies, key)
	g.mu.Unlock()
	close(release)
	<-done
	if response.Code != 401 {
		t.Fatal(response.Code)
	}
}
