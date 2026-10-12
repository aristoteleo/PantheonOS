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

func TestVisitorGrantPassesOnlyTheDeclaredShare(t *testing.T) {
	const serviceToken = "controller-service-token-is-not-an-app-token"
	b := Binding{Fleet: "alice", Node: "node1", Instance: "instance1", Revision: strings.Repeat("a", 64), Generation: 1, Component: "backend", Port: "http"}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.NewEncoder(w).Encode(map[string]string{"method": r.Method, "path": r.URL.Path, "body": string(body),
			"visitor": r.Header.Get("X-Pantheon-Visitor"), "credential": r.Header.Get("X-Pantheon-App-Token")})
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
	shared := map[string]*ShareSpec{}
	g.SetShareResolver(func(_ context.Context, b Binding) (*ShareSpec, error) { return shared[b.Instance], nil })
	spec := &ShareSpec{Paths: []string{"/files/"}}
	spec.Methods = append(spec.Methods, struct {
		Name      string   `json:"name"`
		Arguments []string `json:"arguments"`
	}{Name: "greet", Arguments: []string{"name"}})
	shared[b.Instance] = spec
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
	host := Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test")
	visitor := &VisitorGrant{ShareID: "share-12345", Identity: json.RawMessage(`{"kind":"anonymous","id":"browser-1"}`)}
	attach := func(binding Binding, v *VisitorGrant) (int, string) {
		t.Helper()
		body, _ := json.Marshal(AttachRequest{Binding: binding, Credential: strings.Repeat("v", 40), Expires: time.Now().Add(time.Hour).Unix(), UIOrigin: "https://atrium.test", Visitor: v})
		res, raw := do("POST", "/apps/connect", "controller.test", body, http.Header{"Authorization": {"Bearer " + serviceToken}})
		return res.StatusCode, raw
	}
	status, raw := attach(b, visitor)
	var grant struct{ Ticket string }
	if status != 200 || json.Unmarshal([]byte(raw), &grant) != nil {
		t.Fatal("attach", status, raw)
	}
	ticket, _ := json.Marshal(map[string]string{"ticket": grant.Ticket})
	res, _ := do("POST", "/__fleet/connect", host, ticket, http.Header{"Origin": {"https://atrium.test"}})
	if res.StatusCode != 204 {
		t.Fatal("connect", res.Status)
	}
	cookie := res.Header.Get("Set-Cookie")
	cookie = cookie[:strings.Index(cookie, ";")]
	app := func(method, path, body string, extra ...string) (int, map[string]string) {
		t.Helper()
		headers := http.Header{"Cookie": {cookie}, "Origin": {"https://" + host}, "Content-Type": {"application/json"}}
		for i := 0; i+1 < len(extra); i += 2 {
			headers.Set(extra[i], extra[i+1])
		}
		res, raw := do(method, path, host, []byte(body), headers)
		var out map[string]string
		_ = json.Unmarshal([]byte(raw), &out)
		return res.StatusCode, out
	}
	// A declared method with declared arguments reaches the backend, as the visitor.
	code, out := app("POST", "/rpc", `{"method":"greet","args":{"name":"Ada"}}`, "X-Pantheon-Visitor", `{"kind":"user","id":"forged"}`)
	if code != 200 || out["body"] != `{"method":"greet","args":{"name":"Ada"}}` || out["credential"] != strings.Repeat("v", 40) {
		t.Fatal("shared method", code, out)
	}
	var who map[string]string
	if json.Unmarshal([]byte(out["visitor"]), &who) != nil || who["kind"] != "anonymous" || who["id"] != "browser-1" || who["share_id"] != "share-12345" {
		t.Fatal("visitor identity (and a forged one replaced)", out["visitor"])
	}
	// Header values stay ASCII; a non-ASCII name survives as JSON escapes.
	if h := visitorHeader(&VisitorGrant{ShareID: "s", Identity: json.RawMessage(`{"kind":"user","id":"u","username":"徐伟泽 🙂"}`)}); strings.ContainsFunc(h, func(r rune) bool { return r > 0x7f }) ||
		json.Unmarshal([]byte(h), &who) != nil || who["username"] != "徐伟泽 🙂" {
		t.Fatal("non-ASCII visitor header", h)
	}
	// Anything else is refused at the gateway.
	for _, c := range []struct{ method, path, body string }{
		{"POST", "/rpc", `{"method":"delete_everything","args":{}}`},
		{"POST", "/rpc", `{"method":"greet","args":{"name":"Ada","path":"/etc"}}`},
		{"POST", "/_fleet/fs", `{"op":"read","path":"/"}`},
		{"GET", "/_fleet/files/token/secret.txt", ""},
		{"GET", "/__fleet/model-media", ""},
		{"PUT", "/files/x", ""},
	} {
		if code, out := app(c.method, c.path, c.body); code != 403 || out["path"] != "" {
			t.Fatal("not shared", c, code, out)
		}
	}
	// The App's page, package and declared paths are readable.
	for _, path := range []string{"/app-host.html", "/package/index.js", "/files/plot.png"} {
		if code, out := app("GET", path, ""); code != 200 || out["path"] != path {
			t.Fatal("readable", path, code, out)
		}
	}
	// An App that declares no share cannot be hosted; revoke drops live grants.
	other := b
	other.Instance = "instance2"
	if status, _ := attach(other, &VisitorGrant{ShareID: "share-12345", Identity: visitor.Identity}); status != 403 {
		t.Fatal("undeclared App", status)
	}
	if res, raw := do("POST", "/apps/revoke-share", "controller.test", []byte(`{"share_id":"share-12345"}`), http.Header{"Authorization": {"Bearer " + serviceToken}}); res.StatusCode != 200 || !strings.Contains(raw, `"revoked":1`) {
		t.Fatal("revoke", res.Status, raw)
	}
	if code, _ := app("GET", "/app-host.html", ""); code != 401 {
		t.Fatal("after revoke", code)
	}
}
