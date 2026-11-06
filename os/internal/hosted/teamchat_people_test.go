package hosted

// Phase 3 access rules (docs/apps/team-chat.md, "Phase 3 as built"): a
// person in someone else's space reads, lists, searches and wakes only what
// is theirs, through every method of the service, Activity and search
// included.

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
	"github.com/nats-io/nats.go"
)

const (
	aliceFleet = "f_a11ce00000000000"
	bobFleet   = "f_b0b0000000000000"
	carolFleet = "f_ca7010000000000a"
)

func person(user, name, fleet, role string) *Member {
	return &Member{UserID: user, Username: user, Name: name, Fleet: fleet, Role: role, Joined: time.Now().Add(-time.Hour).Unix()}
}

var (
	alice = person("u-alice", "Alice", aliceFleet, "member")
	bob   = person("u-bob", "Bob", bobFleet, "member")
	carol = person("u-carol", "Carol", carolFleet, "admin")
)

func rpcAs(t *testing.T, h *Host, b apptransport.Binding, m *Member, method string, args any) (int, map[string]any) {
	t.Helper()
	payload, _ := json.Marshal(map[string]any{"method": method, "args": args, "timeout_s": 10})
	status, body := h.invoke(context.Background(), b, payload, true, m)
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatal(string(body))
	}
	return status, out
}

func callAs(t *testing.T, h *Host, b apptransport.Binding, m *Member, method string, args any) map[string]any {
	t.Helper()
	status, out := rpcAs(t, h, b, m, method, args)
	if out["success"] != true {
		t.Fatalf("%s as %s %v: %d %v", method, m.Name, args, status, out)
	}
	result, _ := out["result"].(map[string]any)
	return result
}

func refusedAs(t *testing.T, h *Host, b apptransport.Binding, m *Member, method string, args any) string {
	t.Helper()
	_, out := rpcAs(t, h, b, m, method, args)
	if out["success"] != false {
		t.Fatalf("%s as %s %v was accepted: %v", method, m.Name, args, out)
	}
	return fmt.Sprint(out["error"])
}

func convIDs(space map[string]any) []string {
	var out []string
	for _, c := range space["conversations"].([]any) {
		out = append(out, c.(map[string]any)["cid"].(string))
	}
	return out
}

func memberByName(space map[string]any, name string) map[string]any {
	for _, m := range space["members"].([]any) {
		if m.(map[string]any)["name"] == name {
			return m.(map[string]any)
		}
	}
	return nil
}

// sharedSpace is an owner's space with three agents (Writer: only the owner;
// Shared: everyone; Listed: Alice), a private channel the owner and Writer
// are in, the owner's DM with Writer, a public #team, and Alice, Bob and
// Carol (an admin) in it.
type sharedSpace struct {
	h                      *Host
	b                      apptransport.Binding
	tc                     *TeamChat
	nodes                  *fakeAgents
	url                    string
	writer, shared, listed string
	secret, team, ownerDM  string
	secretMsg, secretFile  string
}

func newSharedSpace(t *testing.T) *sharedSpace {
	t.Helper()
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	s := &sharedSpace{h: h, b: mine, tc: tc, nodes: nodes, url: url}
	s.writer = makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	s.shared = makeAgent(t, h, mine, "Shared", "n_ws", "codex", map[string]any{"access": "everyone"})
	for _, m := range []*Member{alice, bob, carol} {
		callAs(t, h, mine, m, "space_get", map[string]any{})
	}
	aliceMID := personMID(alice.UserID)
	s.listed = makeAgent(t, h, mine, "Listed", "n_ws", "codex", map[string]any{"access": "listed", "access_list": []string{aliceMID}})
	s.secret = call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "secret", "private": true,
		"members": []string{s.writer}})["conversation"].(map[string]any)["cid"].(string)
	s.team = call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "team"})["conversation"].(map[string]any)["cid"].(string)
	s.ownerDM = call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{s.writer}})["conversation"].(map[string]any)["cid"].(string)
	raw := []byte("the launch code is zanzibar")
	up := call(t, h, mine, "file_put", map[string]any{"name": "plan.txt", "mime": "text/plain", "data": base64.StdEncoding.EncodeToString(raw), "final": true})
	s.secretFile = up["file"].(map[string]any)["id"].(string)
	s.secretMsg = call(t, h, mine, "post", map[string]any{"cid": s.secret, "text": "zanzibar plan, private", "files": []string{s.secretFile}})["message"].(map[string]any)["id"].(string)
	call(t, h, mine, "post", map[string]any{"cid": s.ownerDM, "text": "zanzibar in my DM"})
	call(t, h, mine, "task_create", map[string]any{"id": s.secretMsg})
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "hello team"})
	return s
}

// T31: a member sees public channels and their own conversations; never the
// owner's private channel or the owner's DMs, through any method.
func TestAMemberCannotReadListOrSearchWhatIsNotTheirs(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	space := callAs(t, h, b, alice, "space_get", map[string]any{})
	ids := convIDs(space)
	if contains(ids, s.secret) || contains(ids, s.ownerDM) || !contains(ids, "general") || !contains(ids, s.team) {
		t.Fatalf("alice lists %v", ids)
	}
	if space["role"] != "member" || space["me"] != personMID(alice.UserID) ||
		space["subject"] != "fleet."+aliceFleet+".apps.team-chat.spaces."+fleetID {
		t.Fatalf("%v %v %v", space["role"], space["me"], space["subject"])
	}
	for _, x := range space["members"].([]any) {
		m := x.(map[string]any)
		if m["kind"] == "agent" && (m["folder"] != nil || m["node_id"] != nil || m["session_conversation_id"] != nil) {
			t.Fatalf("an agent's computer and folder reach a member: %v", m)
		}
		if m["fleet"] != nil {
			t.Fatalf("a person's Fleet reaches the browser: %v", m)
		}
	}
	if _, ok := space["unread"].(map[string]any)[s.secret]; ok {
		t.Fatal("unread names the private channel")
	}
	for _, task := range space["tasks"].([]any) {
		if task.(map[string]any)["cid"] == s.secret {
			t.Fatal("space_get lists a task of the private channel")
		}
	}
	notIn := "You are not in this conversation"
	for _, cid := range []string{s.secret, s.ownerDM} {
		for method, args := range map[string]map[string]any{
			"messages":     {"cid": cid},
			"read":         {"cid": cid, "id": s.secretMsg},
			"files":        {"cid": cid},
			"notify_set":   {"cid": cid, "level": "all"},
			"post":         {"cid": cid, "text": "let me in"},
			"conv_join":    {"cid": cid},
			"conv_leave":   {"cid": cid},
			"conv_update":  {"cid": cid, "topic": "mine now"},
			"conv_archive": {"cid": cid},
			"search":       {"q": "zanzibar", "cid": cid},
		} {
			if e := refusedAs(t, h, b, alice, method, args); !strings.Contains(e, notIn) && !strings.Contains(e, "no conversation") {
				t.Fatalf("%s %s: %s", method, cid, e)
			}
		}
	}
	for method, args := range map[string]map[string]any{
		"follow":      {"root": s.secretMsg},
		"task_create": {"id": s.secretMsg},
		"task_update": {"id": s.secretMsg, "state": "done"},
		"edit":        {"id": s.secretMsg, "text": "x"},
		"delete":      {"id": s.secretMsg},
	} {
		if e := refusedAs(t, h, b, alice, method, args); !strings.Contains(e, notIn) {
			t.Fatalf("%s: %s", method, e)
		}
	}
	if e := refusedAs(t, h, b, alice, "file_get", map[string]any{"id": s.secretFile}); !strings.Contains(e, "no file") {
		t.Fatal(e)
	}
	// A file of the owner's is not hers to post either.
	if e := refusedAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "look", "files": []string{s.secretFile}}); !strings.Contains(e, "no file") {
		t.Fatal(e)
	}
	found := callAs(t, h, b, alice, "search", map[string]any{"q": "zanzibar"})
	if found["total"].(float64) != 0 {
		t.Fatalf("search found %v", found["results"])
	}
	if tasks := callAs(t, h, b, alice, "tasks", map[string]any{})["tasks"].([]any); len(tasks) != 0 {
		t.Fatalf("tasks: %v", tasks)
	}
	for _, filter := range []string{"all", "unread", "mentions", "review"} {
		for _, r := range callAs(t, h, b, alice, "activity", map[string]any{"filter": filter})["rows"].([]any) {
			if cid := r.(map[string]any)["cid"]; cid == s.secret || cid == s.ownerDM {
				t.Fatalf("Activity %s shows %v", filter, r)
			}
		}
	}
	callAs(t, h, b, alice, "read_all", map[string]any{})
	if callAs(t, h, b, alice, "space_get", map[string]any{})["reads"].(map[string]any)[s.secret] != nil {
		t.Fatal("read_all marked the private channel")
	}
	// The owner's tools stay the owner's.
	for method, args := range map[string]map[string]any{
		"space_update":  {"name": "Mine"},
		"agent_create":  {"spec": map[string]any{"name": "Spy", "node_id": "n_mac", "runtime": "claude"}},
		"agent_update":  {"mid": s.shared, "description": "now mine"},
		"agent_live":    {"mid": s.shared},
		"agent_control": {"mid": s.writer, "action": "reset_full", "confirm": "/home/me/PantheonAgents/writer"},
	} {
		if e := refusedAs(t, h, b, alice, method, args); !strings.Contains(e, "Only the space owner") {
			t.Fatalf("%s: %s", method, e)
		}
	}
	if e := refusedAs(t, h, b, alice, "agent_control", map[string]any{"mid": s.shared, "action": "stop"}); !strings.Contains(e, "owner and admins") {
		t.Fatal(e)
	}
	// Reminders: only those for her.
	fl, _ := s.tc.lockFleet(context.Background(), fleetID)
	r, err := s.tc.setReminder(context.Background(), fl, fl.members[s.writer], s.writer, "in 30 minutes", "", "call the bank", "", "", "", tcOwner, "remind me", nil)
	fl.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	if list := callAs(t, h, b, alice, "reminders", map[string]any{})["reminders"].([]any); len(list) != 0 {
		t.Fatalf("reminders: %v", list)
	}
	if e := refusedAs(t, h, b, alice, "reminder_cancel", map[string]any{"id": r.ID}); !strings.Contains(e, "no reminder") {
		t.Fatal(e)
	}
	if list := call(t, h, b, "reminders", map[string]any{})["reminders"].([]any); len(list) != 1 {
		t.Fatalf("the owner's reminders: %v", list)
	}
}

// A DM between two people is theirs; an agent's DM is also its owner's.
func TestPeoplesDMsAreTheirsAndAgentDMsAlsoTheOwners(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	bobMID := personMID(bob.UserID)
	dm := callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{bobMID}})["conversation"].(map[string]any)["cid"].(string)
	callAs(t, h, b, alice, "post", map[string]any{"cid": dm, "text": "psst quokka"})
	if got := callAs(t, h, b, bob, "messages", map[string]any{"cid": dm})["messages"].([]any); len(got) != 1 {
		t.Fatalf("bob reads %v", got)
	}
	if contains(convIDs(call(t, h, b, "space_get", map[string]any{})), dm) {
		t.Fatal("the owner lists two people's DM")
	}
	if e := refused(t, h, b, "messages", map[string]any{"cid": dm}); !strings.Contains(e, "not in this conversation") {
		t.Fatal(e)
	}
	if call(t, h, b, "search", map[string]any{"q": "quokka"})["total"].(float64) != 0 {
		t.Fatal("the owner's search finds two people's DM")
	}
	if e := refusedAs(t, h, b, carol, "messages", map[string]any{"cid": dm}); !strings.Contains(e, "not in this conversation") {
		t.Fatal("an admin reads two people's DM: " + e)
	}
	// Alice's DM with the shared agent: hers and the agent owner's, not Bob's.
	adm := callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
	if !contains(convIDs(call(t, h, b, "space_get", map[string]any{})), adm) {
		t.Fatal("the owner does not see the DM of their agent")
	}
	if contains(convIDs(callAs(t, h, b, bob, "space_get", map[string]any{})), adm) {
		t.Fatal("bob lists alice's DM with an agent")
	}
	if e := refusedAs(t, h, b, bob, "messages", map[string]any{"cid": adm}); !strings.Contains(e, "not in this conversation") {
		t.Fatal(e)
	}
}

// T32: an "Only me" agent cannot be DMed, added, assigned or woken by a
// member, who is told why; its replies in a shared channel stay visible.
func TestAnOnlyMeAgentIsNotUsableByAMember(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	space := callAs(t, h, b, alice, "space_get", map[string]any{})
	canUse := fmt.Sprint(space["can_use"])
	if strings.Contains(canUse, s.writer) || !strings.Contains(canUse, s.shared) || !strings.Contains(canUse, s.listed) {
		t.Fatalf("alice can use %s", canUse)
	}
	if bobs := fmt.Sprint(callAs(t, h, b, bob, "space_get", map[string]any{})["can_use"]); strings.Contains(bobs, s.listed) {
		t.Fatalf("bob can use the listed agent: %s", bobs)
	}
	only := "Only @owner can use Writer."
	if e := refusedAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{s.writer}}); e != only {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, bob, "conv_create", map[string]any{"kind": "dm", "members": []string{s.listed}}); !strings.Contains(e, "listed people only") {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, alice, "conv_create", map[string]any{"kind": "channel", "name": "mine", "members": []string{s.writer}}); e != only {
		t.Fatal(e)
	}
	mine := callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "channel", "name": "mine"})["conversation"].(map[string]any)["cid"].(string)
	if e := refusedAs(t, h, b, alice, "conv_join", map[string]any{"cid": mine, "mid": s.writer}); e != only {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, alice, "conv_update", map[string]any{"cid": mine, "members": []string{personMID(alice.UserID), s.writer}}); e != only {
		t.Fatal(e)
	}
	task := callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "a job", "as_task": true})["task"].(map[string]any)["id"].(string)
	if e := refusedAs(t, h, b, alice, "task_update", map[string]any{"id": task, "assignee": s.writer}); e != only {
		t.Fatal(e)
	}
	// A mention is posted, wakes nothing, and says why.
	before := s.nodes.promptCount()
	out := callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@writer what is the plan?"})
	nw := out["not_woken"].([]any)
	if len(nw) != 1 || nw[0].(map[string]any)["mid"] != s.writer || nw[0].(map[string]any)["reason"] != only {
		t.Fatalf("%v", out)
	}
	// A channel where every agent hears every message wakes only the
	// agents she may use, and a thread Writer started is no way in either.
	call(t, h, b, "conv_join", map[string]any{"cid": s.team, "mid": s.writer})
	call(t, h, b, "conv_join", map[string]any{"cid": s.team, "mid": s.shared})
	call(t, h, b, "conv_update", map[string]any{"cid": s.team, "agent_reply": "all"})
	s.nodes.mu.Lock()
	s.nodes.hold = true
	s.nodes.mu.Unlock()
	callAs(t, h, b, alice, "post", map[string]any{"cid": s.team, "text": "anyone around?"})
	eventually(t, "shared's turn", func() bool { return s.nodes.promptCount() == before+1 })
	time.Sleep(150 * time.Millisecond)
	s.nodes.mu.Lock()
	for _, send := range s.nodes.sends[before:] {
		if strings.Contains(str(send["cwd"]), "writer") {
			t.Fatalf("writer was woken: %v", send["cwd"])
		}
	}
	s.nodes.hold = false
	s.nodes.mu.Unlock()
	// The owner asks Writer in #general: its reply is there for Alice to read.
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@writer say hi to everyone"})
	eventually(t, "writer's reply", func() bool {
		for _, m := range callAs(t, h, b, alice, "messages", map[string]any{"cid": "general"})["messages"].([]any) {
			if m.(map[string]any)["from"] == s.writer {
				return true
			}
		}
		return false
	})
	// Neither a reply in a thread Writer is in, nor an edit, wakes it for her.
	var root string
	for _, m := range callAs(t, h, b, alice, "messages", map[string]any{"cid": "general"})["messages"].([]any) {
		if m.(map[string]any)["from"] == s.writer {
			root = m.(map[string]any)["id"].(string)
		}
	}
	n := s.nodes.promptCount()
	reply := callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "root": root, "text": "and then?"})["message"].(map[string]any)["id"].(string)
	callAs(t, h, b, alice, "edit", map[string]any{"id": reply, "text": "@writer and then?"})
	time.Sleep(200 * time.Millisecond)
	if s.nodes.promptCount() != n {
		t.Fatal("a member's thread reply or edit woke an Only me agent")
	}
}

// No relay around access: an agent working for Alice cannot wake an agent
// she may not use; working for the owner it can.
func TestAnAgentCannotWakeAnAgentThePersonItWorksForMayNotUse(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	s.nodes.mu.Lock()
	s.nodes.hold = true
	s.nodes.mu.Unlock()
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@shared please ask @writer for the plan"})
	eventually(t, "shared's turn", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return fl.turns[s.shared] != nil && fl.turns[s.shared].State == "running"
	})
	before := s.nodes.promptCount()
	tok := tc.agentToken(fleetID, s.shared)
	r := tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: s.shared, Token: tok, Op: "post", Args: []string{"general"}, Text: "@writer what is the plan?"})
	if r.Code != 0 || !strings.Contains(r.Text, "Not woken: @writer") || !strings.Contains(r.Text, "@u-alice asked you") {
		t.Fatalf("%+v", r)
	}
	r = tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: s.shared, Token: tok, Op: "dm", Args: []string{"@writer"}, Text: "the plan?"})
	if r.Code != 0 || !strings.Contains(r.Text, "Not woken: @writer") {
		t.Fatalf("%+v", r)
	}
	time.Sleep(150 * time.Millisecond)
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	queued := len(fl.queues[s.writer])
	fl.mu.Unlock()
	if queued != 0 || s.nodes.promptCount() != before {
		t.Fatalf("writer was woken through shared (%d queued)", queued)
	}
	// Shared's next turn works for the owner: then Writer is woken.
	s.nodes.mu.Lock()
	s.nodes.hold = false // the held turn ends at the next look
	s.nodes.mu.Unlock()
	eventually(t, "shared idle", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return fl.turns[s.shared] == nil
	})
	s.nodes.mu.Lock()
	s.nodes.hold = true
	s.nodes.mu.Unlock()
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@shared ask @writer for the plan"})
	eventually(t, "shared's second turn", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return fl.turns[s.shared] != nil && fl.turns[s.shared].State == "running"
	})
	r = tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: s.shared, Token: tok, Op: "post", Args: []string{"general"}, Text: "@writer what is the plan?"})
	if r.Code != 0 || strings.Contains(r.Text, "Not woken") {
		t.Fatalf("%+v", r)
	}
	eventually(t, "writer woken for the owner", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return len(fl.queues[s.writer]) > 0 || fl.turns[s.writer] != nil
	})
}

// Each person gets only their events, on their own subject; the owner gets
// none of two people's DM.
func TestEachPersonGetsOnlyTheirEvents(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	nc := dial(t, s.url)
	subs := map[string]*nats.Subscription{}
	for who, subject := range map[string]string{
		"owner": "fleet." + fleetID + ".apps.team-chat.events",
		"alice": "fleet." + aliceFleet + ".apps.team-chat.spaces." + fleetID,
		"bob":   "fleet." + bobFleet + ".apps.team-chat.spaces." + fleetID,
	} {
		sub, err := nc.SubscribeSync(subject)
		if err != nil {
			t.Fatal(err)
		}
		subs[who] = sub
	}
	nc.Flush()
	drain := func(who string) []map[string]any {
		var out []map[string]any
		for {
			msg, err := subs[who].NextMsg(150 * time.Millisecond)
			if err != nil {
				return out
			}
			var ev map[string]any
			_ = json.Unmarshal(msg.Data, &ev)
			out = append(out, ev)
		}
	}
	text := func(evs []map[string]any) string {
		raw, _ := json.Marshal(evs)
		return string(raw)
	}
	drain("owner")
	drain("alice")
	drain("bob")
	call(t, h, b, "post", map[string]any{"cid": s.secret, "text": "owner-only tapir"})
	callAs(t, h, b, bob, "conv_create", map[string]any{"kind": "dm", "members": []string{personMID(alice.UserID)}})
	dm := dmID(personMID(alice.UserID), personMID(bob.UserID))
	callAs(t, h, b, bob, "post", map[string]any{"cid": dm, "text": "between us axolotl"})
	o, a, bb := text(drain("owner")), text(drain("alice")), text(drain("bob"))
	if !strings.Contains(o, "tapir") || strings.Contains(o, "axolotl") {
		t.Fatalf("owner: %s", o)
	}
	if strings.Contains(a, "tapir") || !strings.Contains(a, "axolotl") || strings.Contains(bb, "tapir") || !strings.Contains(bb, "axolotl") {
		t.Fatalf("alice: %s\nbob: %s", a, bb)
	}
	// Revisions are each person's own and have no gaps.
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "one"})
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "two"})
	evs := drain("bob")
	for i := 1; i < len(evs); i++ {
		if evs[i]["rev"].(float64) != evs[i-1]["rev"].(float64)+1 {
			t.Fatalf("bob's revisions jump: %s", text(evs))
		}
	}
	// Read marks are seen by the others in the conversation (T33).
	last := callAs(t, h, b, bob, "post", map[string]any{"cid": "general", "text": "three"})["message"].(map[string]any)["id"]
	drain("bob")
	callAs(t, h, b, alice, "read", map[string]any{"cid": "general", "id": last})
	if !strings.Contains(text(drain("bob")), `"type":"read"`) {
		t.Fatal("bob does not see alice's read mark")
	}
	readsBy := callAs(t, h, b, bob, "space_get", map[string]any{})["reads_by"].(map[string]any)
	if readsBy["general"].(map[string]any)[personMID(alice.UserID)] == nil {
		t.Fatalf("reads_by %v", readsBy)
	}
	// Alice taken out of a private channel she was in: told it is gone.
	priv := call(t, h, b, "conv_create", map[string]any{"kind": "channel", "name": "inner", "private": true,
		"members": []string{personMID(alice.UserID)}})["conversation"].(map[string]any)["cid"].(string)
	drain("alice")
	call(t, h, b, "conv_leave", map[string]any{"cid": priv, "mid": personMID(alice.UserID)})
	if got := text(drain("alice")); !strings.Contains(got, `"gone":true`) || !strings.Contains(got, priv) {
		t.Fatalf("alice: %s", got)
	}
	// An agent working in the private channel: others see that it works, not where.
	fl, _ := s.tc.lockFleet(context.Background(), fleetID)
	st := fl.statusView(map[string]any{"state": "working", "cid": s.secret, "root": "", "line": "Reading plan.txt"}, s.writer, personMID(alice.UserID))
	fl.mu.Unlock()
	if st["cid"] != nil || st["line"] != nil || st["state"] != "working" {
		t.Fatalf("%v", st)
	}
}

// T34: removing a person ends their access at their next call; a grant from
// before the removal stays refused; joining again works.
func TestRemovingAPersonEndsTheirAccessAtOnce(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	nc := dial(t, s.url)
	sub, _ := nc.SubscribeSync("fleet." + aliceFleet + ".apps.team-chat.spaces." + fleetID)
	nc.Flush()
	callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "channel", "name": "alices"})
	if ok, err := tc.MemberChange(context.Background(), fleetID, alice.UserID, "remove", ""); !ok || err != nil {
		t.Fatal(ok, err)
	}
	gotRemoved := false
	for {
		msg, err := sub.NextMsg(150 * time.Millisecond)
		if err != nil {
			break
		}
		if strings.Contains(string(msg.Data), `"type":"removed"`) {
			gotRemoved = true
		}
	}
	if !gotRemoved {
		t.Fatal("alice was not told she was removed")
	}
	for _, method := range []string{"space_get", "messages", "search", "activity", "post"} {
		args := map[string]any{"cid": "general", "q": "x", "text": "x"}
		switch method {
		case "space_get":
			args = map[string]any{}
		case "activity":
			args = map[string]any{}
		case "messages":
			args = map[string]any{"cid": "general"}
		case "search":
			args = map[string]any{"q": "hello"}
		case "post":
			args = map[string]any{"cid": "general", "text": "still here?"}
		}
		if e := refusedAs(t, h, b, alice, method, args); e != "You are no longer in this space." {
			t.Fatalf("%s: %s", method, e)
		}
	}
	// Nothing more reaches her subject.
	callAs(t, h, b, bob, "post", map[string]any{"cid": "general", "text": "after she left"})
	if msg, err := sub.NextMsg(200 * time.Millisecond); err == nil {
		t.Fatalf("a removed person got %s", msg.Data)
	}
	space := call(t, h, b, "space_get", map[string]any{})
	gone := memberByName(space, "Alice")
	if gone["removed_at"] == nil {
		t.Fatalf("%v", gone)
	}
	for _, c := range space["conversations"].([]any) {
		if strings.Contains(fmt.Sprint(c.(map[string]any)["members"]), personMID(alice.UserID)) && c.(map[string]any)["kind"] == "channel" {
			t.Fatalf("still in %v", c)
		}
	}
	// Invited again: a grant made after the removal lets her in.
	again := *alice
	again.Joined = time.Now().Add(time.Minute).Unix()
	if callAs(t, h, b, &again, "space_get", map[string]any{})["role"] != "member" {
		t.Fatal("rejoin")
	}
	// A role change takes effect with the next grant; the service follows the Hub.
	if ok, _ := tc.MemberChange(context.Background(), fleetID, bob.UserID, "role", "admin"); !ok {
		t.Fatal("role")
	}
	if memberByName(call(t, h, b, "space_get", map[string]any{}), "Bob")["role"] != "admin" {
		t.Fatal("bob's role")
	}
}

// Roles: admins manage channels and the agents they may use; members their
// own channels; only the owner the agents and the space.
func TestRoles(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	if e := refusedAs(t, h, b, bob, "conv_update", map[string]any{"cid": s.team, "name": "bobs"}); !strings.Contains(e, "Only the space owner, admins") {
		t.Fatal(e)
	}
	callAs(t, h, b, carol, "conv_update", map[string]any{"cid": s.team, "topic": "set by an admin"})
	own := callAs(t, h, b, bob, "conv_create", map[string]any{"kind": "channel", "name": "bobs", "private": true})["conversation"].(map[string]any)["cid"].(string)
	callAs(t, h, b, bob, "conv_update", map[string]any{"cid": own, "topic": "mine"})
	if !contains(convIDs(call(t, h, b, "space_get", map[string]any{})), own) {
		t.Fatal("the owner does not see a member's private channel")
	}
	if contains(convIDs(callAs(t, h, b, alice, "space_get", map[string]any{})), own) {
		t.Fatal("another member lists a member's private channel")
	}
	if e := refusedAs(t, h, b, carol, "agent_update", map[string]any{"mid": s.shared, "access": "only_me"}); !strings.Contains(e, "Only the space owner") {
		t.Fatal(e)
	}
	callAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.shared, "action": "stop"})
	callAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.shared, "action": "start"})
	if e := refusedAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.writer, "action": "stop"}); e != "Only @owner can use Writer." {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.shared, "action": "delete"}); !strings.Contains(e, "Only the space owner") {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, carol, "space_update", map[string]any{"settings": map[string]any{"turns_per_space_day": 9999}}); !strings.Contains(e, "Only the space owner") {
		t.Fatal(e)
	}
	// A bad grant is no one.
	if e := refusedAs(t, h, b, &Member{UserID: "u-x", Username: "x", Fleet: "f_x", Role: "owner", Joined: 1}, "space_get", map[string]any{}); e != "You are no longer in this space." {
		t.Fatal(e)
	}
	// People are marked in an agent's view.
	fl, _ := s.tc.lockFleet(context.Background(), fleetID)
	label := authorOf(fl, personMID(carol.UserID), s.shared)
	fl.mu.Unlock()
	if label != "@u-carol (person, admin)" {
		t.Fatal(label)
	}
}

// Only team-chat takes a space grant's call.
func TestOnlyTeamChatTakesAMembersCall(t *testing.T) {
	url := jetStream(t)
	h, mine, _ := desktopHost(t, desktopOn(t, url))
	status, out := rpcAs(t, h, mine, alice, "get", map[string]any{})
	if status != 403 || out["success"] != false {
		t.Fatalf("%d %v", status, out)
	}
	if h.MemberOK(mine) {
		t.Fatal("desktop-state counted as team-chat")
	}
	tc := teamOn(t, url, newFakeAgents())
	th, tb, _ := teamHost(t, tc)
	if !th.MemberOK(tb) {
		t.Fatal("team-chat binding")
	}
}

// T35: an agent made shared may not work in a home folder or /, checked on
// its computer; an unreachable computer cannot vouch for a folder.
func TestMakingAnAgentSharedChecksItsFolder(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	home := makeAgent(t, h, b, "Home", "n_mac", "claude", map[string]any{"folder": "/home/me"})
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": home, "access": "everyone"}); !strings.Contains(e, "cannot work in a home folder") {
		t.Fatal(e)
	}
	own := makeAgent(t, h, b, "Own", "n_mac", "claude", nil)
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": own, "access": "everyone", "folder": "/"}); !strings.Contains(e, "the whole computer") {
		t.Fatal(e)
	}
	nodes.mu.Lock()
	nodes.online["n_mac"] = false
	nodes.mu.Unlock()
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": own, "access": "everyone"}); !strings.Contains(e, "could not check") {
		t.Fatal(e)
	}
	nodes.mu.Lock()
	nodes.online["n_mac"] = true
	nodes.mu.Unlock()
	out := call(t, h, b, "agent_update", map[string]any{"mid": own, "access": "everyone"})
	if out["agent"].(map[string]any)["access"] != "everyone" {
		t.Fatalf("%v", out)
	}
}

func TestGrantIDsAndPersonIDsAreStable(t *testing.T) {
	if TeamChatGrantID(fleetID, "u1") != TeamChatGrantID(fleetID, "u1") || TeamChatGrantID(fleetID, "u1") == TeamChatGrantID(otherFleet, "u1") {
		t.Fatal("grant id")
	}
	if len(TeamChatGrantID(fleetID, "u1")) != 36 || !strings.HasPrefix(personMID("u1"), "p") || personMID("u1") == personMID("u2") {
		t.Fatal("ids")
	}
}

// A person's message queued for an agent they may no longer use (its access
// changed meanwhile) does not start a turn.
func TestQueuedAsksOfPeopleWhoLostAccessAreDropped(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	call(t, h, b, "agent_control", map[string]any{"mid": s.shared, "action": "stop"})
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@shared please look at this"})
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	queued := len(fl.queues[s.shared])
	fl.mu.Unlock()
	if queued != 1 {
		t.Fatalf("queued %d", queued)
	}
	call(t, h, b, "agent_update", map[string]any{"mid": s.shared, "access": "only_me"})
	before := s.nodes.promptCount()
	call(t, h, b, "agent_control", map[string]any{"mid": s.shared, "action": "start"})
	eventually(t, "the queue emptied", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return len(fl.queues[s.shared]) == 0
	})
	time.Sleep(150 * time.Millisecond)
	if s.nodes.promptCount() != before {
		t.Fatal("a turn started for a person who may no longer use the agent")
	}
}

// In review waits for the person who made the task, not for everyone who
// sees it.
func TestInReviewIsForWhoeverMadeTheTask(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	mine := callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "alice's job", "as_task": true})["task"].(map[string]any)["id"].(string)
	theirs := call(t, h, b, "post", map[string]any{"cid": "general", "text": "the owner's job", "as_task": true})["task"].(map[string]any)["id"].(string)
	for _, id := range []string{mine, theirs} {
		call(t, h, b, "task_update", map[string]any{"id": id, "state": "in_review"})
	}
	review := func(m *Member) []string {
		var out []string
		var rows []any
		if m == nil {
			rows = call(t, h, b, "activity", map[string]any{"filter": "review"})["rows"].([]any)
		} else {
			rows = callAs(t, h, b, m, "activity", map[string]any{"filter": "review"})["rows"].([]any)
		}
		for _, r := range rows {
			out = append(out, r.(map[string]any)["task"].(map[string]any)["id"].(string))
		}
		return out
	}
	if got := review(alice); len(got) != 1 || got[0] != mine {
		t.Fatalf("alice: %v", got)
	}
	if got := review(bob); len(got) != 0 {
		t.Fatalf("bob: %v", got)
	}
	if got := review(nil); len(got) != 1 || got[0] != theirs {
		t.Fatalf("owner: %v", got)
	}
}
