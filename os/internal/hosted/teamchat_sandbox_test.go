package hosted

// Shared agents run sandboxed (docs/apps/team-chat.md, "Shared agents run
// sandboxed"): a shared agent's turn asks its node for the sandbox; only
// the owner turns it off, confirming the words; a node that cannot apply
// it, or a runtime it does not run, refuses the turn.

import (
	"strings"
	"testing"
	"time"
)

// lastSend is the team part of the node's last agent_send.
func (f *fakeAgents) lastTeam() map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.sends) == 0 {
		return nil
	}
	team, _ := f.sends[len(f.sends)-1]["team"].(map[string]any)
	return team
}

func attentionOf(t *testing.T, s *sharedSpace, name string) string {
	t.Helper()
	m := memberByName(call(t, s.h, s.b, "space_get", map[string]any{}), name)
	if a, ok := m["attention"].(map[string]any); ok {
		return str(a["reason"]) + " | " + str(a["fix"])
	}
	return ""
}

func TestASharedAgentsTurnAsksForTheSandbox(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	// Only me: no sandbox.
	before := s.nodes.promptCount()
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@writer hi"})
	eventually(t, "writer's turn", func() bool { return s.nodes.promptCount() == before+1 })
	if s.nodes.lastTeam()["sandbox"] != false {
		t.Fatalf("an Only me agent's turn asked for the sandbox: %v", s.nodes.lastTeam())
	}
	// Shared (everyone): sandboxed, also when a member asks.
	before = s.nodes.promptCount()
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@shared hi"})
	eventually(t, "shared's turn", func() bool { return s.nodes.promptCount() == before+1 })
	if s.nodes.lastTeam()["sandbox"] != true {
		t.Fatalf("a shared agent's turn ran without the sandbox: %v", s.nodes.lastTeam())
	}
	time.Sleep(300 * time.Millisecond)
	// Off: only the owner, only with the words confirmed.
	if e := refusedAs(t, h, b, carol, "agent_update", map[string]any{"mid": s.shared, "sandbox": "off", "sandbox_confirm": sandboxOffText}); !strings.Contains(e, "Only the space owner") {
		t.Fatalf("an admin turned the sandbox off: %s", e)
	}
	for _, confirm := range []any{nil, "", "yes", strings.ToLower(sandboxOffText)} {
		args := map[string]any{"mid": s.shared, "sandbox": "off"}
		if confirm != nil {
			args["sandbox_confirm"] = confirm
		}
		if e := refused(t, h, b, "agent_update", args); !strings.Contains(e, "needs your confirmation") {
			t.Fatalf("sandbox off with confirm %v: %s", confirm, e)
		}
	}
	out := call(t, h, b, "agent_update", map[string]any{"mid": s.shared, "sandbox": "off", "sandbox_confirm": sandboxOffText})
	off, _ := out["agent"].(map[string]any)["sandbox_off"].(map[string]any)
	if off == nil || off["text"] != sandboxOffText || off["by"] != tcOwner || off["at"] == "" {
		t.Fatalf("the confirmation is not stored: %v", out["agent"])
	}
	before = s.nodes.promptCount()
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@shared again"})
	eventually(t, "shared's turn", func() bool { return s.nodes.promptCount() == before+1 })
	if s.nodes.lastTeam()["sandbox"] != false {
		t.Fatalf("the sandbox stayed on: %v", s.nodes.lastTeam())
	}
	time.Sleep(300 * time.Millisecond)
	out = call(t, h, b, "agent_update", map[string]any{"mid": s.shared, "sandbox": "on"})
	if out["agent"].(map[string]any)["sandbox_off"] != nil {
		t.Fatalf("the sandbox did not turn on: %v", out["agent"])
	}
	before = s.nodes.promptCount()
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@shared once more"})
	eventually(t, "shared's turn", func() bool { return s.nodes.promptCount() == before+1 })
	if s.nodes.lastTeam()["sandbox"] != true {
		t.Fatalf("the sandbox is not back on: %v", s.nodes.lastTeam())
	}
}

// A node with no sandbox, or one too old to know it, gets no turn of a
// shared agent: the owner sees why and how to fix it.
func TestASharedTurnWithoutASandboxIsRefused(t *testing.T) {
	for _, c := range []struct{ node, want string }{
		{"none", "this computer can't run shared agents in a sandbox"},
		{"old", "is too old to run shared agents in a sandbox"},
	} {
		s := newSharedSpace(t)
		s.nodes.mu.Lock()
		s.nodes.sandbox = c.node
		s.nodes.mu.Unlock()
		before := s.nodes.promptCount()
		callAs(t, s.h, s.b, alice, "post", map[string]any{"cid": "general", "text": "@shared hi"})
		eventually(t, "the refusal", func() bool { return strings.Contains(attentionOf(t, s, "Shared"), c.want) })
		if !strings.Contains(attentionOf(t, s, "Shared"), "turn the sandbox off in the agent's settings") {
			t.Errorf("%s: no way out: %s", c.node, attentionOf(t, s, "Shared"))
		}
		if s.nodes.promptCount() != before {
			t.Fatalf("%s: the turn reached the node", c.node)
		}
	}
}

// A runtime the sandbox does not run is refused before the node is asked;
// the node's own refusal is shown in its words.
func TestASharedAgentOnAnotherRuntimeIsRefused(t *testing.T) {
	s := newSharedSpace(t)
	s.nodes.mu.Lock()
	s.nodes.detect = append(s.nodes.detect, map[string]any{"id": "cursor", "conversation": true, "signed_in": true})
	s.nodes.mu.Unlock()
	gem := makeAgent(t, s.h, s.b, "Gem", "n_ws", "cursor", map[string]any{"access": "everyone"})
	_ = gem
	before := s.nodes.promptCount()
	callAs(t, s.h, s.b, alice, "post", map[string]any{"cid": "general", "text": "@gem hi"})
	eventually(t, "the refusal", func() bool {
		return strings.Contains(attentionOf(t, s, "Gem"), "Cursor Agent can't run in Team Chat's sandbox")
	})
	if s.nodes.promptCount() != before {
		t.Fatal("cursor's turn reached the node")
	}
	reason, fix, transient, known := explainKnown(&tcMember{Runtime: "codex"}, TeamNode{}, "Team Chat sandbox: A shared agent runs in a sandbox without your own Codex sign-in, so it needs PantheonOS models. Choose PantheonOS models.")
	if !known || transient || fix != "" || !strings.HasPrefix(reason, "A shared agent runs in a sandbox") {
		t.Fatalf("explain: %q %q %v %v", reason, fix, transient, known)
	}
}
