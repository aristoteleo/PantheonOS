package pantheoncli

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/url"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestArgumentsAreJSONWhenTheyAreJSON(t *testing.T) {
	p, err := parse([]string{"n=3", "name=cells", "on=true", `opts={"a":1}`, "--node", "mac", "--window",
		"--args", `{"extra":"x"}`, "--", "ls", "-la"}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	if p.args["n"] != float64(3) || p.args["name"] != "cells" || p.args["on"] != true || p.args["extra"] != "x" {
		t.Fatalf("args %#v", p.args)
	}
	if p.flags["node"] != "mac" || !p.bools["window"] || strings.Join(p.rest, " ") != "ls -la" {
		t.Fatalf("flags %#v bools %#v rest %#v", p.flags, p.bools, p.rest)
	}
	p, err = parse([]string{"-"}, strings.NewReader(`{"code":"print(1)\n"}`))
	if err != nil || p.args["code"] != "print(1)\n" {
		t.Fatalf("stdin args %#v %v", p.args, err)
	}
	if _, err := parse([]string{"--node"}, strings.NewReader("")); err == nil {
		t.Fatal("a value flag without a value is refused")
	}
}

func TestOnlyExactHTTPSOrigins(t *testing.T) {
	for _, ok := range []string{"https://hub.example.org", "https://hub.example.org/", "http://127.0.0.1:8000"} {
		if _, err := CheckOrigin(ok); err != nil {
			t.Fatalf("%s: %v", ok, err)
		}
	}
	for _, bad := range []string{"http://hub.example.org", "https://u:p@hub.example.org", "https://hub.example.org/api", "hub"} {
		if _, err := CheckOrigin(bad); err == nil {
			t.Fatalf("%s accepted", bad)
		}
	}
}

func TestEnvironmentKeyOverridesTheSavedLogin(t *testing.T) {
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_KEY", "")
	if _, err := LoadCredentials(); err == nil {
		t.Fatal("not signed in yet")
	}
	if err := SaveCredentials(&Credentials{Hub: "https://hub.example.org", Key: "pbk_saved", FleetID: "f_1"}); err != nil {
		t.Fatal(err)
	}
	c, err := LoadCredentials()
	if err != nil || c.Key != "pbk_saved" || c.FleetID != "f_1" {
		t.Fatalf("%#v %v", c, err)
	}
	t.Setenv("PANTHEON_KEY", "pbk_env")
	c, _ = LoadCredentials()
	if c.Key != "pbk_env" || c.FleetID != "" || c.Hub != "https://hub.example.org" {
		t.Fatalf("%#v", c)
	}
}

// fakePlatform is the Hub, the controller and two App instances in one server.
type fakePlatform struct {
	server   *httptest.Server
	connects atomic.Int32
	refuse   atomic.Bool // the next call with the Desktop's grant is refused once
	calls    []map[string]any
}

func newFakePlatform(t *testing.T) *fakePlatform {
	f := &fakePlatform{}
	mux := http.NewServeMux()
	auth := func(w http.ResponseWriter, r *http.Request, want string) bool {
		if r.Header.Get("Authorization") != "Bearer "+want {
			http.Error(w, `{"detail":"bad credential"}`, http.StatusUnauthorized)
			return false
		}
		return true
	}
	mux.HandleFunc("GET /api/fleet/apps/workload-identity", func(w http.ResponseWriter, r *http.Request) {
		if auth(w, r, "pbk_test") {
			json.NewEncoder(w).Encode(map[string]any{"fleet_id": "f_test", "controller_url": f.server.URL})
		}
	})
	mux.HandleFunc("GET /deployments/general-team", func(w http.ResponseWriter, r *http.Request) {
		if !auth(w, r, "pbk_test") {
			return
		}
		app := func(node, instance string) map[string]any {
			return map[string]any{"state": "ready", "node_id": node, "instance_id": instance,
				"revision": strings.Repeat("a", 64), "generation": 2}
		}
		json.NewEncoder(w).Encode(map[string]any{"revision": 3, "status": map[string]any{"apps": map[string]any{
			"desktop": app("n_ws", "desk"), "fleet": app("controller", "flt"), "files": app("n_ws", "fil")}}})
	})
	mux.HandleFunc("POST /api/fleet/apps/workload-connect", func(w http.ResponseWriter, r *http.Request) {
		if !auth(w, r, "pbk_test") {
			return
		}
		var b Binding
		json.NewDecoder(r.Body).Decode(&b)
		f.connects.Add(1)
		json.NewEncoder(w).Encode(map[string]any{"origin": f.server.URL, "access_token": "grant-" + b.InstanceID,
			"expires": time.Now().Add(time.Hour).Unix()})
	})
	mux.HandleFunc("POST /rpc", func(w http.ResponseWriter, r *http.Request) {
		if f.refuse.Load() && r.Header.Get("Authorization") == "Bearer grant-desk" {
			f.refuse.Store(false)
			http.Error(w, "unknown grant", http.StatusUnauthorized)
			return
		}
		var call map[string]any
		json.NewDecoder(r.Body).Decode(&call)
		call["instance"] = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer grant-")
		f.calls = append(f.calls, call)
		var result any = map[string]any{"success": true}
		switch call["method"] {
		case "agent_app_directory":
			result = map[string]any{"success": true, "apps": []any{map[string]any{
				"app_id": "game-of-life", "description": "Life", "window": true,
				"tools": []any{}, "actions": []any{map[string]any{"name": "step"}}}}}
		case "fleet_list_nodes":
			result = map[string]any{"nodes": []any{map[string]any{"node_id": "n_ws", "name": "workspace"}}}
		case "agent_app_call":
			result = map[string]any{"success": true, "result": "stepped"}
		}
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": result})
	})
	f.server = httptest.NewTLSServer(mux)
	t.Cleanup(f.server.Close)
	testTransport = f.server.Client().Transport
	t.Cleanup(func() { testTransport = nil })
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_KEY", "")
	t.Setenv("PANTHEON_HUB_URL", "")
	return f
}

func run(t *testing.T, args ...string) (string, string, int) {
	var out, errOut bytes.Buffer
	code := Main(args, strings.NewReader(""), &out, &errOut)
	return out.String(), errOut.String(), code
}

func TestTheCommandReachesAppsThroughTheHubAndController(t *testing.T) {
	f := newFakePlatform(t)
	if out, errOut, code := run(t, "login", "--hub", f.server.URL, "--key", "pbk_test"); code != 0 {
		t.Fatalf("login: %s %s", out, errOut)
	}
	out, errOut, code := run(t, "apps", "list", "--json")
	if code != 0 {
		t.Fatalf("list: %s", errOut)
	}
	var listed struct {
		Apps []struct{ App, Source, Node string } `json:"apps"`
	}
	json.Unmarshal([]byte(out), &listed)
	got := map[string]string{}
	for _, a := range listed.Apps {
		got[a.App] = a.Source + "@" + a.Node
	}
	if got["desktop"] != "deployment@workspace" || got["game-of-life"] != "library@" {
		t.Fatalf("listed %v", got)
	}
	// A library App by its Agent-style name, through the Desktop.
	out, errOut, code = run(t, "apps", "call", "game_of_life", "step", "n=3")
	if code != 0 || strings.TrimSpace(out) != `{
  "result": "stepped",
  "success": true
}` {
		t.Fatalf("call: %q %s", out, errOut)
	}
	last := f.calls[len(f.calls)-1]
	args := last["args"].(map[string]any)
	if last["method"] != "agent_app_call" || last["instance"] != "desk" || args["app_id"] != "game-of-life" ||
		args["args"].(map[string]any)["n"] != float64(3) {
		t.Fatalf("call %#v", last)
	}
	// A deployment App's method goes to that App.
	if _, errOut, code = run(t, "apps", "call", "file_manager", "list_files", "path=/w"); code != 0 {
		t.Fatal(errOut)
	}
	if last = f.calls[len(f.calls)-1]; last["method"] != "list_files" || last["instance"] != "fil" {
		t.Fatalf("deployment call %#v", last)
	}
	// Grants are reused across commands: one per instance.
	if n := f.connects.Load(); n != 3 {
		t.Fatalf("%d grants requested for three instances", n)
	}
	// A grant the gateway no longer knows (the controller restarted) is
	// replaced once, and the call goes through.
	f.refuse.Store(true)
	if _, errOut, code = run(t, "apps", "call", "game_of_life", "step"); code != 0 {
		t.Fatalf("after a refused grant: %s", errOut)
	}
	if n := f.connects.Load(); n != 4 {
		t.Fatalf("%d grants: the refused one is replaced once", n)
	}
	// Deployment Apps are the owner's to start and stop, in Fleet.
	if _, errOut, code = run(t, "apps", "stop", "desktop"); code == 0 || !strings.Contains(errOut, "Fleet") {
		t.Fatalf("stopping a deployment App: %d %s", code, errOut)
	}
}

func TestARevokedKeySaysToSignInAgain(t *testing.T) {
	f := newFakePlatform(t)
	_, errOut, code := run(t, "login", "--hub", f.server.URL, "--key", "pbk_revoked")
	if code == 0 || !strings.Contains(errOut, "pantheon login") {
		t.Fatalf("%d %s", code, errOut)
	}
}

func TestBrowserSignInTakesTheKeyOnlyFromItsOwnCallback(t *testing.T) {
	f := newFakePlatform(t)
	defer func(saved func(string)) { openBrowser = saved }(openBrowser)
	refused := []int{}
	openBrowser = func(page string) {
		u, _ := url.Parse(page)
		q, _ := url.ParseQuery(strings.TrimPrefix(u.Fragment, "/cli-login?"))
		base := "http://127.0.0.1:" + q.Get("port")
		post := func(origin string, form url.Values) int {
			req, _ := http.NewRequest("POST", base+"/done", strings.NewReader(form.Encode()))
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
			if origin != "" {
				req.Header.Set("Origin", origin)
			}
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				return 0
			}
			resp.Body.Close()
			return resp.StatusCode
		}
		go func() {
			if resp, err := http.Get(base + "/callback"); err == nil {
				resp.Body.Close()
			}
			refused = append(refused,
				post("", url.Values{"state": {q.Get("state")}, "key": {"pbk_test"}}),
				post(base, url.Values{"state": {"0123"}, "key": {"pbk_test"}}),
				post("https://evil.example", url.Values{"state": {q.Get("state")}, "key": {"pbk_test"}}))
			post(base, url.Values{"state": {q.Get("state")}, "key": {"pbk_test"}, "user": {"ada"}})
		}()
	}
	out, errOut, code := run(t, "login", "--hub", f.server.URL)
	if code != 0 || !strings.Contains(out, "as ada") {
		t.Fatalf("browser login: %d %s %s", code, out, errOut)
	}
	if len(refused) != 3 || refused[0] != 403 || refused[1] != 403 || refused[2] != 403 {
		t.Fatalf("posts that are not the callback page's own must be refused: %v", refused)
	}
	c, err := LoadCredentials()
	if err != nil || c.Key != "pbk_test" || c.User != "ada" {
		t.Fatalf("%#v %v", c, err)
	}
}
