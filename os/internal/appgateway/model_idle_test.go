package appgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestModelIdleWorkloadAccess(t *testing.T) {
	const token = "controller-service-token-32-characters"
	b := Binding{Fleet: "alice", Node: "mac", Instance: "connector", Revision: strings.Repeat("a", 64), Generation: 2, Component: "backend", Port: "http"}
	calls := 0
	g, err := New("apps.test", token, []string{"https://atrium.test"},
		func(context.Context, Binding, string, string) error { t.Fatal("inference transport used"); return nil },
		func(_ context.Context, actual Binding) error {
			if actual != b {
				return fmt.Errorf("changed")
			}
			return nil
		})
	if err != nil {
		t.Fatal(err)
	}
	state := ModelIdleSnapshot{ID: "service", Revision: 3, Enabled: true, State: "sleeping",
		Connector:      ModelIdleBinding{b.Instance, b.Revision, b.Generation},
		Engine:         ModelIdleBinding{"engine", strings.Repeat("e", 64), 4},
		ConfigRevision: strings.Repeat("c", 64), IdleSeconds: 60, Cycle: 1}
	g.SetModelIdleDispatch(func(_ context.Context, q ModelIdleRequest) (ModelIdleSnapshot, error) {
		calls++
		if q.ID != "service" || q.Revision != 3 || q.Binding != b {
			return ModelIdleSnapshot{}, fmt.Errorf("changed")
		}
		return state, nil
	})
	mux := http.NewServeMux()
	g.Register(mux)
	q := ModelIdleRequest{Binding: b, ID: "service", Revision: 3, Action: "wake"}
	request := func(body any, credential string) *httptest.ResponseRecorder {
		data, _ := json.Marshal(body)
		r := httptest.NewRequest("POST", "/apps/model-idle", bytes.NewReader(data))
		r.Header.Set("Authorization", "Bearer "+credential)
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		return w
	}
	if request(q, "user-login").Code != 401 {
		t.Fatal("accepted user login")
	}
	wrong := q
	wrong.Fleet = "bob"
	if request(wrong, token).Code != 409 || calls != 0 {
		t.Fatal("cross-owner request reached node")
	}
	for _, action := range []string{"register", "disable", "start", "invoke"} {
		wrong = q
		wrong.Action = action
		if request(wrong, token).Code != 400 || calls != 0 {
			t.Fatal("accepted management action", action)
		}
	}
	data, _ := json.Marshal(q)
	var extra map[string]any
	_ = json.Unmarshal(data, &extra)
	extra["endpoint"] = "http://127.0.0.1/admin"
	if request(extra, token).Code != 400 {
		t.Fatal("accepted caller endpoint")
	}
	wrong = q
	wrong.Revision++
	if request(wrong, token).Code != 409 {
		t.Fatal("accepted stale policy")
	}
	for _, action := range []string{"status", "wake"} {
		q.Action = action
		w := request(q, token)
		if w.Code != 200 || w.Header().Get("Cache-Control") != "no-store" {
			t.Fatal(w.Code, w.Body.String())
		}
		if strings.Contains(w.Body.String(), "credential") || strings.Contains(w.Body.String(), "endpoint") {
			t.Fatal("node configuration leaked")
		}
	}
	state.Connector.Generation++
	if request(q, token).Code != 409 {
		t.Fatal("accepted changed connector")
	}
	state.Connector.Generation--
	state.Enabled = false
	if request(q, token).Code != 409 {
		t.Fatal("accepted disabled policy")
	}
}
