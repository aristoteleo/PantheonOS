//go:build !windows

package codingagentsapp

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

const platformKey = "sk-platform-test-0123456789"

// platformNode gives this process a hosted workspace's model service, a
// proxy listing a few models, and fake Claude Code and Codex that record
// their arguments and environment. The user has keys of their own too.
func platformNode(t *testing.T, claudeBody, codexBody string) (dir, base string) {
	t.Helper()
	return platformAgents(t, map[string]string{"claude": claudeBody, "codex": codexBody})
}

// platformAgents is platformNode with the fake agents named.
func platformAgents(t *testing.T, bodies map[string]string) (dir, base string) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+platformKey {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"data":[{"id":"openrouter/anthropic/claude-sonnet-4.6"},{"id":"openrouter/anthropic/claude-haiku-4.5"},` +
			`{"id":"openrouter/openai/gpt-5.5"},{"id":"openrouter/openai/gpt-5.3-codex"},{"id":"openrouter/openai/gpt-5-image"}]}`))
	}))
	t.Cleanup(srv.Close)
	dir = t.TempDir()
	for name, body := range bodies {
		script := "#!/bin/sh\necho \"ARGS $*\" >> \"$(dirname \"$0\")/" + name + "-calls.txt\"\ncat > /dev/null\n" +
			"env > \"$(dirname \"$0\")/" + name + "-env.txt\"\n" + body
		if err := os.WriteFile(filepath.Join(dir, name), []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("HOME", t.TempDir())
	t.Setenv("CLAUDE_CONFIG_DIR", "")
	t.Setenv("CODEX_HOME", "")
	t.Setenv("SHELL", "/bin/sh")
	t.Setenv("PATH", dir+":/usr/bin:/bin")
	t.Setenv("PANTHEON_WORKSPACE_ENV", "")
	t.Setenv("LLM_API_BASE", srv.URL+"/v1")
	t.Setenv("LLM_API_KEY", platformKey)
	t.Setenv("ANTHROPIC_API_KEY", "sk-ant-users-own")
	t.Setenv("OPENAI_API_KEY", "sk-users-own")
	codingagents.Detect(true)
	return dir, srv.URL
}

func setSource(t *testing.T, a *App, sources map[string]string) {
	t.Helper()
	agents := map[string]codingagents.AgentSettings{}
	for id, s := range sources {
		agents[id] = codingagents.AgentSettings{ModelSource: s}
	}
	if r := a.configure(Configure{Agents: agents}); r["success"] != true {
		t.Fatal(r)
	}
}

func readText(t *testing.T, path string) string {
	t.Helper()
	b, _ := os.ReadFile(path)
	return string(b)
}

func agentInfo(t *testing.T, a *App, id string) AgentInfo {
	t.Helper()
	for _, i := range a.detect(false)["agents"].([]AgentInfo) {
		if i.ID == id {
			return i
		}
	}
	t.Fatalf("%s not detected", id)
	return AgentInfo{}
}

func TestTurnsOnPlatformModels(t *testing.T) {
	dir, base := platformNode(t, claudeTurn, `echo '{"type":"thread.started","thread_id":"th-1"}'
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
`)
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	setSource(t, a, map[string]string{"claude": codingagents.SourcePantheon, "codex": codingagents.SourcePantheon})

	id := a.send(SendArgs{Agent: "claude", Prompt: "hi", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	env := readText(t, filepath.Join(dir, "claude-env.txt"))
	for _, want := range []string{"ANTHROPIC_BASE_URL=" + base + "\n", "ANTHROPIC_AUTH_TOKEN=" + platformKey,
		"ANTHROPIC_MODEL=openrouter/anthropic/claude-sonnet-4.6", "ANTHROPIC_DEFAULT_HAIKU_MODEL=openrouter/anthropic/claude-haiku-4.5"} {
		if !strings.Contains(env, want) {
			t.Errorf("claude env lacks %q", want)
		}
	}
	if strings.Contains(env, "ANTHROPIC_API_KEY") {
		t.Error("the user's own key would override the platform")
	}
	calls := readText(t, filepath.Join(dir, "claude-calls.txt"))
	if !strings.Contains(calls, "--model openrouter/anthropic/claude-sonnet-4.6") || strings.Contains(calls, platformKey) {
		t.Errorf("claude args %s", calls)
	}

	// Codex, with a model chosen by its subscription's name: the platform's is taken.
	id = a.send(SendArgs{Agent: "codex", Prompt: "hi", Model: "gpt-5.3-codex", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	env = readText(t, filepath.Join(dir, "codex-env.txt"))
	if !strings.Contains(env, "PANTHEON_LLM_API_KEY="+platformKey) || strings.Contains(env, "OPENAI_API_KEY") {
		t.Errorf("codex env: key %v, own key %v", strings.Contains(env, "PANTHEON_LLM_API_KEY="), strings.Contains(env, "OPENAI_API_KEY"))
	}
	calls = readText(t, filepath.Join(dir, "codex-calls.txt"))
	for _, want := range []string{`-m openrouter/openai/gpt-5.3-codex`, `-c model_provider="pantheon"`,
		`model_providers.pantheon={name="PantheonOS",base_url="` + base + `/v1",env_key="PANTHEON_LLM_API_KEY",wire_api="responses"}`} {
		if !strings.Contains(calls, want) {
			t.Errorf("codex args lack %s:\n%s", want, calls)
		}
	}
	if strings.Contains(calls, platformKey) {
		t.Error("the key is in codex's arguments")
	}
	// The key is in no record or answer.
	for _, f := range []string{"conversations.json", id + ".jsonl"} {
		if strings.Contains(readText(t, filepath.Join(a.root, f)), platformKey) {
			t.Errorf("%s holds the key", f)
		}
	}
	answers, _ := json.Marshal([]any{a.detect(false), a.getSettings(), a.conversation(id, 0)})
	if strings.Contains(string(answers), platformKey) {
		t.Error("an answer holds the key")
	}

	// Back on the subscription: none of the platform's settings, and the
	// platform's model is none of the account's.
	setSource(t, a, map[string]string{"codex": codingagents.SourceSubscription})
	_ = os.Remove(filepath.Join(dir, "codex-calls.txt"))
	a.send(SendArgs{ConversationID: id, Prompt: "again", Auto: true})
	waitIdle(t, a, id)
	env = readText(t, filepath.Join(dir, "codex-env.txt"))
	calls = readText(t, filepath.Join(dir, "codex-calls.txt"))
	if strings.Contains(env, "PANTHEON_LLM_API_KEY") || !strings.Contains(env, "OPENAI_API_KEY=sk-users-own") ||
		strings.Contains(calls, "model_provider") || strings.Contains(calls, "openrouter/") {
		t.Errorf("subscription turn: %s", calls)
	}
}

func TestDetectionOnPlatformModels(t *testing.T) {
	platformNode(t, "", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	if got := agentInfo(t, a, "claude"); got.ModelSource != codingagents.SourceSubscription || got.Canvas.ModelSource != "" {
		t.Errorf("the default is the subscription: %+v", got)
	}
	if got := agentInfo(t, a, "gemini"); got.ModelSource != "" {
		t.Errorf("gemini has no model source: %q", got.ModelSource)
	}
	setSource(t, a, map[string]string{"claude": codingagents.SourcePantheon})
	got := agentInfo(t, a, "claude")
	if got.SignedIn == nil || !*got.SignedIn || got.Plan != "PantheonOS models" || got.ModelSource != codingagents.SourcePantheon {
		t.Errorf("ready on the platform without a sign-in: %+v", got.Status)
	}
	if c := got.Canvas; c.ModelSource != codingagents.SourcePantheon || c.DefaultModel != "openrouter/anthropic/claude-sonnet-4.6" ||
		len(c.Models) != 2 || c.ModelLabels["openrouter/anthropic/claude-haiku-4.5"] != "Claude Haiku 4.5" {
		t.Errorf("canvas %+v", c)
	}
	if pm := a.detect(false)["platform_models"].(map[string]any); pm["available"] != true || pm["note"] != nil {
		t.Errorf("platform_models %v", pm)
	}

	// A computer without a platform key: not ready, and what to do next; no run starts.
	t.Setenv("LLM_API_KEY", "")
	got = agentInfo(t, a, "claude")
	if got.SignedIn == nil || *got.SignedIn || got.SignInNote != codingagents.PlatformMissing {
		t.Errorf("without a key %+v %q", got.Status, got.SignInNote)
	}
	if pm := a.detect(false)["platform_models"].(map[string]any); pm["available"] != false || pm["note"] != codingagents.PlatformMissing {
		t.Errorf("platform_models %v", pm)
	}
	r := a.send(SendArgs{Agent: "claude", Prompt: "hi"})
	if r["success"] != false || !strings.Contains(fmt.Sprint(r["error"]), codingagents.PlatformMissing) || len(a.list("", "")["conversations"].([]*Conversation)) != 0 {
		t.Errorf("send without a key: %v", r)
	}
	if r := a.terminalCommand(TerminalArgs{Agent: "claude"}); r["success"] != false {
		t.Errorf("terminal without a key: %v", r)
	}
}

func TestPlatformKeyFromTheDesktop(t *testing.T) {
	_, base := platformNode(t, "", "")
	t.Setenv("LLM_API_KEY", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	setSource(t, a, map[string]string{"claude": codingagents.SourcePantheon})
	if pm := a.detect(false)["platform_models"].(map[string]any); pm["available"] != false || pm["connect"] != true {
		t.Fatalf("platform_models %v", pm)
	}
	if r := a.setPlatform("file:///etc", platformKey); r["success"] != false {
		t.Errorf("not a model service: %v", r)
	}
	r := a.setPlatform(base+"/", platformKey)
	if r["success"] != true || strings.Contains(fmt.Sprint(r), platformKey) {
		t.Fatalf("set: %v", r)
	}
	got := agentInfo(t, a, "claude")
	if got.SignedIn == nil || !*got.SignedIn || got.Canvas.DefaultModel != "openrouter/anthropic/claude-sonnet-4.6" {
		t.Errorf("ready with the handed key: %+v %+v", got.Status, got.Canvas)
	}
	if strings.Contains(fmt.Sprint(a.detect(false)), platformKey) {
		t.Error("detection shows the key")
	}
	if r := a.setPlatform("", ""); r["success"] != true {
		t.Fatalf("clear: %v", r)
	}
	if pm := a.detect(false)["platform_models"].(map[string]any); pm["available"] != false {
		t.Errorf("cleared, still %v", pm)
	}
}

func TestTerminalOnPlatformModels(t *testing.T) {
	platformNode(t, "", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	setSource(t, a, map[string]string{"claude": codingagents.SourcePantheon, "codex": codingagents.SourcePantheon})
	for agent, want := range map[string][]string{
		"claude": {"unset ANTHROPIC_API_KEY ", "--model openrouter/anthropic/claude-sonnet-4.6"},
		"codex":  {"unset OPENAI_API_KEY ", "model_provider=", "-m openrouter/openai/gpt-5.3-codex"},
	} {
		res := a.terminalCommand(TerminalArgs{Agent: agent, Auto: true, Cwd: "/w"})
		cmd, _ := res["command"].(string)
		for _, w := range want {
			if !strings.Contains(cmd, w) {
				t.Errorf("%s command lacks %q:\n%s", agent, w, cmd)
			}
		}
		if strings.Contains(cmd, platformKey) || strings.Contains(fmt.Sprint(res["argv"]), platformKey) {
			t.Errorf("%s: the key is in the command line", agent)
		}
		envFile := strings.Trim(strings.Fields(cmd)[5], "';")
		if env := readText(t, envFile); !strings.Contains(env, platformKey) {
			t.Errorf("%s: the env file (the user's only) carries the key", agent)
		}
	}
	setSource(t, a, map[string]string{})
	cmd, _ := a.terminalCommand(TerminalArgs{Agent: "claude"})["command"].(string)
	if strings.Contains(cmd, "unset ") || strings.Contains(cmd, "openrouter/") {
		t.Errorf("subscription terminal: %s", cmd)
	}
}

func TestPlatformTurnErrors(t *testing.T) {
	platformNode(t, `echo '{"type":"result","subtype":"success","is_error":true,"result":"API Error: 400 Budget has been exceeded! Current cost: 20.1, Max budget: 20.0"}'
`, `echo '{"type":"turn.failed","error":{"message":"{\"error\": {\"message\": \"openrouter/openai/gpt-x is not a valid model ID\"}}"}}'
`)
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	setSource(t, a, map[string]string{"claude": codingagents.SourcePantheon, "codex": codingagents.SourcePantheon})
	id := a.send(SendArgs{Agent: "claude", Prompt: "hi"})["conversation_id"].(string)
	if c := waitIdle(t, a, id); c["last_error"] != "PantheonOS budget exhausted" {
		t.Errorf("budget: %v", c["last_error"])
	}
	id = a.send(SendArgs{Agent: "codex", Prompt: "hi"})["conversation_id"].(string)
	if c := waitIdle(t, a, id); c["last_error"] != "PantheonOS models: openrouter/openai/gpt-x is not a valid model ID" {
		t.Errorf("proxy error: %v", c["last_error"])
	}
	// A platform error says nothing of the agent's own sign-in.
	if got := agentInfo(t, a, "codex"); got.SignInNote != "" || got.SignedIn == nil || !*got.SignedIn {
		t.Errorf("codex %+v %q", got.Status, got.SignInNote)
	}
}

func TestPlatformErrorText(t *testing.T) {
	for in, want := range map[string]string{
		"ExceededBudget: User=abc over budget. Spend=20.1, Budget=20.0":   "PantheonOS budget exhausted",
		"API Error: 401 Authentication Error, Invalid proxy server token": "PantheonOS models: Authentication Error, Invalid proxy server token",
		"": "fallback",
	} {
		if got := platformError(in, "fallback"); got != want {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
}

func TestOpenCodeAndPiOnPlatformModels(t *testing.T) {
	dir, base := platformAgents(t, map[string]string{
		"opencode": `echo '{"type":"step_start","sessionID":"ses_1","part":{"type":"step-start"}}'
echo '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"PONG"}}'
`,
		"pi": `echo '{"type":"session","version":3,"id":"01a1"}'
echo '{"type":"agent_end","messages":[]}'
`})
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	setSource(t, a, map[string]string{"opencode": codingagents.SourcePantheon, "pi": codingagents.SourcePantheon})
	if got := agentInfo(t, a, "pi"); got.SignedIn == nil || !*got.SignedIn || got.Canvas.DefaultModel != "openrouter/anthropic/claude-sonnet-4.6" ||
		len(got.Canvas.Models) != 4 {
		t.Errorf("pi on the platform: %+v %+v", got.Status, got.Canvas)
	}
	for _, agent := range []string{"opencode", "pi"} {
		id := a.send(SendArgs{Agent: agent, Prompt: "hi", Auto: true})["conversation_id"].(string)
		waitIdle(t, a, id)
		a.send(SendArgs{ConversationID: id, Prompt: "again", Auto: true})
		waitIdle(t, a, id)
		var calls []string
		for _, l := range strings.Split(strings.TrimSpace(readText(t, filepath.Join(dir, agent+"-calls.txt"))), "\n") {
			if l != "ARGS --version" {
				calls = append(calls, l)
			}
		}
		env := readText(t, filepath.Join(dir, agent+"-env.txt"))
		if len(calls) != 2 || strings.Contains(strings.Join(calls, "\n"), platformKey) || !strings.Contains(env, "PANTHEON_LLM_API_KEY="+platformKey) {
			t.Fatalf("%s calls %v", agent, calls)
		}
		switch agent {
		case "opencode":
			if !strings.Contains(calls[0], "run --format json --model pantheon/openrouter/anthropic/claude-sonnet-4.6 --auto") ||
				!strings.Contains(calls[1], "--session ses_1") {
				t.Errorf("opencode args %v", calls)
			}
			if !strings.Contains(env, `OPENCODE_CONFIG_CONTENT={"model":"pantheon/openrouter/anthropic/claude-sonnet-4.6"`) ||
				!strings.Contains(env, `"baseURL":"`+base+`/v1"`) {
				t.Errorf("opencode config not in its environment")
			}
		case "pi":
			if !strings.Contains(calls[0], "-p --mode json --model openrouter/anthropic/claude-sonnet-4.6 -e ") ||
				!strings.Contains(calls[0], " --provider pantheon") || !strings.Contains(calls[1], "--session 01a1") {
				t.Errorf("pi args %v", calls)
			}
		}
	}
	// On their own sign-in, nothing of the platform's.
	setSource(t, a, map[string]string{})
	_ = os.Remove(filepath.Join(dir, "pi-calls.txt"))
	id := a.send(SendArgs{Agent: "pi", Prompt: "hi"})["conversation_id"].(string)
	waitIdle(t, a, id)
	if calls, env := readText(t, filepath.Join(dir, "pi-calls.txt")), readText(t, filepath.Join(dir, "pi-env.txt")); strings.Contains(calls, "pantheon") ||
		strings.Contains(env, "PANTHEON_LLM_API_KEY") {
		t.Errorf("pi on its own sign-in: %s", calls)
	}
	cmd, _ := a.terminalCommand(TerminalArgs{Agent: "opencode", Auto: true})["command"].(string)
	if !strings.Contains(cmd, "/opencode --auto") || strings.Contains(cmd, "pantheon/") {
		t.Errorf("opencode terminal %s", cmd)
	}
}

// Pi and OpenCode list the user's Model Services routes, and run one through
// the node's route gateway: the launch has a token of its own, not the
// platform's key (the pantheon command's stays, as on any launch).
func TestPiOnARoute(t *testing.T) {
	dir, _ := platformAgents(t, map[string]string{"pi": `echo '{"type":"session","version":3,"id":"01a1"}'
echo '{"type":"agent_end","messages":[]}'
`})
	hub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer pbk_route-test" {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		_, _ = w.Write([]byte(`{"routes":[{"route_id":"tier-normal","name":"Normal","requires":{"operation":"text"},` +
			`"candidates":[{"deployment_id":"platform","model_id":"openrouter/openai/gpt-5.5"}]}]}`))
	}))
	t.Cleanup(hub.Close)
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	setSource(t, a, map[string]string{"pi": codingagents.SourcePantheon})
	if r := a.setAccess("pbk_route-test", 9, hub.URL, false); r["success"] != true {
		t.Fatal(r)
	}
	if got := agentInfo(t, a, "pi").Canvas.Routes; len(got) != 1 || got[0].Ref != "fleet-route://tier-normal" || got[0].Name != "Normal" {
		t.Fatalf("routes %+v", got)
	}
	id := a.send(SendArgs{Agent: "pi", Prompt: "hi", Model: "fleet-route://tier-normal", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	calls, env := readText(t, filepath.Join(dir, "pi-calls.txt")), readText(t, filepath.Join(dir, "pi-env.txt"))
	if !strings.Contains(calls, "--model tier-normal -e ") || !strings.Contains(calls, "pi-route-tier-normal.js --provider pantheon-routes") ||
		strings.Contains(calls, "--provider pantheon ") || strings.Contains(calls, platformKey) || strings.Contains(env, "PANTHEON_LLM_API_KEY="+platformKey) || strings.Contains(calls, "pbk_route-test") ||
		!strings.Contains(env, "PANTHEON_ROUTE_TOKEN=prt_") {
		t.Errorf("pi on a route: %s\n%s", calls, env)
	}
	// The terminal too, from the agent's default model.
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{"pi": {ModelSource: codingagents.SourcePantheon,
		Model: "fleet-route://tier-normal"}}}); r["success"] != true {
		t.Fatal(r)
	}
	cmd, _ := a.terminalCommand(TerminalArgs{Agent: "pi", Auto: true})["command"].(string)
	if !strings.Contains(cmd, "--provider pantheon-routes") || !strings.Contains(cmd, "--model tier-normal") {
		t.Errorf("pi terminal %s", cmd)
	}
}
