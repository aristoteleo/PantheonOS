//go:build !windows

package codingagentsapp

// A Team Chat agent on the Pantheon Agent runtime answers again (fixes after
// the Standalone Apps sixth check, docs/apps/team-chat.md): its turn is the
// pantheon command reaching the owner's Agent, which signs in with the
// node's key. The allow-listed turn environment from the fifth Phase 3 check
// had left the key out, so every turn exited at once ("not signed in", shown
// only as "Pantheon Agent exited: exit status 1").

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// pantheonSignedIn is the pantheon command as it behaves: without a key it
// says so the way staging.17 did and exits 1; with one, it answers.
const pantheonSignedIn = `env > "$(dirname "$0")/env.txt"
if [ -z "$PANTHEON_KEY" ]; then
  echo '{"error":"not signed in: run pantheon login","success":false}'
  exit 1
fi
` + pantheonTurn

func TestATeamTurnOfThePantheonAgentSignsIn(t *testing.T) {
	record := fakeAgent(t, "pantheon", pantheonSignedIn)
	t.Setenv("FLEET_KEY", "eyJfleet")
	t.Setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.NodeID = "n_ws"
	a.teamCaller = func(map[string]any) (map[string]any, error) {
		return map[string]any{"code": float64(0), "text": "ok"}, nil
	}
	signIn(t, a)
	folder := filepath.Join(t.TempDir(), "assistant")
	if r := teamSetup(folder, "brief", "", false); r["success"] != true {
		t.Fatal(r)
	}
	res := a.send(SendArgs{Agent: codingagents.PantheonAgent, Cwd: folder, Prompt: "[Team Chat] Reply with just: OK",
		Model: "low", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "assistant", Name: "Assistant", Turn: "t1", Cred: "c1"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	c := waitIdle(t, a, res["conversation_id"].(string))
	if c["last_error"] != nil || c["session_id"] != "chat-1" {
		t.Fatalf("the turn did not answer: %v", c)
	}
	calls := readText(t, record)
	if !strings.Contains(calls, "KEY pbk_test") {
		t.Errorf("the pantheon command was not signed in:\n%s", calls)
	}
	env := "\n" + readText(t, filepath.Join(filepath.Dir(record), "env.txt"))
	for _, w := range []string{"\nPANTHEON_KEY=pbk_test\n", "\nPANTHEON_HUB_URL=https://hub.example.org\n"} {
		if !strings.Contains(env, w) {
			t.Errorf("the turn's environment lacks %q:\n%s", strings.TrimSpace(w), env)
		}
	}
	// Still the allow-list otherwise.
	for _, bad := range []string{"FLEET_KEY", "eyJfleet", "SSH_AUTH_SOCK", "PANTHEON_CODING_CONVERSATION"} {
		if strings.Contains(env, bad) {
			t.Errorf("the turn's environment has %s:\n%s", bad, env)
		}
	}
	if strings.Contains(calls, "signed in as the user") {
		t.Errorf("the turn was told the pantheon command is signed in:\n%s", calls)
	}
}

// Other runtimes' Team Chat turns still get no PantheonOS key.
func TestTeamSignInIsThePantheonAgentsOnly(t *testing.T) {
	s := codingagents.Settings{Access: codingagents.Access{Key: "pbk_x", HubURL: "https://hub"}}
	for _, agent := range []string{"claude", "codex", "gemini", "opencode", "pi"} {
		if env := codingagents.TeamSignIn(s, agent); env != nil {
			t.Errorf("%s: %v", agent, env)
		}
	}
	if env := strings.Join(codingagents.TeamSignIn(s, codingagents.PantheonAgent), " "); env !=
		"PANTHEON_KEY=pbk_x PANTHEON_CLIENT=coding-agent/pantheon PANTHEON_HUB_URL=https://hub" {
		t.Errorf("pantheon: %s", env)
	}
	if env := codingagents.TeamSignIn(codingagents.Settings{}, codingagents.PantheonAgent); env != nil {
		t.Errorf("no key: %v", env)
	}
}

// pantheonTeamTurn is a Pantheon Agent turn as `pantheon agent chat --json
// --team-tool` runs it: a request of the Agent's conversation (`pantheon
// team post …` in the Agent's Shell App, outside this turn) is run by the
// turn's own process as the turn's pantheon-team, its child.
const pantheonTeamTurn = `tool=""; prev=""
for a in "$@"; do [ "$prev" = "--team-tool" ] && tool=$a; prev=$a; done
echo '{"type":"chat","chat_id":"chat-1"}'
if [ -n "$tool" ]; then
  out=$(printf 'hello from pan\n' | env -i PATH="$PATH" HOME="$HOME" /bin/sh "$tool" post '#general' - 2>&1)
  echo "CHILD $? $out" >> "$(dirname "$0")/calls.txt"
fi
echo '{"type":"text","text":"Done."}'
echo '{"type":"done","chat_id":"chat-1","status":"finished","model":"acme/star-1"}'
`

// The Pantheon Agent's Team Chat commands (docs/apps/team-chat.md, "Pantheon
// Agent turns: Team Chat tools"): the node gives its turn the turn's
// pantheon-team (--team-tool) and tells the Agent to use `pantheon team`;
// what the turn's process runs with that tool is a process of the turn, so
// the node answers it with the turn's credential.
func TestAPantheonAgentTurnCarriesItsTeamChatCommands(t *testing.T) {
	record := fakeAgent(t, "pantheon", pantheonTeamTurn)
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.NodeID = "n_ws"
	var mu sync.Mutex
	var got []map[string]any
	a.teamCaller = func(req map[string]any) (map[string]any, error) {
		mu.Lock()
		defer mu.Unlock()
		got = append(got, req)
		return map[string]any{"code": float64(0), "text": "Posted in #general."}, nil
	}
	signIn(t, a)
	folder := filepath.Join(t.TempDir(), "pan")
	if r := teamSetup(folder, "brief", "", false); r["success"] != true {
		t.Fatal(r)
	}
	res := a.send(SendArgs{Agent: codingagents.PantheonAgent, Cwd: folder, Prompt: "[Team Chat] read #general",
		Model: "low", Auto: true, Origin: "team-chat",
		Team: &TeamTurn{MID: "a1", Token: "tok", Handle: "pan", Name: "Pan", Turn: "t1", Cred: "c1"}})
	if res["success"] != true {
		t.Fatal(res)
	}
	waitIdle(t, a, res["conversation_id"].(string))
	calls := readText(t, record)
	script := filepath.Join(folder, ".team", "pantheon-team")
	if !strings.Contains(calls, "--team-tool "+script) {
		t.Fatalf("the turn was not given its tool:\n%s", calls)
	}
	if !strings.Contains(calls, "CHILD 0 Posted in #general.") {
		t.Fatalf("the turn's own pantheon-team was not answered:\n%s", calls)
	}
	mu.Lock()
	if len(got) != 1 || got[0]["op"] != "post" || got[0]["turn"] != "t1" || got[0]["cred"] != "c1" ||
		got[0]["text"] != "hello from pan\n" || mustJSONText(got[0]["args"]) != `["#general"]` {
		t.Fatalf("requests %v", got)
	}
	mu.Unlock()
	// The Agent is told `pantheon team`, not the script it cannot vouch for.
	if !strings.Contains(calls, "Talk to the team only with pantheon team: `pantheon team inbox`") ||
		strings.Contains(calls, "only with '"+script) {
		t.Fatalf("the turn's note:\n%s", calls)
	}

	// A Pantheon Agent turn that is not Team Chat's (Coding Agents, the
	// Agent Canvas) gets no tool.
	plain := a.send(SendArgs{Agent: codingagents.PantheonAgent, Cwd: folder, Prompt: "hello", Model: "low"})
	if plain["success"] != true {
		t.Fatal(plain)
	}
	waitIdle(t, a, plain["conversation_id"].(string))
	lines := strings.Split(readText(t, record), "\n")
	var last string
	for _, l := range lines {
		if strings.HasPrefix(l, "ARGS ") {
			last = l
		}
	}
	if strings.Contains(last, "--team-tool") {
		t.Fatalf("a turn outside Team Chat got the tool: %s", last)
	}
}

func mustJSONText(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}
