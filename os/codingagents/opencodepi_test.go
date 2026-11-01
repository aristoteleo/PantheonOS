package codingagents

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// The fixtures are real runs of OpenCode 1.19 and Pi 1.4 against a model
// server standing in for the platform proxy (testdata/README.md): a turn
// that runs one shell command and answers, then a turn the budget refuses.
func TestOpenCodeAndPiParse(t *testing.T) {
	for id, want := range map[string]string{
		"opencode": "session tool:done done text done",
		"pi":       "session tool:running tool:done text done",
	} {
		events := parseFixture(t, id)
		if got := types(events); got != want {
			t.Errorf("%s: %s, want %s", id, got, want)
			continue
		}
		if events[0].SessionID == "" {
			t.Errorf("%s: no session id", id)
		}
		var tool, text, done Event
		for _, e := range events {
			switch e.Type {
			case "tool":
				if e.Name != "" {
					tool = e
				}
			case "text":
				text = e
			case "done":
				done = e
			}
		}
		if tool.Name != "bash" || tool.Kind != KindShell || !strings.Contains(tool.Input, "echo hi") {
			t.Errorf("%s tool %+v", id, tool)
		}
		if text.Text != "PONG" || done.Error != "" || done.InputTokens != 220 || done.OutputTokens != 7 || done.ContextTokens != 122 {
			t.Errorf("%s: text %q, end %+v", id, text.Text, done)
		}
	}
	for _, id := range []string{"opencode", "pi"} {
		events := parseFixtureOf(t, id, id+"-budget.jsonl")
		end := events[len(events)-1]
		if end.Type != "done" || !strings.Contains(end.Error, "Budget has been exceeded") {
			t.Errorf("%s budget: %+v", id, end)
		}
	}
}

func parseFixtureOf(t *testing.T, agent, file string) []Event {
	t.Helper()
	a, _ := Lookup(agent)
	b, err := os.ReadFile(filepath.Join("testdata", file))
	if err != nil {
		t.Fatal(err)
	}
	p := a.Headless.Parser()
	var out []Event
	for _, line := range strings.Split(string(b), "\n") {
		out = append(out, p.Parse([]byte(line))...)
	}
	return out
}

func TestOpenCodeAndPiArgs(t *testing.T) {
	oc, _ := Lookup("opencode")
	got := strings.Join(oc.Headless.Args(Turn{Session: "ses_1", Model: "pantheon/openrouter/anthropic/claude-sonnet-4.6", Auto: true}), " ")
	if got != "run --format json --session ses_1 --model pantheon/openrouter/anthropic/claude-sonnet-4.6 --auto" {
		t.Errorf("opencode %s", got)
	}
	if got := strings.Join(oc.TerminalArgv("/bin/opencode", "ses_1", true), " "); got != "/bin/opencode --session ses_1 --auto" {
		t.Errorf("opencode terminal %s", got)
	}
	pi, _ := Lookup("pi")
	got = strings.Join(pi.Headless.Args(Turn{Session: "01a1", Model: "openrouter/openai/gpt-5.5", Effort: "high", Auto: true}), " ")
	if got != "-p --mode json --session 01a1 --model openrouter/openai/gpt-5.5 --thinking high" {
		t.Errorf("pi %s", got)
	}
	if got := strings.Join(pi.TerminalArgv("/bin/pi", "01a1", true), " "); got != "/bin/pi --session 01a1" {
		t.Errorf("pi terminal %s", got)
	}
	for _, c := range []Agent{oc, pi} {
		if !strings.HasPrefix(c.Install, "npm install -g ") || c.Login == nil {
			t.Errorf("%s installs with npm and signs in: %q", c.ID, c.Install)
		}
	}
}

func TestOpenCodeAndPiPlatform(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	p := Platform{Base: "https://hub.test/litellm", Key: testPlatformKey}
	all := append(slices.Clone(proxyModels), "openrouter/deepseek/deepseek-v3.2", "openrouter/openai/gpt-5.5:batch",
		"openrouter/google/gemini-3-pro-image", "openrouter/openai/text-embedding-3-small", "openrouter/meta-llama/Llama-4-Maverick")
	m := platformModelsOf("opencode", all)
	if m.Default != "openrouter/anthropic/claude-sonnet-4.6" {
		t.Errorf("default %q", m.Default)
	}
	for _, out := range []string{"openrouter/openai/gpt-5.5:batch", "anthropic/*", "openrouter/openai/gpt-5-image", "text-embedding-3-large",
		"openrouter/google/gemini-3-pro-image", "openrouter/openai/text-embedding-3-small", "claude-sonnet-4-6", "openrouter/anthropic/claude-3.7-sonnet:thinking"} {
		if slices.Contains(m.IDs, out) {
			t.Errorf("%s is listed", out)
		}
	}
	// Grouped by vendor: Anthropic, OpenAI, Google first, then the rest by name.
	want := []string{"openrouter/anthropic/claude-opus-4.8", "openrouter/anthropic/claude-sonnet-4.6", "openrouter/anthropic/claude-sonnet-4.5",
		"openrouter/anthropic/claude-haiku-4.5", "openrouter/openai/gpt-5.5", "openrouter/openai/gpt-5.3-codex", "openrouter/openai/gpt-5.1-codex-mini",
		"openrouter/openai/gpt-5-chat", "openrouter/openai/gpt-4o", "openrouter/google/gemini-3-pro", "openrouter/deepseek/deepseek-v3.2",
		"openrouter/meta-llama/Llama-4-Maverick"}
	if !slices.Equal(m.IDs, want) {
		t.Errorf("models %v", m.IDs)
	}
	if m.Labels["openrouter/google/gemini-3-pro"] != "Gemini 3 Pro" {
		t.Errorf("labels %v", m.Labels)
	}

	// OpenCode: a configuration for the process, the key named in it, not given.
	set, unset := PlatformEnv("opencode", p, m)
	var cfgText string
	for _, kv := range set {
		if v, ok := strings.CutPrefix(kv, "OPENCODE_CONFIG_CONTENT="); ok {
			cfgText = v
		}
	}
	if !slices.Contains(set, "PANTHEON_LLM_API_KEY="+testPlatformKey) || unset != nil || strings.Contains(cfgText, testPlatformKey) {
		t.Fatalf("opencode env: %d variables, unset %v", len(set), unset)
	}
	var cfg struct {
		Model      string `json:"model"`
		SmallModel string `json:"small_model"`
		Provider   map[string]struct {
			NPM     string                           `json:"npm"`
			Options map[string]string                `json:"options"`
			Models  map[string]struct{ Name string } `json:"models"`
		} `json:"provider"`
	}
	if err := json.Unmarshal([]byte(cfgText), &cfg); err != nil {
		t.Fatal(err)
	}
	prov := cfg.Provider["pantheon"]
	if cfg.Model != "pantheon/openrouter/anthropic/claude-sonnet-4.6" || cfg.SmallModel != "pantheon/openrouter/anthropic/claude-haiku-4.5" || prov.NPM != "@ai-sdk/openai-compatible" ||
		prov.Options["baseURL"] != "https://hub.test/litellm/v1" || prov.Options["apiKey"] != "{env:PANTHEON_LLM_API_KEY}" ||
		prov.Models["openrouter/openai/gpt-5.5"].Name != "GPT-5.5" || len(prov.Models) != len(m.IDs) {
		t.Errorf("opencode config %s", cfgText)
	}
	if a, err := PlatformArgs("opencode", p, m); a != nil || err != nil || PlatformModelArg("opencode", m.Default) != "pantheon/"+m.Default {
		t.Error("OpenCode names the platform's model by provider/model")
	}

	// Pi: an extension of the node's, the key read from the environment when Pi starts.
	set, _ = PlatformEnv("pi", p, m)
	if !slices.Equal(set, []string{"PANTHEON_LLM_API_KEY=" + testPlatformKey}) || PlatformModelArg("pi", "x/y") != "x/y" {
		t.Errorf("pi env %d variables", len(set))
	}
	args, err := PlatformArgs("pi", p, m)
	if err != nil || len(args) != 4 || args[0] != "-e" || args[2] != "--provider" || args[3] != "pantheon" {
		t.Fatalf("pi args %v %v", args, err)
	}
	ext, _ := os.ReadFile(args[1])
	for _, want := range []string{`pi.registerProvider("pantheon"`, `baseUrl: "https://hub.test/litellm/v1"`, "apiKey: process.env.PANTHEON_LLM_API_KEY",
		`"id":"openrouter/anthropic/claude-sonnet-4.6","name":"Claude Sonnet 4.6"`, `api: "openai-completions"`} {
		if !strings.Contains(string(ext), want) {
			t.Errorf("pi extension lacks %s:\n%s", want, ext)
		}
	}
	if strings.Contains(string(ext), testPlatformKey) || !strings.HasPrefix(args[1], Root()) {
		t.Error("the extension holds no key, and lives in the node's folder")
	}
}

func TestOpenCodeAndPiSignIn(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_DATA_HOME", "")
	t.Setenv("PI_CODING_AGENT_DIR", "")
	for _, id := range []string{"opencode", "pi"} {
		if got := signedIn(id, ""); got != nil {
			t.Errorf("%s without a login is unknown (it may take keys from elsewhere): %v", id, *got)
		}
	}
	writeFile := func(path, body string) {
		_ = os.MkdirAll(filepath.Dir(path), 0o700)
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeFile(filepath.Join(home, ".pi", "agent", "auth.json"), "{}")
	if signedIn("pi", "") != nil {
		t.Error("Pi's empty auth.json is no login")
	}
	writeFile(filepath.Join(home, ".pi", "agent", "auth.json"), `{"anthropic":{"type":"oauth"}}`)
	writeFile(filepath.Join(home, ".local", "share", "opencode", "auth.json"), `{"openai":{}}`)
	for _, id := range []string{"opencode", "pi"} {
		if got := signedIn(id, ""); got == nil || !*got || CredentialTime(id).IsZero() {
			t.Errorf("%s with a login: %v", id, got)
		}
	}
}
