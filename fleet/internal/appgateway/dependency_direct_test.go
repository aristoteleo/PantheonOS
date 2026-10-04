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

	"github.com/aristoteleo/pantheon-fleet/internal/appdirect"
	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/aristoteleo/pantheon-fleet/internal/dataplane"
)

func TestDirectDependencyRealQUICScopeAndLifetime(t *testing.T) {
	for _, stop := range []string{"revoke", "consumer", "provider", "expiry", "disconnect", "journal", "control-unavailable"} {
		t.Run(stop, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			const owner = "owner-controller-credential-for-test"
			b := Binding{Fleet: "owner", Node: "node", Instance: "model", Revision: strings.Repeat("a", 64), Generation: 2, Component: "backend", Port: "http"}
			consumer := apptransport.InstanceIdentity{Fleet: "owner", Node: "agent-node", Instance: "agent", Revision: strings.Repeat("b", 64), Generation: 3}
			var consumerGone, providerGone, controllerGone atomic.Bool
			var calls, relays atomic.Int32
			closed, rescue := make(chan struct{}), make(chan struct{})
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				io.Copy(io.Discard, r.Body)
				if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("X-Fleet-RPC-Token") != "" || r.Header.Get("X-Model-Config") != "pinned" || r.Header.Get("X-Pantheon-App-Token") != strings.Repeat("signed", 8) {
					t.Error("upstream authority changed")
				}
				w.Write([]byte("data: direct\n\n"))
				w.(http.Flusher).Flush()
				select {
				case <-r.Context().Done():
					close(closed)
				case <-rescue:
				}
			}))
			defer upstream.Close()
			defer close(rescue)
			providerCheck := func(_ context.Context, binding Binding) error {
				if binding != b || providerGone.Load() {
					return fmt.Errorf("provider changed")
				}
				return nil
			}
			g, err := New("apps.test", owner, []string{"https://atrium.test"}, func(context.Context, Binding, string, string) error {
				relays.Add(1)
				return fmt.Errorf("unexpected relay")
			}, providerCheck)
			if err != nil {
				t.Fatal(err)
			}
			g.SetDependencyDispatch(func(_ context.Context, c apptransport.InstanceIdentity, _ string) error {
				if c != consumer || consumerGone.Load() {
					return fmt.Errorf("consumer changed")
				}
				return nil
			}, func(context.Context, Binding, string, json.RawMessage, int) (json.RawMessage, error) {
				return nil, fmt.Errorf("unexpected RPC")
			})
			if err := g.OpenDependencyStore(t.TempDir() + "/grants"); err != nil {
				t.Fatal(err)
			}
			defer g.CloseDependencyStore()
			mux := http.NewServeMux()
			g.Register(mux)
			handler := g.Handler(mux)
			controller := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/apps/dependencies/check" && controllerGone.Load() {
					w.WriteHeader(503)
					return
				}
				handler.ServeHTTP(w, r)
			}))
			defer controller.Close()
			node, err := dataplane.NewAppClient(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer node.Close()
			client, err := dataplane.NewAppClient(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer client.Close()
			direct := appdirect.New(ctx, node, func(binding Binding) (string, func(), error) {
				return upstream.URL, func() {}, providerCheck(ctx, binding)
			}, func() bool { return true })
			check, err := appdirect.ControllerCheck(ctx, controller.URL)
			if err != nil {
				t.Fatal(err)
			}
			direct.SetDependencyCheck(check)
			var private appdirect.Request
			g.SetDirectDispatch(func(_ context.Context, q appdirect.Request) (appdirect.Grant, error) {
				private = q
				return direct.Issue(q)
			})
			control := func(path string, value any, token string) (int, []byte) {
				t.Helper()
				body, _ := json.Marshal(value)
				r, _ := http.NewRequestWithContext(ctx, "POST", controller.URL+path, bytes.NewReader(body))
				r.Header.Set("Authorization", "Bearer "+token)
				res, err := controller.Client().Do(r)
				if err != nil {
					t.Fatal(err)
				}
				defer res.Body.Close()
				raw, _ := io.ReadAll(res.Body)
				return res.StatusCode, raw
			}
			expires := time.Now().Add(time.Minute).Unix()
			if stop == "expiry" {
				expires = time.Now().Add(4 * time.Second).Unix()
			}
			q := DependencyRequest{Consumer: consumer, Provider: b, AppID: "model-service", Timeout: 60, Expires: expires,
				HTTP: &HTTPDependency{Credential: strings.Repeat("signed", 8), Rules: []HTTPRule{{Method: "POST", Path: "/v1/chat/completions"}}, Headers: map[string]string{"X-Model-Config": "pinned"}}}
			status, raw := control("/apps/dependencies", q, owner)
			var issued struct {
				ID string `json:"grant_id"`
			}
			if status != 200 || json.Unmarshal(raw, &issued) != nil {
				t.Fatal(status, string(raw))
			}
			exchange := map[string]string{"fleet_id": "owner", "grant_id": issued.ID, "peer_id": client.ID()}
			if status, _ := control("/apps/dependencies/direct", exchange, "not-owner"); status != 401 {
				t.Fatal(status)
			}
			attach := func() appdirect.Grant {
				t.Helper()
				status, raw := control("/apps/dependencies/direct", exchange, owner)
				var grant appdirect.Grant
				if status != 200 || json.Unmarshal(raw, &grant) != nil {
					t.Fatal(status, string(raw))
				}
				if bytes.Contains(raw, []byte("proof")) || bytes.Contains(raw, []byte("credential")) || bytes.Contains(raw, []byte("consumer")) {
					t.Fatal("private authority leaked")
				}
				return grant
			}
			httpCall := func(path string) *http.Response {
				t.Helper()
				conn, err := appdirect.Dial(ctx, client, attach())
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { conn.Close() })
				transport := &http.Transport{DisableKeepAlives: true, DialContext: func(context.Context, string, string) (net.Conn, error) { return conn, nil }}
				t.Cleanup(transport.CloseIdleConnections)
				r, _ := http.NewRequestWithContext(ctx, "POST", "http://app.test"+path, strings.NewReader(`{}`))
				r.Header.Set("Authorization", "Bearer forged")
				r.Header.Set("Cookie", "ambient=secret")
				r.Header.Set("X-Fleet-RPC-Token", "forged")
				r.Header.Set("Connection", "X-Model-Config")
				res, err := (&http.Client{Transport: transport}).Do(r)
				if err != nil {
					t.Fatal(err)
				}
				return res
			}
			denied := httpCall("/rpc")
			denied.Body.Close()
			if denied.StatusCode != 403 {
				t.Fatal("scope escaped", denied.Status)
			}
			altered := private
			dep := *altered.Dependency
			altered.Dependency = &dep
			dep.Consumer.Generation++
			if status, _ := control("/apps/dependencies/check", altered, ""); status != 409 {
				t.Fatal("changed consumer accepted", status)
			}
			dep.Consumer = consumer
			dep.Proof = strings.Repeat("0", 64)
			if status, _ := control("/apps/dependencies/check", altered, ""); status != 401 {
				t.Fatal("forged proof accepted", status)
			}
			stream := httpCall("/v1/chat/completions")
			defer stream.Body.Close()
			first := make([]byte, len("data: direct\n\n"))
			if _, err := io.ReadFull(stream.Body, first); err != nil || string(first) != "data: direct\n\n" {
				t.Fatal("not streaming", err)
			}
			switch stop {
			case "consumer":
				consumerGone.Store(true)
			case "provider":
				providerGone.Store(true)
			case "disconnect":
				stream.Body.Close()
			case "journal":
				g.CloseDependencyStore()
			case "control-unavailable":
				controllerGone.Store(true)
			case "revoke":
				body, _ := json.Marshal(map[string]string{"fleet_id": "owner", "grant_id": issued.ID})
				r, _ := http.NewRequestWithContext(ctx, "DELETE", controller.URL+"/apps/dependencies", bytes.NewReader(body))
				r.Header.Set("Authorization", "Bearer "+owner)
				res, err := controller.Client().Do(r)
				if err != nil {
					t.Fatal(err)
				}
				res.Body.Close()
				if res.StatusCode != 204 {
					t.Fatal(res.Status)
				}
			}
			select {
			case <-closed:
			case <-time.After(8 * time.Second):
				t.Fatal("direct upstream survived lost authority")
			}
			if calls.Load() != 1 || relays.Load() != 0 {
				t.Fatal("replayed or relayed inference", calls.Load(), relays.Load())
			}
		})
	}
}
