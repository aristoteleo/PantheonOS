//go:build !windows

package codingagentsapp

import (
	"fmt"
	"net/http"
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
	t.Setenv("CLAUDE_CONFIG_DIR", "")
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
	if c["status"] != "error" || c["last_error"] != "Codex could not sign in: Not signed in. Run codex login." {
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
	if last := ev[len(ev)-1]; !last.Stopped || last.Error != "" {
		t.Fatalf("last event %+v", last)
	}
	if c := a.snapshot(id); c["status"] != "idle" {
		t.Fatalf("a stopped turn is not an error: %v", c)
	}
}

// A terminal launch reads its secrets from a file only the user can read
// (and removes it), runs the agent in a subshell, and gives it hooks that
// report to this service.
func TestTerminalCommand(t *testing.T) {
	fakeAgent(t, "codex", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	if r := a.setAccess("pbk_test", 7, "https://hub.test/"); r["success"] != true {
		t.Fatal(r)
	}
	res := a.terminalCommand(TerminalArgs{Agent: "codex", Resume: "it's-1", Auto: true, Cwd: "/w"})
	cmd, _ := res["command"].(string)
	if !strings.HasPrefix(cmd, "( set -a; . ") || !strings.Contains(cmd, `/codex resume 'it'\''s-1' --dangerously-bypass-approvals-and-sandbox`) ||
		!strings.Contains(cmd, "developer_instructions=") || !strings.Contains(cmd, "notify=") || strings.Contains(cmd, "pbk_test") {
		t.Fatalf("command %q", cmd)
	}
	envFile := strings.Trim(strings.Fields(cmd)[4], "';")
	st, err := os.Stat(envFile)
	if err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("env file %s: %v %v", envFile, st, err)
	}
	env, _ := os.ReadFile(envFile)
	for _, want := range []string{"PANTHEON_KEY=pbk_test", "PANTHEON_HUB_URL=https://hub.test", "PANTHEON_CLIENT=coding-agent/codex",
		"PANTHEON_AGENT_HOOK_URL=http://127.0.0.1:", "PANTHEON_AGENT_TERMINAL=" + res["terminal_id"].(string)} {
		if !strings.Contains(string(env), want) {
			t.Errorf("env file lacks %s:\n%s", want, env)
		}
	}
	installed := map[string]bool{}
	for _, f := range codingagents.Detect(false) {
		installed[f.ID] = true
	}
	for _, agent := range codingagents.Catalog {
		if !installed[agent.ID] {
			if r := a.terminalCommand(TerminalArgs{Agent: agent.ID}); r["success"] != false {
				t.Fatalf("%s is not installed: %v", agent.ID, r)
			}
			break
		}
	}
	if r := a.terminalCommand(TerminalArgs{Agent: "no-such-agent"}); r["success"] != false {
		t.Fatalf("unknown agent: %v", r)
	}
}

// Claude Code reports through the hooks of its launch: the terminal session
// follows it, and the status line's limits become the agent's quota.
func TestTerminalHooks(t *testing.T) {
	fakeAgent(t, "claude", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	res := a.terminalCommand(TerminalArgs{Agent: "claude", Auto: true})
	id := res["terminal_id"].(string)
	cmd := res["command"].(string)
	i := strings.Index(cmd, "--settings ")
	if i < 0 {
		t.Fatalf("no settings: %s", cmd)
	}
	settings := strings.Trim(strings.Fields(cmd[i:])[1], "'")
	doc, _ := os.ReadFile(settings)
	if !strings.Contains(string(doc), `"PermissionRequest"`) || !strings.Contains(string(doc), "statusLine") {
		t.Fatalf("settings %s", doc)
	}
	h, _ := a.hookServer()
	post := func(event, body, token string) int {
		req, _ := http.NewRequest("POST", h.url+"/hook/"+id+"/"+event, strings.NewReader(body))
		req.Header.Set("X-Pantheon-Hook", token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		return resp.StatusCode
	}
	state := func() TerminalSession {
		for _, s := range a.terminalList()["terminals"].([]TerminalSession) {
			if s.ID == id {
				return s
			}
		}
		t.Fatal("terminal session gone")
		return TerminalSession{}
	}
	if post("Stop", "{}", "wrong") != http.StatusForbidden {
		t.Fatal("a hook without the token must be refused")
	}
	post("SessionStart", `{"session_id":"s-9"}`, h.token)
	if s := state(); s.State != "ready" || s.SessionID != "s-9" {
		t.Fatalf("%+v", s)
	}
	post("UserPromptSubmit", `{}`, h.token)
	post("PermissionRequest", `{"tool_name":"Bash"}`, h.token)
	if s := state(); s.State != "waiting" || s.Detail != "Bash" {
		t.Fatalf("%+v", s)
	}
	post("StatusLine", `{"rate_limits":{"five_hour":{"used_percentage":42,"resets_at":5}}}`, h.token)
	if l := state().Limits["five_hour"]; l.Used != 0.42 || l.ResetsAt != 5 {
		t.Fatalf("limits %+v", l)
	}
	post("SessionEnd", `{}`, h.token)
	if s := state(); s.State != "ended" {
		t.Fatalf("%+v", s)
	}
	if _, err := os.Stat(settings); !os.IsNotExist(err) {
		t.Error("an ended session's launch settings stay behind")
	}
}

// Settings change every launch: a replaced command, added arguments and
// variables, a default model; with PantheonOS access, the agent gets the key,
// the pantheon command and a note on what it is for.
func TestSettingsAndAccessShapeTurns(t *testing.T) {
	record := fakeAgent(t, "claude", `env | grep -E '^(PANTHEON_|MY_VAR)' >> `+"\"$(dirname \"$0\")/env.txt\"\n"+claudeTurn)
	dir := filepath.Dir(record)
	if err := os.Rename(filepath.Join(dir, "claude"), filepath.Join(dir, "my-claude")); err != nil {
		t.Fatal(err)
	}
	codingagents.Detect(true)
	a := NewApp(nil, "", t.TempDir())
	a.NodeID, a.NodeName = "n_mac", "My Mac"
	if r := a.send(SendArgs{Agent: "claude", Prompt: "hi"}); r["success"] != false {
		t.Fatalf("without its binary claude is not installed: %v", r)
	}
	mine := []codingagents.CustomAgent{{ID: "mine", Label: "Mine", Command: "echo hi"}}
	r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{"claude": {Command: "my-claude", Args: []string{"--effort", "high"},
		Env: map[string]string{"MY_VAR": "1"}, Model: "opus"}}, Custom: &mine})
	if r["success"] != true {
		t.Fatal(r)
	}
	a.setAccess("pbk_k", 3, "https://hub.test")
	id := a.send(SendArgs{Agent: "claude", Prompt: "hi", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	calls, _ := os.ReadFile(record)
	for _, want := range []string{"--model opus", "--effort high", "--append-system-prompt You are running on My Mac"} {
		if !strings.Contains(string(calls), want) {
			t.Errorf("args lack %q:\n%s", want, calls)
		}
	}
	env, _ := os.ReadFile(filepath.Join(dir, "env.txt"))
	for _, want := range []string{"MY_VAR=1", "PANTHEON_KEY=pbk_k", "PANTHEON_CODING_CONVERSATION=" + id, "PANTHEON_NODE_ID=n_mac"} {
		if !strings.Contains(string(env), want) {
			t.Errorf("env lacks %s:\n%s", want, env)
		}
	}
	agents := a.detect(false)["agents"].([]AgentInfo)
	var custom bool
	for _, i := range agents {
		custom = custom || i.ID == "mine" && i.Custom && i.Terminal && !i.Headless
	}
	if !custom {
		t.Errorf("the custom agent is not listed: %+v", agents)
	}
	if got := a.getSettings()["settings"].(map[string]any)["access"].(map[string]any); got["enabled"] != true || strings.Contains(fmt.Sprint(got), "pbk_k") {
		t.Errorf("access %v", got)
	}
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{"claude": {Env: map[string]string{"BAD NAME": "x"}}}}); r["success"] != false {
		t.Errorf("a bad variable name must be refused: %v", r)
	}
	if r := a.setAccess("", 0, ""); r["replaced_key_id"] != int64(3) {
		t.Errorf("turning access off hands back the key to revoke: %v", r)
	}

	// The project list is saved apart from the agents' settings, and only folders on the node are taken.
	project := t.TempDir()
	if r := a.configure(Configure{Projects: &[]codingagents.Project{{Path: project + "/"}}}); r["success"] != true {
		t.Fatal(r)
	}
	if r := a.configure(Configure{Projects: &[]codingagents.Project{{Path: project}, {Path: filepath.Join(project, "missing")}}}); r["success"] != false {
		t.Errorf("a folder that is not there must be refused: %v", r)
	}
	saved := codingagents.LoadSettings()
	if len(saved.Projects) != 1 || saved.Projects[0].Path != project || saved.Agents["claude"].Model != "opus" || len(saved.Custom) != 1 {
		t.Errorf("projects %+v, agents %+v, custom %+v", saved.Projects, saved.Agents, saved.Custom)
	}
	if got := a.detect(false)["projects"].([]string); len(got) != 1 || got[0] != project {
		t.Errorf("detection lists the projects: %v", got)
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

func TestBrowseAndCreateProject(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	root := t.TempDir()
	for _, d := range []string{"alpha", ".hidden", "Beta/.git"} {
		if err := os.MkdirAll(filepath.Join(root, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "file.txt"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	a := NewApp(nil, "", t.TempDir())
	r := a.browse(root)
	folders := r["folders"].([]Folder)
	if len(folders) != 2 || folders[0].Name != "alpha" || folders[0].Git || folders[1].Name != "Beta" || !folders[1].Git {
		t.Fatalf("folders only, hidden left out, git marked: %+v", folders)
	}
	if r["parent"] != filepath.Dir(root) {
		t.Errorf("parent %v", r["parent"])
	}

	res := a.createProject(root, "new-thing", true)
	if res["success"] != true {
		t.Fatal(res)
	}
	path := filepath.Join(root, "new-thing")
	if _, err := os.Stat(filepath.Join(path, ".git")); err != nil {
		t.Errorf("git init: %v", err)
	}
	if got := codingagents.LoadSettings().Projects; len(got) != 1 || got[0].Path != path {
		t.Errorf("the new folder is a project: %+v", got)
	}
	if res := a.createProject(root, "new-thing", false); res["success"] != false {
		t.Errorf("an existing folder is refused: %v", res)
	}
	if res := a.createProject(root, "a/b", false); res["success"] != false {
		t.Errorf("a name with / is refused: %v", res)
	}
}

func TestKnownSessions(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	a := NewApp(nil, "", t.TempDir())
	if err := a.remember(knownSession{Agent: "claude", ID: "s1"}, knownSession{Agent: "codex", ID: "t1"}); err != nil {
		t.Fatal(err)
	}
	if err := a.forget(knownSession{Agent: "claude", ID: "s1"}); err != nil {
		t.Fatal(err)
	}
	k := a.knownSet()
	if _, ok := k["claude/s1"]; ok || len(k) != 1 {
		t.Fatalf("forgotten sessions leave the list: %v", k)
	}
	// A session an agent in a terminal here reports joins the list; Codex says thread-id.
	term := a.newTerminal("codex", "Codex", "/w")
	a.onHook(term.ID, "Notify", map[string]any{"type": "agent-turn-complete", "thread-id": "t2"})
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, ok := a.knownSet()["codex/t2"]; ok {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("codex/t2 was not remembered: %v", a.knownSet())
}

// A Pantheon Agent chat started in a terminal here is listed under its
// folder, titled by its first prompt, though the chat lives with the Agent.
func TestPantheonChatsListed(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	a := NewApp(nil, "", t.TempDir())
	term := a.newTerminal(codingagents.PantheonAgent, "Pantheon Agent", "/w/proj")
	a.onHook(term.ID, "UserPromptSubmit", map[string]any{"session_id": "chat-9", "title": "  fix the\n flaky   test "})
	if got := a.terminals[term.ID]; got.State != "working" || got.Title != "fix the flaky test" {
		t.Fatalf("the terminal works on its first prompt: %+v", got)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, ok := a.knownSet()["pantheon/chat-9"]; ok {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	a.onHook(term.ID, "Stop", map[string]any{"session_id": "chat-9"})
	if got := a.terminals[term.ID].State; got != "ready" {
		t.Errorf("a finished turn leaves it ready: %s", got)
	}
	rows, _ := a.sessions(codingagents.PantheonAgent, 10)["sessions"].([]SessionRow)
	if len(rows) != 1 || rows[0].ID != "chat-9" || rows[0].Cwd != "/w/proj" || rows[0].Title != "fix the flaky test" || !rows[0].Known {
		t.Fatalf("the chat is listed: %+v", rows)
	}
}

// The Pantheon Agent started from a computer works on that computer, in the
// chosen folder.
func TestPantheonAgentWorksWhereItStarts(t *testing.T) {
	fakeAgent(t, "pantheon", "")
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	a.NodeID = "n_mac"
	res := a.terminalCommand(TerminalArgs{Agent: codingagents.PantheonAgent, Resume: "chat-1", Cwd: "/Users/me/proj"})
	cmd, _ := res["command"].(string)
	if !strings.Contains(cmd, "agent chat --chat chat-1 --node n_mac --workdir /Users/me/proj") {
		t.Fatalf("command %q", cmd)
	}
}
