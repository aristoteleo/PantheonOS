//go:build !windows

package codingagentsapp

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// pantheonTurn is `pantheon agent chat --json` answering one turn.
const pantheonTurn = `echo "KEY $PANTHEON_KEY" >> "$(dirname "$0")/calls.txt"
cat <<'EOF'
{"type":"chat","chat_id":"chat-1"}
{"type":"tool_call","id":"t1","name":"file_manager__read_file","args":{"file_path":"/work/acme/README.md","node":"n_mac"}}
{"type":"tool_result","id":"t1","name":"file_manager__read_file","output":"{\"content\": \"# Night Sky\\n\", \"success\": true}"}
{"type":"text","text":"Done."}
{"type":"done","chat_id":"chat-1","status":"finished","model":"acme/star-1","usage":{"input_tokens":10,"cached_tokens":0,"output_tokens":2,"context_tokens":12}}
EOF
`

// signIn lets agents on this node use PantheonOS.
func signIn(t *testing.T, a *App) {
	t.Helper()
	if r := a.setAccess("pbk_test", 7, "https://hub.example.org"); r["success"] != true {
		t.Fatal(r)
	}
}

// A canvas turn reaches the Pantheon Agent through the node's pantheon
// command: on this node, in the group's folder, the model tier as asked;
// the canvas settings, pinned files and linked groups in the message (it
// takes no instructions otherwise), nothing it cannot take (effort, roles,
// research off, images). The next turn continues its chat.
func TestCanvasTurnPantheon(t *testing.T) {
	record := fakeAgent(t, "pantheon", pantheonTurn)
	a := NewApp(nil, "", t.TempDir())
	t.Cleanup(a.Close)
	a.NodeID = "n_mac"
	signIn(t, a)
	repo, outside, pinned, img := canvasFixture(t)
	links := []Link{{ConversationID: "w", Name: "Writer", Direction: "out"}}
	res := a.send(SendArgs{Agent: codingagents.PantheonAgent, Cwd: repo, Prompt: "fix it", Model: "low", Auto: true,
		Origin: "canvas", Canvas: fullCanvas(outside, pinned, img, false), Links: &links, LinkName: "Researcher"})
	if res["success"] != true {
		t.Fatalf("send: %v", res)
	}
	id := res["conversation_id"].(string)
	if c := waitIdle(t, a, id); c["session_id"] != "chat-1" || c["last_error"] != nil {
		t.Fatalf("conversation %v", c)
	}
	calls, _ := os.ReadFile(record)
	first := string(calls)
	for _, want := range []string{
		"ARGS agent chat --json --node n_mac --workdir " + repo + " --model low\n[Focus on these folders:\n- " + outside,
		`This conversation is the group "Researcher"`, `send 'Writer' - <<'EOF'`,
		"These settings are for this turn and replace any from earlier turns.",
		"not the user's instruction.]\n\nfix it\n\nFiles I pinned for this turn (start from them):\n- " + pinned,
		"KEY pbk_test",
	} {
		if !strings.Contains(first, want) {
			t.Errorf("missing %q in:\n%s", want, first)
		}
	}
	for _, not := range []string{"explorer", "Agent tool", "Web research", "Reference images", img, "You are running on", "effort"} {
		if strings.Contains(first, not) {
			t.Errorf("%q reached the Pantheon Agent:\n%s", not, first)
		}
	}
	var tools []string
	for _, e := range events(a, id) {
		if e.Type == "tool" {
			tools = append(tools, e.Status+" "+e.Kind+" "+e.Path+" "+e.Output)
		}
	}
	if strings.Join(tools, "|") != "running read /work/acme/README.md |done   # Night Sky\n" {
		t.Errorf("tools %q", tools)
	}

	if r := a.send(SendArgs{ConversationID: id, Prompt: "again", Canvas: &CanvasTurn{Research: true}}); r["success"] != true {
		t.Fatal(r)
	}
	waitIdle(t, a, id)
	calls, _ = os.ReadFile(record)
	if second := string(calls)[len(first):]; !strings.Contains(second, "ARGS agent chat --json --chat chat-1 --node n_mac --workdir "+repo+" --model low\n") {
		t.Errorf("second turn:\n%s", second)
	}
}

// Without the node's key the Pantheon Agent cannot start: the send says so,
// naming it, and leaves no conversation; agents_detect says it is not
// signed in, with what a canvas turn can ask of it.
func TestPantheonAgentNeedsTheNodesKey(t *testing.T) {
	fakeAgent(t, "pantheon", pantheonTurn)
	a := NewApp(nil, "", t.TempDir())
	r := a.send(SendArgs{Agent: codingagents.PantheonAgent, Cwd: t.TempDir(), Prompt: "hi", Origin: "canvas"})
	if r["success"] != false || !strings.HasPrefix(r["error"].(string), "Could not start Pantheon Agent: it is not signed in on this node") {
		t.Fatalf("send %v", r)
	}
	if len(a.list("", "")["conversations"].([]*Conversation)) != 0 {
		t.Error("a conversation was left behind")
	}
	detected := func() (canvas string, signedIn any) {
		b, _ := json.Marshal(a.detect(false)["agents"])
		var agents []struct {
			ID       string          `json:"id"`
			Canvas   json.RawMessage `json:"canvas"`
			SignedIn any             `json:"signed_in"`
		}
		json.Unmarshal(b, &agents)
		for _, ag := range agents {
			if ag.ID == codingagents.PantheonAgent {
				return string(ag.Canvas), ag.SignedIn
			}
		}
		t.Fatal("the Pantheon Agent was not detected")
		return
	}
	canvas, in := detected()
	if canvas != `{"effort":null,"research":false,"images":false,"skills":false,"roles":false,"subagents":true,"models":["high","normal","low"]}` || in != false {
		t.Errorf("canvas %s, signed in %v", canvas, in)
	}
	signIn(t, a)
	if _, in := detected(); in != true {
		t.Errorf("signed in %v", in)
	}
}

// A turn the pantheon command could not start ends with its reason, naming
// the agent.
func TestPantheonTurnThatCannotStart(t *testing.T) {
	fakeAgent(t, "pantheon", `echo '{"type":"error","error":"App \"agent\" is not ready (starting); check it in Fleet"}'
exit 1`)
	a := NewApp(nil, "", t.TempDir())
	signIn(t, a)
	id := a.send(SendArgs{Agent: codingagents.PantheonAgent, Cwd: t.TempDir(), Prompt: "hi"})["conversation_id"].(string)
	if c := waitIdle(t, a, id); c["last_error"] != `Could not start Pantheon Agent: App "agent" is not ready (starting); check it in Fleet` {
		t.Errorf("last error %q", c["last_error"])
	}
}
