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

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
)

func TestLocalRPCOwnerAuthorityAndRevocation(t *testing.T) {
	server := httptest.NewUnstartedServer(nil)
	defer server.Close()
	origin := "https://" + server.Listener.Addr().String()
	consumer := apptransport.InstanceIdentity{Fleet: "owner", Node: "node", Instance: "consumer", Revision: strings.Repeat("a", 64), Generation: 2}
	provider := Binding{Fleet: "owner", Node: "node", Instance: "provider", Revision: strings.Repeat("b", 64), Generation: 3, Component: "backend", Port: "http"}
	var live atomic.Bool
	var calls atomic.Int32
	g, err := NewLocalRPC(origin, strings.Repeat("s", 32), func(context.Context, Binding, string, string) error {
		t.Error("RPC escaped to HTTP tunnel")
		return fmt.Errorf("denied")
	}, func(_ context.Context, b Binding) error {
		if b != provider {
			return fmt.Errorf("wrong provider")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	g.SetDependencyDispatch(func(_ context.Context, c apptransport.InstanceIdentity, prep string) error {
		if c != consumer || (!live.Load() && prep != "prepared") {
			return fmt.Errorf("consumer unavailable")
		}
		return nil
	}, func(_ context.Context, b Binding, app string, raw json.RawMessage, timeout int) (json.RawMessage, error) {
		var q struct {
			Method string         `json:"method"`
			Args   map[string]any `json:"args"`
		}
		if json.Unmarshal(raw, &q) != nil || b != provider || app != "shell" || timeout != 60 || q.Method != "run_command" || q.Args["command"] != "printf OK" || q.Args["shell_id"] != "owned-session" || len(q.Args) != 2 {
			return nil, fmt.Errorf("scope changed")
		}
		calls.Add(1)
		return json.RawMessage(`{"success":true,"result":"OK"}`), nil
	})
	if err = g.OpenDependencyStore(t.TempDir() + "/grants"); err != nil {
		t.Fatal(err)
	}
	defer g.CloseDependencyStore()
	mux := http.NewServeMux()
	g.Register(mux)
	if err = g.RegisterLocalAuthority(mux, func(key string) (string, bool) {
		switch key {
		case "owner-key":
			return "owner", true
		case "other-key":
			return "other", true
		}
		return "", false
	}); err != nil {
		t.Fatal(err)
	}
	server.Config.Handler = g.Handler(mux)
	server.StartTLS()
	request := func(method, path, key string, value any, headers http.Header) (int, []byte) {
		t.Helper()
		raw, _ := json.Marshal(value)
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(raw))
		req.Header = headers.Clone()
		if req.Header == nil {
			req.Header = make(http.Header)
		}
		req.Header.Set("Authorization", "Bearer "+key)
		response, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		body, _ := io.ReadAll(response.Body)
		return response.StatusCode, body
	}
	const path = "/api/fleet/apps/dependency-grants"
	c, p := consumer, provider
	c.Fleet = ""
	p.Fleet = ""
	q := map[string]any{"operation_id": "local-issue", "consumer": c, "provider": p, "app_id": "shell", "preparation_id": "prepared", "methods": map[string]RPCMethod{"run_command": {Arguments: []string{"command"}, Bound: map[string]json.RawMessage{"shell_id": json.RawMessage(`"owned-session"`)}}}}
	if code, _ := request("POST", path, "invalid", q, nil); code != 401 {
		t.Fatal(code)
	}
	for _, headers := range []http.Header{{"Origin": {"https://atrium.test"}}, {"Sec-Fetch-Site": {"same-origin"}}} {
		if code, _ := request("POST", path, "owner-key", q, headers); code != 403 {
			t.Fatal(code)
		}
	}
	for _, v := range []any{nil, true, 1.5, "300", 0, 901} {
		q["ttl_seconds"] = v
		if code, _ := request("POST", path, "owner-key", q, nil); code != 400 {
			t.Fatal("invalid ttl", v, code)
		}
	}
	delete(q, "ttl_seconds")
	code, raw := request("POST", path, "owner-key", q, nil)
	if code != 200 {
		t.Fatal(code, string(raw))
	}
	var grant struct {
		ID       string                        `json:"grant_id"`
		Token    string                        `json:"access_token"`
		Endpoint string                        `json:"endpoint"`
		Consumer apptransport.InstanceIdentity `json:"consumer"`
		Provider Binding                       `json:"provider"`
	}
	if json.Unmarshal(raw, &grant) != nil || grant.Endpoint != origin+"/rpc" || grant.Consumer != consumer || grant.Provider != provider || grant.Token == "" {
		t.Fatal(string(raw))
	}
	_, replay := request("POST", path, "owner-key", q, nil)
	if !bytes.Contains(replay, []byte(grant.Token)) {
		t.Fatal("lost idempotent grant")
	}
	call := map[string]any{"method": "run_command", "args": map[string]any{"command": "printf OK"}}
	if code, _ := request("POST", "/rpc", grant.Token, call, nil); code != 409 {
		t.Fatal("prepared consumer invoked", code)
	}
	live.Store(true)
	if code, raw := request("POST", "/rpc", grant.Token, call, nil); code != 200 {
		t.Fatal(code, string(raw))
	}
	call["args"].(map[string]any)["shell_id"] = "foreign"
	if code, _ := request("POST", "/rpc", grant.Token, call, nil); code != 403 {
		t.Fatal("scope overridden", code)
	}
	delete(call["args"].(map[string]any), "shell_id")
	if code, _ := request("PATCH", path+"/"+grant.ID, grant.Token, map[string]any{}, nil); code != 403 {
		t.Fatal("consumer became owner", code)
	}
	if code, _ := request("PATCH", path+"/"+grant.ID, "other-key", map[string]any{}, nil); code != 410 {
		t.Fatal("foreign renewal", code)
	}
	if code, raw := request("PATCH", path+"/"+grant.ID, "owner-key", map[string]any{}, nil); code != 200 || bytes.Contains(raw, []byte(grant.Token)) {
		t.Fatal(code, string(raw))
	}
	if code, _ := request("DELETE", path+"/"+grant.ID, "owner-key", nil, nil); code != 204 {
		t.Fatal(code)
	}
	if code, _ := request("POST", "/rpc", grant.Token, call, nil); code != 401 {
		t.Fatal("revoked grant used", code)
	}
	if code, _ := request("POST", path, "owner-key", q, nil); code != 410 {
		t.Fatal("revoked attempt resurrected", code)
	}
	if calls.Load() != 1 {
		t.Fatal("forbidden call reached provider", calls.Load())
	}
	for _, p := range []string{"/__fleet/connect", "/__fleet/tunnel", "/apps/bind"} {
		if code, _ := request("POST", p, "owner-key", nil, nil); code != 404 {
			t.Fatal("browser route exposed", p, code)
		}
	}
}
