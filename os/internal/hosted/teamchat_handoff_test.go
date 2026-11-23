package hosted

import (
	"strings"
	"testing"
	"time"
)

// A person handing a task to an agent wakes it in the task's thread: assigning it, or moving the agent's task
// back to In progress (from In review). The turn is told so, with the latest person's comment in the thread, and
// its final answer is posted in the thread. Other changes (Done, a task nobody holds) and the agent's own changes
// wake no one.
func TestAHandedOffTaskWakesItsAgent(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	ch := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "docs", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)

	id := call(t, h, mine, "post", map[string]any{"cid": ch, "text": "Fix the CI", "as_task": true})["message"].(map[string]any)["id"].(string)
	call(t, h, mine, "post", map[string]any{"cid": ch, "root": id, "text": "it fails on the Windows runner"})
	// Moving a task nobody holds wakes no one.
	call(t, h, mine, "task_update", map[string]any{"id": id, "state": "in_progress"})
	time.Sleep(80 * time.Millisecond)
	if n := nodes.promptCount(); n != 0 {
		t.Fatalf("a task nobody holds woke an agent (%d)", n)
	}
	call(t, h, mine, "task_update", map[string]any{"id": id, "assignee": writer})
	eventually(t, "writer's turn for the assignment", func() bool { return nodes.promptCount() == 1 })
	p := nodes.lastPrompt()
	if !strings.Contains(p, "@owner assigned this task to you") || !strings.Contains(p, "it fails on the Windows runner") ||
		!strings.Contains(p, "This is task "+id+", In progress, yours") || !strings.Contains(p, "reply "+id) {
		t.Fatalf("turn input: %s", p)
	}
	// The agent's own change wakes no one (it is working: nothing is queued).
	if r := agentSays(tc, writer, "task", []string{"state", id, "in_review"}, nil, "Fixed the path separator."); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	time.Sleep(80 * time.Millisecond)
	if q := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["queue"]; q != float64(0) {
		t.Fatalf("the agent's own change queued a wake: %v", q)
	}
	nodes.release <- struct{}{}
	eventually(t, "the first turn ends", func() bool {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["state"] == "ready"
	})

	// Returned from In review: woken again, told it is not finished; its final answer goes in the thread.
	call(t, h, mine, "post", map[string]any{"cid": ch, "root": id, "text": "still red on arm64"})
	// A person's reply in a thread the agent answered in wakes it (the thread rule): let that turn end quietly.
	eventually(t, "the thread reply's turn", func() bool { return nodes.promptCount() == 2 })
	nodes.mu.Lock()
	nodes.reply = func(string) (string, string) { return "No reply needed.", "" }
	nodes.mu.Unlock()
	nodes.release <- struct{}{}
	eventually(t, "idle before the return", func() bool {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["state"] == "ready"
	})
	n := nodes.promptCount()
	nodes.mu.Lock()
	nodes.reply = func(string) (string, string) { return "Pinned the arm64 image too.", "" }
	nodes.mu.Unlock()
	call(t, h, mine, "task_update", map[string]any{"id": id, "state": "in_progress"})
	eventually(t, "writer's turn for the return", func() bool { return nodes.promptCount() == n+1 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "moved this task, yours, back to In progress") || !strings.Contains(p, "still red on arm64") {
		t.Fatalf("turn input: %s", p)
	}
	nodes.release <- struct{}{}
	eventually(t, "the answer in the task's thread", func() bool {
		m := lastFrom(msgs(t, h, mine, ch, id), writer)
		return m != nil && m["text"] == "Pinned the arm64 image too."
	})
	if m := lastFrom(msgs(t, h, mine, ch, ""), writer); m != nil {
		t.Fatalf("the answer was posted at the top level: %v", m)
	}

	// Done wakes no one.
	n = nodes.promptCount()
	call(t, h, mine, "task_update", map[string]any{"id": id, "state": "done"})
	time.Sleep(80 * time.Millisecond)
	if nodes.promptCount() != n {
		t.Fatal("Done woke the agent")
	}
}

// An agent records long work as a task: `task claim` on a message that is not a task makes it one it holds. The
// turn input says when to do it.
func TestAnAgentClaimsARequestAsATask(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	id := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer write the quarterly report"})["message"].(map[string]any)["id"].(string)
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 1 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "more than a quick answer") || !strings.Contains(p, "task claim <message id>") {
		t.Fatalf("turn input: %s", p)
	}
	r := agentSays(tc, writer, "task", []string{"claim", id}, nil, "")
	if r.Code != 0 || !strings.Contains(r.Text, "You hold task "+id) {
		t.Fatalf("%v", r)
	}
	var task map[string]any
	for _, x := range call(t, h, mine, "tasks", map[string]any{})["tasks"].([]any) {
		if x := x.(map[string]any); x["id"] == id {
			task = x
		}
	}
	if task == nil || task["assignee"] != writer || task["state"] != "in_progress" || task["created_by"] != writer {
		t.Fatalf("task: %v", task)
	}
	if r := agentSays(tc, writer, "task", []string{"claim", "nosuchmessage"}, nil, ""); r.Code == 0 || !strings.Contains(r.Text, "no task") {
		t.Fatalf("claim of no message: %v", r)
	}
	if r := agentSays(tc, writer, "task", []string{"state", id, "in_review"}, nil, "Report in report.md."); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	nodes.release <- struct{}{}
}
