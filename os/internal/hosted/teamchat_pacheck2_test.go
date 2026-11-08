package hosted

// Fixes after the second PA check (docs/apps/team-chat.md): a message
// waiting for an agent whose computer is away says so under it; the
// Workspace is never called paused when it may be starting; the wait behind
// earlier work in a Pantheon Agent's conversation reads as that; the Team
// Chat commands left have plain words.

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

func TestAMessageWaitingForAnAgentsComputerShowsUnderIt(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.detect = append(nodes.detect, map[string]any{"id": "pantheon", "conversation": true, "signed_in": true})
	tc := teamOn(t, url, nodes)
	tc.tick = 30 * time.Millisecond
	h, mine, _ := teamHost(t, tc)
	pan := makeAgent(t, h, mine, "Pan", "n_ws", "pantheon", nil)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc.Run(ctx)
	status := func(mid string) map[string]any {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[mid].(map[string]any)
	}
	set := func(f func()) { nodes.mu.Lock(); f(); nodes.mu.Unlock() }

	// The Workspace away (paused, restarting or resuming: the controller
	// cannot tell which): a mention in a thread waits under its thread.
	set(func() { nodes.online["n_ws"] = false })
	root := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "plan for today"})["message"].(map[string]any)["id"].(string)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@pan what is next", "root": root})
	eventually(t, "Pan waits under the thread", func() bool {
		s := status(pan)
		return s["state"] == "offline" && s["queue"] == float64(1) && s["cid"] == "general" && s["root"] == root && s["since"] != nil
	})
	if r := str(status(pan)["reason"]); r != WorkspaceAway || r == "Your Workspace is paused" || !strings.Contains(r, "starting") {
		t.Fatalf("Workspace reason %q", r)
	}

	// Any runtime: a Mac that is offline.
	set(func() { nodes.online["n_mac"] = false })
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer draft it"})
	eventually(t, "Writer waits under the message", func() bool {
		s := status(writer)
		return s["state"] == "offline" && s["cid"] == "general" && str(s["root"]) == "" && s["since"] != nil &&
			s["reason"] == "Node n_mac is offline"
	})

	// Back: answered, and the waiting state is gone.
	set(func() { nodes.online["n_ws"], nodes.online["n_mac"] = true, true })
	eventually(t, "both answered", func() bool {
		return lastFrom(msgs(t, h, mine, "general", root), pan) != nil && lastFrom(msgs(t, h, mine, "general", ""), writer) != nil &&
			status(pan)["state"] == "ready" && status(writer)["state"] == "ready"
	})
	if s := status(pan); s["cid"] != nil || s["queue"] != float64(0) {
		t.Fatalf("still waiting: %v", s)
	}
}

func TestAPantheonTurnWaitingForEarlierWorkSaysSo(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	nodes.detect = append(nodes.detect, map[string]any{"id": "pantheon", "conversation": true, "signed_in": true})
	nodes.held = []any{map[string]any{"seq": 1, "type": "wait", "status": "busy"}}
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	pan := makeAgent(t, h, mine, "Pan", "n_ws", "pantheon", nil)
	line := func() string {
		return str(call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[pan].(map[string]any)["line"])
	}
	set := func(f func()) { nodes.mu.Lock(); f(); nodes.mu.Unlock() }
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@pan hello"})
	eventually(t, "waiting for earlier work", func() bool { return line() == "Waiting for Pan to finish earlier work…" })
	// Its own run starts: no longer waiting.
	set(func() {
		nodes.held = []any{map[string]any{"seq": 1, "type": "wait", "status": "busy"}, map[string]any{"seq": 2, "type": "wait", "status": "done"}}
	})
	eventually(t, "its own run", func() bool { return line() == "Starting" })
	set(func() {
		nodes.held = append(nodes.held, map[string]any{"seq": 3, "type": "tool", "kind": "shell", "name": "shell__run_command", "input": "pantheon team task create '#general' - <<'EOF'"})
	})
	eventually(t, "its first step", func() bool { return line() == "Creating a task" })
	nodes.release <- struct{}{}
	eventually(t, "answered", func() bool { return lastFrom(msgs(t, h, mine, "general", ""), pan) != nil })
}

func TestTeamChatCommandsHavePlainWords(t *testing.T) {
	for _, team := range []string{"pantheon team", "'/home/me/PantheonAgents/writer/.team/pantheon-team'"} {
		for in, want := range map[string]string{
			" task create '#docs' - <<'EOF'\nWrite the intro\nEOF": "Creating a task",
			" task claim 01M6Y2DV7CK0MACVABW3CXN3FM":               "Taking a task",
			" task state 01M6Y2 in_review - <<'EOF'":               "Updating a task",
			" task assign 01M6Y2 @hana":                            "Assigning a task",
			" task list --state todo":                              "Looking at tasks",
			" search release checklist":                            "Searching messages",
			" remind 'in 2 hours' - <<'EOF'\ncheck CI\nEOF":        "Setting a reminder",
			" remind list":       "Looking at reminders",
			" remind cancel r12": "Cancelling a reminder",
			" join '#pc2-two'":   "Joining #pc2-two",
			" join \"#docs\"":    "Joining #docs",
		} {
			cmd := team + in
			if got := toolLine(codingagents.Event{Kind: codingagents.KindShell, Input: cmd}); got != want {
				t.Errorf("%q: %q, want %q", cmd, got, want)
			}
		}
	}
}
