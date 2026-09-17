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
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/gorilla/websocket"
)

func TestGatewayStreamingIsolationAndWebSocket(t *testing.T) {
	const serviceToken = "controller-service-token-is-not-an-app-token"
	const credential = "hub-signed-instance-credential-not-login"
	b := Binding{Fleet: "alice", Node: "node1", Instance: "instance1", Revision: strings.Repeat("a", 64), Generation: 1, Component: "office", Port: "http"}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Pantheon-App-Token") != credential {
			http.Error(w, "bad identity", 403)
			return
		}
		if strings.Contains(r.Header.Get("Cookie"), "fleetapp") {
			http.Error(w, "leaked gateway cookie", 500)
			return
		}
		if r.URL.Path == "/ws" {
			ws, e := (&websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}).Upgrade(w, r, nil)
			if e != nil {
				return
			}
			defer ws.Close()
			for {
				kind, data, e := ws.ReadMessage()
				if e != nil {
					return
				}
				if ws.WriteMessage(kind, data) != nil {
					return
				}
			}
		}
		if r.URL.Path == "/slow" {
			_, _ = w.Write([]byte("first"))
			w.(http.Flusher).Flush()
			time.Sleep(200 * time.Millisecond)
			_, _ = w.Write([]byte("last"))
			return
		}
		data, _ := io.ReadAll(r.Body)
		sum := sha256.Sum256(data)
		w.Header().Add("Set-Cookie", "app-session=test; Domain=.apps.test; Path=/")
		w.Header().Add("Set-Cookie", "__Host-fleetapp=malicious; Path=/")
		_, _ = w.Write([]byte(hex.EncodeToString(sum[:]) + "|" + r.URL.RawQuery))
	}))
	defer upstream.Close()
	var server *httptest.Server
	g, err := New("apps.test", serviceToken, []string{"https://atrium.test"},
		func(ctx context.Context, got Binding, id, secret string) error {
			if got != b {
				return fmt.Errorf("wrong binding")
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
		}, func(_ context.Context, got Binding) error {
			if got != b {
				return fmt.Errorf("wrong owner or generation")
			}
			return nil
		})
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	g.Register(mux)
	server = httptest.NewServer(g.Handler(mux))
	defer server.Close()
	do := func(method, path, host string, body []byte, headers http.Header) *http.Response {
		t.Helper()
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(body))
		req.Host = host
		req.Header = headers
		res, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return res
	}
	host := Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test")
	grantBody, _ := json.Marshal(AttachRequest{Binding: b, Credential: credential, Expires: time.Now().Add(time.Hour).Unix(), UIOrigin: "https://atrium.test"})
	res := do("POST", "/apps/connect", "controller.test", grantBody, http.Header{})
	if res.StatusCode != 401 {
		t.Fatal(res.Status)
	}
	res.Body.Close()
	res = do("POST", "/apps/connect", "controller.test", grantBody, http.Header{"Authorization": {"Bearer " + serviceToken}})
	var attached struct{ Ticket string }
	if json.NewDecoder(res.Body).Decode(&attached) != nil || attached.Ticket == "" {
		t.Fatal("no ticket", res.Status)
	}
	res.Body.Close()
	body, _ := json.Marshal(map[string]string{"ticket": attached.Ticket})
	res = do("POST", "/__fleet/connect", host, body, http.Header{"Origin": {"https://evil.test"}})
	if res.StatusCode != 403 {
		t.Fatal(res.Status)
	}
	res.Body.Close()
	res = do("POST", "/__fleet/connect", host, body, http.Header{"Origin": {"https://atrium.test"}})
	if res.StatusCode != 204 {
		t.Fatal(res.Status)
	}
	cookies := res.Cookies()
	res.Body.Close()
	if len(cookies) != 1 || !cookies[0].HttpOnly || !cookies[0].Secure || !cookies[0].Partitioned {
		t.Fatal("cookie is not isolated", cookies)
	}
	cookie := cookies[0].Name + "=" + cookies[0].Value
	res = do("POST", "/__fleet/connect", host, body, http.Header{"Origin": {"https://atrium.test"}})
	if res.StatusCode != 401 {
		t.Fatal("ticket replay", res.Status)
	}
	res.Body.Close()
	res = do("GET", "/apps/connect", host, nil, http.Header{})
	if res.StatusCode != 401 {
		t.Fatal("App reached Controller API", res.Status)
	}
	res.Body.Close()
	res = do("GET", "/", host, nil, http.Header{"Cookie": {cookie}, "Origin": {"https://evil.test"}})
	if res.StatusCode != 403 {
		t.Fatal("cross-origin request", res.Status)
	}
	res.Body.Close()
	for _, tc := range []struct {
		name, referrer, destination string
		status                      int
	}{
		{"editor frame", "https://atrium.test/desktop", "iframe", 200},
		{"service worker reload", "https://atrium.test/desktop", "empty", 200},
		{"unrelated frame", "https://evil.test/", "iframe", 403},
		{"unrelated worker", "https://evil.test/", "empty", 403},
		{"missing referrer", "", "empty", 403},
		{"top-level navigation", "https://atrium.test/", "document", 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			response := do("GET", "/editor", host, nil, http.Header{
				"Cookie": {cookie}, "Referer": {tc.referrer},
				"Sec-Fetch-Site": {"cross-site"}, "Sec-Fetch-Mode": {"navigate"},
				"Sec-Fetch-Dest": {tc.destination},
			})
			defer response.Body.Close()
			if response.StatusCode != tc.status {
				t.Fatalf("navigation returned %d, want %d", response.StatusCode, tc.status)
			}
		})
	}
	data := bytes.Repeat([]byte("document-stream"), 160000)
	sum := sha256.Sum256(data)
	res = do("POST", "/upload?version=2", host, data, http.Header{"Cookie": {cookie}, "Origin": {"https://atrium.test"}, "X-Pantheon-App-Token": {"forged"}})
	read, _ := io.ReadAll(res.Body)
	res.Body.Close()
	if string(read) != hex.EncodeToString(sum[:])+"|version=2" {
		t.Fatal("stream changed", res.Status, string(read))
	}
	if res.Header.Get("Access-Control-Allow-Origin") != "https://atrium.test" {
		t.Fatal("missing CORS")
	}
	if len(res.Cookies()) != 1 || res.Cookies()[0].Domain != "" || res.Cookies()[0].Name != "app-session" {
		t.Fatal("unsafe App cookies", res.Header)
	}
	res = do("GET", "/slow", host, nil, http.Header{"Cookie": {cookie}})
	first := make([]byte, 5)
	if _, err = io.ReadFull(res.Body, first); err != nil || string(first) != "first" {
		t.Fatal("stream buffering", err)
	}
	res.Body.Close()
	dialer := *websocket.DefaultDialer
	ws, _, err := dialer.Dial("ws"+strings.TrimPrefix(server.URL, "http")+"/ws", http.Header{"Host": {host}, "Cookie": {cookie}, "Origin": {"https://" + host}})
	if err != nil {
		t.Fatal(err)
	}
	if err = ws.WriteMessage(websocket.TextMessage, []byte("edit operation")); err != nil {
		t.Fatal(err)
	}
	_, echo, err := ws.ReadMessage()
	if err != nil || string(echo) != "edit operation" {
		t.Fatal("WebSocket relay failed", err)
	}
	ws.Close()
	deadline := time.Now().Add(2 * time.Second)
	for len(g.slots) > 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if len(g.slots) > 0 {
		t.Fatal("connections leaked")
	}
}

func TestRestartCannotReplaceAnotherGenerationsCookieOrigin(t *testing.T) {
	if Host("instance", "office", "http", 1, "apps.test") == Host("instance", "office", "http", 2, "apps.test") {
		t.Fatal("two generations share browser credentials")
	}
}
