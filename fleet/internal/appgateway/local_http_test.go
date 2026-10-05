package appgateway

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
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

func TestLocalHTTPAuthorityStreamsAndRevokes(t *testing.T) {
	server := httptest.NewUnstartedServer(nil)
	defer server.Close()
	origin := "https://" + server.Listener.Addr().String()
	provider := Binding{Fleet: "owner", Node: "node", Instance: "model", Revision: strings.Repeat("a", 64), Generation: 2, Component: "backend", Port: "http"}
	consumer := apptransport.InstanceIdentity{Fleet: "owner", Node: "node", Instance: "consumer", Revision: strings.Repeat("b", 64), Generation: 3}
	var live atomic.Bool
	live.Store(true)
	var calls atomic.Int32
	closed, rescue := make(chan struct{}), make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = io.Copy(io.Discard, r.Body)
		for _, h := range []string{"Authorization", "Cookie", "X-Fleet-RPC-Token", "X-Pantheon-App-Token"} {
			if r.Header.Get(h) != "" {
				t.Errorf("credential reached provider: %s", h)
			}
		}
		w.Header().Set("Set-Cookie", "provider=must-not-persist; Path=/")
		_, _ = w.Write([]byte("data: first\n\n"))
		w.(http.Flusher).Flush()
		select {
		case <-r.Context().Done():
			close(closed)
		case <-rescue:
		}
	}))
	defer upstream.Close()
	defer close(rescue)
	g, err := NewLocalRPC(origin, strings.Repeat("s", 32), func(ctx context.Context, b Binding, id, secret string) error {
		if b != provider {
			return fmt.Errorf("wrong provider")
		}
		dialer := websocket.Dialer{TLSClientConfig: server.Client().Transport.(*http.Transport).TLSClientConfig}
		ws, _, err := dialer.DialContext(ctx, "wss"+strings.TrimPrefix(origin, "https")+"/apps/tunnel/"+id, http.Header{"Authorization": {"Bearer " + secret}})
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
		if b != provider {
			return fmt.Errorf("wrong provider")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if err = g.EnableLocalHTTP(); err != nil {
		t.Fatal(err)
	}
	g.SetDependencyDispatch(func(_ context.Context, c apptransport.InstanceIdentity, prep string) error {
		if c != consumer || !live.Load() || prep != "" {
			return fmt.Errorf("consumer unavailable")
		}
		return nil
	}, func(context.Context, Binding, string, json.RawMessage, int) (json.RawMessage, error) {
		t.Error("HTTP sent through RPC")
		return nil, fmt.Errorf("denied")
	})
	if err = g.OpenDependencyStore(t.TempDir() + "/grants"); err != nil {
		t.Fatal(err)
	}
	defer g.CloseDependencyStore()
	mux := http.NewServeMux()
	g.Register(mux)
	if err = g.RegisterLocalAuthority(mux, func(key string) (string, bool) { return "owner", key == "owner-key" }); err != nil {
		t.Fatal(err)
	}
	server.Config.Handler = g.Handler(mux)
	server.StartTLS()
	do := func(method, path, key string, body any, headers http.Header) *http.Response {
		t.Helper()
		raw, _ := json.Marshal(body)
		r, _ := http.NewRequest(method, origin+path, bytes.NewReader(raw))
		r.Header = headers.Clone()
		if r.Header == nil {
			r.Header = make(http.Header)
		}
		r.Header.Set("Authorization", "Bearer "+key)
		result, err := server.Client().Do(r)
		if err != nil {
			t.Fatal(err)
		}
		return result
	}
	status := func(method, path, key string, body any, headers http.Header) int {
		t.Helper()
		r := do(method, path, key, body, headers)
		defer r.Body.Close()
		return r.StatusCode
	}
	const path = "/api/fleet/apps/dependency-http-grants"
	c, p := consumer, provider
	c.Fleet = ""
	p.Fleet = ""
	q := map[string]any{"operation_id": "local-model", "consumer": c, "provider": p, "app_id": "model-service", "rules": []HTTPRule{{Method: "POST", Path: "/v1/chat/completions"}}}
	if code := status("POST", path, "invalid", q, nil); code != 401 {
		t.Fatal(code)
	}
	for _, field := range []string{"credential", "node_bound", "methods"} {
		q[field] = nil
		if code := status("POST", path, "owner-key", q, nil); code != 400 {
			t.Fatal(field, code)
		}
		delete(q, field)
	}
	if code := status("POST", path, "owner-key", q, http.Header{"Origin": {"https://atrium.test"}}); code != 403 {
		t.Fatal(code)
	}
	response := do("POST", path, "owner-key", q, nil)
	var grant struct {
		Token  string `json:"access_token"`
		ID     string `json:"grant_id"`
		Origin string `json:"origin"`
	}
	if response.StatusCode != 200 || json.NewDecoder(response.Body).Decode(&grant) != nil {
		t.Fatal(response.Status)
	}
	response.Body.Close()
	if grant.Origin != origin || grant.Token == "" {
		t.Fatal("invalid local receipt")
	}
	for _, target := range []string{"/rpc", "/api/fleet/apps/dependency-http-grants", "/apps/tunnel/wrong", "/v1/models"} {
		if code := status("POST", target, grant.Token, nil, nil); code != 403 {
			t.Fatal(target, code)
		}
	}
	if code := status("POST", "/v1/chat/completions", grant.Token, nil, http.Header{"Origin": {"https://atrium.test"}}); code != 403 {
		t.Fatal(code)
	}
	if code := status("GET", "/v1/chat/completions", grant.Token, nil, nil); code != 403 {
		t.Fatal(code)
	}
	live.Store(false)
	if code := status("POST", "/v1/chat/completions", grant.Token, nil, nil); code != 409 {
		t.Fatal(code)
	}
	live.Store(true)
	stream := do("POST", "/v1/chat/completions", grant.Token, nil, http.Header{"Cookie": {"private=cookie"}, "X-Fleet-Rpc-Token": {"forged"}, "X-Pantheon-App-Token": {"forged"}})
	defer stream.Body.Close()
	first := make([]byte, len("data: first\n\n"))
	if _, err = io.ReadFull(stream.Body, first); err != nil || string(first) != "data: first\n\n" {
		t.Fatal("stream buffered", err)
	}
	if len(stream.Cookies()) != 0 {
		t.Fatal("workload emitted browser cookie")
	}
	if code := status("PATCH", path+"/"+grant.ID, "owner-key", map[string]int{"ttl_seconds": 900}, nil); code != 200 {
		t.Fatal("local renewal", code)
	}
	if code := status("DELETE", path+"/"+grant.ID, "owner-key", nil, nil); code != 204 {
		t.Fatal(code)
	}
	select {
	case <-closed:
	case <-time.After(5 * time.Second):
		t.Fatal("upstream survived revocation")
	}
	if code := status("POST", path, "owner-key", q, nil); code != 410 {
		t.Fatal("revocation resurrected", code)
	}
	if calls.Load() != 1 {
		t.Fatal("unauthorized request reached provider", calls.Load())
	}
}

func TestLocalHTTPExplicitModeBoundary(t *testing.T) {
	dispatch := func(context.Context, Binding, string, string) error { return nil }
	verify := func(context.Context, Binding) error { return nil }
	cloud, _ := New("apps.test", strings.Repeat("s", 32), []string{"https://atrium.test"}, dispatch, verify)
	local, _ := NewLocalRPC("https://127.0.0.1:443", strings.Repeat("s", 32), dispatch, verify)
	nodePolicy := &HTTPDependency{NodeBound: true, Rules: []HTTPRule{{Method: "GET", Path: "/data"}}}
	signedPolicy := &HTTPDependency{Credential: strings.Repeat("x", 32), Rules: nodePolicy.Rules}
	if !nodePolicy.Valid() || cloud.acceptsHTTPDependency(nodePolicy) || local.acceptsHTTPDependency(nodePolicy) || local.acceptsHTTPDependency(signedPolicy) {
		t.Fatal("implicit local authority")
	}
	if cloud.EnableLocalHTTP() == nil {
		t.Fatal("cloud gateway enabled local authority")
	}
	mux := http.NewServeMux()
	local.Register(mux)
	_ = local.RegisterLocalAuthority(mux, func(string) (string, bool) { return "owner", true })
	for _, path := range []string{"/apps/tunnel/test", "/api/fleet/apps/dependency-http-grants"} {
		r := httptest.NewRequest("POST", path, nil)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		if w.Code != 404 {
			t.Fatal("RPC-only mode exposed HTTP", path, w.Code)
		}
	}
	_ = local.EnableLocalHTTP()
	if !local.acceptsHTTPDependency(nodePolicy) || local.acceptsHTTPDependency(signedPolicy) || !cloud.acceptsHTTPDependency(signedPolicy) {
		t.Fatal("local/cloud policy mismatch")
	}
	nodePolicy.Credential = "must-not-forward"
	if nodePolicy.Valid() || local.acceptsHTTPDependency(nodePolicy) {
		t.Fatal("mixed authorization accepted")
	}
}

// The durable journal hashes this exact representation. Adding local authority
// must not invalidate existing Hub-issued HTTP grants on a Controller upgrade.
func TestHTTPPolicyRetainsLegacyCanonicalEncoding(t *testing.T) {
	q := DependencyRequest{HTTP: &HTTPDependency{Credential: strings.Repeat("x", 32), Rules: []HTTPRule{{Method: "POST", Path: "/v1/chat/completions"}}}}
	legacy := `{"consumer":{"fleet_id":"","node_id":"","instance_id":"","revision":"","generation":0},"provider":{"fleet_id":"","node_id":"","instance_id":"","revision":"","generation":0,"component":"","port":""},"app_id":"","methods":null,"http":{"rules":[{"method":"POST","path":"/v1/chat/completions"}],"credential":""},"expires":0,"timeout_seconds":0}`
	sum := sha256.Sum256([]byte(legacy))
	if dependencyPolicy(q) != hex.EncodeToString(sum[:]) {
		t.Fatal("legacy HTTP policy hash changed")
	}
}
