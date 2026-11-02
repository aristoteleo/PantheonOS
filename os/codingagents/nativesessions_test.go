package codingagents

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// copyTree copies testdata/<from> into dir, the newest file first in want
// order: mtimes say which session is newer.
func copyTree(t *testing.T, from, dir string) {
	t.Helper()
	root := filepath.Join("testdata", from)
	err := filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		b, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		dst := filepath.Join(dir, rel)
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}
		return os.WriteFile(dst, b, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
}

func touch(t *testing.T, path string, at time.Time) {
	t.Helper()
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
}

// Pi's own session files (pi-sessions/: Pi 1.4.0 runs, one named with
// /name, one resumed with --session <id> for a second prompt) list as
// sessions: the name or the first prompt as title, the folder and model from
// the file; files that are not sessions or never got a prompt are left out.
func TestPiSessions(t *testing.T) {
	agentDir := t.TempDir()
	t.Setenv("PI_CODING_AGENT_DIR", agentDir)
	t.Setenv("PI_CODING_AGENT_SESSION_DIR", "")
	sessions := filepath.Join(agentDir, "sessions")
	copyTree(t, "pi-sessions", sessions)
	now := time.Now()
	touch(t, filepath.Join(sessions, "--work-acme--", "2026-11-02T00-28-47-147Z_01a199a8-9eaa-765e-94da-e2d6a62567ef.jsonl"), now)
	touch(t, filepath.Join(sessions, "--work-piproj--", "2026-11-02T00-24-13-941Z_01a199a4-7374-75b6-8fa8-0d037a3922ac.jsonl"), now.Add(-time.Hour))
	// A session that never got a prompt, and a file that is no session.
	unstarted := filepath.Join(sessions, "--work-new--", "2026-11-02T01-00-00-000Z_01a199b0-0000-7000-8000-000000000000.jsonl")
	_ = os.MkdirAll(filepath.Dir(unstarted), 0o755)
	_ = os.WriteFile(unstarted, []byte(`{"type":"session","version":3,"id":"01a199b0-0000-7000-8000-000000000000","timestamp":"2026-11-02T01:00:00.000Z","cwd":"/work/new"}`+"\n"), 0o644)
	_ = os.WriteFile(filepath.Join(sessions, "--work-new--", "notes.jsonl"), []byte("{\"type\":\"x\"}\n"), 0o644)

	got := PiSessions(10)
	if len(got) != 2 {
		t.Fatalf("%d sessions: %+v", len(got), got)
	}
	named, resumed := got[0], got[1]
	if named.Agent != "pi" || named.ID != "01a199a8-9eaa-765e-94da-e2d6a62567ef" || named.Title != "Night sky README" ||
		named.Preview != "Add a README to this project" || named.Cwd != "/work/acme" || named.Model != "m1" ||
		named.CreatedAt != float64(time.Date(2026, 11, 2, 0, 28, 47, 0, time.UTC).Unix()) || named.UpdatedAt != float64(now.Unix()) {
		t.Errorf("named %+v", named)
	}
	if resumed.ID != "01a199a4-7374-75b6-8fa8-0d037a3922ac" || resumed.Title != "hello from the pi tui" || resumed.Cwd != "/work/piproj" {
		t.Errorf("resumed %+v", resumed)
	}
	if got := PiSessions(1); len(got) != 1 || got[0].ID != named.ID {
		t.Errorf("limit 1: %+v", got)
	}
}

// Pi's session folder is the one it uses itself: its environment's, else
// its settings' (flat, no per-folder subfolders), else under its folder.
func TestPiSessionsDir(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("PI_CODING_AGENT_DIR", "")
	t.Setenv("PI_CODING_AGENT_SESSION_DIR", "")
	if got := piSessionsDir(); got != filepath.Join(home, ".pi", "agent", "sessions") {
		t.Errorf("default %s", got)
	}
	_ = os.MkdirAll(filepath.Join(home, ".pi", "agent"), 0o755)
	_ = os.WriteFile(filepath.Join(home, ".pi", "agent", "settings.json"), []byte(`{"sessionDir":"~/pi-sessions"}`), 0o644)
	if got := piSessionsDir(); got != filepath.Join(home, "pi-sessions") {
		t.Errorf("settings %s", got)
	}
	// A flat folder of sessions.
	src := filepath.Join("testdata", "pi-sessions", "--work-acme--", "2026-11-02T00-28-47-147Z_01a199a8-9eaa-765e-94da-e2d6a62567ef.jsonl")
	b, _ := os.ReadFile(src)
	_ = os.MkdirAll(filepath.Join(home, "pi-sessions"), 0o755)
	_ = os.WriteFile(filepath.Join(home, "pi-sessions", filepath.Base(src)), b, 0o644)
	if got := PiSessions(5); len(got) != 1 || got[0].Title != "Night sky README" {
		t.Errorf("flat %+v", got)
	}
	_ = os.WriteFile(filepath.Join(home, ".pi", "agent", "settings.json"), []byte(`{"sessionDir":"relative/dir"}`), 0o644)
	if got := piSessionsDir(); got != filepath.Join(home, ".pi", "agent", "sessions") {
		t.Errorf("a relative sessionDir is not followed: %s", got)
	}
	t.Setenv("PI_CODING_AGENT_DIR", "~/elsewhere")
	if got := piSessionsDir(); got != filepath.Join(home, "elsewhere", "sessions") {
		t.Errorf("agent dir %s", got)
	}
	t.Setenv("PI_CODING_AGENT_SESSION_DIR", "/srv/pi")
	if got := piSessionsDir(); got != "/srv/pi" {
		t.Errorf("session dir %s", got)
	}
}

// fakeOpenCode is an opencode that answers `db` with dbScript and `session
// list` with listScript (both shell), recording the folder each ran in.
func fakeOpenCode(t *testing.T, dbScript, listScript string) (path, record string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("a shell script stands in for opencode")
	}
	dir := t.TempDir()
	record = filepath.Join(dir, "calls.txt")
	script := "#!/bin/sh\necho \"$PWD $*\" >> " + record + "\ncase \"$1\" in\ndb)\n" + dbScript + "\n;;\nsession)\n" + listScript + "\n;;\nesac\n"
	path = filepath.Join(dir, "opencode")
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return path, record
}

func fixturePath(t *testing.T, name string) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// OpenCode's sessions come from its database through the CLI
// (opencode-sessions-db.json: the real output of the query, OpenCode 1.19.5,
// sessions run in a git repository and in a plain folder, one of them
// spawning a sub-agent, whose session the query leaves out).
func TestOpenCodeSessions(t *testing.T) {
	path, record := fakeOpenCode(t, "cat '"+fixturePath(t, "opencode-sessions-db.json")+"'", "exit 1")
	got, err := OpenCodeSessions(context.Background(), path, os.Environ(), 50, []string{t.TempDir()})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 8 {
		t.Fatalf("%d sessions", len(got))
	}
	first := got[0]
	// Its title is still OpenCode's placeholder: the prompt stands in.
	if first.Agent != "opencode" || first.ID != "ses_e6657ea10ffe0yqzakTXBq2Hwc" || first.Title != "SPAWN a sub-agent please" ||
		first.Preview != "SPAWN a sub-agent please" || first.Cwd != "/work/repoA" || first.Model != "fake/m1" ||
		first.CreatedAt != 1793579292.143 || first.UpdatedAt != 1793579295.982 {
		t.Errorf("first %+v", first)
	}
	if got[1].Title != "PONG" || got[1].Preview != "opencode tui prompt" {
		t.Errorf("second %+v", got[1])
	}
	calls, _ := os.ReadFile(record)
	if c := string(calls); strings.Count(c, " db SELECT ") != 1 || !strings.Contains(c, "LIMIT 50 --format json") ||
		strings.Contains(c, "session list") {
		t.Errorf("calls %q", calls)
	}
}

// Without the database (an OpenCode without `db`), each folder's project is
// asked with `session list` (opencode-session-list.json: its real output in
// the repository): the sessions once each, newest first is left to the
// caller.
func TestOpenCodeSessionsPerFolder(t *testing.T) {
	repo, plain := t.TempDir(), t.TempDir()
	list := fixturePath(t, "opencode-session-list.json")
	path, record := fakeOpenCode(t, "echo 'Error: Unexpected error' >&2; echo 'no such column: s.time_archived' >&2; exit 1",
		"case \"$PWD\" in *"+filepath.Base(repo)+") cat '"+list+"';; *) echo '[]';; esac")
	got, err := OpenCodeSessions(context.Background(), path, os.Environ(), 20, []string{repo, plain, repo, filepath.Join(plain, "gone")})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 5 || got[0].ID != "ses_e6657ea10ffe0yqzakTXBq2Hwc" || got[0].Title != "New session - 2026-11-02T00:28:12.143Z" ||
		got[1].Title != "PONG" || got[1].Cwd != "/work/repoA" || got[1].UpdatedAt != 1793579262.079 {
		t.Errorf("%+v", got)
	}
	calls, _ := os.ReadFile(record)
	if !strings.Contains(string(calls), "session list --format json -n 20") {
		t.Errorf("calls %s", calls)
	}
	// Nothing to read at all: the database's error says why.
	path, _ = fakeOpenCode(t, "echo 'Error: Unexpected error' >&2; echo 'no such column: s.time_archived' >&2; exit 1", "exit 1")
	if _, err := OpenCodeSessions(context.Background(), path, os.Environ(), 20, nil); err == nil ||
		err.Error() != "opencode db: no such column: s.time_archived" {
		t.Errorf("error %v", err)
	}
}

// The terminal hooks of Pi and OpenCode are files in the node's state folder,
// given to the launch: Pi's an extension, OpenCode's a plugin laid over the
// configuration the launch has (the platform's, with its own plugins kept).
func TestPiAndOpenCodeHooks(t *testing.T) {
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	args, err := PiHookArgs()
	if err != nil || len(args) != 2 || args[0] != "-e" || args[1] != filepath.Join(Root(), "hooks", "pi-hooks.js") {
		t.Fatalf("%v %v", args, err)
	}
	b, _ := os.ReadFile(args[1])
	for _, want := range []string{"PANTHEON_AGENT_HOOK_URL", "X-Pantheon-Hook", `"session_start"`, `"input"`, `"agent_end"`, "getSessionId()"} {
		if !strings.Contains(string(b), want) {
			t.Errorf("pi extension lacks %s", want)
		}
	}
	if st, _ := os.Stat(args[1]); st.Mode().Perm() != 0o600 {
		t.Errorf("mode %v", st.Mode())
	}

	plugin := "file://" + filepath.ToSlash(filepath.Join(Root(), "hooks", "opencode-hooks.js"))
	config, err := OpenCodeHookConfig(`{"plugin":["mine"],"provider":{"pantheon":{"name":"PantheonOS"}},"model":"pantheon/x"}`)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	_ = json.Unmarshal([]byte(config), &doc)
	if p, _ := doc["plugin"].([]any); len(p) != 2 || p[0] != "mine" || p[1] != plugin || doc["model"] != "pantheon/x" || doc["provider"] == nil {
		t.Errorf("config %s", config)
	}
	if config, err := OpenCodeHookConfig(""); err != nil || config != `{"plugin":["`+plugin+`"]}` {
		t.Errorf("alone %s %v", config, err)
	}
	if _, err := OpenCodeHookConfig("not json"); err == nil {
		t.Error("a configuration that is not JSON is not replaced")
	}
	b, _ = os.ReadFile(filepath.Join(Root(), "hooks", "opencode-hooks.js"))
	for _, want := range []string{`"chat.message"`, "session.idle", "parentID", "X-Pantheon-Hook"} {
		if !strings.Contains(string(b), want) {
			t.Errorf("opencode plugin lacks %s", want)
		}
	}
	for _, agent := range []string{"pi", "opencode"} {
		for event, want := range map[string]string{"SessionStart": "ready", "UserPromptSubmit": "working", "Stop": "ready"} {
			if got := HookState(agent, event, nil); got != want {
				t.Errorf("%s %s: %q", agent, event, got)
			}
		}
	}
}
