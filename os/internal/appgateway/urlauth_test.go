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

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/gorilla/websocket"
)

func TestURLAuthGrantForwardsCookielessReadsWithoutTheOwnersCredential(t *testing.T) {
	const serviceToken = "controller-service-token-is-not-an-app-token"
	b := Binding{Fleet: "alice", Node: "node1", Instance: "instance1", Revision: strings.Repeat("a", 64), Generation: 1, Component: "backend", Port: "data"}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "https://evil.test") // replaced by the gateway
		_, _ = w.Write([]byte("credential=" + r.Header.Get("X-Pantheon-App-Token")))
	}))
	defer upstream.Close()
	var server *httptest.Server
	g, err := New("apps.test", serviceToken, []string{"https://atrium.test"},
		func(ctx context.Context, _ Binding, id, secret string) error {
			ws, _, err := websocket.DefaultDialer.DialContext(ctx, "ws"+strings.TrimPrefix(server.URL, "http")+"/apps/tunnel/"+id, http.Header{"Authorization": {"Bearer " + secret}})
			if err != nil {
				return err
			}
			conn, err := net.Dial("tcp", strings.TrimPrefix(upstream.URL, "http://"))
			if err != nil {
				ws.Close()
				return err
			}
			go apptransport.Relay(apptransport.New(ws), conn)
			return nil
		}, func(context.Context, Binding) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	g.Register(mux)
	server = httptest.NewServer(g.Handler(mux))
	defer server.Close()
	do := func(method, path, host string, body []byte, headers http.Header) (*http.Response, string) {
		t.Helper()
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(body))
		req.Host = host
		req.Header = headers
		res, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		raw, _ := io.ReadAll(res.Body)
		return res, string(raw)
	}
	connect := func(binding Binding, urlAuth bool) {
		t.Helper()
		body, _ := json.Marshal(AttachRequest{Binding: binding, Credential: strings.Repeat("c", 40), Expires: time.Now().Add(time.Hour).Unix(), UIOrigin: "https://atrium.test", URLAuth: urlAuth})
		res, raw := do("POST", "/apps/connect", "controller.test", body, http.Header{"Authorization": {"Bearer " + serviceToken}})
		var grant struct{ Ticket string }
		if res.StatusCode != 200 || json.Unmarshal([]byte(raw), &grant) != nil {
			t.Fatal("attach", res.Status, raw)
		}
		ticket, _ := json.Marshal(map[string]string{"ticket": grant.Ticket})
		host := Host(binding.Instance, binding.Component, binding.Port, binding.Generation, "apps.test")
		if res, _ := do("POST", "/__fleet/connect", host, ticket, http.Header{"Origin": {"https://atrium.test"}}); res.StatusCode != 204 {
			t.Fatal("connect", res.Status)
		}
	}
	host := Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test")
	sandboxed := http.Header{"Origin": {"null"}}
	if res, _ := do("GET", "/d/token/main.js", host, nil, sandboxed); res.StatusCode != 401 {
		t.Fatal("cookieless read before any grant", res.Status)
	}
	connect(b, true)
	res, raw := do("GET", "/d/token/main.js", host, nil, sandboxed)
	if res.StatusCode != 200 || raw != "credential=" || res.Header.Get("Access-Control-Allow-Origin") != "*" || res.Header.Get("Access-Control-Allow-Credentials") != "" {
		t.Fatal("URL-capability read", res.Status, raw, res.Header)
	}
	if res, _ := do("POST", "/d/token/main.js", host, nil, sandboxed); res.StatusCode != 401 {
		t.Fatal("cookieless write", res.Status)
	}
	// An ordinary grant never opens its port to cookieless requests.
	other := b
	other.Port = "http"
	connect(other, false)
	if res, _ := do("GET", "/", Host(other.Instance, other.Component, other.Port, other.Generation, "apps.test"), nil, sandboxed); res.StatusCode != 401 {
		t.Fatal("ordinary grant opened", res.Status)
	}
	// Workloads cannot ask for URL authentication.
	body, _ := json.Marshal(AttachRequest{Binding: b, Credential: strings.Repeat("c", 40), Expires: time.Now().Add(time.Hour).Unix(), Workload: true, URLAuth: true})
	if res, _ := do("POST", "/apps/connect", "controller.test", body, http.Header{"Authorization": {"Bearer " + serviceToken}}); res.StatusCode != 400 {
		t.Fatal("workload URL auth", res.Status)
	}
}
