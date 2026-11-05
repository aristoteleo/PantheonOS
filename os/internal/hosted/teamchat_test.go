package hosted

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/aristoteleo/pantheonos/os/internal/auth"
	"github.com/aristoteleo/pantheonos/os/internal/deployments"
	"github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// fakeAgents is a node's Coding Agents service as Team Chat uses it: each
// turn ends at once with the reply the test sets (or an error).
type fakeAgents struct {
	mu      sync.Mutex
	online  map[string]bool
	gone    map[string]bool      // its record is gone (heartbeat stopped)
	deaf    map[string]bool      // online by its record, but nothing answers calls
	seen    map[string]time.Time // last heartbeat
	resume  string               // agent_send refuses to resume a session with this error
	prompts []string
	sends   []map[string]any
	reply   func(prompt string) (text, errText string)
	hold    bool // turns run until released
	release chan struct{}
	convs   map[string][]map[string]any
	stopped []string
	setups  []map[string]any
	files   map[string]int64
	detect  []any
	seq     int
}

func newFakeAgents() *fakeAgents {
	return &fakeAgents{online: map[string]bool{"n_mac": true, "n_ws": true}, convs: map[string][]map[string]any{},
		files: map[string]int64{}, release: make(chan struct{}, 16),
		reply: func(string) (string, string) { return "Done.", "" },
		detect: []any{map[string]any{"id": "claude", "conversation": true, "signed_in": true},
			map[string]any{"id": "codex", "conversation": true}, map[string]any{"id": "gemini", "conversation": true, "signed_in": false}}}
}

func (f *fakeAgents) Node(_ context.Context, _ string, id string) (TeamNode, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.gone[id] {
		return TeamNode{ID: id}, nil
	}
	kind := "machine"
	if id == "n_ws" {
		kind = "sandbox"
	}
	return TeamNode{ID: id, Name: "Node-" + id + ".local", Kind: kind, OS: "linux", Online: f.online[id], Seen: f.seen[id]}, nil
}

func (f *fakeAgents) Call(_ context.Context, _ string, node, method string, args map[string]any, _ time.Duration) (map[string]any, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.online[node] || f.gone[node] {
		return nil, fmt.Errorf("The node is offline; reconnect it before accessing its services")
	}
	if f.deaf[node] {
		return nil, fmt.Errorf("nats: no responders available for request")
	}
	switch method {
	case "agents_detect":
		return map[string]any{"success": true, "agents": f.detect, "home": "/home/me"}, nil
	case "team_setup":
		f.setups = append(f.setups, args)
		folder := str(args["folder"])
		return map[string]any{"success": true, "folder": folder, "home": folder == "/home/me", "root": folder == "/"}, nil
	case "team_file_put":
		f.files[str(args["name"])] += int64(len(str(args["data"])))
		return map[string]any{"success": true}, nil
	case "agent_send":
		if f.resume != "" && str(args["conversation_id"]) != "" {
			return map[string]any{"success": false, "error": f.resume}, nil
		}
		f.sends = append(f.sends, args)
		f.prompts = append(f.prompts, str(args["prompt"]))
		id := str(args["conversation_id"])
		if id == "" {
			f.seq++
			id = fmt.Sprintf("conv%d", f.seq)
		}
		f.convs[id] = append(f.convs[id], map[string]any{"pending": str(args["prompt"])})
		return map[string]any{"success": true, "conversation_id": id, "from_seq": float64(len(f.convs[id]) * 10)}, nil
	case "agent_stop":
		f.stopped = append(f.stopped, str(args["conversation_id"]))
		id := str(args["conversation_id"])
		f.convs[id] = append(f.convs[id], map[string]any{"stopped": true})
		return map[string]any{"success": true}, nil
	case "agent_conversation":
		id := str(args["conversation_id"])
		list := f.convs[id]
		if len(list) == 0 {
			return map[string]any{"success": true, "events": []any{}}, nil
		}
		last := list[len(list)-1]
		if last["stopped"] == true {
			f.convs[id] = list[:len(list)-1]
			return map[string]any{"success": true, "events": []any{map[string]any{"seq": 9000, "type": "done", "stopped": true}}}, nil
		}
		if p, ok := last["pending"].(string); ok {
			if f.hold {
				select {
				case <-f.release:
				default:
					return map[string]any{"success": true, "events": []any{map[string]any{"seq": 1, "type": "tool", "kind": "shell", "name": "Bash", "input": "npm test"}}}, nil
				}
			}
			f.convs[id] = list[:len(list)-1]
			text, errText := f.reply(p)
			done := map[string]any{"seq": 3, "type": "done"}
			if errText != "" {
				done["error"] = errText
			}
			return map[string]any{"success": true, "events": []any{
				map[string]any{"seq": 1, "type": "tool", "kind": "shell", "name": "Bash", "input": "ls"},
				map[string]any{"seq": 2, "type": "text", "text": text}, done}}, nil
		}
		return map[string]any{"success": true, "events": []any{}}, nil
	}
	return map[string]any{"success": false, "error": "unknown method " + method}, nil
}

func (f *fakeAgents) promptCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.prompts)
}

func (f *fakeAgents) lastPrompt() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.prompts) == 0 {
		return ""
	}
	return f.prompts[len(f.prompts)-1]
}

func teamOn(t *testing.T, url string, nodes TeamNodes) *TeamChat {
	t.Helper()
	nc := dial(t, url)
	tc := &TeamChat{Connect: func() (*nats.Conn, error) { return nc, nil }, Nodes: nodes, Key: []byte("k-k-k-k-k-k-k-k-k-k-k-k-k-k-k-k"),
		Dir: t.TempDir(), Backups: filepath.Join(t.TempDir(), "backups"), poll: 20 * time.Millisecond,
		backoffs: []time.Duration{20 * time.Millisecond}}
	return tc
}

func teamHost(t *testing.T, tc *TeamChat) (*Host, apptransport.Binding, apptransport.Binding) {
	t.Helper()
	spec := map[string]deployments.AppSpec{"team-chat": {Hosted: "team-chat", Scope: "team-chat", Intent: deployments.Running}}
	store := storeWith(t, spec)
	if _, err := store.Put(otherFleet, "general-team", 0, deployments.Spec{Release: deployments.Release{URL: "https://example.test/r.tar.gz", SHA256: releaseOK},
		Apps: spec}); err != nil {
		t.Fatal(err)
	}
	h := NewHost(store)
	h.Register("team-chat", tc)
	mine, _ := Binding(fleetID, "general-team", "team-chat", "team-chat")
	theirs, _ := Binding(otherFleet, "general-team", "team-chat", "team-chat")
	return h, mine, theirs
}

func eventually(t *testing.T, what string, ok func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !ok() {
		if time.Now().After(deadline) {
			t.Fatal("timed out waiting for " + what)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func makeAgent(t *testing.T, h *Host, b apptransport.Binding, name, node, runtime string, extra map[string]any) string {
	t.Helper()
	spec := map[string]any{"name": name, "node_id": node, "runtime": runtime, "description": name + " does things"}
	for k, v := range extra {
		spec[k] = v
	}
	out := call(t, h, b, "agent_create", map[string]any{"spec": spec})
	return out["agent"].(map[string]any)["mid"].(string)
}

func msgs(t *testing.T, h *Host, b apptransport.Binding, cid, root string) []map[string]any {
	t.Helper()
	out := call(t, h, b, "messages", map[string]any{"cid": cid, "root": root})
	var list []map[string]any
	for _, m := range out["messages"].([]any) {
		list = append(list, m.(map[string]any))
	}
	return list
}

func lastFrom(list []map[string]any, from string) map[string]any {
	for i := len(list) - 1; i >= 0; i-- {
		if list[i]["from"] == from {
			return list[i]
		}
	}
	return nil
}

func TestASpaceStartsWithGeneralAndKeepsItsConversationsPerFleet(t *testing.T) {
	url := jetStream(t)
	tc := teamOn(t, url, newFakeAgents())
	h, mine, theirs := teamHost(t, tc)
	space := call(t, h, mine, "space_get", map[string]any{})
	convs := space["conversations"].([]any)
	if len(convs) != 1 || convs[0].(map[string]any)["name"] != "general" || space["subject"] != "fleet."+fleetID+".apps.team-chat.events" {
		t.Fatalf("%v", space)
	}
	call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "Docs"})
	call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "secret", "private": true})
	if e := refused(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "docs"}); !strings.Contains(e, "exists") {
		t.Fatal(e)
	}
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "hello"})
	// Another Fleet sees only its own space.
	other := call(t, h, theirs, "space_get", map[string]any{})
	if len(other["conversations"].([]any)) != 1 || len(msgs(t, h, theirs, "general", "")) != 0 {
		t.Fatalf("%v", other)
	}
	// A new service on the same bucket (a controller restart) has it all.
	tc2 := teamOn(t, url, newFakeAgents())
	h2, mine2, _ := teamHost(t, tc2)
	again := call(t, h2, mine2, "space_get", map[string]any{})
	if len(again["conversations"].([]any)) != 3 || msgs(t, h2, mine2, "general", "")[0]["text"] != "hello" {
		t.Fatalf("%v", again)
	}
	if e := refused(t, h2, mine2, "conv_leave", map[string]any{"cid": "general"}); !strings.Contains(e, "cannot be left") {
		t.Fatal(e)
	}
}

func TestCreatingAnAgentChecksItsRuntimeAndFolder(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	if e := refused(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "G", "node_id": "n_mac", "runtime": "gemini"}}); !strings.Contains(e, "not signed in") {
		t.Fatal(e)
	}
	if e := refused(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "C", "node_id": "n_mac", "runtime": "cursor"}}); !strings.Contains(e, "not installed") {
		t.Fatal(e)
	}
	nodes.online["n_mac"] = false
	if e := refused(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "W", "node_id": "n_mac", "runtime": "claude"}}); !strings.Contains(e, "offline") {
		t.Fatal(e)
	}
	nodes.online["n_mac"] = true
	// Default folders: under the home on a machine, /workspace on the Workspace.
	dry := call(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "Writer", "node_id": "n_mac", "runtime": "claude", "dry_run": true}})
	if dry["folder"] != "/home/me/PantheonAgents/writer" || dry["warning"] != "" {
		t.Fatalf("%v", dry)
	}
	ws := call(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "Ops", "node_id": "n_ws", "runtime": "codex"}})
	if ws["agent"].(map[string]any)["folder"] != "/workspace/agents/ops" {
		t.Fatalf("%v", ws)
	}
	// A home folder: warned for a private agent, refused for a shared one.
	home := call(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "Home", "node_id": "n_mac", "runtime": "claude", "folder": "/home/me"}})
	if !strings.Contains(fmt.Sprint(home["warning"]), "home folder") {
		t.Fatalf("%v", home)
	}
	if e := refused(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "Shared", "node_id": "n_mac", "runtime": "claude", "folder": "/", "access": "everyone"}}); !strings.Contains(e, "shared agent") {
		t.Fatal(e)
	}
	if e := refused(t, h, mine, "agent_create", map[string]any{"spec": map[string]any{"name": "Ops", "node_id": "n_mac", "runtime": "claude"}}); !strings.Contains(e, "taken") {
		t.Fatal(e)
	}
	if !strings.Contains(str(nodes.setups[len(nodes.setups)-1]["agent_md"]), "{{TEAM}} inbox") {
		t.Fatal("AGENT.md names the tool")
	}
	space := call(t, h, mine, "space_get", map[string]any{})
	status := space["agent_status"].(map[string]any)[ws["agent"].(map[string]any)["mid"].(string)].(map[string]any)
	if status["state"] != "ready" {
		t.Fatalf("%v", status)
	}
}

// Wake rules: a DM, a mention, a followed thread, an "all" channel; an
// unaddressed message wakes no one.
func TestWakeRules(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true // turns stay running: what is queued is visible
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	coder := makeAgent(t, h, mine, "Coder", "n_ws", "codex", nil)
	ch := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "docs", "members": []string{writer, coder}})["conversation"].(map[string]any)["cid"].(string)

	call(t, h, mine, "post", map[string]any{"cid": ch, "text": "nobody in particular"})
	time.Sleep(100 * time.Millisecond)
	if nodes.promptCount() != 0 {
		t.Fatal("an unaddressed message woke an agent")
	}
	m := call(t, h, mine, "post", map[string]any{"cid": ch, "text": "@writer please draft"})["message"].(map[string]any)
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 1 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "@writer please draft") || !strings.Contains(p, "#docs") || !strings.Contains(p, "nobody in particular") {
		t.Fatalf("turn input: %s", p)
	}
	if nodes.sends[0]["agent"] != "claude" || nodes.sends[0]["cwd"] != "/home/me/PantheonAgents/writer" {
		t.Fatalf("%v", nodes.sends[0])
	}
	// A reply in the thread writer was mentioned in wakes it (queued: it is busy).
	call(t, h, mine, "post", map[string]any{"cid": ch, "text": "also the intro", "root": m["id"]})
	time.Sleep(50 * time.Millisecond)
	space := call(t, h, mine, "space_get", map[string]any{})
	if q := space["agent_status"].(map[string]any)[writer].(map[string]any)["queue"]; q != float64(1) {
		t.Fatalf("writer's queue: %v", q)
	}
	if nodes.promptCount() != 1 {
		t.Fatal("coder was woken by a thread it does not follow")
	}
	// agent_reply all: every member agent.
	call(t, h, mine, "conv_update", map[string]any{"cid": ch, "agent_reply": "all"})
	call(t, h, mine, "post", map[string]any{"cid": ch, "text": "status, anyone?"})
	eventually(t, "coder's turn", func() bool { return nodes.promptCount() == 2 })
	// An agent's message in a thread or an "all" channel wakes no other
	// agent (only a DM or a mention does).
	before := nodes.promptCount()
	queued := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["queue"]
	ctok := tc.agentToken(fleetID, coder)
	if r := tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: coder, Token: ctok, Op: "post", Args: []string{"docs"}, Text: "noted"}); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	if r := tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: coder, Token: ctok, Op: "reply", Args: []string{m["id"].(string)}, Text: "noted too"}); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	time.Sleep(50 * time.Millisecond)
	if q := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["queue"]; q != queued || nodes.promptCount() != before {
		t.Fatalf("an agent's note woke another agent: queue %v", q)
	}
	// A DM.
	call(t, h, mine, "conv_update", map[string]any{"cid": ch, "agent_reply": "mention"})
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{coder}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "psst"})
	space = call(t, h, mine, "space_get", map[string]any{})
	if q := space["agent_status"].(map[string]any)[coder].(map[string]any)["queue"]; q != float64(1) {
		t.Fatalf("coder's queue: %v", q)
	}
	// A mention in a public channel adds the agent.
	pub := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "random"})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": pub, "text": "@coder look"})
	space = call(t, h, mine, "space_get", map[string]any{})
	for _, c := range space["conversations"].([]any) {
		c := c.(map[string]any)
		if c["cid"] == pub && !strings.Contains(fmt.Sprint(c["members"]), coder) {
			t.Fatalf("%v", c)
		}
	}
}

// Messages sent while an agent works reach it once: through inbox in that
// turn, or in the next turn. The queue survives a restart.
func TestQueueDurabilityAndInboxNoneTwice(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "one"})
	eventually(t, "the first turn", func() bool { return nodes.promptCount() == 1 })
	for _, text := range []string{"two", "three", "four"} {
		call(t, h, mine, "post", map[string]any{"cid": dm, "text": text})
	}
	// The running turn takes two of them through inbox...
	token := tc.agentToken(fleetID, writer)
	in := tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: writer, Token: token, Op: "inbox"})
	if in.Code != 0 || !strings.Contains(in.Text, "two") || !strings.Contains(in.Text, "four") {
		t.Fatalf("%v", in)
	}
	if again := tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: writer, Token: token, Op: "inbox"}); !strings.Contains(again.Text, "Nothing new") {
		t.Fatalf("%v", again)
	}
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "five"})
	// A controller restart in the middle: the new service resumes.
	tc.Close()
	tc2 := teamOn(t, url, nodes)
	h2, mine2, _ := teamHost(t, tc2)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc2.Run(ctx)
	// Ending the first turn: the old service's watcher and the new one's.
	nodes.release <- struct{}{}
	eventually(t, "the next turn", func() bool { return nodes.promptCount() == 2 })
	p := nodes.lastPrompt()
	if !strings.Contains(p, "five") || strings.Contains(p, "two") || strings.Contains(p, "four") {
		t.Fatalf("second turn input: %s", p)
	}
	nodes.release <- struct{}{}
	eventually(t, "the replies", func() bool {
		n := 0
		for _, m := range msgs(t, h2, mine2, dm, "") {
			if m["from"] == writer {
				n++
			}
		}
		return n >= 2
	})
	// Bad identities are refused.
	if r := tc2.AgentRequest(context.Background(), fleetID, agentRequest{MID: writer, Token: "nope", Op: "inbox"}); r.Code == 0 {
		t.Fatal("a bad token was taken")
	}
	if r := tc2.AgentRequest(context.Background(), otherFleet, agentRequest{MID: writer, Token: token, Op: "inbox"}); r.Code == 0 {
		t.Fatal("a token of another Fleet was taken")
	}
}

func TestTheFinalTextIsPostedInTheThreadThatWokeTheAgent(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.reply = func(string) (string, string) { return "Here are the notes.", "" }
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", map[string]any{"model": "sonnet"})
	root := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "Release notes"})["message"].(map[string]any)["id"].(string)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer draft them", "root": root})
	eventually(t, "the reply", func() bool { return lastFrom(msgs(t, h, mine, "general", root), writer) != nil })
	reply := lastFrom(msgs(t, h, mine, "general", root), writer)
	if reply["text"] != "Here are the notes." || reply["root"] != root || reply["runtime"] != "claude" || reply["model"] != "sonnet" {
		t.Fatalf("%v", reply)
	}
	top := msgs(t, h, mine, "general", "")
	if top[len(top)-1]["reply_count"] != float64(2) {
		t.Fatalf("%v", top)
	}
	// An agent that posted where it was woken gets nothing posted for it.
	nodes.reply = func(string) (string, string) { return "final words", "" }
	before := nodes.promptCount()
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer again", "root": root})
	eventually(t, "a turn", func() bool { return nodes.promptCount() > before })
	eventually(t, "the second reply", func() bool {
		return lastFrom(msgs(t, h, mine, "general", root), writer)["text"] == "final words"
	})
}

// Edit and delete: folded for readers; the agent woken by the message gets
// the change in its next turn.
func TestEditAndDeleteFold(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	m := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer use tabs"})["message"].(map[string]any)
	eventually(t, "the first turn", func() bool { return nodes.promptCount() == 1 })
	eventually(t, "the reply", func() bool { return lastFrom(msgs(t, h, mine, "general", ""), writer) != nil })
	edited := call(t, h, mine, "edit", map[string]any{"id": m["id"], "text": "@writer use spaces"})["message"].(map[string]any)
	if edited["text"] != "@writer use spaces" || edited["edited_at"] == nil {
		t.Fatalf("%v", edited)
	}
	eventually(t, "the edit's turn", func() bool { return nodes.promptCount() == 2 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "edited message "+m["id"].(string)) || !strings.Contains(p, "use spaces") {
		t.Fatalf("%s", p)
	}
	reply := lastFrom(msgs(t, h, mine, "general", ""), writer)
	if e := refused(t, h, mine, "edit", map[string]any{"id": reply["id"], "text": "mine now"}); !strings.Contains(e, "your own") {
		t.Fatal(e)
	}
	call(t, h, mine, "delete", map[string]any{"id": m["id"]})
	var got map[string]any
	for _, x := range msgs(t, h, mine, "general", "") {
		if x["id"] == m["id"] {
			got = x
		}
	}
	if got["deleted"] != true || got["text"] != "" {
		t.Fatalf("%v", got)
	}
	// A new service reads the same folded history from the stream.
	tc2 := teamOn(t, url, nodes)
	h2, mine2, _ := teamHost(t, tc2)
	for _, x := range msgs(t, h2, mine2, "general", "") {
		if x["id"] == m["id"] && x["deleted"] != true {
			t.Fatalf("%v", x)
		}
	}
	eventually(t, "the delete's turn", func() bool { return nodes.promptCount() == 3 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "deleted message "+m["id"].(string)) {
		t.Fatalf("%s", p)
	}
}

func TestAFailedTurnSaysWhyKeepsTheMessageAndRetries(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.reply = func(string) (string, string) {
		return "", "Claude Code is not signed in. Run claude and log in."
	}
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "hi"})
	eventually(t, "the failure line", func() bool { return lastFrom(msgs(t, h, mine, dm, ""), "system") != nil })
	line := lastFrom(msgs(t, h, mine, dm, ""), "system")
	if !strings.Contains(line["text"].(string), "Writer could not answer: Claude Code is not signed in on Node n_mac") ||
		!strings.Contains(line["text"].(string), "Sign in") || line["action"] != "retry" || line["about"] != writer {
		t.Fatalf("%v", line)
	}
	status := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
	if status["state"] != "attention" || status["queue"] != float64(1) {
		t.Fatalf("%v", status)
	}
	time.Sleep(100 * time.Millisecond)
	if nodes.promptCount() != 1 {
		t.Fatal("a failed agent was retried without the owner")
	}
	nodes.reply = func(string) (string, string) { return "Hello!", "" }
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "retry"})
	eventually(t, "the answer", func() bool { return lastFrom(msgs(t, h, mine, dm, ""), writer) != nil })
	if n := len(msgs(t, h, mine, dm, "")); n != 3 {
		t.Fatalf("%d messages", n)
	}
}

// Guards: the per-agent turn limit pauses an agent with a visible reason;
// agent chains stop at the hop limit with a system line; an overlong turn
// is stopped.
func TestGuards(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	call(t, h, mine, "space_update", map[string]any{"settings": map[string]any{"turns_per_agent_hour": 2, "hop_limit": 3}})
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	for i := 0; i < 2; i++ {
		call(t, h, mine, "post", map[string]any{"cid": dm, "text": fmt.Sprintf("m%d", i)})
		eventually(t, "a turn", func() bool { return nodes.promptCount() == i+1 })
		eventually(t, "a reply", func() bool { return len(msgs(t, h, mine, dm, "")) == 2*(i+1) })
	}
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "m2"})
	eventually(t, "the pause line", func() bool { return lastFrom(msgs(t, h, mine, dm, ""), "system") != nil })
	if l := lastFrom(msgs(t, h, mine, dm, ""), "system"); !strings.Contains(l["text"].(string), "limit of 2 turns an hour") {
		t.Fatalf("%v", l)
	}
	space := call(t, h, mine, "space_get", map[string]any{})
	if s := space["agent_status"].(map[string]any)[writer].(map[string]any); s["state"] != "attention" || !strings.Contains(fmt.Sprint(s["reason"]), "limit") {
		t.Fatalf("%v", s)
	}
	if u := space["usage"].(map[string]any); u["turns_last_hour"].(map[string]any)[writer] != float64(2) {
		t.Fatalf("%v", u)
	}
	if nodes.promptCount() != 2 {
		t.Fatal("a turn started past the limit")
	}

	// Two agents mentioning each other: the chain stops at hop 3.
	call(t, h, mine, "space_update", map[string]any{"settings": map[string]any{"turns_per_agent_hour": 60}})
	nodes.reply = func(p string) (string, string) {
		if strings.Contains(p, "You are @ping") {
			return "@pong over", ""
		}
		return "@ping over", ""
	}
	makeAgent(t, h, mine, "Ping", "n_mac", "claude", nil)
	makeAgent(t, h, mine, "Pong", "n_ws", "codex", nil)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@ping start"})
	eventually(t, "the hop line", func() bool {
		l := lastFrom(msgs(t, h, mine, "general", ""), "system")
		return l != nil && strings.Contains(l["text"].(string), "after 3 hops")
	})
	agentMsgs := 0
	for _, m := range msgs(t, h, mine, "general", "") {
		if m["from"] != tcOwner && m["from"] != "system" {
			agentMsgs++
		}
	}
	if agentMsgs != 3 {
		t.Fatalf("%d agent messages in the chain", agentMsgs)
	}

	// A turn over the time limit is stopped.
	call(t, h, mine, "space_update", map[string]any{"settings": map[string]any{"turn_max_minutes": 1}})
	nodes.hold = true
	clock := time.Now()
	var mu sync.Mutex
	tc.now = func() time.Time { mu.Lock(); defer mu.Unlock(); return clock }
	before := nodes.promptCount()
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@ping long job"})
	eventually(t, "the long turn", func() bool { return nodes.promptCount() > before })
	mu.Lock()
	clock = clock.Add(2 * time.Minute)
	mu.Unlock()
	eventually(t, "the timeout line", func() bool {
		l := lastFrom(msgs(t, h, mine, "general", ""), "system")
		return l != nil && strings.Contains(l["text"].(string), "stopped after 1 minutes")
	})
}

func TestStopTurnAndResets(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer go"})
	eventually(t, "the turn", func() bool {
		live := call(t, h, mine, "agent_live", map[string]any{"mid": writer})
		return live["running"] == true && live["line"] == "Running npm test"
	})
	live := call(t, h, mine, "agent_live", map[string]any{"mid": writer})
	if live["stream_subject"] != "fleet."+fleetID+".apps.pantheon.stream.coding_conv1" {
		t.Fatalf("%v", live)
	}
	if e := refused(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "reset_session"}); !strings.Contains(e, "stop its turn") {
		t.Fatal(e)
	}
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "stop_turn"})
	eventually(t, "ready", func() bool {
		s := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
		return s["state"] == "ready"
	})
	if lastFrom(msgs(t, h, mine, "general", ""), writer) != nil {
		t.Fatal("a stopped turn posted")
	}
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "reset_session"})
	if e := refused(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "reset_full", "confirm": "/tmp"}); !strings.Contains(e, "PantheonAgents/writer") {
		t.Fatal(e)
	}
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "reset_full", "confirm": "/home/me/PantheonAgents/writer"})
	if nodes.setups[len(nodes.setups)-1]["reset"] != "full" {
		t.Fatalf("%v", nodes.setups)
	}
	nodes.hold = false
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer fresh"})
	eventually(t, "a new session", func() bool { return nodes.promptCount() == 2 })
	if nodes.sends[1]["conversation_id"] != nil || !strings.Contains(nodes.lastPrompt(), "fresh session") {
		t.Fatalf("%v", nodes.sends[1])
	}
}

func TestOfflineAgentsWaitAndAnswerWhenTheirNodeIsBack(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	tc.tick = 30 * time.Millisecond
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc.Run(ctx)
	nodes.mu.Lock()
	nodes.online["n_mac"] = false
	nodes.mu.Unlock()
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer are you there"})
	eventually(t, "offline", func() bool {
		s := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
		return s["state"] == "offline" && s["queue"] == float64(1)
	})
	nodes.mu.Lock()
	nodes.online["n_mac"] = true
	nodes.mu.Unlock()
	eventually(t, "the answer", func() bool { return lastFrom(msgs(t, h, mine, "general", ""), writer) != nil })
}

// A node that stopped (its record gone) or that nothing answers for shows
// Offline at once, by its name, and its queue is answered when it is back.
func TestUnreachableOrGoneNodesShowOfflineAndAnswerWhenBack(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.gone, nodes.deaf = map[string]bool{}, map[string]bool{}
	nodes.seen = map[string]time.Time{"n_mac": time.Unix(1000, 0)}
	tc := teamOn(t, url, nodes)
	tc.tick = 30 * time.Millisecond
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc.Run(ctx)
	status := func() map[string]any {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
	}
	set := func(f func()) { nodes.mu.Lock(); f(); nodes.mu.Unlock() }

	// Gone: offline by the name it had.
	set(func() { nodes.gone["n_mac"] = true })
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer one"})
	eventually(t, "offline (gone)", func() bool {
		s := status()
		return s["state"] == "offline" && s["reason"] == "Node n_mac is offline"
	})
	set(func() { nodes.gone["n_mac"] = false; nodes.seen["n_mac"] = time.Unix(1010, 0) })
	eventually(t, "the first answer", func() bool {
		return lastFrom(msgs(t, h, mine, "general", ""), writer) != nil && status()["state"] == "ready"
	})

	// Deaf: online by its record, nothing answers: offline until a newer heartbeat.
	set(func() { nodes.deaf["n_mac"] = true })
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer two"})
	eventually(t, "offline (deaf)", func() bool { return status()["state"] == "offline" })
	set(func() { nodes.deaf["n_mac"] = false })
	time.Sleep(200 * time.Millisecond) // sweeps with the same heartbeat: still offline
	if s := status(); s["state"] != "offline" || s["queue"] != float64(1) {
		t.Fatalf("%v", s)
	}
	set(func() { nodes.seen["n_mac"] = time.Unix(1020, 0) })
	eventually(t, "the second answer", func() bool {
		return len(msgs(t, h, mine, "general", "")) >= 4 && status()["queue"] == float64(0) && status()["state"] == "ready"
	})
}

// An agent whose model source changed cannot resume its session: a new one
// starts by itself.
func TestModelSourceChangeStartsANewSession(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	coder := makeAgent(t, h, mine, "Coder", "n_mac", "codex", nil)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@coder one"})
	eventually(t, "the first answer", func() bool { return lastFrom(msgs(t, h, mine, "general", ""), coder) != nil })
	eventually(t, "ready", func() bool {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[coder].(map[string]any)["state"] == "ready"
	})
	nodes.mu.Lock()
	nodes.resume = "PantheonOS models: thread/resume failed: This chat uses openai, but your current provider is pantheon. Switch to openai to continue."
	nodes.mu.Unlock()
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@coder two"})
	eventually(t, "a new session", func() bool { return nodes.promptCount() == 2 })
	if nodes.sends[1]["conversation_id"] != nil || !strings.Contains(nodes.lastPrompt(), "fresh session") {
		t.Fatalf("%v", nodes.sends[1])
	}
	// The same refusal at the end of a turn (the node took the message).
	eventually(t, "ready again", func() bool {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[coder].(map[string]any)["state"] == "ready"
	})
	nodes.mu.Lock()
	nodes.resume = ""
	nodes.reply = func(p string) (string, string) {
		if strings.Contains(p, "fresh session") {
			return "Done.", ""
		}
		return "", "thread/resume failed: This chat uses openai, but your current provider is pantheon."
	}
	nodes.mu.Unlock()
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@coder three"})
	eventually(t, "another new session", func() bool { return nodes.promptCount() == 4 })
	if nodes.sends[3]["conversation_id"] != nil || !strings.Contains(nodes.lastPrompt(), "@coder three") {
		t.Fatalf("%v", nodes.sends[3])
	}
	eventually(t, "answered", func() bool {
		s := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[coder].(map[string]any)
		return s["state"] == "ready" && s["queue"] == float64(0)
	})
}

func TestFailureLinesArePlainWords(t *testing.T) {
	m := &tcMember{Handle: "writer", Runtime: "claude", NodeID: "n_1316bc635ce442f489c5", Folder: "/Users/me/agents/writer", Model: "haiku"}
	mac := TeamNode{ID: m.NodeID, Name: "Weizes-MacBook-Pro.local", Kind: "machine"}
	for raw, want := range map[string]string{
		"API Error: 502 Bad Gateway":                         "the model service had a temporary problem",
		"HTTP 503 Service Unavailable":                       "the model service had a temporary problem",
		"Error: status 429 Too Many Requests":                "the model service is limiting requests right now",
		"API Error: 401 {\"type\":\"authentication_error\"}": "Claude Code is not signed in on Weizes MacBook Pro",
		"Invalid API key · Please run /login":                "Claude Code is not signed in on Weizes MacBook Pro",
		"HTTP 402: PantheonOS budget exhausted":              "its model budget is used up",
		"model_not_found: claude-x":                          "the model haiku is not available",
		"prompt is too long: 250000 tokens > 200000 maximum": "its conversation got too long for the model",
		"nats: no responders available for request":          "Weizes MacBook Pro did not answer",
		"connect ECONNREFUSED 127.0.0.1:443":                 "Weizes MacBook Pro could not reach the model service",
		"session is gone":                                    "its session on Weizes MacBook Pro ended unexpectedly",
		"exec: claude: executable file not found in $PATH":   "Claude Code is not installed on Weizes MacBook Pro",
		"something odd at 0x1f":                              "its turn failed on Weizes MacBook Pro",
		`thread/resume failed: This chat uses openai, but your current provider is pantheon. (code -32600)`: "its session was made with other models than it now uses",
	} {
		reason, fix, _ := explain(m, mac, raw)
		if reason != want || fix == "" || strings.Contains(reason+fix, "n_") || strings.Contains(reason+fix, ".local") {
			t.Errorf("%q: %q %q", raw, reason, fix)
		}
	}
	if got := nodeLabel(TeamNode{ID: "n_x", Kind: "sandbox", Name: "workspace-abc"}, "n_x"); got != "your Workspace" {
		t.Error(got)
	}
	if got := nodeLabel(TeamNode{ID: "n_x"}, "n_x"); got != "its computer" {
		t.Error(got)
	}
	// A node whose record is gone is named by what the agent's record keeps.
	ws := &tcMember{Handle: "pan", Runtime: "pantheon", NodeID: "n_ws", NodeName: "workspace-1", NodeKind: "sandbox"}
	if reason, _, _ := explain(ws, TeamNode{ID: "n_ws"}, "no responders"); reason != "your Workspace did not answer" {
		t.Error(reason)
	}
}

func TestAgentToolsPostReplyDMReadJoinAndFiles(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	makeAgent(t, h, mine, "Coder", "n_ws", "codex", nil)
	pub := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "dev"})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "hidden", "private": true})
	// An image in a DM reaches the agent's folder with its turn.
	raw := []byte("\x89PNG fake image bytes")
	up := call(t, h, mine, "file_put", map[string]any{"name": "../shot.png", "mime": "image/png", "data": base64.StdEncoding.EncodeToString(raw), "final": true})
	file := up["file"].(map[string]any)
	if file["name"] != "shot.png" || file["size"] != float64(len(raw)) {
		t.Fatalf("%v", file)
	}
	got := call(t, h, mine, "file_get", map[string]any{"id": file["id"]})
	if b, _ := base64.StdEncoding.DecodeString(got["data"].(string)); string(b) != string(raw) || got["eof"] != true {
		t.Fatalf("%v", got)
	}
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, mine, "post", map[string]any{"cid": dm, "text": "what is in this?", "files": []string{file["id"].(string)}})
	eventually(t, "the turn", func() bool { return nodes.promptCount() == 1 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "shot.png is saved in your folder as files/") {
		t.Fatalf("%s", p)
	}
	tok := tc.agentToken(fleetID, writer)
	do := func(op string, args []string, text string, files ...string) agentReply {
		return tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: writer, Token: tok, Op: op, Args: args, Text: text, Files: files})
	}
	if r := do("post", []string{"dev"}, "hi dev"); r.Code == 0 {
		t.Fatal("posted without joining")
	}
	if r := do("join", []string{"hidden"}, ""); r.Code == 0 {
		t.Fatal("joined a private channel")
	}
	if r := do("join", []string{"#dev"}, ""); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	if r := do("post", []string{"dev"}, "hi dev"); r.Code != 0 || !strings.Contains(r.Text, "Posted to #dev") {
		t.Fatalf("%v", r)
	}
	if r := do("dm", []string{"@coder"}, "can you check the build?"); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	eventually(t, "coder's turn", func() bool { return nodes.promptCount() == 2 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "DM with @writer") || !strings.Contains(p, "can you check the build?") {
		t.Fatalf("%s", p)
	}
	// An agent attaches a file it made.
	ftok := tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: writer, Token: tok, Op: "file_put",
		Upload: filePutArgs{Name: "report.md", Data: base64.StdEncoding.EncodeToString([]byte("# Report")), Final: true}})
	var stored struct {
		File tcFile `json:"file"`
	}
	_ = json.Unmarshal(ftok.File.(json.RawMessage), &stored)
	fid := stored.File.ID
	if r := do("post", []string{"dev"}, "the report", fid); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	last := lastFrom(msgs(t, h, mine, pub, ""), writer)
	if len(last["files"].([]any)) != 1 || last["runtime"] != "claude" {
		t.Fatalf("%v", last)
	}
	if r := do("reply", []string{last["id"].(string)}, "in thread"); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	if r := do("read", []string{"dev"}, ""); r.Code != 0 || !strings.Contains(r.Text, "hi dev") || !strings.Contains(r.Text, "1 replies") {
		t.Fatalf("%v", r)
	}
	if r := do("read", []string{last["id"].(string)}, ""); !strings.Contains(r.Text, "in thread") {
		t.Fatalf("%v", r)
	}
	if r := do("members", nil, ""); !strings.Contains(r.Text, "@coder") || !strings.Contains(r.Text, "← you") {
		t.Fatalf("%v", r)
	}
	if r := do("channels", nil, ""); strings.Contains(r.Text, "hidden") || !strings.Contains(r.Text, "#dev (member)") {
		t.Fatalf("%v", r)
	}
	if r := do("edit", []string{last["id"].(string)}, "the final report"); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	owner := call(t, h, mine, "post", map[string]any{"cid": pub, "text": "mine"})["message"].(map[string]any)
	if r := do("edit", []string{owner["id"].(string)}, "theirs"); r.Code == 0 {
		t.Fatal("an agent edited the owner's message")
	}
}

// Only the team-chat credential reaches its bucket, stream and files; it
// answers a node's request on the App namespace, and the browser hears its
// changes.
func TestOnlyTheTeamChatCredentialReachesItsStores(t *testing.T) {
	root := t.TempDir()
	authority, err := auth.Bootstrap(filepath.Join(root, "authority"))
	if err != nil {
		t.Fatal(err)
	}
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := l.Addr().String()
	l.Close()
	config := filepath.Join(root, "nats.conf")
	if err := os.WriteFile(config, []byte(authority.ServerConfig(address, filepath.Join(root, "js"))), 0o600); err != nil {
		t.Fatal(err)
	}
	opts, err := server.ProcessConfigFile(config)
	if err != nil {
		t.Fatal(err)
	}
	opts.NoLog, opts.NoSigs = true, true
	s, err := server.NewServer(opts)
	if err != nil {
		t.Fatal(err)
	}
	go s.Start()
	if !s.ReadyForConnections(10 * time.Second) {
		t.Fatal("broker did not start")
	}
	t.Cleanup(s.Shutdown)
	creds, err := authority.MintTeamChat(time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	nc := dial(t, "nats://"+address, nats.UserCredentialBytes(creds), nats.CustomInboxPrefix(auth.TeamChatInbox))
	nodes := newFakeAgents()
	tc := &TeamChat{Connect: func() (*nats.Conn, error) { return nc, nil }, Nodes: nodes, Key: []byte("0123456789abcdef0123456789abcdef"),
		Dir: t.TempDir(), Backups: filepath.Join(root, "backups"), poll: 20 * time.Millisecond}
	h, mine, _ := teamHost(t, tc)
	owner, err := authority.MintFleetApps(fleetID, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	browser := dial(t, "nats://"+address, nats.UserJWTAndSeed(owner.JWT, owner.Seed), nats.CustomInboxPrefix(owner.InboxPrefix))
	heard := make(chan *nats.Msg, 64)
	if _, err := browser.ChanSubscribe("fleet."+fleetID+".apps.team-chat.events", heard); err != nil {
		t.Fatal(err)
	}
	_ = browser.Flush()
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "hello @writer"})
	select {
	case <-heard:
	case <-time.After(5 * time.Second):
		t.Fatal("the owner's browser did not hear the change")
	}
	eventually(t, "the reply", func() bool { return lastFrom(msgs(t, h, mine, "general", ""), writer) != nil })
	// Files through the object store.
	up := call(t, h, mine, "file_put", map[string]any{"name": "a.txt", "data": base64.StdEncoding.EncodeToString([]byte("abc")), "final": true})
	got := call(t, h, mine, "file_get", map[string]any{"id": up["file"].(map[string]any)["id"]})
	if got["data"] != base64.StdEncoding.EncodeToString([]byte("abc")) {
		t.Fatalf("%v", got)
	}
	// A node (the App credential) asks for the agent.
	req, _ := json.Marshal(map[string]any{"mid": writer, "token": tc.agentToken(fleetID, writer), "op": "members"})
	msg, err := browser.Request("fleet."+fleetID+".apps.team-chat.rpc", req, 10*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	var reply agentReply
	_ = json.Unmarshal(msg.Data, &reply)
	if reply.Code != 0 || !strings.Contains(reply.Text, "@writer") {
		t.Fatalf("%v", reply)
	}
	// A snapshot of the bucket and the stream.
	path, keys, n, err := tc.Snapshot(context.Background(), time.Now())
	if err != nil || keys < 4 || n < 2 {
		t.Fatalf("%s %d %d %v", path, keys, n, err)
	}
	for name, mint := range map[string]func() ([]byte, error){
		"fleet user":    func() ([]byte, error) { return authority.MintFleetUser(fleetID) },
		"node":          func() ([]byte, error) { return authority.MintFleetNode(fleetID, "n_1") },
		"desktop":       func() ([]byte, error) { return authority.MintDesktopState(time.Hour) },
		"App (owner's)": func() ([]byte, error) { return nil, nil },
	} {
		raw, err := mint()
		if err != nil {
			t.Fatal(err)
		}
		var other *nats.Conn
		if raw == nil {
			other = browser
		} else {
			other = dial(t, "nats://"+address, nats.UserCredentialBytes(raw), nats.CustomInboxPrefix("_INBOX_"+fleetID))
		}
		js, _ := jetstream.New(other)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		if _, err := js.KeyValue(ctx, TeamChatBucket); err == nil {
			t.Fatalf("a %s credential opened the bucket", name)
		}
		if _, err := js.Stream(ctx, TeamChatStream); err == nil {
			t.Fatalf("a %s credential opened the stream", name)
		}
		if _, err := js.ObjectStore(ctx, TeamChatFiles); err == nil {
			t.Fatalf("a %s credential opened the files", name)
		}
		cancel()
	}
}

func TestSnapshotRestoresTeamChatIntoEmptyStores(t *testing.T) {
	url := jetStream(t)
	tc := teamOn(t, url, newFakeAgents())
	h, mine, _ := teamHost(t, tc)
	call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "docs"})
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "keep me"})
	path, _, _, err := tc.Snapshot(context.Background(), time.Now())
	if err != nil {
		t.Fatal(err)
	}
	empty := teamOn(t, jetStream(t), newFakeAgents())
	keys, n, err := empty.Restore(context.Background(), path)
	if err != nil || keys < 4 || n != 1 {
		t.Fatalf("%d %d %v", keys, n, err)
	}
	h2, mine2, _ := teamHost(t, empty)
	if msgs(t, h2, mine2, "general", "")[0]["text"] != "keep me" || len(call(t, h2, mine2, "space_get", map[string]any{})["conversations"].([]any)) != 2 {
		t.Fatal("restore")
	}
}
