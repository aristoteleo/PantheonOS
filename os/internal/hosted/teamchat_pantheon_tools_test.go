package hosted

// A Pantheon Agent turn's Team Chat commands are `pantheon team`, which the
// turn's own process carries (docs/apps/team-chat.md, "Pantheon Agent turns:
// Team Chat tools"): its turn input, its turn's note and its AGENT.md say
// so; other runtimes keep the pantheon-team script in their folder.

import (
	"strings"
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

func TestAPantheonAgentIsToldPantheonTeam(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	nodes.detect = append(nodes.detect, map[string]any{"id": "pantheon", "conversation": true, "signed_in": true})
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	pan := makeAgent(t, h, mine, "Pan", "n_ws", "pantheon", nil)
	brief := str(nodes.setups[len(nodes.setups)-1]["agent_md"])
	if !strings.Contains(brief, "    pantheon team inbox") || !strings.Contains(brief, "Use the command pantheon team:") ||
		strings.Contains(brief, "{{TEAM}}") || strings.Contains(brief, "run it with sh") {
		t.Fatalf("AGENT.md:\n%s", brief)
	}
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	if b := str(nodes.setups[len(nodes.setups)-1]["agent_md"]); !strings.Contains(b, "{{TEAM}} inbox") {
		t.Fatalf("Writer's AGENT.md:\n%s", b)
	}
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{pan}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "read #general please"})
	eventually(t, "pan's turn", func() bool { return nodes.promptCount() == 1 })
	p := nodes.lastPrompt()
	if !strings.Contains(p, "pantheon team dm @") || !strings.Contains(p, "`pantheon team inbox`") ||
		strings.Contains(p, ".team/pantheon-team") {
		t.Fatalf("turn input:\n%s", p)
	}
	// It works from the Agent App, so it is told its folder, where its
	// commands start and where its memory goes (Fixes after the first PA check, M2).
	var folder string
	for _, x := range call(t, h, mine, "space_get", map[string]any{})["members"].([]any) {
		if m := x.(map[string]any); m["mid"] == pan {
			folder = str(m["folder"])
		}
	}
	if folder == "" || !strings.Contains(p, "Your folder is "+folder+": your commands start there") ||
		!strings.Contains(p, "first read "+folder+"/AGENT.md and "+folder+"/MEMORY.md") ||
		!strings.Contains(p, "remember in "+folder+"/MEMORY.md") {
		t.Fatalf("folder %q in the turn input:\n%s", folder, p)
	}
	send := nodes.sends[len(nodes.sends)-1]
	note := str(send["team"].(map[string]any)["note"])
	if !strings.Contains(note, "AGENT.md in your folder "+folder+";") {
		t.Fatalf("note without the folder: %s", note)
	}
	if send["agent"] != codingagents.PantheonAgent || !strings.Contains(note, "Talk to the team only with pantheon team: `pantheon team inbox`") ||
		strings.Contains(note, "{{TEAM}}") {
		t.Fatalf("note: %s", note)
	}
	dm2 := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm2, "text": "hello"})
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 2 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "/PantheonAgents/writer/.team/pantheon-team' dm @") ||
		strings.Contains(p, "Your folder is") {
		t.Fatalf("writer's turn input:\n%s", p)
	}
	if note := str(nodes.sends[len(nodes.sends)-1]["team"].(map[string]any)["note"]); !strings.Contains(note, "only with {{TEAM}}") {
		t.Fatalf("writer's note: %s", note)
	}
}

func TestAPantheonAgentsCommandsShowAsPlainWords(t *testing.T) {
	for in, want := range map[string]string{
		"pantheon team read '#general' --limit 3":             "Reading the conversation",
		"pantheon team post '#docs' - <<'EOF'\nhi\nEOF":       "Writing a message",
		"pantheon team inbox":                                 "Checking new messages",
		"cd /workspace/agents/pan && pantheon team skip done": "Deciding not to reply",
	} {
		if got := toolLine(codingagents.Event{Kind: codingagents.KindShell, Input: in}); got != want {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
}

// A Pantheon Agent turn that could not run says the real cause: its
// Workspace still starting (its messages wait), or its conversation busy
// with earlier work (Fixes after the first PA check, H1 and M1).
func TestAPantheonAgentTurnThatCouldNotRunSaysWhy(t *testing.T) {
	m := &tcMember{Handle: "pix", Name: "Pix", Runtime: codingagents.PantheonAgent, NodeID: "n_ws"}
	starting := `Your Workspace is still starting, so this conversation's shell is not ready yet (App "shell" is not ready (starting)); nothing was sent to the model. Try again in a minute.`
	if !appNotReady(starting) {
		t.Fatal("a Workspace still starting does not wait")
	}
	if reason, _, transient := explain(m, TeamNode{}, starting); !strings.Contains(reason, "Workspace is still starting") || !transient {
		t.Fatalf("starting: %q", reason)
	}
	for _, busy := range []string{
		"This conversation is still busy with earlier work (a run that is not this Team Chat turn, such as the follow-up of a background task); the message was not sent. Retry when that work has finished.",
		"This conversation is already running other work (such as the follow-up of a background task); the message was queued into that run, not answered in a turn of its own",
		"This conversation is already running; the message was queued for it",
	} {
		reason, fix, _ := explain(m, TeamNode{}, busy)
		if !strings.Contains(reason, "busy with earlier work") || strings.Contains(reason, "its turn failed") || !strings.Contains(fix, "Retry") {
			t.Fatalf("%q: %q %q", busy, reason, fix)
		}
	}
}
