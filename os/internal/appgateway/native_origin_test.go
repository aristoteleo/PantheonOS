package appgateway

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestNativeOriginsRemainExplicitAndExact(t *testing.T) {
	for _, origin := range []string{"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"} {
		g, err := New("apps.test", strings.Repeat("s", 32), []string{origin},
			func(context.Context, Binding, string, string) error { return nil },
			func(context.Context, Binding) error { return nil })
		if err != nil {
			t.Fatal(err)
		}
		for _, candidate := range []string{origin, "tauri://evil", "null", "https://evil.test"} {
			req := httptest.NewRequest(http.MethodOptions, "https://test.apps.test/__fleet/connect", nil)
			req.Header.Set("Origin", candidate)
			w := httptest.NewRecorder()
			g.serveApp(w, req)
			if candidate == origin {
				if w.Code != 204 || w.Header().Get("Access-Control-Allow-Origin") != origin {
					t.Fatalf("native preflight rejected: %s", origin)
				}
			} else if w.Header().Get("Access-Control-Allow-Origin") != "" {
				t.Fatalf("allowed unrelated origin %s", candidate)
			}
		}
	}
	for _, origin := range []string{"tauri://evil", "tauri://localhost:80", "tauri://localhost/", "tauri://localhost?x=1", "http://tauri.localhost.evil", "null"} {
		_, err := New("apps.test", strings.Repeat("s", 32), []string{origin},
			func(context.Context, Binding, string, string) error { return nil },
			func(context.Context, Binding) error { return nil })
		if err == nil {
			t.Fatalf("accepted invalid origin %s", origin)
		}
	}
}

func TestNativeFrameWithoutReferrerRequiresBoundCookieAndAncestorPolicy(t *testing.T) {
	for _, ui := range []string{"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost", "https://atrium.test"} {
		g, err := New("apps.test", strings.Repeat("s", 32), []string{ui},
			func(context.Context, Binding, string, string) error { return nil },
			func(context.Context, Binding) error { return nil })
		if err != nil {
			t.Fatal(err)
		}
		v := &grant{AttachRequest: AttachRequest{Binding: Binding{Instance: "instance", Component: "app", Port: "http", Generation: 1}, UIOrigin: ui, Expires: time.Now().Add(time.Hour).Unix()}, cookie: "session"}
		g.grants[v.cookie] = v
		host := Host(v.Instance, v.Component, v.Port, v.Generation, g.domain)
		for _, tc := range []struct {
			name, cookie, referrer, destination, origin string
			status                                      int
		}{
			{"native frame", "session", "", "iframe", "", 200},
			{"no cookie", "", "", "iframe", "", 401},
			{"wrong cookie", "other", "", "iframe", "", 401},
			{"unrelated referrer", "session", "https://evil.test/", "iframe", "", 403},
			{"unrelated Origin", "session", "", "iframe", "https://evil.test", 403},
			{"top level", "session", "", "document", "", 403},
			{"worker", "session", "", "empty", "", 403},
		} {
			req := httptest.NewRequest("GET", "https://"+host+"/__fleet/status", nil)
			req.Header.Set("Sec-Fetch-Site", "cross-site")
			req.Header.Set("Sec-Fetch-Mode", "navigate")
			req.Header.Set("Sec-Fetch-Dest", tc.destination)
			req.Header.Set("Referer", tc.referrer)
			req.Header.Set("Origin", tc.origin)
			if tc.cookie != "" {
				req.AddCookie(&http.Cookie{Name: "__Host-fleetapp", Value: tc.cookie})
			}
			w := httptest.NewRecorder()
			g.serveApp(w, req)
			want := tc.status
			if tc.name == "native frame" && ui == "https://atrium.test" {
				want = 403
			}
			if w.Code != want {
				t.Fatalf("%s %s: got %d want %d", ui, tc.name, w.Code, want)
			}
			if w.Code == 200 && w.Header().Get("Content-Security-Policy") != "frame-ancestors 'self' "+ui {
				t.Fatalf("missing native ancestor restriction: %v", w.Header())
			}
		}
	}
}
