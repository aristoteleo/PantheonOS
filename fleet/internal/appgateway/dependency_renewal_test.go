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

func TestDependencyRenewalPreservesScopeAndCannotResurrect(t *testing.T) {
	const control = "controller-owner-service-secret"
	consumer := apptransport.InstanceIdentity{Fleet: "owner", Node: "consumer", Instance: "consumer", Revision: strings.Repeat("a", 64), Generation: 2}
	provider := Binding{Fleet: "owner", Node: "provider", Instance: "provider", Revision: strings.Repeat("b", 64), Generation: 3, Component: "backend", Port: "http"}
	var missingConsumer, missingProvider, blockNextCheck atomic.Bool
	entered, release := make(chan struct{}, 1), make(chan struct{}, 1)
	var invoked atomic.Int32
	g, err := New("apps.test", control, []string{"https://atrium.test"}, func(context.Context, Binding, string, string) error {
		return fmt.Errorf("must not tunnel")
	}, func(_ context.Context, got Binding) error {
		if got != provider || missingProvider.Load() {
			return fmt.Errorf("provider unavailable")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	g.SetDependencyDispatch(func(ctx context.Context, got apptransport.InstanceIdentity, prep string) error {
		if got != consumer || prep != "" || missingConsumer.Load() {
			return fmt.Errorf("consumer unavailable")
		}
		if blockNextCheck.CompareAndSwap(true, false) {
			entered <- struct{}{}
			select {
			case <-release:
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		return nil
	}, func(_ context.Context, got Binding, app string, raw json.RawMessage, timeout int) (json.RawMessage, error) {
		if got != provider || app != "files" || timeout != 5 {
			return nil, fmt.Errorf("scope changed")
		}
		var request struct {
			Method string         `json:"method"`
			Args   map[string]any `json:"args"`
		}
		if json.Unmarshal(raw, &request) != nil || request.Method != "read" || len(request.Args) != 2 || request.Args["workspace"] != "a" || request.Args["path"] != "one" {
			return nil, fmt.Errorf("arguments changed")
		}
		invoked.Add(1)
		return json.RawMessage(`{"success":true,"result":"ok"}`), nil
	})
	mux := http.NewServeMux()
	g.Register(mux)
	server := httptest.NewServer(g.Handler(mux))
	defer server.Close()
	type reply struct {
		code int
		raw  []byte
	}
	do := func(method, host, path, token string, value any) reply {
		body, _ := json.Marshal(value)
		request, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(body))
		request.Host = host
		request.Header.Set("Authorization", "Bearer "+token)
		response, err := server.Client().Do(request)
		if err != nil {
			return reply{0, []byte(err.Error())}
		}
		defer response.Body.Close()
		raw, _ := io.ReadAll(response.Body)
		return reply{response.StatusCode, raw}
	}
	q := DependencyRequest{Consumer: consumer, Provider: provider, AppID: "files", Timeout: 5, Expires: time.Now().Add(time.Minute).Unix(),
		Methods: map[string]RPCMethod{"read": {Arguments: []string{"path"}, Bound: map[string]json.RawMessage{"workspace": json.RawMessage(`"a"`)}}}}
	issued := do("POST", "control.test", "/apps/dependencies", control, q)
	if issued.code != 200 {
		t.Fatal(issued.code, string(issued.raw))
	}
	var grant struct {
		ID    string `json:"grant_id"`
		Token string `json:"access_token"`
	}
	if json.Unmarshal(issued.raw, &grant) != nil || grant.ID == "" {
		t.Fatal("bad issue")
	}
	host := Host(provider.Instance, provider.Component, provider.Port, provider.Generation, "apps.test")
	renew := map[string]any{"fleet_id": "owner", "grant_id": grant.ID, "expires": time.Now().Add(5 * time.Minute).Unix()}
	call := map[string]any{"method": "read", "args": map[string]any{"path": "one"}}
	if result := do("PATCH", "control.test", "/apps/dependencies", grant.Token, renew); result.code != 401 {
		t.Fatal(result)
	}
	if result := do("PATCH", host, "/apps/dependencies", grant.Token, renew); result.code != 403 {
		t.Fatal(result)
	}
	for key, value := range map[string]any{"methods": q.Methods, "provider": provider, "consumer": consumer, "preparation_id": "old", "timeout_seconds": 600} {
		bad := map[string]any{}
		for k, v := range renew {
			bad[k] = v
		}
		bad[key] = value
		if result := do("PATCH", "control.test", "/apps/dependencies", control, bad); result.code != 400 {
			t.Fatal(key, result)
		}
	}
	for _, failure := range []*atomic.Bool{&missingConsumer, &missingProvider} {
		failure.Store(true)
		if result := do("PATCH", "control.test", "/apps/dependencies", control, renew); result.code != 409 {
			t.Fatal(result)
		}
		failure.Store(false)
		if g.dependencies[grant.Token].Expires != q.Expires {
			t.Fatal("failed renewal extended grant")
		}
	}
	// A renewal during the admission check does not cancel an otherwise valid
	// call: only revocation/expiry or changed instance state closes admission.
	blockNextCheck.Store(true)
	done := make(chan reply, 1)
	go func() { done <- do("POST", host, "/rpc", grant.Token, call) }()
	<-entered
	result := do("PATCH", "control.test", "/apps/dependencies", control, renew)
	if result.code != 200 || strings.Contains(string(result.raw), grant.Token) || strings.Contains(string(result.raw), "access_token") {
		t.Fatal(result)
	}
	var receipt map[string]any
	if json.Unmarshal(result.raw, &receipt) != nil || len(receipt) != 4 || receipt["grant_id"] != grant.ID {
		t.Fatal(string(result.raw))
	}
	release <- struct{}{}
	if result = <-done; result.code != 200 || invoked.Load() != 1 {
		t.Fatal(result)
	}
	// Retrying an older absolute expiry never shortens the current grant.
	renew["expires"] = q.Expires
	if result = do("PATCH", "control.test", "/apps/dependencies", control, renew); result.code != 200 {
		t.Fatal(result)
	}
	if g.dependencies[grant.Token].Expires <= q.Expires {
		t.Fatal("grant was shortened")
	}
	renew["fleet_id"] = "other"
	if result = do("PATCH", "control.test", "/apps/dependencies", control, renew); result.code != 410 {
		t.Fatal(result)
	}
	renew["fleet_id"] = "owner"
	// Revocation wins even if a renewal already passed its first lookup.
	blockNextCheck.Store(true)
	go func() { done <- do("PATCH", "control.test", "/apps/dependencies", control, renew) }()
	<-entered
	if result = do("DELETE", "control.test", "/apps/dependencies", control, map[string]string{"fleet_id": "owner", "grant_id": grant.ID}); result.code != 204 {
		t.Fatal(result)
	}
	release <- struct{}{}
	if result = <-done; result.code != 410 {
		t.Fatal(result)
	}
	if result = do("POST", host, "/rpc", grant.Token, call); result.code != 401 {
		t.Fatal(result)
	}
	if result = do("PATCH", "control.test", "/apps/dependencies", control, renew); result.code != 410 {
		t.Fatal("revocation resurrected", result)
	}
	// Expiry is terminal too, even though the caller still has owner authority.
	issued = do("POST", "control.test", "/apps/dependencies", control, q)
	if issued.code != 200 || json.Unmarshal(issued.raw, &grant) != nil {
		t.Fatal(issued)
	}
	g.mu.Lock()
	expired := *g.dependencies[grant.Token]
	expired.Expires = time.Now().Add(-time.Second).Unix()
	g.dependencies[grant.Token] = &expired
	g.mu.Unlock()
	renew["grant_id"] = grant.ID
	if result = do("PATCH", "control.test", "/apps/dependencies", control, renew); result.code != 410 {
		t.Fatal("expiry resurrected", result)
	}
}
