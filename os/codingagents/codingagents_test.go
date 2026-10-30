package codingagents

import (
	"bufio"
	"context"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

func parseFixture(t *testing.T, id string) []Event {
	t.Helper()
	a, ok := Lookup(id)
	if !ok || a.Headless == nil {
		t.Fatalf("%s has no conversation mode", id)
	}
	f, err := os.Open(filepath.Join("testdata", id+".jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	p := a.Headless.Parser()
	var out []Event
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		out = append(out, p.Parse(sc.Bytes())...)
	}
	return out
}

func types(events []Event) string {
	var t []string
	for _, e := range events {
		s := e.Type
		if e.Type == "tool" {
			s += ":" + e.Status
		}
		t = append(t, s)
	}
	return strings.Join(t, " ")
}

// Each fixture is a real run of "cat a.txt, then reply with one word" (Gemini
// CLI's follows its documented format). Every agent's stream must come out as
// the same steps: the session, the shell call and its result, the answer and
// the end of the turn.
func TestParsersNormaliseOneTurn(t *testing.T) {
	cases := map[string]string{
		"claude": "session tool:running tool:done text done",
		"codex":  "session text tool:running tool:done text done",
		"cursor": "session thinking thinking thinking thinking thinking thinking thinking thinking thinking thinking tool:running tool:done text done",
		"gemini": "session tool:running tool:done text done",
	}
	for id, want := range cases {
		events := parseFixture(t, id)
		if got := types(events); got != want {
			t.Errorf("%s: got %q, want %q", id, got, want)
			continue
		}
		if events[0].SessionID == "" {
			t.Errorf("%s: session event has no id", id)
		}
		var call, result Event
		for _, e := range events {
			if e.Type == "tool" && e.Status == "running" {
				call = e
			}
			if e.Type == "tool" && e.Status == "done" {
				result = e
			}
		}
		if !strings.Contains(call.Input, "cat a.txt") || call.ID == "" || call.ID != result.ID {
			t.Errorf("%s: tool call %+v / result %+v", id, call, result)
		}
		if !strings.Contains(result.Output, "hello") {
			t.Errorf("%s: tool output %q", id, result.Output)
		}
		if last := events[len(events)-1]; last.Error != "" {
			t.Errorf("%s: successful turn reported %q", id, last.Error)
		}
	}
}

func TestClaudeErrorResult(t *testing.T) {
	p := (&claudeParser{})
	ev := p.Parse([]byte(`{"type":"result","subtype":"error_during_execution","is_error":true,"result":"Credit balance is too low"}`))
	if len(ev) != 1 || ev[0].Type != "done" || ev[0].Error != "Credit balance is too low" {
		t.Fatalf("got %+v", ev)
	}
}

func TestHeadlessArgs(t *testing.T) {
	claude, _ := Lookup("claude")
	got := claude.Headless.Args(Turn{Prompt: "hi", Session: "s1", Auto: true})
	want := []string{"-p", "--output-format", "stream-json", "--verbose", "--resume", "s1", "--dangerously-skip-permissions"}
	if !slices.Equal(got, want) {
		t.Errorf("claude: %v", got)
	}
	codex, _ := Lookup("codex")
	got = codex.Headless.Args(Turn{Prompt: "hi", Session: "t1", Model: "gpt-5"})
	want = []string{"exec", "resume", "t1", "--json", "--skip-git-repo-check", "-m", "gpt-5", "-"}
	if !slices.Equal(got, want) {
		t.Errorf("codex: %v", got)
	}
	cursor, _ := Lookup("cursor")
	if got := cursor.Headless.Args(Turn{Prompt: "-rf", Auto: true}); got[len(got)-1] != "-rf" || !cursor.Headless.PromptArg {
		t.Errorf("cursor carries the prompt last: %v", got)
	}
}

func TestTerminalArgv(t *testing.T) {
	codex, _ := Lookup("codex")
	got := codex.TerminalArgv("/bin/codex", "abc", true)
	want := []string{"/bin/codex", "resume", "abc", "--dangerously-bypass-approvals-and-sandbox"}
	if !slices.Equal(got, want) {
		t.Errorf("got %v", got)
	}
	goose, _ := Lookup("goose")
	if got := goose.TerminalArgv("/bin/goose", "abc", true); !slices.Equal(got, []string{"/bin/goose"}) {
		t.Errorf("an agent without resume or auto flags starts bare: %v", got)
	}
}

func TestPantheonAgentIsThisNodesCommand(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "fleet")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PANTHEON_CLI_BIN", bin)
	var found *Found
	for _, f := range detectIn(nil, "darwin") {
		if f.ID == PantheonAgent {
			found = &f
		}
	}
	if found == nil || found.Path != bin || !found.Terminal || found.Headless {
		t.Fatalf("the node's own command is the Pantheon Agent's terminal: %+v", found)
	}
	agent, _ := Lookup(PantheonAgent)
	if got := agent.TerminalArgv(bin, "chat-1", true); !slices.Equal(got, []string{bin, "agent", "chat", "--chat", "chat-1"}) {
		t.Errorf("resume reopens the chat: %v", got)
	}
}

func TestDetectFindsExecutablesOnly(t *testing.T) {
	a, b := t.TempDir(), t.TempDir()
	write := func(dir, name string, mode os.FileMode) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("#!/bin/sh\n"), mode); err != nil {
			t.Fatal(err)
		}
	}
	write(a, "claude", 0o644) // not executable: skipped
	write(b, "claude", 0o755)
	write(b, "kimi-code", 0o755) // an alias
	if err := os.Mkdir(filepath.Join(a, "codex"), 0o755); err != nil {
		t.Fatal(err)
	}
	found := detectIn([]string{a, b}, "linux")
	got := map[string]string{}
	for _, f := range found {
		got[f.ID] = f.Path
	}
	if got["claude"] != filepath.Join(b, "claude") || got["kimi"] != filepath.Join(b, "kimi-code") || got["codex"] != "" {
		t.Fatalf("found %+v", found)
	}
	for _, f := range found {
		if f.ID == "claude" && (!f.Headless || !f.Terminal) {
			t.Errorf("claude modes: %+v", f)
		}
		if f.ID == "kimi" && f.Headless {
			t.Errorf("kimi has no conversation mode: %+v", f)
		}
	}
}

func TestParseMarkedPath(t *testing.T) {
	out := []byte("motd noise\x1b[0m" + pathMarker + "/a/bin:relative:/b/bin" + pathMarker + "trailing")
	if got := parseMarkedPath(out); !slices.Equal(got, []string{"/a/bin", "/b/bin"}) {
		t.Fatalf("got %v", got)
	}
	if parseMarkedPath([]byte("no markers")) != nil {
		t.Fatal("unmarked output must give nothing")
	}
}

// The login shell's PATH comes first: a CLI installed under a directory only
// the user's rc file adds is found.
func TestLoginShellPath(t *testing.T) {
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("no /bin/sh")
	}
	home := t.TempDir()
	bin := filepath.Join(home, "tools")
	if err := os.Mkdir(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	rc := filepath.Join(home, ".profile")
	if err := os.WriteFile(rc, []byte("export PATH=\""+bin+":$PATH\"\necho welcome\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("ENV", "")
	got := probeShellPath("/bin/sh")
	if len(got) == 0 || got[0] != bin {
		t.Fatalf("login PATH %v does not start with %s", got, bin)
	}
}

func TestOneLine(t *testing.T) {
	if got := oneLine(map[string]any{"command": "ls\nrm", "x": 1}); got != "ls …" {
		t.Errorf("got %q", got)
	}
	if got := oneLine(map[string]any{"b": 1, "a": 2}); got != `{"a":2,"b":1}` {
		t.Errorf("got %q", got)
	}
}

// Every agent's turn reports its size the same way; Claude Code also reports
// its account quota and how full the context is.
func TestTurnUsageAndQuota(t *testing.T) {
	last := func(events []Event) Event { return events[len(events)-1] }
	claude := last(parseFixture(t, "claude"))
	if claude.InputTokens == 0 || claude.OutputTokens == 0 || claude.CachedTokens == 0 ||
		claude.ContextWindow != 1_000_000 || claude.ContextTokens == 0 || claude.DurationMs == 0 || claude.CostUSD == 0 {
		t.Errorf("claude done %+v", claude)
	}
	codex := last(parseFixture(t, "codex"))
	if codex.InputTokens != 36303 || codex.CachedTokens != 31616 || codex.OutputTokens != 61 {
		t.Errorf("codex done %+v", codex)
	}
	cursor := last(parseFixture(t, "cursor"))
	if cursor.OutputTokens != 178 || cursor.DurationMs != 5373 || cursor.InputTokens < cursor.CachedTokens {
		t.Errorf("cursor done %+v", cursor)
	}
	ev := (&claudeParser{}).Parse([]byte(`{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{` +
		`"five_hour":{"utilization":0.09,"resetsAt":1793239800},"seven_day":{"utilization":0.65,"resetsAt":1793523600}}}}`))
	if len(ev) != 1 || ev[0].Type != "limits" || ev[0].Limits["five_hour"].Used != 0.09 || ev[0].Limits["seven_day"].ResetsAt != 1793523600 {
		t.Fatalf("limits %+v", ev)
	}
}

func TestCleanEnvKeepsConfigurationDropsSessionMarkers(t *testing.T) {
	got := CleanEnv([]string{"HOME=/h", "CLAUDECODE=1", "CLAUDE_CODE_CHILD_SESSION=1", "CLAUDE_CONFIG_DIR=/c",
		"CLAUDE_CODE_USE_BEDROCK=1", "CODEX_THREAD_ID=x"})
	if !slices.Equal(got, []string{"HOME=/h", "CLAUDE_CONFIG_DIR=/c", "CLAUDE_CODE_USE_BEDROCK=1"}) {
		t.Fatalf("got %v", got)
	}
}

func TestUnwrapShell(t *testing.T) {
	for in, want := range map[string]string{
		`/bin/zsh -lc 'sleep 60; echo done'`:   "sleep 60; echo done",
		`/bin/bash -c 'echo '\''hi'\'''`:       "echo 'hi'",
		`bash -lc ls`:                          "ls",
		`/bin/zsh -lc "rg 'a b' -m 3 \"\$x\""`: `rg 'a b' -m 3 "$x"`,
		`python -c 'print(1)'`:                 `python -c 'print(1)'`,
	} {
		if got := unwrapShell(in); got != want {
			t.Errorf("%s: got %q, want %q", in, got, want)
		}
	}
}

func TestApplySettings(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "my-codex"), []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	found := []Found{{ID: "claude", Label: "Claude Code", Path: "/x/claude", Terminal: true, Headless: true}}
	s := Settings{Agents: map[string]AgentSettings{
		"claude": {Hidden: true}, "codex": {Command: "my-codex"}, "gemini": {Command: "/no/gemini"}},
		Custom: []CustomAgent{{ID: "mine", Label: "Mine", Command: "mine --tui"}}}
	got := map[string]Found{}
	for _, f := range applyIn(found, s, []string{dir}, "linux") {
		got[f.ID] = f
	}
	if !got["claude"].Hidden || got["codex"].Path != filepath.Join(dir, "my-codex") || !got["codex"].Headless ||
		!got["gemini"].Missing || !got["mine"].Custom || got["mine"].Path != "mine --tui" {
		t.Fatalf("%+v", got)
	}
	if err := s.Validate(); err != nil {
		t.Fatal(err)
	}
	bad := Settings{Custom: []CustomAgent{{ID: "claude", Label: "x", Command: "y"}}}
	if bad.Validate() == nil {
		t.Fatal("a custom agent may not take a catalog id")
	}
}

func TestClaudeSessions(t *testing.T) {
	root := t.TempDir()
	project := filepath.Join(root, "-work-repo")
	if err := os.MkdirAll(project, 0o755); err != nil {
		t.Fatal(err)
	}
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(project, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("s1.jsonl", `{"type":"user","cwd":"/work/repo","gitBranch":"main","timestamp":"2026-10-28T10:00:00Z","message":{"role":"user","content":"<command-name>/clear</command-name>"}}
{"type":"user","cwd":"/work/repo","gitBranch":"main","timestamp":"2026-10-28T10:00:01Z","message":{"role":"user","content":[{"type":"text","text":"Fix the parser\nplease"}]}}
{"type":"ai-title","aiTitle":"Parser fix"}
`)
	write("s2.jsonl", `{"type":"summary","summary":"nothing"}`+"\n")
	write("agent-1.jsonl", `{"type":"user","message":{"content":"sub"}}`+"\n")
	got := claudeSessionsIn(root, 10)
	if len(got) != 1 || got[0].ID != "s1" || got[0].Title != "Parser fix" || got[0].Preview != "Fix the parser …" ||
		got[0].Cwd != "/work/repo" || got[0].Branch != "main" || got[0].CreatedAt == 0 {
		t.Fatalf("%+v", got)
	}
}

// Codex answers through its app-server; a fake one stands in.
func TestCodexAppServer(t *testing.T) {
	dir := t.TempDir()
	codex := filepath.Join(dir, "codex")
	script := `#!/bin/sh
while read -r line; do
  case "$line" in
    *'"id":2'*rateLimits*) printf '%s\n' '{"id":2,"result":{"rateLimits":{"primary":{"usedPercent":20,"windowDurationMins":10080,"resetsAt":9},"secondary":{"usedPercent":5,"windowDurationMins":300,"resetsAt":3},"planType":"pro"}}}' ;;
    *'"id":2'*thread/list*) printf '%s\n' '{"id":2,"result":{"data":[{"id":"th1","name":"","preview":"Check NAS\nrights","cwd":"/srv","model":"gpt-x","source":"cli","createdAt":1,"updatedAt":2,"gitInfo":{"branch":"dev"}}]}}' ;;
    *'"id":2'*skills/list*) printf '%s\n' '{"id":2,"result":{"data":[{"cwd":"/w","errors":[],"skills":[{"name":"night-sky","description":"Stars","path":"/w/.agents/skills/night-sky/SKILL.md","scope":"repo","enabled":true,"pluginId":null},{"name":"pdf:pdf","description":"PDFs","path":"/p/SKILL.md","scope":"user","enabled":true,"pluginId":"pdf@acme"},{"name":"off","description":"","path":"/o/SKILL.md","scope":"user","enabled":false,"pluginId":null}]}]}}' ;;
    *'"id":1'*) printf '%s\n' '{"id":1,"result":{}}' ;;
  esac
done
`
	if err := os.WriteFile(codex, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
	defer cancel()
	q, err := CodexQuota(ctx, codex, os.Environ())
	if err != nil || q.Plan != "pro" || q.Limits["seven_day"].Used != 0.2 || q.Limits["five_hour"].ResetsAt != 3 {
		t.Fatalf("%+v %v", q, err)
	}
	s, err := CodexSessions(ctx, codex, os.Environ(), 5)
	if err != nil || len(s) != 1 || s[0].Title != "Check NAS …" || s[0].Branch != "dev" || s[0].Agent != "codex" {
		t.Fatalf("%+v %v", s, err)
	}
	sk, err := CodexSkills(ctx, codex, os.Environ(), "/w")
	if err != nil || len(sk) != 2 || sk[0].Scope != "repo" || sk[1].Plugin != "pdf" {
		t.Fatalf("%+v %v", sk, err)
	}
}

// A Codex that never answers, behind a launcher whose child keeps its
// output open, still gives up at the deadline.
func TestCodexAppServerDeadline(t *testing.T) {
	codex := filepath.Join(t.TempDir(), "codex")
	if err := os.WriteFile(codex, []byte("#!/bin/sh\nsleep 30 &\nsleep 30\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(t.Context(), time.Second)
	defer cancel()
	start := time.Now()
	_, err := CodexSkills(ctx, codex, os.Environ(), "/w")
	if err == nil || time.Since(start) > 5*time.Second {
		t.Fatalf("after %v: %v", time.Since(start), err)
	}
}

func TestHookStates(t *testing.T) {
	cases := []struct {
		agent, event string
		payload      map[string]any
		want         string
	}{
		{"claude", "PreToolUse", map[string]any{"tool_name": "AskUserQuestion"}, "waiting"},
		{"claude", "PreToolUse", map[string]any{"tool_name": "Bash"}, "working"},
		{"claude", "Notification", map[string]any{"notification_type": "permission_prompt"}, "waiting"},
		{"claude", "Notification", map[string]any{"notification_type": "auth_success"}, ""},
		{"codex", "Notify", map[string]any{"type": "agent-turn-complete"}, "ready"},
	}
	for _, c := range cases {
		if got := HookState(c.agent, c.event, c.payload); got != c.want {
			t.Errorf("%s %s: got %q want %q", c.agent, c.event, got, c.want)
		}
	}
	if l := StatusLineLimits(map[string]any{"rate_limits": map[string]any{"seven_day": map[string]any{"utilization": 0.3}}}); l["seven_day"].Used != 0.3 {
		t.Errorf("%+v", l)
	}
}
