package codingagents

import (
	"bufio"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
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
		`/bin/zsh -lc 'sleep 60; echo done'`: "sleep 60; echo done",
		`/bin/bash -c 'echo '\''hi'\'''`:     "echo 'hi'",
		`bash -lc ls`:                        "ls",
		`python -c 'print(1)'`:               `python -c 'print(1)'`,
	} {
		if got := unwrapShell(in); got != want {
			t.Errorf("%s: got %q, want %q", in, got, want)
		}
	}
}
