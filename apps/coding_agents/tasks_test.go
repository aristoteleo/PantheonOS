package codingagentsapp

import (
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// A known agent missing here comes with its install command, and with a note
// instead when the program that command needs is not on the node.
func TestInstallableNeeds(t *testing.T) {
	codex, _ := codingagents.Lookup("codex")
	claude, _ := codingagents.Lookup("claude")
	none := func(string) string { return "" }
	all := func(name string) string { return "/usr/bin/" + name }

	if i := installable(codex, all); i.Needs != "npm" || i.Note != "" || i.Command != "npm install -g @openai/codex" {
		t.Fatalf("with npm: %+v", i)
	}
	if i := installable(codex, none); !strings.Contains(i.Note, "npm is not on this computer") || !strings.Contains(i.Note, "Node.js") {
		t.Fatalf("without npm: %+v", i)
	}
	if i := installable(claude, none); i.Needs != "curl" || !strings.Contains(i.Note, "curl is not on this computer") {
		t.Fatalf("without curl: %+v", i)
	}
}

// Every agent the Agents page offers to install or sign in has a command for
// it; Codex signs in with a code on Linux, whose browser is elsewhere.
func TestCatalogTasks(t *testing.T) {
	for _, id := range []string{"claude", "codex", "gemini", "cursor"} {
		c, _ := codingagents.Lookup(id)
		if c.Install == "" || c.Login == nil {
			t.Errorf("%s: install %q, login %v", id, c.Install, c.Login != nil)
		}
	}
	codex, _ := codingagents.Lookup("codex")
	if got := strings.Join(codex.Login("linux"), " "); got != "login --device-auth" {
		t.Errorf("codex on linux: %q", got)
	}
	if got := strings.Join(codex.Login("darwin"), " "); got != "login" {
		t.Errorf("codex on darwin: %q", got)
	}
	claude, _ := codingagents.Lookup("claude")
	if got := strings.Join(claude.Login("linux"), " "); got != "auth login" {
		t.Errorf("claude: %q", got)
	}
}

// A sign-in runs the agent's own command in a terminal, and its exit status
// comes back to the terminal session, which says it was a task.
func TestTaskCommandReportsExit(t *testing.T) {
	record := fakeAgent(t, "claude", "exit 3\n")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()

	det := a.detect(true)
	if det["tasks"] != true {
		t.Fatalf("detect does not offer tasks: %v", det)
	}
	var login string
	for _, info := range det["agents"].([]AgentInfo) {
		if info.ID == "claude" {
			login = info.Login
		}
	}
	if !strings.HasSuffix(login, "/claude auth login") {
		t.Fatalf("claude's login: %q", login)
	}

	res := a.taskCommand("claude", TaskLogin)
	if res["success"] != true {
		t.Fatal(res)
	}
	id, _ := res["terminal_id"].(string)
	if res["label"] != "Sign in to Claude Code" || id == "" {
		t.Fatalf("task: %v", res)
	}
	out, err := exec.Command("/bin/sh", "-c", res["command"].(string)).CombinedOutput()
	if code := exitCode(err); code != 3 {
		t.Fatalf("exit %d (%v): %s", code, err, out)
	}
	if !strings.Contains(string(out), "auth login") || !strings.Contains(string(out), "Failed (exit status 3)") {
		t.Errorf("output: %s", out)
	}
	calls, _ := os.ReadFile(record)
	if !strings.Contains(string(calls), "ARGS auth login") {
		t.Errorf("calls: %s", calls)
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		var term *TerminalSession
		for _, s := range a.terminalList()["terminals"].([]TerminalSession) {
			if s.ID == id {
				term = &s
			}
		}
		if term != nil && term.Exited {
			if term.Task != TaskLogin || term.ExitCode == nil || *term.ExitCode != 3 || term.State != "ended" {
				t.Fatalf("terminal: %+v", term)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("no exit report: %+v", term)
		}
		time.Sleep(20 * time.Millisecond)
	}

	for _, bad := range []struct{ agent, task, want string }{
		{"claude", "uninstall", "Unknown task"},
		{"claude", TaskInstall, ""},
		{"aider", TaskInstall, "cannot install"},
		{"codex", TaskLogin, "not installed"},
	} {
		r := a.taskCommand(bad.agent, bad.task)
		msg, _ := r["error"].(string)
		if bad.want == "" {
			// Claude Code is installed: installing it again is still a task.
			if r["success"] != true || !strings.Contains(r["command"].(string), "claude.ai/install.sh") {
				t.Errorf("%s %s: %v", bad.agent, bad.task, r)
			}
			continue
		}
		if r["success"] != false || !strings.Contains(msg, bad.want) {
			t.Errorf("%s %s: %v", bad.agent, bad.task, r)
		}
	}
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	if e, ok := err.(*exec.ExitError); ok {
		return e.ExitCode()
	}
	return -1
}
