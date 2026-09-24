package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheon-fleet/internal/appmedia"
)

func TestMediaOffersUseBrowserCookieAndImmutableBinding(t *testing.T) {
	const origin = "https://atrium.test"
	b := Binding{Fleet: "alice", Node: "node", Instance: "video", Revision: strings.Repeat("a", 64), Generation: 2, Component: "backend", Port: "http"}
	q := appmedia.Offer{SDP: "v=0\r\na=application-offer", Artifact: strings.Repeat("b", 32), Config: strings.Repeat("c", 64), SHA256: strings.Repeat("d", 64), Size: 16, MIME: "video/mp4"}
	for _, kind := range []string{"valid", "missing-cookie", "foreign-origin", "own-app-origin", "missing-origin", "wrong-host", "workload", "injected-binding", "injected-credential", "injected-url", "trailing-json", "oversize", "stale", "unsupported", "wrong-answer"} {
		t.Run(kind, func(t *testing.T) {
			stale := false
			g, err := New("apps.test", strings.Repeat("service", 8), []string{origin}, func(context.Context, Binding, string, string) error {
				return errors.New("media must not use HTTP Relay")
			}, func(_ context.Context, got Binding) error {
				if stale || got != b {
					return errors.New("stale")
				}
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			g.SetMediaDispatch(func(_ context.Context, r appmedia.Request) (appmedia.Answer, error) {
				calls++
				if r.Binding != b || r.Offer != q || r.Credential != strings.Repeat("instance-credential", 4) || r.Expires > time.Now().Add(appmedia.MaxLifetime).Unix() {
					t.Fatal("authority changed")
				}
				answer := appmedia.Answer{SDP: "v=0\r\na=application-answer", Transport: "fleet_browser_direct", Expires: r.Expires}
				if kind == "wrong-answer" {
					answer.Expires++
				}
				return answer, nil
			})
			mux := http.NewServeMux()
			g.Register(mux)
			handler := g.Handler(mux)
			host := Host(b.Instance, b.Component, b.Port, b.Generation, "apps.test")
			do := func(path, host string, body []byte, headers map[string]string) *httptest.ResponseRecorder {
				req := httptest.NewRequest("POST", "https://"+host+path, bytes.NewReader(body))
				for k, v := range headers {
					req.Header.Set(k, v)
				}
				w := httptest.NewRecorder()
				handler.ServeHTTP(w, req)
				return w
			}
			access := AttachRequest{Binding: b, Credential: strings.Repeat("instance-credential", 4), Expires: time.Now().Add(time.Hour).Unix(), UIOrigin: origin}
			if kind == "workload" {
				access.Workload = true
				access.UIOrigin = ""
			}
			raw, _ := json.Marshal(access)
			res := do("/apps/connect", "controller.test", raw, map[string]string{"Authorization": "Bearer " + strings.Repeat("service", 8)})
			var grant struct {
				Ticket      string `json:"ticket"`
				AccessToken string `json:"access_token"`
			}
			if res.Code != 200 || json.Unmarshal(res.Body.Bytes(), &grant) != nil {
				t.Fatal("grant failed", res.Code)
			}
			headers := map[string]string{"Origin": origin}
			if kind == "workload" {
				headers = map[string]string{"Authorization": "Bearer " + grant.AccessToken}
			} else {
				raw, _ = json.Marshal(map[string]string{"ticket": grant.Ticket})
				res = do("/__fleet/connect", host, raw, headers)
				if res.Code != 204 || len(res.Result().Cookies()) != 1 {
					t.Fatal("cookie failed", res.Code)
				}
				cookie := res.Result().Cookies()[0]
				headers["Cookie"] = cookie.Name + "=" + cookie.Value
			}
			raw, _ = json.Marshal(q)
			want := 403
			switch kind {
			case "valid":
				want = 200
			case "missing-cookie":
				delete(headers, "Cookie")
				want = 401
			case "foreign-origin":
				headers["Origin"] = "https://evil.test"
			case "own-app-origin":
				headers["Origin"] = "https://" + host
			case "missing-origin":
				delete(headers, "Origin")
			case "wrong-host":
				host = Host("other", b.Component, b.Port, b.Generation, "apps.test")
				want = 401
			case "injected-binding", "injected-credential", "injected-url":
				key := map[string]string{"injected-binding": "node_id", "injected-credential": "credential", "injected-url": "url"}[kind]
				raw = append(raw[:len(raw)-1], []byte(`,"`+key+`":"foreign"}`)...)
				want = 400
			case "trailing-json":
				raw = append(raw, []byte(` {}`)...)
				want = 400
			case "oversize":
				raw = bytes.Repeat([]byte("x"), 70001)
				want = 400
			case "stale":
				stale = true
				want = 409
			case "unsupported":
				g.SetMediaDispatch(nil)
				want = 503
			case "wrong-answer":
				want = 503
			}
			res = do("/__fleet/model-media", host, raw, headers)
			if res.Code != want {
				t.Fatalf("status %d, want %d: %s", res.Code, want, res.Body.String())
			}
			expectedCalls := 0
			if kind == "valid" || kind == "wrong-answer" {
				expectedCalls = 1
			}
			if calls != expectedCalls {
				t.Fatalf("dispatch called %d times", calls)
			}
			if strings.Contains(res.Body.String(), access.Credential) || strings.Contains(res.Body.String(), `"node_id"`) {
				t.Fatal("answer leaked authority")
			}
		})
	}
}
