package codingagentsapp

import (
	"testing"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// Deleting a Team Chat Pantheon agent removes its conversation here and the
// Agent conversation that one continued; another agent's session stays.
func TestRemoveWithSessionDeletesThePantheonAgentConversation(t *testing.T) {
	a := NewApp(nil, "", t.TempDir())
	defer a.Close()
	var forgot [][2]string
	saved := forgetAgentSession
	defer func() { forgetAgentSession = saved }()
	forgetAgentSession = func(agentID, session string) error {
		forgot = append(forgot, [2]string{agentID, session})
		return nil
	}
	a.mu.Lock()
	a.convs["c1"] = &Conversation{ID: "c1", Agent: codingagents.PantheonAgent, Session: "chat-1", Status: "idle"}
	a.convs["c2"] = &Conversation{ID: "c2", Agent: "claude", Session: "sess-2", Status: "idle"}
	a.mu.Unlock()
	if out := a.removeWithSession("c1"); out["success"] != true || out["session_deleted"] != "chat-1" {
		t.Fatal(out)
	}
	if out := a.removeWithSession("c2"); out["success"] != true || out["session_deleted"] != nil {
		t.Fatal(out)
	}
	if len(forgot) != 2 || forgot[0] != [2]string{"pantheon", "chat-1"} {
		t.Fatal(forgot)
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(a.convs) != 0 {
		t.Fatal("conversations kept", a.convs)
	}
}
