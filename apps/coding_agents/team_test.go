//go:build !windows

package codingagentsapp

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
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
}
