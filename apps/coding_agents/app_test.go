//go:build !windows

package codingagentsapp

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// fakeAgent installs a script named like a catalog command on a PATH of its
// own and returns the file it records its arguments and stdin to.
func fakeAgent(t *testing.T, name, body string) string {
	t.Helper()
	dir := t.TempDir()
	record := filepath.Join(dir, "calls.txt")
	script := "#!/bin/sh\necho \"ARGS $*\" >> " + record + "\ncat >> " + record + "\necho >> " + record + "\n" + body
	if err := os.WriteFile(filepath.Join(dir, name), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", t.TempDir())
	t.Setenv("SHELL", "/bin/sh")
	t.Setenv("PATH", dir+":/usr/bin:/bin")
	codingagents.Detect(true)
	return record
}

const claudeTurn = `cat <<'EOF'
{"type":"system","subtype":"init","session_id":"sess-1","model":"claude-test"}
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"a.txt"}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Done."}]}}
{"type":"result","subtype":"success","is_error":false,"result":"Done.","total_cost_usd":0.01}
EOF
`

func waitIdle(t *testing.T, a *App, id string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if c := a.snapshot(id); c != nil && c["status"] != "running" {
			return c
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("the turn did not finish")
	return nil
}

func eventTypes(events []codingagents.Event) string {
	var out []string
	for _, e := range events {
		out = append(out, e.Type)
	}
	return strings.Join(out, " ")
}

func TestConversationTurnsResumeTheAgentSession(t *testing.T) {
	record := fakeAgent(t, "claude", claudeTurn)
	a := NewApp(nil, "", t.TempDir())
	cwd := t.TempDir()

	res := a.send(SendArgs{Agent: "claude", Cwd: cwd, Prompt: "list files\nplease", Auto: true})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	id := res["conversation_id"].(string)
	c := waitIdle(t, a, id)
	if c["status"] != "idle" || c["session_id"] != "sess-1" || c["title"] != "list files …" || c["model"] != "claude-test" {
		t.Fatalf("conversation after turn: %v", c)
	}
	got := a.conversation(id, 0)["events"].([]codingagents.Event)
	if eventTypes(got) != "user session tool tool text done" {
		t.Fatalf("events: %s", eventTypes(got))
	}
	if got[0].Text != "list files\nplease" || got[5].CostUSD != 0.01 {
		t.Fatalf("user %+v done %+v", got[0], got[5])
	}

	if res := a.send(SendArgs{ConversationID: id, Prompt: "and again"}); res["success"] != true {
		t.Fatalf("second send: %v", res)
	}
	waitIdle(t, a, id)
	calls, _ := os.ReadFile(record)
	lines := strings.Split(strings.TrimSpace(string(calls)), "\n")
	if !strings.Contains(lines[0], "--dangerously-skip-permissions") || strings.Contains(lines[0], "--resume") {
		t.Errorf("first turn args: %s", lines[0])
	}
	if !strings.Contains(string(calls), "--resume sess-1") || !strings.Contains(string(calls), "and again") {
		t.Errorf("second turn must resume the agent's session with the prompt on stdin:\n%s", calls)
	}
	if after := a.conversation(id, 6)["events"].([]codingagents.Event); eventTypes(after) != "user session tool tool text done" || after[0].Seq != 7 {
		t.Errorf("events after seq 6: %s", eventTypes(after))
	}

	// The index survives a restart of the service.
	b := NewApp(nil, "", a.root)
	if l := b.list("claude", cwd)["conversations"].([]*Conversation); len(l) != 1 || l[0].Session != "sess-1" || l[0].Seq != 12 {
		t.Fatalf("reloaded: %+v", l)
	}
	if r := b.remove(id); r["success"] != true {
		t.Fatal(r)
	}
	if _, err := os.Stat(b.logPath(id)); !os.IsNotExist(err) {
		t.Error("the event log stays after delete")
	}
}

func TestFailedStartReportsStderr(t *testing.T) {
	fakeAgent(t, "codex", "echo 'Not signed in. Run codex login.' >&2\nexit 3\n")
	a := NewApp(nil, "", t.TempDir())
	res := a.send(SendArgs{Agent: "codex", Prompt: "hi"})
	id := res["conversation_id"].(string)
	c := waitIdle(t, a, id)
	if c["status"] != "error" || !strings.Contains(c["last_error"].(string), "exited") {
		t.Fatalf("conversation: %v", c)
	}
	ev := a.conversation(id, 0)["events"].([]codingagents.Event)
	if last := ev[len(ev)-1]; last.Type != "done" || !strings.Contains(last.Error, "Not signed in") {
		t.Fatalf("last event %+v", last)
	}
}

func TestStopInterruptsTheTurn(t *testing.T) {
	fakeAgent(t, "claude", `echo '{"type":"system","subtype":"init","session_id":"s"}'`+"\nsleep 30\n")
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "claude", Prompt: "work"})["conversation_id"].(string)
	time.Sleep(200 * time.Millisecond)
	if r := a.send(SendArgs{ConversationID: id, Prompt: "more"}); r["success"] != false {
		t.Fatalf("a second message while running must be refused: %v", r)
	}
	start := time.Now()
	a.stop(id)
	if time.Since(start) > 5*time.Second {
		t.Fatal("stop took too long")
	}
	ev := a.conversation(id, 0)["events"].([]codingagents.Event)
	if last := ev[len(ev)-1]; last.Error != "Stopped" {
		t.Fatalf("last event %+v", last)
	}
}

func TestTerminalCommand(t *testing.T) {
	fakeAgent(t, "codex", "")
	a := NewApp(nil, "", t.TempDir())
	res := a.terminalCommand("codex", "it's-1", true)
	cmd, _ := res["command"].(string)
	if !strings.HasSuffix(cmd, `/codex resume 'it'\''s-1' --dangerously-bypass-approvals-and-sandbox`) {
		t.Fatalf("command %q", cmd)
	}
	installed := map[string]bool{}
	for _, f := range codingagents.Detect(false) {
		installed[f.ID] = true
	}
	for _, agent := range codingagents.Catalog {
		if !installed[agent.ID] {
			if r := a.terminalCommand(agent.ID, "", true); r["success"] != false {
				t.Fatalf("%s is not installed: %v", agent.ID, r)
			}
			break
		}
	}
	if r := a.terminalCommand("no-such-agent", "", true); r["success"] != false {
		t.Fatalf("unknown agent: %v", r)
	}
}

func TestUnknownAndTerminalOnlyAgents(t *testing.T) {
	fakeAgent(t, "goose", "")
	a := NewApp(nil, "", t.TempDir())
	if r := a.send(SendArgs{Agent: "goose", Prompt: "hi"}); r["success"] != false {
		t.Fatalf("goose has no conversation mode: %v", r)
	}
	if r := a.send(SendArgs{Agent: "claude", Cwd: "/no/such/dir", Prompt: "hi"}); r["success"] != false {
		t.Fatalf("missing cwd: %v", r)
	}
}

// The quota an agent reports during a turn shows with the agent afterwards,
// also after the service restarts.
func TestQuotaFollowsTheAgent(t *testing.T) {
	fakeAgent(t, "claude", `cat <<'EOF'
{"type":"system","subtype":"init","session_id":"s"}
{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{"five_hour":{"utilization":0.2,"resetsAt":1}}}}
{"type":"result","subtype":"success","is_error":false,"result":"ok","duration_ms":1200,"usage":{"input_tokens":10,"output_tokens":5}}
EOF
`)
	a := NewApp(nil, "", t.TempDir())
	id := a.send(SendArgs{Agent: "claude", Prompt: "hi"})["conversation_id"].(string)
	waitIdle(t, a, id)
	info := func(app *App) AgentInfo {
		for _, i := range app.detect(false)["agents"].([]AgentInfo) {
			if i.ID == "claude" {
				return i
			}
		}
		t.Fatal("claude not detected")
		return AgentInfo{}
	}
	if l := info(a).Limits["five_hour"]; l.Used != 0.2 {
		t.Fatalf("limits %+v", info(a))
	}
	ev := a.conversation(id, 0)["events"].([]codingagents.Event)
	if done := ev[len(ev)-1]; done.DurationMs != 1200 || done.InputTokens != 10 || done.OutputTokens != 5 {
		t.Fatalf("done %+v", done)
	}
	if l := info(NewApp(nil, "", a.root)).Limits["five_hour"]; l.Used != 0.2 {
		t.Fatal("the quota is forgotten after a restart")
	}
}
