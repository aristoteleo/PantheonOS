package hosted

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/nats-io/nats.go"
)

// offlineRig is a space with one agent (Writer, on n_mac) whose turns are
// held until released, the service's loop running, and the owner's events
// heard.
type offlineRig struct {
	t      *testing.T
	nodes  *fakeAgents
	tc     *TeamChat
	h      *Host
	mine   apptransport.Binding
	writer string
	mu     sync.Mutex
	states []string // every agent_status state of Writer, in order
}

func newOfflineRig(t *testing.T) (*offlineRig, func(method string, args map[string]any) map[string]any) {
	t.Helper()
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	tc.tick = 30 * time.Millisecond
	h, mine, _ := teamHost(t, tc)
	r := &offlineRig{t: t, nodes: nodes, tc: tc, h: h, mine: mine}
	r.writer = makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	nc := dial(t, url)
	if _, err := nc.Subscribe(tcSubject(fleetID), func(msg *nats.Msg) {
		var ev map[string]any
		if json.Unmarshal(msg.Data, &ev) == nil && ev["type"] == "agent_status" && ev["mid"] == r.writer {
			st, _ := ev["status"].(map[string]any)
			r.mu.Lock()
			r.states = append(r.states, str(st["state"]))
			r.mu.Unlock()
		}
	}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go tc.Run(ctx)
	return r, func(method string, args map[string]any) map[string]any { return call(t, h, mine, method, args) }
}

func (r *offlineRig) set(f func(n *fakeAgents)) { r.nodes.mu.Lock(); f(r.nodes); r.nodes.mu.Unlock() }

func (r *offlineRig) sawStates() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.states...)
}

func statusOfAgent(call func(string, map[string]any) map[string]any, mid string) map[string]any {
	return call("space_get", map[string]any{})["agent_status"].(map[string]any)[mid].(map[string]any)
}

// cut ends the held turn of conversation conv as a node does when its
// service stops: done, stopped, "Interrupted: …".
func (r *offlineRig) cut(conv string) {
	r.set(func(n *fakeAgents) {
		list := n.convs[conv]
		list[len(list)-1] = map[string]any{"interrupted": true}
	})
}

// A turn whose computer goes away is offline (not working) while it is away;
// when the computer is back and says the turn was cut off, its message is
// answered again, once, with a note that it was cut off.
func TestATurnCutOffByItsComputerIsAnsweredWhenItIsBack(t *testing.T) {
	r, call := newOfflineRig(t)
	writer := r.writer
	call("post", map[string]any{"cid": "general", "text": "@writer count to three"})
	eventually(t, "the turn", func() bool { return statusOfAgent(call, writer)["state"] == "working" })

	r.set(func(n *fakeAgents) { n.online["n_mac"] = false })
	eventually(t, "offline during the turn", func() bool {
		s := statusOfAgent(call, writer)
		return s["state"] == "offline" && s["turn"] == true
	})
	s := statusOfAgent(call, writer)
	if !strings.Contains(str(s["line"]), "went offline during Writer's turn") || s["reason"] != "Node n_mac is offline" {
		t.Fatalf("%v", s)
	}
	if w, _ := s["waiting"].([]any); len(w) != 1 || w[0].(map[string]any)["cid"] != "general" {
		t.Fatalf("the cut-off turn's message waits: %v", s)
	}

	// The node's service stopped with it: back, it says the turn was cut off.
	r.cut("conv1")
	r.set(func(n *fakeAgents) { n.online["n_mac"] = true; n.hold = false })
	eventually(t, "the answer", func() bool { return lastFrom(msgs(t, r.h, r.mine, "general", ""), writer) != nil })
	if n := r.nodes.promptCount(); n != 2 {
		t.Fatalf("asked %d times, want 2", n)
	}
	if !strings.Contains(r.nodes.lastPrompt(), "cut off before you answered") {
		t.Fatalf("the second turn is not told: %s", r.nodes.lastPrompt())
	}
	eventually(t, "ready", func() bool { return statusOfAgent(call, writer)["state"] == "ready" })
	posts := 0
	for _, m := range msgs(t, r.h, r.mine, "general", "") {
		if m["from"] == writer {
			posts++
		}
	}
	if posts != 1 {
		t.Fatalf("%d answers", posts)
	}
}

// A message the agent already answered in a turn that was then cut off is
// not asked again.
func TestACutOffTurnThatPostedIsNotAskedAgain(t *testing.T) {
	r, call := newOfflineRig(t)
	writer := r.writer
	call("post", map[string]any{"cid": "general", "text": "@writer hello"})
	eventually(t, "the turn", func() bool { return statusOfAgent(call, writer)["state"] == "working" })
	fl, err := r.tc.lockFleet(context.Background(), fleetID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.tc.agentPost(context.Background(), fl, fl.members[writer], fl.turns[writer], "", "general", "", "Hi!", nil); err != nil {
		fl.mu.Unlock()
		t.Fatal(err)
	}
	fl.mu.Unlock()
	r.cut("conv1")
	r.set(func(n *fakeAgents) { n.hold = false })
	eventually(t, "ready", func() bool { return statusOfAgent(call, writer)["state"] == "ready" })
	time.Sleep(150 * time.Millisecond)
	if n := r.nodes.promptCount(); n != 1 {
		t.Fatalf("asked %d times, want 1", n)
	}
}

// The owner's Stop while the computer is away ends the turn at once: reset,
// delete and moving are not blocked by it, its message is not answered
// again, a line says so, and the node is told to stop once it is back.
func TestStopWhileTheComputerIsAwayEndsTheTurnAtOnce(t *testing.T) {
	r, call := newOfflineRig(t)
	writer := r.writer
	call("post", map[string]any{"cid": "general", "text": "@writer long job"})
	eventually(t, "the turn", func() bool { return statusOfAgent(call, writer)["state"] == "working" })
	r.set(func(n *fakeAgents) { n.online["n_mac"] = false })
	eventually(t, "offline during the turn", func() bool { return statusOfAgent(call, writer)["turn"] == true })

	out := call("agent_control", map[string]any{"mid": writer, "action": "stop_turn"})
	if out["ended"] != true {
		t.Fatalf("%v", out)
	}
	if s := statusOfAgent(call, writer); s["state"] != "offline" || s["turn"] != nil || s["waiting"] != nil {
		t.Fatalf("%v", s)
	}
	line := lastFrom(msgs(t, r.h, r.mine, "general", ""), "system")
	if line == nil || !strings.Contains(str(line["text"]), "Writer's turn was stopped while Node n_mac is offline") {
		t.Fatalf("%v", line)
	}
	call("agent_control", map[string]any{"mid": writer, "action": "reset_session"})

	// Back: the node is told to stop that run; nothing is asked again.
	r.cut("conv1")
	r.set(func(n *fakeAgents) { n.online["n_mac"] = true; n.hold = false })
	eventually(t, "the stop sent", func() bool {
		r.nodes.mu.Lock()
		defer r.nodes.mu.Unlock()
		return len(r.nodes.stopped) == 1 && r.nodes.stopped[0] == "conv1"
	})
	time.Sleep(150 * time.Millisecond)
	if n := r.nodes.promptCount(); n != 1 {
		t.Fatalf("asked %d times, want 1", n)
	}
}

// A Stop while the computer is online is the owner's: a node that then says
// the turn was interrupted does not make it answer again.
func TestAnOwnersStopIsNotTakenForACutOff(t *testing.T) {
	r, call := newOfflineRig(t)
	writer := r.writer
	call("post", map[string]any{"cid": "general", "text": "@writer go"})
	eventually(t, "the turn", func() bool { return statusOfAgent(call, writer)["state"] == "working" })
	// The stop reaches the node, which stops as its service also stops.
	r.set(func(n *fakeAgents) { n.convs["conv1"][len(n.convs["conv1"])-1] = map[string]any{"interrupted": true} })
	call("agent_control", map[string]any{"mid": writer, "action": "stop_turn"})
	r.set(func(n *fakeAgents) { n.hold = false })
	eventually(t, "ready", func() bool { return statusOfAgent(call, writer)["state"] == "ready" })
	time.Sleep(150 * time.Millisecond)
	if n := r.nodes.promptCount(); n != 1 {
		t.Fatalf("asked %d times, want 1", n)
	}
	if s := statusOfAgent(call, writer); s["queue"] != float64(0) {
		t.Fatalf("%v", s)
	}
}

// While an agent's computer is away, the sweep does not start (and drop) a
// turn every tick: its status stays offline, never working; every waiting
// message is listed; its computer keeps its name as it is.
func TestAnOfflineAgentDoesNotFlickerAndListsEveryWaitingMessage(t *testing.T) {
	r, call := newOfflineRig(t)
	writer := r.writer
	before := 0
	r.set(func(n *fakeAgents) { n.online["n_mac"] = false; n.hold = false; before = len(n.calls) })
	call("post", map[string]any{"cid": "general", "text": "@writer one"})
	eventually(t, "offline", func() bool { return statusOfAgent(call, writer)["state"] == "offline" })
	call("post", map[string]any{"cid": "general", "text": "@writer two"})
	dm := call("conv_create", map[string]any{"kind": "dm", "members": []any{writer}})
	dmID := str(dm["conversation"].(map[string]any)["cid"])
	call("post", map[string]any{"cid": dmID, "text": "three"})
	time.Sleep(400 * time.Millisecond) // more than ten sweeps
	for _, st := range r.sawStates() {
		if st == "working" {
			t.Fatalf("flickered: %v", r.sawStates())
		}
	}
	r.nodes.mu.Lock()
	for _, c := range r.nodes.calls[before:] {
		if strings.HasSuffix(c, " team_setup") || strings.HasSuffix(c, " agent_send") {
			r.nodes.mu.Unlock()
			t.Fatalf("a turn was tried: %v", c)
		}
	}
	r.nodes.mu.Unlock()
	s := statusOfAgent(call, writer)
	w, _ := s["waiting"].([]any)
	if len(w) != 3 || w[0].(map[string]any)["cid"] != "general" || w[2].(map[string]any)["cid"] != dmID {
		t.Fatalf("%v", s)
	}
	if s["away_since"] == nil {
		// The service saw it go: since when it is away.
		t.Fatalf("%v", s)
	}
	r.set(func(n *fakeAgents) { n.online["n_mac"] = true })
	eventually(t, "all answered", func() bool {
		s := statusOfAgent(call, writer)
		return s["state"] == "ready" && s["queue"] == float64(0)
	})
}

func TestComputerNamesAreNotCapitalised(t *testing.T) {
	if got := sentenceStart("acct4-local"); got != "acct4-local" {
		t.Fatal(got)
	}
	if got := sentenceStart("its computer"); got != "Its computer" {
		t.Fatal(got)
	}
	if got := cutOffLine("your Cloud Workspace", "sandbox", "Pax"); got != "Your Cloud Workspace stopped during Pax's turn; Pax picks it up again when it runs" {
		t.Fatal(got)
	}
}

// An agent's owner is told once per time its computer is away with messages
// waiting (Activity row, status, one push); the count follows; others read
// only that the owner was told; it goes when the computer is back. The log
// says what happened without any message text.
func TestTheOwnerIsToldOnceWhileTheComputerIsAway(t *testing.T) {
	var logs strings.Builder
	var logMu sync.Mutex
	log.SetOutput(writerFunc(func(p []byte) (int, error) { logMu.Lock(); defer logMu.Unlock(); return logs.Write(p) }))
	defer log.SetOutput(os.Stderr)
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	tc.tick = 30 * time.Millisecond
	tc.NudgeAfter = 80 * time.Millisecond
	var pushMu sync.Mutex
	var pushes []TeamPush
	tc.Push = func(_ string, n TeamPush) { pushMu.Lock(); pushes = append(pushes, n); pushMu.Unlock() }
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	call := func(method string, args map[string]any) map[string]any { return call(t, h, mine, method, args) }
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc.Run(ctx)
	set := func(f func()) { nodes.mu.Lock(); f(); nodes.mu.Unlock() }
	pushed := func() []TeamPush { pushMu.Lock(); defer pushMu.Unlock(); return append([]TeamPush(nil), pushes...) }

	set(func() { nodes.online["n_mac"] = false })
	call("post", map[string]any{"cid": "general", "text": "@writer secret-one"})
	call("post", map[string]any{"cid": "general", "text": "@writer secret-two"})
	eventually(t, "the nudge", func() bool {
		n, _ := statusOfAgent(call, writer)["nudge"].(map[string]any)
		return n != nil && n["count"] == float64(2)
	})
	time.Sleep(200 * time.Millisecond) // more sweeps: still one push
	if p := pushed(); len(p) != 1 || !strings.HasPrefix(p[0].Body, "2 messages waiting for Writer: Node n_mac offline since ") ||
		p[0].URL != "/app/team-chat?agent="+writer || p[0].Title != "Writer is waiting for Node n_mac" {
		t.Fatalf("%+v", p)
	}
	rows := call("activity", map[string]any{"filter": "all"})["rows"].([]any)
	var away map[string]any
	for _, r := range rows {
		if r.(map[string]any)["kind"] == "away" {
			away = r.(map[string]any)
		}
	}
	if away == nil || away["mid"] != writer || away["count"] != float64(2) || away["node_id"] != "n_mac" {
		t.Fatalf("%v", rows)
	}

	// A third message: the count follows, no second push.
	call("post", map[string]any{"cid": "general", "text": "@writer secret-three"})
	eventually(t, "the count", func() bool {
		n, _ := statusOfAgent(call, writer)["nudge"].(map[string]any)
		return n != nil && n["count"] == float64(3)
	})
	if p := pushed(); len(p) != 1 {
		t.Fatalf("pushed again: %+v", p)
	}

	// Someone else reads only that the owner was told.
	fl, err := tc.lockFleet(context.Background(), fleetID)
	if err != nil {
		t.Fatal(err)
	}
	other := fl.statusView(tc.statusOf(fl, fl.members[writer]), writer, "pmember")
	fl.mu.Unlock()
	if other["told"] != true || other["nudge"] != nil {
		t.Fatalf("%v", other)
	}

	// Mine by computer.
	on := call("agents_on_nodes", map[string]any{})["agents"].([]any)
	if len(on) != 1 || on[0].(map[string]any)["node_id"] != "n_mac" || on[0].(map[string]any)["state"] != "offline" || on[0].(map[string]any)["workspace"] != false {
		t.Fatalf("%v", on)
	}
	// An agent on the Cloud Workspace says so, so the paused Workspace (not listed) still counts it.
	makeAgent(t, h, mine, "Cloudy", "n_ws", "codex", nil)
	for _, a := range call("agents_on_nodes", map[string]any{})["agents"].([]any) {
		if a := a.(map[string]any); a["name"] == "Cloudy" && a["workspace"] != true {
			t.Fatalf("%v", a)
		}
	}

	// Back: answered, and the notice goes.
	set(func() { nodes.online["n_mac"] = true })
	eventually(t, "answered and taken back", func() bool {
		s := statusOfAgent(call, writer)
		return s["nudge"] == nil && s["queue"] == float64(0) && s["state"] == "ready"
	})
	for _, r := range call("activity", map[string]any{"filter": "all"})["rows"].([]any) {
		if r.(map[string]any)["kind"] == "away" {
			t.Fatalf("still there: %v", r)
		}
	}
	logMu.Lock()
	out := logs.String()
	logMu.Unlock()
	for _, want := range []string{"waiting for n_mac (offline)", "nudge sent to its owner: 2 waiting", "n_mac is back"} {
		if !strings.Contains(out, want) {
			t.Fatalf("no %q in the log:\n%s", want, out)
		}
	}
	if strings.Contains(out, "secret-") {
		t.Fatalf("message text in the log:\n%s", out)
	}
}

func TestNudgeWords(t *testing.T) {
	loc := time.UTC
	now := time.Date(2026, 11, 24, 20, 30, 0, 0, loc)
	if got := nudgeText("Tally", 2, "acct4-local", "machine", now.Add(-23*time.Minute), now, loc); got != "2 messages waiting for Tally: acct4-local offline since 8:07 PM" {
		t.Fatal(got)
	}
	if got := nudgeText("Pax", 1, "your Cloud Workspace", "sandbox", now, now, loc); got != "1 message waiting for Pax: your Cloud Workspace is paused" {
		t.Fatal(got)
	}
	if got := sinceClock(now.Add(-48*time.Hour), now, loc); got != "Nov 22, 8:30 PM" {
		t.Fatal(got)
	}
}

type writerFunc func([]byte) (int, error)

func (f writerFunc) Write(p []byte) (int, error) { return f(p) }
