package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/gorilla/websocket"
)

func TestHTTPDependencyStreamingScopeAndLifetime(t *testing.T) {
	for _, stop := range []string{"revoke", "consumer", "provider", "expiry", "disconnect", "journal"} {
		t.Run(stop, func(t *testing.T) {
			const owner = "controller-owner-credential-for-test"
			provider := Binding{Fleet: "owner", Node: "provider", Instance: "model", Revision: strings.Repeat("a", 64), Generation: 2, Component: "backend", Port: "http"}
			consumer := apptransport.InstanceIdentity{Fleet: "owner", Node: "consumer", Instance: "agent", Revision: strings.Repeat("b", 64), Generation: 3}
			var consumerGone, providerGone atomic.Bool
			var calls atomic.Int32
			closed := make(chan struct{})
			rescue := make(chan struct{})
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				io.Copy(io.Discard, r.Body)
				if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("X-Fleet-RPC-Token") != "" || r.Header.Get("X-Pantheon-App-Token") != strings.Repeat("signed", 8) || r.Header.Get("X-Model-Config") != "pinned" {
					t.Error("upstream headers escaped grant", r.Header)
				}
				w.Write([]byte("data: first\n\n"))
				w.(http.Flusher).Flush()
				select {
				case <-r.Context().Done():
					close(closed)
				case <-rescue:
				}
			}))
			defer upstream.Close()
			defer close(rescue)
			var server *httptest.Server
			g, err := New("apps.test", owner, []string{"https://atrium.test"}, func(ctx context.Context, b Binding, id, secret string) error {
				if b != provider {
					return fmt.Errorf("wrong provider")
				}
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
			}, func(_ context.Context, b Binding) error {
				if b != provider || providerGone.Load() {
					return fmt.Errorf("provider changed")
				}
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			g.SetDependencyDispatch(func(_ context.Context, c apptransport.InstanceIdentity, prep string) error {
				if c != consumer || consumerGone.Load() || prep != "" {
					return fmt.Errorf("consumer changed")
				}
				return nil
			}, func(context.Context, Binding, string, json.RawMessage, int) (json.RawMessage, error) {
				t.Error("HTTP data used RPC")
				return nil, fmt.Errorf("forbidden")
			})
			if err := g.OpenDependencyStore(t.TempDir() + "/grants"); err != nil {
				t.Fatal(err)
			}
			defer g.CloseDependencyStore()
			mux := http.NewServeMux()
			g.Register(mux)
			server = httptest.NewServer(g.Handler(mux))
			defer server.Close()
			expires := time.Now().Add(time.Minute).Unix()
			if stop == "expiry" {
				expires = time.Now().Add(3 * time.Second).Unix()
			}
			q := DependencyRequest{Consumer: consumer, Provider: provider, AppID: "model-service", Timeout: 60, Expires: expires,
				HTTP: &HTTPDependency{Rules: []HTTPRule{{Method: "POST", Path: "/v1/chat/completions"}}, Headers: map[string]string{"X-Model-Config": "pinned"}, Credential: strings.Repeat("signed", 8)}}
			body, _ := json.Marshal(q)
			do := func(method, target, host, token string, body []byte, extra http.Header) *http.Response {
				t.Helper()
				r, _ := http.NewRequest(method, server.URL+target, bytes.NewReader(body))
				r.Host = host
				r.Header = extra.Clone()
				if r.Header == nil {
					r.Header = http.Header{}
				}
				r.Header.Set("Authorization", "Bearer "+token)
				res, err := server.Client().Do(r)
				if err != nil {
					t.Fatal(err)
				}
				return res
			}
			issued := do("POST", "/apps/dependencies", "controller.test", owner, body, nil)
			var grant struct {
				Token  string `json:"access_token"`
				ID     string `json:"grant_id"`
				Origin string `json:"origin"`
			}
			if issued.StatusCode != 200 || json.NewDecoder(issued.Body).Decode(&grant) != nil {
				t.Fatal(issued.Status)
			}
			issued.Body.Close()
			host := Host(provider.Instance, provider.Component, provider.Port, provider.Generation, "apps.test")
			if grant.Origin != "https://"+host || grant.Token == grant.ID {
				t.Fatal("invalid grant")
			}
			for _, target := range []string{"/rpc", "/v1/models", "/__fleet/status", "/v1/chat/%63ompletions", "/v1/chat/completions/../admin"} {
				res := do("POST", target, host, grant.Token, []byte(`{}`), nil)
				res.Body.Close()
				if res.StatusCode != 403 {
					t.Fatal(target, res.Status)
				}
			}
			res := do("GET", "/v1/chat/completions", host, grant.Token, nil, nil)
			res.Body.Close()
			if res.StatusCode != 403 {
				t.Fatal("method escaped", res.Status)
			}
			res = do("POST", "/v1/chat/completions", host, grant.Token, []byte(`{}`), http.Header{"Origin": {"https://atrium.test"}})
			res.Body.Close()
			if res.StatusCode != 403 {
				t.Fatal("browser accepted")
			}
			stream := do("POST", "/v1/chat/completions", host, grant.Token, []byte(`{}`), http.Header{"X-Fleet-RPC-Token": {"forged"}, "Cookie": {"foreign=secret"}, "Connection": {"X-Model-Config"}, "X-Model-Config": {"forged"}})
			first := make([]byte, len("data: first\n\n"))
			if _, err := io.ReadFull(stream.Body, first); err != nil || string(first) != "data: first\n\n" {
				t.Fatal("stream buffered", err)
			}
			switch stop {
			case "revoke":
				body, _ := json.Marshal(map[string]string{"fleet_id": "owner", "grant_id": grant.ID})
				res := do("DELETE", "/apps/dependencies", "controller.test", owner, body, nil)
				res.Body.Close()
				if res.StatusCode != 204 {
					t.Fatal(res.Status)
				}
			case "consumer":
				consumerGone.Store(true)
			case "provider":
				providerGone.Store(true)
			case "disconnect":
				stream.Body.Close()
			case "journal":
				if err := g.CloseDependencyStore(); err != nil {
					t.Fatal(err)
				}
			}
			select {
			case <-closed:
			case <-time.After(8 * time.Second):
				t.Fatal("upstream survived lost authorization")
			}
			stream.Body.Close()
			if calls.Load() != 1 {
				t.Fatal("unauthorized call or inference replay", calls.Load())
			}
		})
	}
}

func TestHTTPDependencyPathBoundaries(t *testing.T) {
	p := HTTPDependency{Credential: strings.Repeat("x", 32), Rules: []HTTPRule{{Method: "GET", Path: "/artifacts", Prefix: true}}}
	if !p.valid() {
		t.Fatal("valid rule rejected")
	}
	for _, target := range []string{"/artifactsevil", "/artifacts/../rpc", "/artifacts/%2e%2e/rpc", "/artifacts//file"} {
		r := httptest.NewRequest("GET", "https://node.apps.test"+target, nil)
		if p.permits(r) {
			t.Fatal("path escaped", target)
		}
	}
	if !p.permits(httptest.NewRequest("GET", "https://node.apps.test/artifacts/one?download=1", nil)) {
		t.Fatal("child path rejected")
	}
	for _, key := range []string{"Authorization", "Cookie", "X-Fleet-Rpc-Token", "X-Pantheon-App-Token", "Connection", "Content-Length"} {
		p.Headers = map[string]string{key: "secret"}
		if p.valid() {
			t.Fatal("unsafe header", key)
		}
	}
}
