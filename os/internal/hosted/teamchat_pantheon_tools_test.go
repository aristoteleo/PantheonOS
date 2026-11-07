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
	send := nodes.sends[len(nodes.sends)-1]
	note := str(send["team"].(map[string]any)["note"])
	if send["agent"] != codingagents.PantheonAgent || !strings.Contains(note, "Talk to the team only with pantheon team: `pantheon team inbox`") ||
		strings.Contains(note, "{{TEAM}}") {
		t.Fatalf("note: %s", note)
	}
	dm2 := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm2, "text": "hello"})
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 2 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "/PantheonAgents/writer/.team/pantheon-team' dm @") {
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
