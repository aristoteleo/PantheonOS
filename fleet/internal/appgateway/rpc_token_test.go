package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/gorilla/websocket"
)

func TestAuthorizedRPCCallsCarryTheInstanceCredential(t *testing.T) {
	const serviceToken = "controller-service-token-is-not-an-app-token"
	b := Binding{Fleet: "alice", Node: "node1", Instance: "instance1", Revision: strings.Repeat("a", 64), Generation: 3, Component: "backend", Port: "http"}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(r.Method + " " + r.URL.Path + " token=" + r.Header.Get("X-Fleet-RPC-Token")))
	}))
	defer upstream.Close()
	var server *httptest.Server
	g, err := New("apps.test", serviceToken, []string{"https://atrium.test"},
		func(ctx context.Context, got Binding, id, secret string) error {
			ws, _, err := websocket.DefaultDialer.DialContext(ctx, "ws"+strings.TrimPrefix(server.URL, "http")+"/apps/tunnel/"+id, http.Header{"Authorization": {"Bearer " + secret}})
			if err != nil {
				return err
			}
			conn, err := net.Dial("tcp", strings.TrimPrefix(upstream.URL, "http://"))
			if err != nil {
				return err
			}
			go apptransport.Relay(apptransport.New(ws), conn)
			return nil
		}, func(context.Context, Binding) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	lookups := 0
	g.SetRPCTokenDispatch(func(_ context.Context, got Binding) (string, error) {
		lookups++
		if got != b {
			t.Fatalf("token for another instance: %+v", got)
		}
		return "runner-issued-rpc-token", nil
	})
	mux := http.NewServeMux()
	g.Register(mux)
	server = httptest.NewServer(g.Handler(mux))
	defer server.Close()
	do := func(method, path, host string, headers http.Header) string {
		t.Helper()
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader([]byte(`{"method":"x","args":{}}`)))
		req.Host, req.Header = host, headers
		res, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		body, _ := io.ReadAll(res.Body)
		return string(body)
	}
	body, _ := json.Marshal(AttachRequest{Binding: b, Credential: "hub-signed-instance-credential-not-login", Expires: time.Now().Add(time.Hour).Unix(), Workload: true})
	var grant struct {
		AccessToken string `json:"access_token"`
	}
	req, _ := http.NewRequest("POST", server.URL+"/apps/connect", bytes.NewReader(body))
	req.Host, req.Header = "controller.test", http.Header{"Authorization": {"Bearer " + serviceToken}}
	res, err := server.Client().Do(req)
	if err != nil || json.NewDecoder(res.Body).Decode(&grant) != nil || grant.AccessToken == "" {
		t.Fatal("no grant", err)
	}
	host := Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test")
	auth := http.Header{"Authorization": {"Bearer " + grant.AccessToken}, "X-Fleet-RPC-Token": {"forged"}}
	if got := do("POST", "/rpc", host, auth); got != "POST /rpc token=runner-issued-rpc-token" {
		t.Fatalf("authorized /rpc: %q", got)
	}
	do("POST", "/rpc", host, auth)
	if lookups != 1 {
		t.Fatalf("one lookup per generation, got %d", lookups)
	}
	if got := do("GET", "/index.html", host, auth); got != "GET /index.html token=" {
		t.Fatalf("other paths never carry it (and a forged one is removed): %q", got)
	}
}
