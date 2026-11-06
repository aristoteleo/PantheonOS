//go:build !windows

package codingagentsapp

// Shared agents run sandboxed (docs/apps/team-chat.md, "Shared agents run
// sandboxed"): a shared agent's Team Chat turn runs its whole runtime in
// the OS sandbox (macOS: sandbox-exec, real here), with a home of its own;
// a turn that cannot be sandboxed is refused, never run without it.

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// sandboxProbe is a fake runtime's body: it tries what a shared turn must
// not do and what it must still do, and writes one line per try into the
// agent's folder (its working directory): "denied" or "readable" and the
// like, never what it read.
func sandboxProbe(name string, v map[string]string) string {
	q := func(k string) string { return shellQuote(v[k]) }
	return `case "$*" in *--help*|*"features list"*) exit 0;; esac
out="$PWD/probe-` + name + `.txt"
: > "$out"
say() { echo "$*" >> "$out"; }
for f in ` + q("ssh") + ` ` + q("auth") + ` ` + q("settings") + ` ` + q("realssh") + ` ` + q("realcodex") + ` ` + q("realclaude") + ` ` + q("realclaudejson") + `; do
	if cat "$f" > /dev/null 2>&1; then say "READABLE $f"; else say "denied read $f"; fi
done
if ls ` + q("users") + ` > /dev/null 2>&1; then say "LISTED users"; else say "denied list users"; fi
if ls ` + q("other") + ` > /dev/null 2>&1; then say "LISTED other"; else say "denied list other"; fi
if stat ` + q("ssh") + ` > /dev/null 2>&1; then say "STAT secret"; else say "denied stat secret"; fi
if (echo x > ` + q("outside") + `) 2> /dev/null; then say "WROTE outside"; else say "denied write outside"; fi
if (echo x >> "$PWD/.team/pantheon-team") 2> /dev/null; then say "WROTE team script"; else say "denied write team script"; fi
if (echo x > "$PWD/mine.txt") 2> /dev/null && cat "$PWD/mine.txt" > /dev/null; then say "ok folder"; else say "FAILED folder"; fi
if kill -0 ` + q("victim") + ` 2> /dev/null; then say "SIGNALLED outside"; else say "denied signal outside"; fi
if security list-keychains > /dev/null 2>&1; then say "KEYCHAIN list"; else say "denied keychain list"; fi
if security find-generic-password -s ` + q("item") + ` > /dev/null 2>&1; then say "KEYCHAIN item"; else say "denied keychain item"; fi
c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' ` + q("control") + `)
if [ "$c" = 200 ]; then say "CALLED control"; else say "denied control $c"; fi
gw="$ANTHROPIC_BASE_URL"
[ -n "$gw" ] || gw=$(echo "$*" | sed -n 's/.*base_url="\([^"]*\)\/v1".*/\1/p')
tok="$ANTHROPIC_AUTH_TOKEN$PANTHEON_LLM_API_KEY"
c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" "$gw/v1/models")
say "gateway $c"
if [ -x /bin/zsh ]; then h=$(/bin/zsh -c 'cat <<EOF
heredoc
EOF' 2>&1); say "zsh $h"; else say "zsh heredoc"; fi
t=$("$PWD/.team/pantheon-team" members 2>&1)
say "team $t"
say "home $HOME"
say "config claude=$CLAUDE_CONFIG_DIR codex=$CODEX_HOME"
` + map[string]string{"claude": claudeDone, "codex": codexDone}[name]
}

// sandboxTrial runs one sandboxed Team Chat turn of each runtime and
// returns what each probe found.
func sandboxTrial(t *testing.T) (probes map[string]string, root string) {
	t.Helper()
	if runtime.GOOS != "darwin" {
		t.Skip("the real sandbox here is macOS's")
	}
	if kind, why := codingagents.SandboxKind(); kind == "" {
		t.Skip("no sandbox: " + why)
	}
	realHome, _ := os.UserHomeDir()
	root = t.TempDir()
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", root)
	secrets := t.TempDir()
	_ = os.MkdirAll(filepath.Join(secrets, ".ssh"), 0o700)
	_ = os.WriteFile(filepath.Join(secrets, ".ssh", "id_ed25519"), []byte("SECRET-KEY"), 0o600)
	_ = os.WriteFile(filepath.Join(secrets, "auth.json"), []byte(`{"token":"SECRET"}`), 0o600)
	other := filepath.Join(secrets, "other-user")
	_ = os.MkdirAll(other, 0o755)
	victim := exec.Command("/bin/sleep", "60")
	if err := victim.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = victim.Process.Kill(); _, _ = victim.Process.Wait() })
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("node")) }))
	t.Cleanup(control.Close)
	v := map[string]string{"ssh": filepath.Join(secrets, ".ssh", "id_ed25519"), "auth": filepath.Join(secrets, "auth.json"),
		"settings": filepath.Join(root, "settings.json"), "realssh": filepath.Join(realHome, ".ssh", "config"),
		"realcodex": filepath.Join(realHome, ".codex", "auth.json"), "realclaude": filepath.Join(realHome, ".claude", "settings.json"),
		"realclaudejson": filepath.Join(realHome, ".claude.json"), "users": "/Users", "other": other,
		"outside": filepath.Join(secrets, "written.txt"), "victim": fmt.Sprint(victim.Process.Pid),
		"item": "Claude Code-credentials", "control": control.URL}
	platformAgents(t, map[string]string{"claude": sandboxProbe("claude", v), "codex": sandboxProbe("codex", v)})
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "TEAM-OK"}, nil
	}
	setSource(t, a, map[string]string{"claude": "pantheon", "codex": "pantheon"})
	if _, err := os.Stat(filepath.Join(root, "settings.json")); err != nil {
		t.Fatalf("no node settings.json to protect: %v", err)
	}
	probes = map[string]string{}
	for _, agent := range []string{"claude", "codex"} {
		folder := filepath.Join(t.TempDir(), agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		res := a.send(SendArgs{Agent: agent, Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
			Team: &TeamTurn{MID: "a" + agent, Token: "tok", Handle: agent, Name: agent, Turn: "t1", Cred: "c1", Sandbox: true}})
		if res["success"] != true {
			t.Fatal(res)
		}
		waitIdle(t, a, res["conversation_id"].(string))
		probes[agent] = readText(t, filepath.Join(folder, "probe-"+agent+".txt"))
		if _, err := os.Stat(v["outside"]); err == nil {
			t.Fatalf("%s's sandboxed turn wrote outside its folder", agent)
		}
	}
	return probes, root
}

// A sandboxed turn reads and writes only its folder and its own home: not
// the user's secrets, the node's settings, other people's folders, nor
// anything outside; it signals no process outside, reaches no Keychain and
// no loopback port but the model gateway; pantheon-team still works.
func TestASharedAgentsTurnRunsInTheSandbox(t *testing.T) {
	probes, root := sandboxTrial(t)
	for agent, got := range probes {
		for _, want := range []string{"denied list users", "denied list other", "denied stat secret", "denied write outside",
			"denied write team script", "ok folder", "denied signal outside", "denied keychain list", "denied keychain item",
			"gateway 200", "team TEAM-OK", "zsh heredoc"} {
			if !strings.Contains(got, want+"\n") {
				t.Errorf("%s: want %q:\n%s", agent, want, got)
			}
		}
		for _, line := range strings.Split(got, "\n") {
			if strings.HasPrefix(line, "READABLE") || strings.HasPrefix(line, "CALLED") {
				t.Errorf("%s: %s", agent, line)
			}
			if strings.HasPrefix(line, "denied control ") && strings.HasSuffix(line, " 200") {
				t.Errorf("%s: called the node's control endpoint", agent)
			}
		}
		if !strings.Contains(got, "home "+filepath.Join(evalPath(root), "team-sandbox", "a"+agent, "home")+"\n") {
			t.Errorf("%s: its HOME is not its own sandbox home:\n%s", agent, got)
		}
		own := filepath.Join(evalPath(root), "team-sandbox", "a"+agent, "home")
		if !strings.Contains(got, "config claude="+own+"/.claude codex="+own+"/.codex\n") {
			t.Errorf("%s: its config home is not its own:\n%s", agent, got)
		}
	}
}

func evalPath(p string) string {
	if r, err := filepath.EvalSymlinks(p); err == nil {
		return r
	}
	return p
}

// A turn that cannot run in its sandbox is refused, with words for the
// owner: no sandbox on this computer, a runtime the sandbox does not run,
// or the agent on the user's own subscription. Never run unsandboxed.
func TestASharedTurnThatCannotBeSandboxedIsRefused(t *testing.T) {
	dir, _ := platformAgents(t, map[string]string{"claude": claudeDone, "codex": codexDone, "gemini": "exit 0"})
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	setSource(t, a, map[string]string{"claude": "pantheon", "codex": "subscription"})
	turn := func(agent string) map[string]any {
		folder := filepath.Join(t.TempDir(), agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		return a.send(SendArgs{Agent: agent, Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
			Team: &TeamTurn{MID: "a1", Token: "tok", Handle: agent, Name: agent, Turn: "t1", Cred: "c1", Sandbox: true}})
	}
	ran := func(agent string) bool { _, err := os.Stat(filepath.Join(dir, agent+"-calls.txt")); return err == nil }
	saved := codingagents.SandboxKind
	codingagents.SandboxKind = func() (string, string) { return "", "no sandbox on this system" }
	res := turn("claude")
	codingagents.SandboxKind = saved
	if res["success"] == true || !strings.Contains(fmt.Sprint(res["error"]), "This computer can't run shared agents in a sandbox. The owner can turn the sandbox off") {
		t.Errorf("no sandbox: %v", res)
	}
	if ran("claude") {
		t.Fatal("claude ran without its sandbox")
	}
	if kind, _ := codingagents.SandboxKind(); kind == "" {
		return // the rest needs a sandbox
	}
	if res := turn("codex"); res["success"] == true || !strings.Contains(fmt.Sprint(res["error"]), "needs PantheonOS models") {
		t.Errorf("subscription: %v", res)
	}
	if ran("codex") {
		t.Fatal("codex ran on the user's own sign-in")
	}
	if _, err := exec.LookPath("gemini"); err == nil {
		if res := turn("gemini"); res["success"] == true || !strings.Contains(fmt.Sprint(res["error"]), "can't run in Team Chat's sandbox") {
			t.Errorf("gemini: %v", res)
		}
		if ran("gemini") {
			t.Fatal("gemini ran without its sandbox")
		}
	}
	// An Only me agent's turn (no sandbox asked) and a Coding Agents turn
	// still run as before.
	folder := filepath.Join(t.TempDir(), "mine")
	_ = teamSetup(folder, "brief", "", false)
	res = a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a2", Token: "tok", Handle: "c", Name: "c", Turn: "t1", Cred: "c1"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	if args := readText(t, filepath.Join(dir, "claude-calls.txt")); args == "" {
		t.Error("an Only me agent's turn did not run")
	}
	if env := readText(t, filepath.Join(dir, "claude-env.txt")); strings.Contains(env, "team-sandbox") {
		t.Errorf("an Only me agent's turn ran with a sandbox home:\n%s", env)
	}
}

// A conversation that ran outside the sandbox is not resumed in it (its
// session lives in the user's home), nor the reverse: Team Chat starts a
// new one ("start a new one to use").
func TestASandboxChangeStartsANewConversation(t *testing.T) {
	if kind, _ := codingagents.SandboxKind(); kind == "" {
		t.Skip("no sandbox here")
	}
	platformAgents(t, map[string]string{"claude": claudeDone, "codex": codexDone})
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	setSource(t, a, map[string]string{"claude": "pantheon"})
	folder := filepath.Join(t.TempDir(), "w")
	_ = teamSetup(folder, "brief", "", false)
	tt := TeamTurn{MID: "a3", Token: "tok", Handle: "w", Name: "w", Turn: "t1", Cred: "c1"}
	res := a.send(SendArgs{Agent: "claude", Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat", Team: &tt})
	id := res["conversation_id"].(string)
	waitIdle(t, a, id)
	tt.Sandbox = true
	res = a.send(SendArgs{ConversationID: id, Prompt: "[Team Chat] again", Auto: true, Origin: "team-chat", Team: &tt})
	if res["success"] == true || !strings.Contains(fmt.Sprint(res["error"]), "start a new one to use") {
		t.Fatalf("resumed an unsandboxed conversation in the sandbox: %v", res)
	}
}
