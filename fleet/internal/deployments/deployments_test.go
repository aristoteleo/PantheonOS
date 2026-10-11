package deployments

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const token = "service-token-for-tests-0123456789"

func spec() Spec {
	return Spec{
		Release: Release{URL: "https://example.com/release.tar.gz", SHA256: strings.Repeat("a", 64)},
		Apps: map[string]AppSpec{
			"allocator": {Package: "allocator", Scope: "allocator", Intent: Running},
			"agent": {Package: "agent", Scope: "agent", Intent: Running,
				Bindings: map[string]Binding{"allocator": {App: "allocator", Component: "backend", Methods: json.RawMessage(`{"allocate":{"arguments":[],"bound":{}}}`)}},
				Config:   map[string]json.RawMessage{"backend": json.RawMessage(`{"key":{"$secret":"budget"}}`)}},
		},
		Secrets: []string{"budget"},
	}
}

func TestValidateRejectsInconsistentSpecs(t *testing.T) {
	cases := map[string]func(*Spec){
		"http release":   func(s *Spec) { s.Release.URL = "http://example.com/r.tar.gz" },
		"bad digest":     func(s *Spec) { s.Release.SHA256 = "x" },
		"unknown intent": func(s *Spec) { a := s.Apps["agent"]; a.Intent = "paused"; s.Apps["agent"] = a },
		"dangling binding": func(s *Spec) {
			a := s.Apps["agent"]
			a.Bindings = map[string]Binding{"x": {App: "nope"}}
			s.Apps["agent"] = a
		},
		"undeclared secret": func(s *Spec) { s.Secrets = nil },
		"same identity": func(s *Spec) {
			a := s.Apps["agent"]
			a.Package, a.Scope = "allocator", "allocator"
			s.Apps["agent"] = a
		},
		"cycle": func(s *Spec) {
			a := s.Apps["allocator"]
			a.Bindings = map[string]Binding{"agent": {App: "agent"}}
			s.Apps["allocator"] = a
		},
	}
	for name, change := range cases {
		s := spec()
		change(&s)
		if s.Validate() == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if err := spec().Validate(); err != nil {
		t.Fatal(err)
	}
	order, _ := Order(spec())
	if strings.Join(order, ",") != "allocator,agent" {
		t.Fatalf("providers come first: %v", order)
	}
}

func TestStoreIsDurableRevisionCheckedAndKeepsStatus(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "deployments")
	s, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Put("f_a", "agent", 1, spec()); err != ErrConflict {
		t.Fatalf("creating needs revision 0: %v", err)
	}
	d, err := s.Put("f_a", "agent", 0, spec())
	if err != nil || d.Revision != 1 {
		t.Fatal(d, err)
	}
	if err := s.SetStatus("f_a", "agent", 1, Status{ObservedRevision: 1, Apps: map[string]AppStatus{"agent": {NodeID: "n1", State: "ready"}}}); err != nil {
		t.Fatal(err)
	}
	if err := s.SetStatus("f_a", "agent", 2, Status{}); err != ErrConflict {
		t.Fatalf("status for a stale spec: %v", err)
	}
	d, err = s.Patch("f_a", "agent", 1, "agent", func(a *AppSpec) error { a.Intent = Stopped; return nil })
	if err != nil || d.Revision != 2 || d.Spec.Apps["agent"].Intent != Stopped || d.Status.Apps["agent"].NodeID != "n1" {
		t.Fatal(d, err)
	}
	if _, err := Open(dir); err == nil {
		t.Fatal("a second writer must not open the store")
	}
	s.Close()
	reopened, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	got, err := reopened.Get("f_a", "agent")
	if err != nil || got.Revision != 2 || got.Status.Apps["agent"].State != "ready" || len(reopened.List("f_b")) != 0 {
		t.Fatal(got, err)
	}
	reopened.Close()
	if err := os.WriteFile(filepath.Join(dir, "f_a", "agent.json"), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(dir); err == nil || !strings.Contains(err.Error(), "recovery required") {
		t.Fatalf("corruption must be fatal, got %v", err)
	}
}

func TestAPIScopesEveryRequestToTheCallersFleet(t *testing.T) {
	s, err := Open(filepath.Join(t.TempDir(), "deployments"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	mux := http.NewServeMux()
	Register(mux, s, nil, Auth{ServiceToken: token, Resolve: func(key string) (string, bool) { return "f_local", key == "owner-key" }})
	call := func(method, path, fleet, bearer string, body any) *httptest.ResponseRecorder {
		var raw []byte
		if body != nil {
			raw, _ = json.Marshal(body)
		}
		r := httptest.NewRequest(method, path, bytes.NewReader(raw))
		r.Header.Set("Authorization", "Bearer "+bearer)
		if fleet != "" {
			r.Header.Set("X-Fleet-Id", fleet)
		}
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, r)
		return w
	}
	if w := call("GET", "/deployments", "f_a", "wrong", nil); w.Code != 401 {
		t.Fatalf("bad token: %d", w.Code)
	}
	if w := call("PUT", "/deployments/agent", "f_a", token, map[string]any{"revision": 0, "spec": spec()}); w.Code != 200 {
		t.Fatalf("create: %d %s", w.Code, w.Body)
	}
	if w := call("GET", "/deployments/agent", "f_b", token, nil); w.Code != 404 {
		t.Fatalf("another fleet must not see it: %d", w.Code)
	}
	if w := call("PUT", "/deployments/agent", "f_a", token, map[string]any{"revision": 0, "spec": spec()}); w.Code != 409 {
		t.Fatalf("stale revision: %d", w.Code)
	}
	bad := spec()
	bad.Release.URL = "http://x/y"
	if w := call("PUT", "/deployments/other", "f_a", token, map[string]any{"revision": 0, "spec": bad}); w.Code != 422 {
		t.Fatalf("invalid spec: %d", w.Code)
	}
	if w := call("DELETE", "/deployments/agent", "f_a", token, map[string]any{"revision": 1}); w.Code != 409 {
		t.Fatalf("running Apps block deletion: %d", w.Code)
	}
	for i, app := range []string{"agent", "allocator"} {
		if w := call("PATCH", "/deployments/agent/apps/"+app, "f_a", token, map[string]any{"revision": 1 + i, "intent": "stopped"}); w.Code != 200 {
			t.Fatalf("stop %s: %d %s", app, w.Code, w.Body)
		}
	}
	if w := call("DELETE", "/deployments/agent", "f_a", token, map[string]any{"revision": 3}); w.Code != 204 {
		t.Fatalf("delete stopped deployment: %d %s", w.Code, w.Body)
	}
	if w := call("PUT", "/deployments/agent", "", "owner-key", map[string]any{"revision": 0, "spec": spec()}); w.Code != 200 {
		t.Fatalf("local owner key: %d %s", w.Code, w.Body)
	}
	if got, _ := s.Get("f_local", "agent"); got.Revision != 1 {
		t.Fatal("local owner writes its own fleet")
	}
}

func TestSecretsAreSealedVersionedAndNeverListedWithValues(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenSecrets(dir + "/secrets")
	if err != nil {
		t.Fatal(err)
	}
	a, err := s.Put("f_1", "budget", "sk-one", "https://hub.test/litellm/v1")
	if err != nil {
		t.Fatal(err)
	}
	if b, _ := s.Put("f_1", "budget", "sk-one", "https://hub.test/litellm/v1"); b.Version != a.Version {
		t.Fatal("the same value keeps its version (no restart)")
	}
	c, _ := s.Put("f_1", "budget", "sk-two", "https://hub.test/litellm/v1")
	if c.Version == a.Version || VaultRef("budget", c.Version) == VaultRef("budget", a.Version) {
		t.Fatal("a new value is a new version and vault reference")
	}
	raw, _ := os.ReadFile(dir + "/secrets/f_1/budget.json")
	if strings.Contains(string(raw), "sk-two") {
		t.Fatal("stored sealed")
	}
	listed, _ := json.Marshal(s.List("f_1"))
	if strings.Contains(string(listed), "sk-") {
		t.Fatal("listing never contains values")
	}
	if _, err := s.Put("f_2", "budget", "has space", "https://hub.test"); err == nil {
		t.Fatal("invalid value refused")
	}
	reopened, _ := OpenSecrets(dir + "/secrets")
	if v, _, err := reopened.Get("f_1", "budget"); err != nil || v != "sk-two" {
		t.Fatalf("unsealed after reopen: %v %q", err, v)
	}
	if _, _, err := reopened.Get("f_2", "budget"); err == nil {
		t.Fatal("another fleet cannot read it")
	}
}

func TestLateReferencesAreNotStartDependencies(t *testing.T) {
	spec := Spec{Apps: map[string]AppSpec{
		"allocator": {Package: "allocator", Scope: "a", Intent: Running, Config: map[string]json.RawMessage{
			"backend": json.RawMessage(`{"values":{"policy":{"consumer":{"$app":"agent","late":true},"files":{"$app":"files","late":true}}}}`)}},
		"agent": {Package: "agent", Scope: "b", Intent: Running, Bindings: map[string]Binding{
			"allocator": {App: "allocator", Component: "backend", Methods: json.RawMessage(`{}`)}}},
		"files": {Package: "files", Scope: "c", Intent: Running},
	}}
	units, err := Units(spec)
	if err != nil {
		t.Fatal(err)
	}
	for _, unit := range units {
		if len(unit) != 1 {
			t.Fatalf("no start unit forms through late references: %v", units)
		}
	}
	if refs := ConfigRefs(spec.Apps["allocator"]); len(refs) != 0 {
		t.Fatalf("late references are not pinned: %v", refs)
	}
}
