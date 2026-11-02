//go:build !windows

package codingagentsapp

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

func terminalRecord(t *testing.T, a *App, id string) TerminalSession {
	t.Helper()
	for _, s := range a.terminalList()["terminals"].([]TerminalSession) {
		if s.ID == id {
			return s
		}
	}
	t.Fatal("terminal session gone")
	return TerminalSession{}
}

// The launch line outlives the agent: whatever ends it (an update, a crash,
// the user quitting), the service hears how it exited, and the shell the
// window drops to is told nothing secret.
func TestTerminalReportsTheAgentExit(t *testing.T) {
	if _, err := exec.LookPath("curl"); err != nil {
		t.Skip("the exit report needs curl")
	}
	fakeAgent(t, "codex", "echo \"HOOK=${PANTHEON_AGENT_HOOK_URL:+set}\"\nexit 3\n")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	res := a.terminalCommand(TerminalArgs{Agent: "codex", Resume: "th-1", Auto: true})
	id := res["terminal_id"].(string)
	cmd := res["command"].(string)
	if strings.Contains(cmd, "exec ") {
		t.Fatalf("the agent must not replace the subshell that reports it: %q", cmd)
	}
	// What the PTY runs: the line, then a shell left behind.
	out, err := exec.Command("/bin/sh", "-c", cmd+"\necho \"LEFT=${PANTHEON_AGENT_HOOK_TOKEN:-none}\"").CombinedOutput()
	if err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	if !strings.Contains(string(out), "HOOK=set") || !strings.Contains(string(out), "LEFT=none") {
		t.Fatalf("output %s", out)
	}
	deadline := time.Now().Add(5 * time.Second)
	for !terminalRecord(t, a, id).Exited && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	s := terminalRecord(t, a, id)
	if !s.Exited || s.State != "ended" || s.ExitCode == nil || *s.ExitCode != 3 || s.Detail != "exit status 3" {
		t.Fatalf("%+v", s)
	}
}

// Quitting an agent with Ctrl-C twice often sends the second one just after
// the agent let go of the terminal, to the whole foreground job: the report
// on its way must not die of it.
func TestExitReportSurvivesALateCtrlC(t *testing.T) {
	if _, err := exec.LookPath("curl"); err != nil {
		t.Skip("the exit report needs curl")
	}
	fakeAgent(t, "codex", "exit 0\n")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	var job *exec.Cmd
	// A listener that is hit by the Ctrl-C while the report is in flight, and
	// takes it only from a report still alive.
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = syscall.Kill(-job.Process.Pid, syscall.SIGINT)
		select {
		case <-r.Context().Done():
			return
		case <-time.After(300 * time.Millisecond):
		}
		var payload map[string]any
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &payload)
		parts := strings.Split(r.URL.Path, "/")
		a.onHook(parts[2], parts[3], payload)
	}))
	defer slow.Close()
	a.hooks = &hookServer{url: slow.URL, token: "tok", srv: &http.Server{}}
	res := a.terminalCommand(TerminalArgs{Agent: "codex"})
	id := res["terminal_id"].(string)
	job = exec.Command("/bin/sh", "-c", res["command"].(string))
	job.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	_ = job.Run()
	if s := terminalRecord(t, a, id); !s.Exited {
		t.Fatalf("the report died of the Ctrl-C: %+v", s)
	}
}

// Once the agent is gone its record stays ended: a hook that lands late
// (Codex's notify runs apart from the agent) changes no state, though the
// session it names is kept for resuming.
func TestExitedTerminalStaysEnded(t *testing.T) {
	fakeAgent(t, "codex", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	id := a.terminalCommand(TerminalArgs{Agent: "codex"})["terminal_id"].(string)
	a.onHook(id, "Notify", map[string]any{"type": "agent-turn-complete", "thread-id": "th-7", "input-messages": []any{"fix it"}})
	if s := terminalRecord(t, a, id); s.State != "ready" || s.Exited {
		t.Fatalf("%+v", s)
	}
	a.onHook(id, codingagents.ExitHookEvent, map[string]any{"exit_code": float64(0)})
	a.onHook(id, "Notify", map[string]any{"type": "agent-turn-complete", "thread-id": "th-7"})
	s := terminalRecord(t, a, id)
	if s.State != "ended" || !s.Exited || s.ExitCode == nil || *s.ExitCode != 0 || s.Detail != "" || s.SessionID != "th-7" || s.Title != "fix it" {
		t.Fatalf("%+v", s)
	}
	// A report without a status still ends it.
	other := a.terminalCommand(TerminalArgs{Agent: "codex"})["terminal_id"].(string)
	a.onHook(other, codingagents.ExitHookEvent, map[string]any{})
	if s := terminalRecord(t, a, other); !s.Exited || *s.ExitCode != -1 {
		t.Fatalf("%+v", s)
	}
}

// Codex's update ends its session, so a terminal launch keeps it from
// offering one (the user's own arguments come after, and may turn it back
// on); the update is the desktop's to run, with the command detection
// reports.
func TestCodexUpdatesOnlyWhenAsked(t *testing.T) {
	fakeAgent(t, "codex", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	if _, err := codingagents.UpdateSettings(func(s *codingagents.Settings) error {
		s.Agents = map[string]codingagents.AgentSettings{"codex": {Args: []string{"-c", "check_for_update_on_startup=true"}}}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	cmd := a.terminalCommand(TerminalArgs{Agent: "codex", Resume: "th-1"})["command"].(string)
	quiet := strings.Index(cmd, "check_for_update_on_startup=false")
	if !strings.Contains(cmd, "resume th-1 -c check_for_update_on_startup=false") || quiet > strings.Index(cmd, "check_for_update_on_startup=true") {
		t.Fatalf("command %q", cmd)
	}
	if u := updateCommand(codingagents.Found{ID: "codex", Path: "/opt/my tools/codex"}); u != "'/opt/my tools/codex' update" {
		t.Fatalf("update %q", u)
	}
	if u := updateCommand(codingagents.Found{ID: "gemini", Path: "/bin/gemini"}); u != "" {
		t.Fatalf("Gemini CLI updates itself as it runs: %q", u)
	}
}

// Pi and OpenCode take no hook commands: a terminal launch gives Pi the
// node's hook extension and OpenCode its hook plugin (laid over the
// configuration the launch has), and what they report makes the session
// they run the desktop's, listed with the agent's own sessions.
func TestPiAndOpenCodeTerminalsReportTheirSessions(t *testing.T) {
	piDir := t.TempDir()
	sessions := filepath.Join(piDir, "sessions", "--w-proj--")
	if err := os.MkdirAll(sessions, 0o755); err != nil {
		t.Fatal(err)
	}
	piFile := `{"type":"session","version":3,"id":"01a1-pi","timestamp":"2026-11-02T00:28:47.147Z","cwd":"/w/proj"}
{"type":"model_change","id":"a","parentId":null,"timestamp":"2026-11-02T00:28:47.218Z","provider":"pantheon","modelId":"openrouter/anthropic/claude-sonnet-4.6"}
{"type":"message","id":"b","parentId":"a","timestamp":"2026-11-02T00:28:50.000Z","message":{"role":"user","content":[{"type":"text","text":"add a README"}],"timestamp":1793579330000}}
`
	if err := os.WriteFile(filepath.Join(sessions, "2026-11-02T00-28-47-147Z_01a1-pi.jsonl"), []byte(piFile), 0o644); err != nil {
		t.Fatal(err)
	}
	dir := fakeAgents(t, map[string]string{"pi": "", "opencode": `case "$1" in db) cat <<'EOF2'
[{"id":"ses_1","title":"New session - 2026-11-02T00:28:12.143Z","directory":"/w/proj","created":1793579292143,"updated":1793579295982,"prompt":"fix the build\n","model":"pantheon/openrouter/openai/gpt-5.5"}]
EOF2
;; esac`})
	t.Setenv("PI_CODING_AGENT_DIR", piDir)
	t.Setenv("PI_CODING_AGENT_SESSION_DIR", "")
	t.Setenv("OPENCODE_CONFIG_CONTENT", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()

	res := a.terminalCommand(TerminalArgs{Agent: "pi", Cwd: "/w/proj"})
	argv, _ := res["argv"].([]string)
	ext := filepath.Join(codingagents.Root(), "hooks", "pi-hooks.js")
	if !slices.Contains(argv, ext) || argv[slices.Index(argv, ext)-1] != "-e" {
		t.Fatalf("pi argv %v", argv)
	}
	piTerm := res["terminal_id"].(string)

	res = a.terminalCommand(TerminalArgs{Agent: "opencode", Cwd: "/w/proj"})
	cmd := res["command"].(string)
	env := readText(t, strings.Trim(strings.Fields(cmd)[4], "';"))
	plugin := "file://" + filepath.Join(codingagents.Root(), "hooks", "opencode-hooks.js")
	if !strings.Contains(env, `OPENCODE_CONFIG_CONTENT='{"plugin":["`+plugin+`"]}'`) {
		t.Fatalf("opencode env %s", env)
	}
	ocTerm := res["terminal_id"].(string)

	a.onHook(piTerm, "SessionStart", map[string]any{"session_id": "01a1-pi", "cwd": "/w/proj"})
	if s := terminalRecord(t, a, piTerm); s.State != "ready" || s.SessionID != "01a1-pi" {
		t.Fatalf("pi %+v", s)
	}
	a.onHook(piTerm, "UserPromptSubmit", map[string]any{"session_id": "01a1-pi", "prompt": "add a README"})
	if s := terminalRecord(t, a, piTerm); s.State != "working" || s.Title != "add a README" {
		t.Fatalf("pi %+v", s)
	}
	a.onHook(ocTerm, "UserPromptSubmit", map[string]any{"session_id": "ses_1", "prompt": "fix the build\n"})
	a.onHook(ocTerm, "Stop", map[string]any{"session_id": "ses_1"})
	if s := terminalRecord(t, a, ocTerm); s.State != "ready" || s.SessionID != "ses_1" || s.Title != "fix the build" {
		t.Fatalf("opencode %+v", s)
	}
	waitFor(t, "both sessions known", func() bool {
		k := a.knownSet()
		_, pi := k["pi/01a1-pi"]
		_, oc := k["opencode/ses_1"]
		return pi && oc
	})
	res = a.sessions("", 50)
	rows, _ := res["sessions"].([]SessionRow)
	byID := map[string]SessionRow{}
	for _, r := range rows {
		byID[r.Agent+"/"+r.ID] = r
	}
	if r := byID["pi/01a1-pi"]; !r.Known || r.Title != "add a README" || r.Cwd != "/w/proj" || r.Model != "openrouter/anthropic/claude-sonnet-4.6" {
		t.Errorf("pi row %+v (%v)", r, res["warnings"])
	}
	if r := byID["opencode/ses_1"]; !r.Known || r.Title != "fix the build" || r.Model != "pantheon/openrouter/openai/gpt-5.5" || r.UpdatedAt != 1793579295.982 {
		t.Errorf("opencode row %+v (%v)", r, res["warnings"])
	}
	if calls := readText(t, filepath.Join(dir, "calls-opencode.txt")); !strings.Contains(calls, "ARGS db SELECT") {
		t.Errorf("opencode calls %s", calls)
	}
	// Resuming one reopens it by its id.
	if argv, _ := a.terminalCommand(TerminalArgs{Agent: "pi", Resume: "01a1-pi", Cwd: "/w/proj"})["argv"].([]string); !strings.Contains(strings.Join(argv, " "), "--session 01a1-pi") {
		t.Errorf("resume %v", argv)
	}
}
