package appgateway

import (
	"context"
	"crypto/tls"
	_ "embed"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheon-fleet/internal/apptransport"
	"github.com/gorilla/websocket"
)

//go:embed browser-check.mjs
var browserCheck string

// Opt-in because Fleet itself does not depend on Node or a browser install.
// Uses a private Playwright browser profile, never the user's open browser.
func TestBrowserPartitionedCookiesAndEditorFrames(t *testing.T) {
	module := os.Getenv("PANTHEON_TEST_PLAYWRIGHT_MODULE")
	api := os.Getenv("PANTHEON_TEST_OFFICE_API")
	if module == "" || api == "" {
		t.Skip("requires Playwright and the built native Office API")
	}
	nativeAPI, err := os.ReadFile(api)
	if err != nil {
		t.Fatal(err)
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/documents/api.js" {
			w.Header().Set("Content-Type", "text/javascript")
			_, _ = w.Write(nativeAPI)
		} else {
			w.Header().Set("Content-Type", "text/html")
			_, _ = w.Write([]byte("<p>Editor reached the selected Fleet service</p>"))
		}
	}))
	defer upstream.Close()
	var server *httptest.Server
	gateway, err := New("apps.test", "browser-test-controller-service-secret", []string{"https://atrium.test"},
		func(ctx context.Context, _ Binding, id, secret string) error {
			ws, _, err := (&websocket.Dialer{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}).DialContext(ctx, "ws"+strings.TrimPrefix(server.URL, "http")+"/apps/tunnel/"+id, http.Header{"Authorization": {"Bearer " + secret}})
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
	gateway.Register(mux)
	handler := gateway.Handler(mux)
	server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Host == "atrium.test" || r.Host == "unrelated.test" {
			w.Header().Set("Content-Type", "text/html")
			_, _ = w.Write([]byte(`<div id="first"></div><div id="second"></div><div id="third"></div>`))
			return
		}
		handler.ServeHTTP(w, r)
	}))
	defer server.Close()
	cmd := exec.Command("node", "--input-type=module", "-e", browserCheck)
	cmd.Env = append(os.Environ(), "FLEET_TEST_URL="+server.URL, "FLEET_TEST_PLAYWRIGHT="+module)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("browser: %v\n%s", err, output)
	} else {
		t.Log(string(output))
	}
}
