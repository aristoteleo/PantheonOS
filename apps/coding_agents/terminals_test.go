//go:build !windows

package codingagentsapp

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os/exec"
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
