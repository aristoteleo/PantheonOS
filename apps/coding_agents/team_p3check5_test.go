//go:build !windows

package codingagentsapp

// Fixes after the fifth Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the fifth Phase 3 check"): a Team Chat turn's environment is an
// allow-list with no PantheonOS key (only the model key, on PantheonOS
// models) and none of the node's plugin folders; a request is answered at
// most once per turn, whoever writes it again; the inbox is emptied when
// the turn ends.

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// Findings 1 and 3: the turn's runtime gets the model proxy and the model
// key, the computer basics and the user's own variables for the agent, and
// nothing else: no PantheonOS key, no Fleet key, no ssh agent, no plugin
// folders, no other variable of the node's. A Coding Agents turn (not Team
// Chat) keeps its PantheonOS access.
func TestATeamTurnCarriesOnlyTheModelKey(t *testing.T) {
	dir, _ := platformNode(t, claudeDone, codexDone)
	pbk := "pbk_" + strings.Repeat("K", 43)
	for name, value := range map[string]string{"CLAUDE_CODE_PLUGIN_DIRS": "/plugins/hooked", "FLEET_KEY": "eyJfleet",
		"SSH_AUTH_SOCK": "/tmp/agent.sock", "ORCA_AGENT_HOOK_TOKEN": "orca-token", "NODE_OPTIONS": "--require /x.js",
		"PANTHEON_KEY": pbk, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"} {
		t.Setenv(name, value)
	}
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "ok"}, nil
	}
	setSource(t, a, map[string]string{"claude": "pantheon", "codex": "pantheon"})
	if r := a.configure(Configure{Agents: map[string]codingagents.AgentSettings{
		"claude": {ModelSource: "pantheon", Env: map[string]string{"MY_VAR": "1", "CLAUDE_CODE_PLUGIN_DIRS": "/mine", "PANTHEON_KEY": pbk}},
		"codex":  {ModelSource: "pantheon", Env: map[string]string{"MY_VAR": "2"}}}}); r["success"] != true {
		t.Fatal(r)
	}
	a.setAccess(pbk, 3, "https://hub.test", false)
	for _, agent := range []string{"claude", "codex"} {
		folder := filepath.Join(t.TempDir(), agent)
		if r := teamSetup(folder, "brief", "", false); r["success"] != true {
			t.Fatal(r)
		}
		res := a.send(SendArgs{Agent: agent, Cwd: folder, Prompt: "[Team Chat] go", Auto: true, Origin: "team-chat",
			Team: &TeamTurn{MID: "a1", Token: "tok", Handle: agent, Name: agent, Turn: "t1", Cred: "c1"}})
		if res["success"] != true {
			t.Fatal(res)
		}
		waitIdle(t, a, res["conversation_id"].(string))
		env := "\n" + readText(t, filepath.Join(dir, agent+"-env.txt"))
		calls := readText(t, filepath.Join(dir, agent+"-calls.txt"))
		for _, bad := range []string{"pbk_", "PANTHEON_KEY=", "PANTHEON_HUB_URL", "PLUGIN", "FLEET_KEY", "eyJfleet", "SSH_AUTH_SOCK",
			"ORCA_", "NODE_OPTIONS", "PANTHEON_CODING_CONVERSATION", "\nLLM_API_KEY=", "sk-ant-users-own", "sk-users-own"} {
			if strings.Contains(env, bad) {
				t.Errorf("%s's Team Chat turn environment has %s:\n%s", agent, bad, env)
			}
		}
		if strings.Contains(calls, "signed in as the user") {
			t.Errorf("%s's Team Chat turn was told the pantheon command is signed in:\n%s", agent, calls)
		}
		// The model key is the turn's own token since the sixth check
		// (team_p3check6_test.go); the user's variables no longer pass.
		want := map[string]string{"claude": "ANTHROPIC_AUTH_TOKEN=ptk_", "codex": "PANTHEON_LLM_API_KEY=ptk_"}[agent]
		if strings.Contains(env, platformKey) || strings.Contains(env, "MY_VAR=") {
			t.Errorf("%s's Team Chat turn environment has the model key or the user's variable:\n%s", agent, env)
		}
		for _, w := range []string{want, "HOME=", "PATH=", "LANG=en_US.UTF-8", "LC_ALL=en_US.UTF-8"} {
			if !strings.Contains(env, w) {
				t.Errorf("%s's Team Chat turn environment lacks %s:\n%s", agent, w, env)
			}
		}
		// Every variable is one the allow-list or PantheonOS put there.
		known := regexp.MustCompile(`^(HOME|USER|LOGNAME|SHELL|PATH|TMPDIR|TERM|CODEX_HOME|CLAUDE_CONFIG_DIR|LANG|LC_ALL|PWD|SHLVL|OLDPWD|_|MY_VAR|ANTHROPIC_[A-Z_]+|` +
			`PANTHEON_LLM_API_KEY|IS_SANDBOX|__CF_USER_TEXT_ENCODING)=`)
		for _, line := range strings.Split(strings.TrimSpace(env), "\n") {
			if !known.MatchString(line) {
				t.Errorf("%s's Team Chat turn environment has %q", agent, strings.SplitN(line, "=", 2)[0])
			}
		}
	}
	// A Coding Agents turn keeps the pantheon command signed in.
	id := a.send(SendArgs{Agent: "claude", Prompt: "hi", Auto: true})["conversation_id"].(string)
	waitIdle(t, a, id)
	if env := readText(t, filepath.Join(dir, "claude-env.txt")); !strings.Contains(env, "PANTHEON_KEY="+pbk) {
		t.Errorf("a Coding Agents turn lost its PantheonOS access:\n%s", env)
	}
}

// TeamEnv itself: the node's variables pass only when allow-listed, the
// user's own ones unless they are credentials or load code.
func TestTeamEnvIsAnAllowList(t *testing.T) {
	t.Setenv("PANTHEON_WORKSPACE_ENV", "")
	t.Setenv("PANTHEON_CODING_AGENTS_DIR", t.TempDir())
	t.Setenv("HOME", t.TempDir())
	t.Setenv("CLAUDE_CODE_PLUGIN_DIRS", "/p")
	t.Setenv("CODEX_HOME", "/codex-home")
	t.Setenv("GIT_ASKPASS", "/askpass")
	s := codingagents.Settings{Agents: map[string]codingagents.AgentSettings{"codex": {Env: map[string]string{
		"KEEP": "1", "DYLD_INSERT_LIBRARIES": "/x", "BASH_ENV": "/x", "PANTHEON_TOKEN": "t", "MY_PLUGIN_DIR": "/x"}}}}
	_ = s // the user's variables: none pass since the sixth check (team_p3check6_test.go)
	env := strings.Join(codingagents.TeamEnv("codex", nil, codingagents.Models{}), "\n")
	for _, bad := range []string{"CLAUDE_CODE_PLUGIN_DIRS", "GIT_ASKPASS", "DYLD_", "BASH_ENV", "PANTHEON_TOKEN", "MY_PLUGIN_DIR", "KEEP=1"} {
		if strings.Contains(env, bad) {
			t.Errorf("TeamEnv has %s:\n%s", bad, env)
		}
	}
	for _, w := range []string{"CODEX_HOME=/codex-home", "HOME="} {
		if !strings.Contains(env, w) {
			t.Errorf("TeamEnv lacks %s:\n%s", w, env)
		}
	}
}

// Finding 2: a process that removes the answer to the turn's own post
// before the turn reads it and writes the same request again under its id
// (a new file, byte for byte the same, while the turn's process still
// waits) gets it refused: an id is answered once per turn. The inbox is
// empty when the turn ends.
func TestAReplayedTeamRequestIsAnsweredOnce(t *testing.T) {
	if !procVouch {
		t.Skip("this system cannot read another process's arguments")
	}
	reqs, read := teamRun(t, `
( n=0; r=0; while [ $n -lt 40000 ] && [ ! -f "$d/stop" ]; do
	for f in "$box"/.*.tmp "$box"/*.msg; do
		[ -f "$f" ] || continue
		b=$(basename "$f"); id=${b#.}; id=${id%.tmp}; id=${id%.msg}
		[ -f "$d/c-$id" ] || cp "$f" "$d/c-$id" 2>/dev/null
	done
	for f in "$box"/*.reply; do
		[ -f "$f" ] && [ "$r" -lt 2 ] || continue
		id=$(basename "$f" .reply)
		[ -f "$d/c-$id" ] || continue
		cat "$f" >> "$d/answers" 2>/dev/null
		rm "$f" 2>/dev/null || continue
		r=$((r+1))
		cp "$d/c-$id" "$box/.r$id" && mv "$box/.r$id" "$box/$id.msg" && echo "$id" >> "$d/replays"
	done
	n=$((n+1))
done ) &
"$team" post general - > "$d/out.txt" 2>&1 <<'TEXT'
REPLAY-CHECK
TEXT
touch "$d/stop"
wait
ls -A "$box" > "$d/left-in-turn"
`)
	replays := strings.Count(read("replays"), "\n")
	if replays == 0 {
		t.Skip("no answer was caught before the turn read it on this computer")
	}
	posts := 0
	for _, r := range reqs {
		if r["op"] == "post" {
			posts++
		}
	}
	if posts != 1 {
		t.Fatalf("the post reached the controller %d times after %d replays: %v", posts, replays, reqs)
	}
	if got := read("answers") + read("out.txt"); !strings.Contains(got, "answered already") {
		t.Fatalf("the replay was not refused: %q", got)
	}
}

// The node empties the turn's inbox when the turn ends, whatever the turn
// left there.
func TestATeamTurnsInboxIsEmptiedWhenItEnds(t *testing.T) {
	var inbox string
	_, read := teamRun(t, `
printf 'op: read\narg: general\n\n' > "$box/.9-9.tmp"
printf '0\nstale\n' > "$box/8-8.reply"
echo "$box" > "$d/box"
`)
	inbox = strings.TrimSpace(read("box"))
	if inbox == "" {
		t.Fatal("no inbox")
	}
	names, err := os.ReadDir(inbox)
	if err != nil {
		t.Fatal(err)
	}
	if len(names) != 0 {
		t.Fatalf("left in the inbox after the turn: %v", names)
	}
}
