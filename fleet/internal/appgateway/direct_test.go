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

func TestControllerGrantToRealQUICApp(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	const serviceToken = "controller-service-key-not-for-apps"
	const credential = "hub-signed-exact-instance-credential"
	b := Binding{Fleet: "alice", Node: "mac", Instance: "model", Revision: strings.Repeat("a", 64), Generation: 2, Component: "model", Port: "http"}
	var engineCalls, relayCalls, grants atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		engineCalls.Add(1)
		if r.Header.Get("X-Pantheon-App-Token") != credential {
			t.Error("wrong App credential")
		}
		_, _ = w.Write([]byte("DIRECT_OK"))
	}))
	defer upstream.Close()
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
	direct := appdirect.New(ctx, node, func(binding apptransport.Binding) (string, func(), error) {
		if binding != b {
			return "", nil, fmt.Errorf("wrong binding")
		}
		return upstream.URL, func() {}, nil
	}, func() bool { return true })
	gateway, err := New("apps.test", serviceToken, []string{"https://atrium.test"},
		func(context.Context, Binding, string, string) error {
			relayCalls.Add(1)
			return fmt.Errorf("unexpected relay")
		},
		func(_ context.Context, binding Binding) error {
			if binding != b {
				return fmt.Errorf("wrong binding")
			}
			return nil
		})
	if err != nil {
		t.Fatal(err)
	}
	gateway.SetDirectDispatch(func(_ context.Context, q appdirect.Request) (appdirect.Grant, error) {
		grants.Add(1)
		return direct.Issue(q)
	})
	mux := http.NewServeMux()
	gateway.Register(mux)
	controller := httptest.NewServer(gateway.Handler(mux))
	defer controller.Close()
	q := appdirect.Request{Binding: b, Peer: client.ID(), Credential: credential, Expires: time.Now().Add(time.Minute).Unix()}
	attach := func(body any, token string) *http.Response {
		data, _ := json.Marshal(body)
		req, _ := http.NewRequestWithContext(ctx, "POST", controller.URL+"/apps/direct-connect", bytes.NewReader(data))
		req.Header.Set("Authorization", "Bearer "+token)
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { res.Body.Close() })
		return res
	}
	if attach(q, "user-login").StatusCode != 401 {
		t.Fatal("accepted caller login instead of controller authentication")
	}
	wrong := q
	wrong.Fleet = "bob"
	if attach(wrong, serviceToken).StatusCode != 409 {
		t.Fatal("wrong owner accepted")
	}
	if attach(map[string]any{"address": "http://127.0.0.1/admin"}, serviceToken).StatusCode != 400 {
		t.Fatal("arbitrary destination accepted")
	}
	res := attach(q, serviceToken)
	if res.StatusCode != 200 || res.Header.Get("Set-Cookie") != "" {
		t.Fatal("invalid grant response", res.StatusCode)
	}
	var grant appdirect.Grant
	if err := json.NewDecoder(res.Body).Decode(&grant); err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	conn, err := appdirect.Dial(ctx, client, grant)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	transport := &http.Transport{DisableKeepAlives: true, DialContext: func(context.Context, string, string) (net.Conn, error) { return conn, nil }}
	defer transport.CloseIdleConnections()
	httpClient := &http.Client{Transport: transport}
	response, err := httpClient.Get("http://app.test/infer")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	data, err := io.ReadAll(response.Body)
	if err != nil || string(data) != "DIRECT_OK" || engineCalls.Load() != 1 || grants.Load() != 1 || relayCalls.Load() != 0 {
		t.Fatal("direct App flow failed", string(data), err)
	}
}
