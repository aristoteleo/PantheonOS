package pantheoncli

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
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
	// Text that only contains "=" is a word: a sub-agent's task, for one.
	task := "Count the files.\nAnswer as COUNT=<number>; FILES=<names>"
	p, err = parse([]string{"run", "Worker", task, "a = b"}, strings.NewReader(""))
	if err != nil || len(p.args) != 0 || strings.Join(p.positional, "|") != "run|Worker|"+task+"|a = b" {
		t.Fatalf("words %#v args %#v %v", p.positional, p.args, err)
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

func TestACommandAnAgentRunsGoesThroughThatAgent(t *testing.T) {
	var got map[string]any
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/rpc" || r.Header.Get("Authorization") != "Bearer grant-1" {
			http.Error(w, "no", http.StatusForbidden)
			return
		}
		json.NewDecoder(r.Body).Decode(&got)
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": map[string]any{
			"success": true, "result": map[string]any{"apps": []any{map[string]any{"app": "web"}}}}})
	}))
	defer server.Close()
	testTransport = server.Client().Transport
	defer func() { testTransport = nil }()
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_ENDPOINT", server.URL+"/rpc")
	t.Setenv("PANTHEON_GRANT", "grant-1")
	t.Setenv("PANTHEON_TOKEN", "tok")
	out, errOut, code := run(t, "apps", "call", "web", "duckduckgo_search", "query=cells", "--json")
	if code != 0 || !strings.Contains(out, `"app":"web"`) {
		t.Fatalf("%d %s %s", code, out, errOut)
	}
	args := got["args"].(map[string]any)
	request := args["request"].(map[string]any)
	if got["method"] != "pantheon_command" || args["token"] != "tok" || request["op"] != "apps.call" ||
		request["params"].(map[string]any)["args"].(map[string]any)["query"] != "cells" {
		t.Fatalf("request %#v", got)
	}
	// No saved login is needed or used.
	if _, err := LoadCredentials(); err == nil {
		t.Fatal("agent mode must not create a login")
	}
	if _, _, code = run(t, "task", "notify", "done", "--wait"); code != 0 {
		t.Fatal("task notify")
	}
	params := got["args"].(map[string]any)["request"].(map[string]any)["params"].(map[string]any)
	if params["blocked_on_user"] != true || params["message"] != "done" {
		t.Fatalf("notify %#v", params)
	}
}

// call_agent, the skill tools and background_task became commands.
func TestSubAgentsSkillsAndJobsGoThroughTheAgent(t *testing.T) {
	var ops []map[string]any
	waits := 0
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		request := body["args"].(map[string]any)["request"].(map[string]any)
		ops = append(ops, request)
		result := map[string]any{"success": true}
		switch request["op"] {
		case "agent.start":
			result = map[string]any{"run_id": "agent_1"}
		case "agent.wait":
			waits++
			result = map[string]any{"run_id": "agent_1", "done": waits > 1, "answer": "42 files"}
		}
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": map[string]any{"success": true, "result": result}})
	}))
	defer server.Close()
	testTransport = server.Client().Transport
	defer func() { testTransport = nil }()
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_ENDPOINT", server.URL+"/rpc")
	t.Setenv("PANTHEON_GRANT", "grant-1")
	t.Setenv("PANTHEON_TOKEN", "tok")
	out, errOut, code := run(t, "agent", "run", "Worker", "count", "the", "files")
	if code != 0 || !strings.HasPrefix(out, "42 files\n") || !strings.Contains(out, "--resume agent_1") || waits != 2 {
		t.Fatalf("%d %q %s waits=%d", code, out, errOut, waits)
	}
	start := ops[0]["params"].(map[string]any)
	if ops[0]["op"] != "agent.start" || start["profile"] != "Worker" || start["task"] != "count the files" {
		t.Fatalf("%#v", ops[0])
	}
	if _, _, code = run(t, "skills", "view", "omics/qc", "references/x.md"); code != 0 {
		t.Fatal("skills view")
	}
	if p := ops[len(ops)-1]; p["op"] != "skills.view" || p["params"].(map[string]any)["file"] != "references/x.md" {
		t.Fatalf("%#v", p)
	}
	if _, _, code = run(t, "skills", "search", "single", "cell", "qc"); code != 0 {
		t.Fatal("skills search")
	}
	if p := ops[len(ops)-1]["params"].(map[string]any); p["action"] != "search_store" || p["options"].(map[string]any)["query"] != "single cell qc" {
		t.Fatalf("%#v", p)
	}
	if _, _, code = run(t, "jobs", "wait", "bg_1"); code != 0 {
		t.Fatal("jobs wait")
	}
	if p := ops[len(ops)-1]["params"].(map[string]any); p["action"] != "wait" || p["task_id"] != "bg_1" || p["timeout"] != float64(50) {
		t.Fatalf("%#v", p)
	}
	// Outside a conversation they say where they work.
	t.Setenv("PANTHEON_TOKEN", "")
	if _, errOut, code = run(t, "agent", "list"); code == 0 || !strings.Contains(errOut, "a command a Pantheon agent runs") {
		t.Fatal(errOut)
	}
}

// A sub-agent defined in a file, a team (by name or file, with the agent
// files it names by path), a run resumed, and the conversation's runs.
func TestSubAgentsFromFilesTeamsAndResumedRuns(t *testing.T) {
	var ops []map[string]any
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		request := body["args"].(map[string]any)["request"].(map[string]any)
		ops = append(ops, request)
		result := map[string]any{"run_id": "c1-0abc.d1.reviewer.0001", "profile": "Reviewer"}
		switch request["op"] {
		case "agent.wait":
			result = map[string]any{"run_id": "c1-0abc.d1.reviewer.0001", "done": true, "answer": "ok"}
		case "agent.runs":
			result = map[string]any{"runs": []any{map[string]any{"run_id": "c1-0abc.d1.reviewer.0001",
				"profile": "Reviewer", "status": "done", "task": "Review it."}}}
		}
		json.NewEncoder(w).Encode(map[string]any{"success": true, "result": map[string]any{"success": true, "result": result}})
	}))
	defer server.Close()
	testTransport = server.Client().Transport
	defer func() { testTransport = nil }()
	t.Setenv("PANTHEON_CLI_HOME", t.TempDir())
	t.Setenv("PANTHEON_ENDPOINT", server.URL+"/rpc")
	t.Setenv("PANTHEON_GRANT", "grant-1")
	t.Setenv("PANTHEON_TOKEN", "tok")
	dir := t.TempDir()
	os.MkdirAll(filepath.Join(dir, "agents"), 0o755)
	os.WriteFile(filepath.Join(dir, "agents", "reviewer.md"), []byte("---\nid: reviewer\nname: Reviewer\n---\nReview.\n"), 0o644)
	os.WriteFile(filepath.Join(dir, "team.md"), []byte("---\nid: t\ntype: team\nagents:\n  - lead\n  - ./agents/reviewer.md  # the reviewer\n  - worker\nlead:\n  name: Lead\n---\nLead.\n"), 0o644)
	params := func() map[string]any { return ops[len(ops)-2]["params"].(map[string]any) }

	if out, errOut, code := run(t, "agent", "run", "--profile", filepath.Join(dir, "agents", "reviewer.md"), "review", "it"); code != 0 || !strings.HasPrefix(out, "ok") {
		t.Fatalf("%d %s %s", code, out, errOut)
	}
	file := params()["profile_file"].(map[string]any)
	if params()["task"] != "review it" || !strings.Contains(file["text"].(string), "name: Reviewer") || !filepath.IsAbs(file["path"].(string)) {
		t.Fatalf("%#v", params())
	}
	if _, errOut, code := run(t, "agent", "run", "--team", filepath.Join(dir, "team.md"), "--profile", "Reviewer", "review"); code != 0 {
		t.Fatal(errOut)
	}
	team := params()["team_file"].(map[string]any)
	refs := team["refs"].(map[string]any)
	if params()["profile"] != "Reviewer" || len(refs) != 1 || !strings.Contains(refs["./agents/reviewer.md"].(string), "Review.") {
		t.Fatalf("%#v", params())
	}
	if _, errOut, code := run(t, "agent", "run", "--team", "paper_write", "write", "it"); code != 0 || params()["team"] != "paper_write" {
		t.Fatalf("%s %#v", errOut, params())
	}
	if _, errOut, code := run(t, "agent", "run", "--resume", "c1-0abc.d1.reviewer.0001", "and", "the", "tests?"); code != 0 {
		t.Fatal(errOut)
	}
	if p := params(); p["resume"] != "c1-0abc.d1.reviewer.0001" || p["task"] != "and the tests?" || p["profile"] != nil {
		t.Fatalf("%#v", p)
	}
	out, _, code := run(t, "agent", "runs")
	if code != 0 || !strings.Contains(out, "c1-0abc.d1.reviewer.0001\tReviewer\tdone\tReview it.") {
		t.Fatal(out)
	}
	if _, errOut, code := run(t, "agent", "run", "--team", filepath.Join(dir, "missing.md"), "x"); code == 0 || !strings.Contains(errOut, "missing.md") {
		t.Fatal(errOut)
	}
	if _, errOut, code := run(t, "agent", "run", "--profile", "Worker"); code == 0 || !strings.Contains(errOut, "usage") {
		t.Fatal(errOut)
	}
}

func TestATeamFilesAgentsListIsReadInEitherForm(t *testing.T) {
	if got := teamAgentEntries("---\nid: t\nagents: [lead, './a.md', b/c.md]\n---\n"); strings.Join(got, ",") != "lead,./a.md,b/c.md" {
		t.Fatal(got)
	}
	if got := teamAgentEntries("---\r\nagents:\r\n- lead\r\n\r\n- \"./x.md\"\r\nlead:\r\n  name: L\r\n---\r\n"); strings.Join(got, ",") != "lead,./x.md" {
		t.Fatal(got)
	}
	if teamAgentEntries("no frontmatter") != nil || !isFileRef("a.MD") || !isFileRef("dir/agent") || isFileRef("Worker") {
		t.Fatal("refs")
	}
}
