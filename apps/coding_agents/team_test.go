//go:build !windows

package codingagentsapp

import (
	"encoding/base64"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// teamCommand finds the pantheon-team command a turn was told about.
const teamCommand = `team=$(printf '%s\n%s' "$*" "$input" | grep -o "/[^' ]*/pantheon-team" | head -n 1)
`

func TestTeamSetupMakesTheAgentFolder(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	folder := filepath.Join(home, "PantheonAgents", "writer")
	if r := teamSetup("~/PantheonAgents/writer", "", "", true); r["success"] != true || r["exists"] != nil || r["folder"] != folder {
		t.Fatal(r)
	}
	r := teamSetup("~/PantheonAgents/writer", "# Writer\nUse {{TEAM}} inbox\n", "", false)
	if r["success"] != true || r["home"] != false {
		t.Fatal(r)
	}
	brief, _ := os.ReadFile(filepath.Join(folder, "AGENT.md"))
	if !strings.Contains(string(brief), "'"+filepath.Join(folder, ".team", "pantheon-team")+"' inbox") {
		t.Fatalf("%s", brief)
	}
	st, err := os.Stat(filepath.Join(folder, ".team"))
	if err != nil || st.Mode().Perm() != 0o700 {
		t.Fatalf("%v %v", st, err)
	}
	for _, p := range []string{"MEMORY.md", "notes", ".team/pantheon-team", ".team/inbox"} {
		if _, err := os.Stat(filepath.Join(folder, p)); err != nil {
			t.Fatal(p, err)
		}
	}
	// Memory is the agent's: kept on a rewrite, gone with a full reset.
	os.WriteFile(filepath.Join(folder, "MEMORY.md"), []byte("always answer in French"), 0o644)
	teamSetup(folder, "# Writer v2\n", "", false)
	if b, _ := os.ReadFile(filepath.Join(folder, "MEMORY.md")); string(b) != "always answer in French" {
		t.Fatal("memory was rewritten")
	}
	if r := teamSetup(folder, "# Writer v2\n", "full", false); r["success"] != true {
		t.Fatal(r)
	}
	if b, _ := os.ReadFile(filepath.Join(folder, "MEMORY.md")); strings.Contains(string(b), "French") {
		t.Fatal("full reset kept the memory")
	}
	// The home folder and / are reported; a full reset never empties them.
	if r := teamSetup(home, "", "", true); r["home"] != true {
		t.Fatal(r)
	}
	if r := teamSetup("/", "", "", true); r["root"] != true {
		t.Fatal(r)
	}
	if r := teamSetup(home, "", "full", false); r["success"] != false {
		t.Fatal("emptied the home folder")
	}
	if r := teamSetup("relative/dir", "", "", false); r["success"] != false {
		t.Fatal(r)
	}
	// Files arrive under files/, once.
	data := base64.StdEncoding.EncodeToString([]byte("image"))
	if r := teamFilePut(folder, "files/f1-shot.png", 0, 5, data, true); r["success"] != true {
		t.Fatal(r)
	}
	if r := teamFilePut(folder, "files/f1-shot.png", 0, 5, data, true); r["exists"] != true {
		t.Fatal(r)
	}
	if r := teamFilePut(folder, "../escape", 0, 5, data, true); r["success"] != false {
		t.Fatal(r)
	}
}

// A Team Chat turn: the agent's tool reaches the controller with its
// identity, files included, and the turn is told how to use it.
func TestATeamTurnRunsItsToolThroughTheController(t *testing.T) {
	dir := fakeAgents(t, map[string]string{
		"claude": teamCommand + `"$team" reply 01ABC - > "$(dirname "$0")/reply.txt" 2>&1 <<'EOF'
the answer
EOF
echo "exit $?" >> "$(dirname "$0")/reply.txt"
printf 'report' > report.md
"$team" post general --file report.md "here it is" > "$(dirname "$0")/post.txt" 2>&1
"$team" inbox > "$(dirname "$0")/inbox.txt" 2>&1
echo "exit $?" >> "$(dirname "$0")/inbox.txt"
"$team" remind in 2 minutes --about 01ABC --every '1 hour' check CI > /dev/null 2>&1
"$team" task claim 01ABC > /dev/null 2>&1
"$team" 'rm -rf' > "$(dirname "$0")/bad.txt" 2>&1
echo "exit $?" >> "$(dirname "$0")/bad.txt"
` + claudeDone,
	})
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	var mu sync.Mutex
	var reqs []map[string]any
	a.teamCaller = func(req map[string]any) (map[string]any, error) {
		mu.Lock()
		defer mu.Unlock()
		reqs = append(reqs, req)
		switch req["op"] {
		case "file_put":
			up := req["upload"].(map[string]any)
			b, _ := base64.StdEncoding.DecodeString(up["data"].(string))
			if string(b) != "report" || up["final"] != true {
				return map[string]any{"code": float64(1), "text": "bad upload"}, nil
			}
			return map[string]any{"code": float64(0), "file": map[string]any{"file": map[string]any{"id": "fREPORT"}}}, nil
		case "inbox":
			return map[string]any{"code": float64(1), "text": "Nothing new since your turn started."}, nil
		}
		return map[string]any{"code": float64(0), "text": "Replied in the thread of 01ABC."}, nil
	}
	folder := filepath.Join(t.TempDir(), "writer")
	if r := teamSetup(folder, "brief", "", false); r["success"] != true {
		t.Fatal(r)
	}
	res := a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] You are @writer", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "writer", Name: "Writer"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	out, _ := os.ReadFile(filepath.Join(dir, "reply.txt"))
	if string(out) != "Replied in the thread of 01ABC.\nexit 0\n" {
		t.Fatalf("%q", out)
	}
	inbox, _ := os.ReadFile(filepath.Join(dir, "inbox.txt"))
	if !strings.Contains(string(inbox), "Nothing new") || !strings.HasSuffix(string(inbox), "exit 1\n") {
		t.Fatalf("%q", inbox)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(reqs) != 6 {
		t.Fatalf("%v", reqs)
	}
	// Any command word reaches Team Chat with its options; a word that is
	// not one is refused by the script.
	remind, claim := reqs[4], reqs[5]
	if remind["op"] != "remind" || strings.Join(remind["args"].([]string), " ") != "in 2 minutes check CI" ||
		remind["opts"].(map[string]string)["about"] != "01ABC" || remind["opts"].(map[string]string)["every"] != "1 hour" {
		t.Fatalf("%v", remind)
	}
	if claim["op"] != "task" || strings.Join(claim["args"].([]string), " ") != "claim 01ABC" {
		t.Fatalf("%v", claim)
	}
	if bad, _ := os.ReadFile(filepath.Join(dir, "bad.txt")); !strings.Contains(string(bad), "usage:") || !strings.HasSuffix(string(bad), "exit 2\n") {
		t.Fatalf("%q", bad)
	}
	reply, post := reqs[0], reqs[2]
	if reply["mid"] != "a1" || reply["token"] != "tok" || reply["op"] != "reply" || reply["text"] != "the answer\n" ||
		strings.Join(reply["args"].([]string), ",") != "01ABC" {
		t.Fatalf("%v", reply)
	}
	if post["op"] != "post" || strings.Join(post["args"].([]string), "|") != "general|here it is" || strings.Join(post["files"].([]string), ",") != "fREPORT" {
		t.Fatalf("%v", post)
	}
	calls, _ := os.ReadFile(filepath.Join(dir, "calls-claude.txt"))
	if !strings.Contains(string(calls), "You are Writer (@writer), an agent member of a Team Chat space") {
		t.Fatalf("%s", calls)
	}
	// Without the computer user's own settings, plugins, hooks and MCP
	// servers (docs/apps/team-chat.md, "What Team Chat cannot prevent").
	if !strings.Contains(string(calls), "--setting-sources  --strict-mcp-config") {
		t.Fatalf("a Team Chat turn's runtime loads the user's setup: %s", calls)
	}
}

// Only Team Chat turns start without the user's setup, and only with a
// runtime version that has the flag.
func TestTeamCleanArgs(t *testing.T) {
	old := runtimeHelp
	t.Cleanup(func() { runtimeHelp = old })
	runtimeHelp = func(string, ...string) string { return "--ignore-user-config --setting-sources --strict-mcp-config" }
	if got := strings.Join(teamCleanArgs("codex", "codex"), " "); got != "--ignore-user-config" {
		t.Fatal(got)
	}
	// No settings files at all: not the folder's (its hooks), not the user's.
	if got := teamCleanArgs("claude", "claude"); strings.Join(got, "|") != "--setting-sources||--strict-mcp-config" {
		t.Fatalf("%q", got)
	}
	// Codex with hooks: they are off.
	runtimeHelp = func(_ string, args ...string) string {
		if strings.Join(args, " ") == "features list" {
			return "guardian_trust   under development  false\nhooks   stable   true\nplugins  stable  true\n"
		}
		return "--ignore-user-config --disable <FEATURE>"
	}
	if got := strings.Join(teamCleanArgs("codex", "codex"), " "); got != "--ignore-user-config --disable hooks" {
		t.Fatal(got)
	}
	runtimeHelp = func(string, ...string) string { return "--model" }
	if teamCleanArgs("codex", "codex") != nil || teamCleanArgs("claude", "claude") != nil {
		t.Fatal("a flag the runtime does not have")
	}
}

// A Team Chat turn's credential stays in the node (not in the turn's
// environment or files); a command written by a background loop left by an
// earlier turn is refused without reaching the controller; and what a turn leaves running
// (nohup, &, a process that started a session of its own) ends with it
// (docs/apps/team-chat.md, "Turn-bound commands").
func TestATeamTurnsCommandsAndProcessesEndWithIt(t *testing.T) {
	old := teamProcLook
	teamProcLook = 50 * time.Millisecond
	t.Cleanup(func() { teamProcLook = old })
	t.Setenv("PANTHEON_TEAM_TURN", "t0.inherited")
	dir := fakeAgents(t, map[string]string{
		"claude": teamCommand + `d="$(dirname "$0")"
box="$(dirname "$team")/inbox"
if [ -f "$d/second" ]; then
	# The next turn: a request with the first turn's credential, as a loop
	# left from it would write, then one of its own.
	printf 'op: read\nturn: t1.c1\narg: general\n\n' > "$box/.1-1.tmp" && mv "$box/.1-1.tmp" "$box/1-1.msg"
	i=0; while [ ! -f "$box/1-1.reply" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
	cat "$box/1-1.reply" > "$d/stale.txt"
	"$team" read general > "$d/own.txt" 2>&1
else
	printf '%s' "$PANTHEON_TEAM_TURN" > "$d/env.txt"
	nohup sh -c 'echo $$ > "$0/bg.pid"; while :; do sleep 0.2; done' "$d" > /dev/null 2>&1 &
	if command -v perl > /dev/null; then
		perl -MPOSIX -e 'POSIX::setsid(); open(my $f, ">", "$ARGV[0]/away.pid"); print $f "$$"; close $f; exec "sh", "-c", "while :; do sleep 0.2; done"' "$d" > /dev/null 2>&1 &
	fi
	sleep 0.6
	touch "$d/second"
fi
` + claudeDone,
	})
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	var mu sync.Mutex
	var reqs []map[string]any
	a.teamCaller = func(req map[string]any) (map[string]any, error) {
		mu.Lock()
		defer mu.Unlock()
		reqs = append(reqs, req)
		return map[string]any{"code": float64(0), "text": "#general: hello"}, nil
	}
	folder := filepath.Join(t.TempDir(), "coder")
	if r := teamSetup(folder, "brief", "", false); r["success"] != true {
		t.Fatal(r)
	}
	alive := func(name string) (int, bool) {
		b, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			return 0, false
		}
		pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
		return pid, pid > 1 && syscall.Kill(pid, 0) == nil
	}
	res := a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] first", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "coder", Name: "Coder", Turn: "t1", Cred: "c1"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	conv := res["conversation_id"].(string)
	waitIdle(t, a, conv)
	// The credential stays in the node: not in the turn's environment.
	if env, _ := os.ReadFile(filepath.Join(dir, "env.txt")); string(env) != "" {
		t.Fatalf("the turn's environment: %q", env)
	}
	for _, name := range []string{"bg.pid", "away.pid"} {
		if name == "away.pid" {
			if _, err := os.Stat(filepath.Join(dir, name)); err != nil {
				continue // no perl here
			}
		}
		deadline := time.Now().Add(3 * time.Second)
		for {
			pid, live := alive(name)
			if pid == 0 {
				t.Fatalf("%s was not written", name)
			}
			if !live {
				break
			}
			if time.Now().After(deadline) {
				_ = syscall.Kill(pid, syscall.SIGKILL)
				t.Fatalf("%s (%d) outlived its turn", name, pid)
			}
			time.Sleep(50 * time.Millisecond)
		}
	}
	// The credential is in no file of the folder.
	_ = filepath.WalkDir(folder, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			if b, _ := os.ReadFile(p); strings.Contains(string(b), "c1") && strings.Contains(string(b), "t1.") {
				t.Errorf("%s holds the credential", p)
			}
		}
		return nil
	})
	res = a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] second", Auto: true, Origin: "team-chat",
		ConversationID: conv, Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "coder", Name: "Coder", Turn: "t2", Cred: "c2"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, conv)
	if stale, _ := os.ReadFile(filepath.Join(dir, "stale.txt")); !strings.Contains(string(stale), "not part of the agent's running Team Chat turn") {
		t.Fatalf("a request with the first turn's credential: %q", stale)
	}
	if own, _ := os.ReadFile(filepath.Join(dir, "own.txt")); !strings.Contains(string(own), "#general: hello") {
		t.Fatalf("the turn's own request: %q", own)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(reqs) != 1 || reqs[0]["turn"] != "t2" || reqs[0]["cred"] != "c2" {
		t.Fatalf("what reached the controller: %v", reqs)
	}
}

// A command is answered only when a process of the running turn vouches for
// it (the pantheon-team process waiting for its answer, whose environment
// holds the request and the turn's credential): a process that left the
// turn (fork, setsid, fork) is refused even with the running turn's
// credential, and so is a request file written by hand with no such
// process; once the tool itself is changed, the turn's Team Chat is off
// (docs/apps/team-chat.md, "Turn-bound commands").
func TestATeamCommandNeedsAProcessOfTheRunningTurn(t *testing.T) {
	if !procVouch {
		t.Skip("this system cannot read another process's arguments")
	}
	if _, err := exec.LookPath("perl"); err != nil {
		t.Skip("no perl to start an escaped process")
	}
	dir := fakeAgents(t, map[string]string{
		"claude": teamCommand + `d="$(dirname "$0")"
box="$(dirname "$team")/inbox"
# A process that leaves the turn (fork, setsid, fork) and uses the tool with
# the running turn's credential.
perl -MPOSIX -e 'exit 0 if fork; POSIX::setsid(); exit 0 if fork; exec "sh", "-c", "\"$ARGV[0]\" read general > \"$ARGV[1]/escaped.txt\" 2>&1; echo \"exit \$?\" >> \"$ARGV[1]/escaped.txt\"", "x"' "$team" "$d" < /dev/null > /dev/null 2>&1
i=0; while [ ! -s "$d/escaped.txt" ] || ! grep -q '^exit' "$d/escaped.txt"; do sleep 0.1; i=$((i+1)); [ $i -lt 100 ] || break; done
# A request written by hand inside the turn, with its credential.
printf 'op: read\nturn: %s\narg: general\n\n' "$PANTHEON_TEAM_TURN" > "$box/.9-9.tmp" && mv "$box/.9-9.tmp" "$box/9-9.msg"
i=0; while [ ! -f "$box/9-9.reply" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
cat "$box/9-9.reply" > "$d/byhand.txt"
"$team" read general > "$d/own.txt" 2>&1
echo '# changed' >> "$team"
"$team" read general > "$d/changed.txt" 2>&1
` + claudeDone,
	})
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	var mu sync.Mutex
	var reqs []map[string]any
	a.teamCaller = func(req map[string]any) (map[string]any, error) {
		mu.Lock()
		defer mu.Unlock()
		reqs = append(reqs, req)
		return map[string]any{"code": float64(0), "text": "#general: hello"}, nil
	}
	folder := filepath.Join(t.TempDir(), "coder")
	if r := teamSetup(folder, "brief", "", false); r["success"] != true {
		t.Fatal(r)
	}
	res := a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "coder", Name: "Coder", Turn: "t1", Cred: "c1"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	read := func(name string) string {
		b, _ := os.ReadFile(filepath.Join(dir, name))
		return string(b)
	}
	if got := read("escaped.txt"); !strings.Contains(got, "not part of the agent's running Team Chat turn") || !strings.Contains(got, "exit 1") {
		t.Fatalf("an escaped process with the turn's credential: %q", got)
	}
	if got := read("byhand.txt"); !strings.Contains(got, "not part of the agent's running Team Chat turn") {
		t.Fatalf("a request written by hand: %q", got)
	}
	if got := read("own.txt"); !strings.Contains(got, "#general: hello") {
		t.Fatalf("the turn's own command: %q", got)
	}
	if got := read("changed.txt"); !strings.Contains(got, "was changed by something other than PantheonOS") {
		t.Fatalf("after the tool was changed: %q", got)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(reqs) != 1 {
		t.Fatalf("what reached the controller: %v", reqs)
	}
}
