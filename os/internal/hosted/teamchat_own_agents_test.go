package hosted

// People bring their own agents (docs/apps/team-chat.md, "Agents people
// bring"): an agent a person who joined makes runs on a computer of their
// own Fleet, with its sign-ins and their PantheonOS account; only they
// change or control it; the space owner may remove it from the space; it
// leaves with them.

import (
	"context"
	"strings"
	"testing"
)

// aliceAgent is an agent Alice brings on her own computer (n_alice).
func aliceAgent(t *testing.T, s *sharedSpace, name string, extra map[string]any) string {
	t.Helper()
	spec := map[string]any{"name": name, "node_id": "n_alice", "runtime": "claude"}
	for k, v := range extra {
		spec[k] = v
	}
	out := callAs(t, s.h, s.b, alice, "agent_create", map[string]any{"spec": spec})
	return out["agent"].(map[string]any)["mid"].(string)
}

// callsTo are the node calls made to node, as "fleet method".
func (f *fakeAgents) callsTo(node string) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for _, c := range f.calls {
		parts := strings.Fields(c)
		if len(parts) == 3 && parts[1] == node {
			out = append(out, parts[0]+" "+parts[2])
		}
	}
	return out
}

func TestAMemberBringsAnAgentOnTheirOwnComputer(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc, nodes := s.h, s.b, s.tc, s.nodes
	aliceMID := personMID(alice.UserID)

	// Readiness is her computer's: an offline one, a runtime not signed in
	// there, or a computer of the owner's is refused.
	nodes.mu.Lock()
	nodes.online["n_alice"] = false
	nodes.mu.Unlock()
	if e := refusedAs(t, h, b, alice, "agent_create", map[string]any{"spec": map[string]any{"name": "Tally", "node_id": "n_alice", "runtime": "claude"}}); !strings.Contains(e, "offline") {
		t.Fatal(e)
	}
	nodes.mu.Lock()
	nodes.online["n_alice"] = true
	nodes.mu.Unlock()
	if e := refusedAs(t, h, b, alice, "agent_create", map[string]any{"spec": map[string]any{"name": "Tally", "node_id": "n_alice", "runtime": "gemini"}}); !strings.Contains(e, "not signed in") {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, alice, "agent_create", map[string]any{"spec": map[string]any{"name": "Tally", "node_id": "n_ws", "runtime": "claude"}}); !strings.Contains(e, "not one of yours") {
		t.Fatal(e)
	}

	tally := aliceAgent(t, s, "Tally", nil)
	for _, c := range nodes.callsTo("n_alice") {
		if !strings.HasPrefix(c, aliceFleet+" ") {
			t.Fatalf("a call to her computer went through another Fleet: %v", nodes.callsTo("n_alice"))
		}
	}
	// Hers: she sees its computer and folder; the owner sees whose it is.
	mine := memberByName(callAs(t, h, b, alice, "space_get", map[string]any{}), "Tally")
	if mine["created_by"] != aliceMID || mine["folder"] == nil || mine["node_id"] != "n_alice" || mine["fleet"] != nil {
		t.Fatalf("her view: %v", mine)
	}
	theirs := memberByName(call(t, h, b, "space_get", map[string]any{}), "Tally")
	if theirs["folder"] != nil || theirs["node_id"] != nil || !strings.HasPrefix(str(theirs["node_name"]), "Alice") {
		t.Fatalf("the owner's view: %v", theirs)
	}

	// Her DM wakes it on her computer, through her Fleet.
	nodes.mu.Lock()
	nodes.hold = true
	nodes.mu.Unlock()
	dm := callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{tally}})["conversation"].(map[string]any)["cid"].(string)
	before := len(nodes.callsTo("n_alice"))
	callAs(t, h, b, alice, "post", map[string]any{"cid": dm, "text": "count the beans"})
	eventually(t, "Tally's turn", func() bool {
		nodes.mu.Lock()
		defer nodes.mu.Unlock()
		return len(nodes.sends) > 0 && str(nodes.sends[len(nodes.sends)-1]["cwd"]) == "/home/me/PantheonAgents/tally"
	})
	for _, c := range nodes.callsTo("n_alice")[before:] {
		if !strings.HasPrefix(c, aliceFleet+" ") {
			t.Fatalf("her agent's turn went through another Fleet: %v", c)
		}
	}
	team := nodes.lastTeam()
	req := agentRequest{MID: tally, Token: str(team["token"]), Turn: str(team["turn"]), Cred: str(team["cred"]), Op: "post",
		Args: []string{"#general"}, Text: "beans: 12"}
	// Its node's requests come on her Fleet's subject and reach the space;
	// the same request on the space's own subject is not its.
	if r := tc.AgentRequest(context.Background(), fleetID, req); r.Code == 0 {
		t.Fatalf("answered from the space's Fleet: %+v", r)
	}
	if r := tc.AgentRequest(context.Background(), bobFleet, req); r.Code == 0 {
		t.Fatalf("answered from someone else's Fleet: %+v", r)
	}
	if r := tc.AgentRequest(context.Background(), aliceFleet, req); r.Code != 0 {
		t.Fatalf("its own Fleet: %+v", r)
	}
	if m := lastFrom(msgs(t, h, b, "general", ""), tally); m == nil || m["text"] != "beans: 12" {
		t.Fatalf("its post: %v", m)
	}
	// The space owner may not stop its turn or watch it; she may.
	if e := refused(t, h, b, "agent_control", map[string]any{"mid": tally, "action": "stop_turn"}); !strings.Contains(e, "Only @") {
		t.Fatal(e)
	}
	if e := refused(t, h, b, "agent_live", map[string]any{"mid": tally}); !strings.Contains(e, "Only @") {
		t.Fatal(e)
	}
	live := callAs(t, h, b, alice, "agent_live", map[string]any{"mid": tally})
	if !strings.HasPrefix(str(live["stream_subject"]), "fleet."+aliceFleet+".") {
		t.Fatalf("its stream: %v", live)
	}
	callAs(t, h, b, alice, "agent_control", map[string]any{"mid": tally, "action": "stop_turn"})
	nodes.mu.Lock()
	nodes.hold = false
	nodes.mu.Unlock()
	nodes.release <- struct{}{}
	quiet(t, tc, tally)
}

func TestOnlyAnAgentsOwnerChangesItAndTheSpaceOwnerMayRemoveIt(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	tally := aliceAgent(t, s, "Tally", map[string]any{"access": "everyone", "folder": "/home/me/agents/tally"})
	for _, who := range []*Member{carol, bob} {
		for _, args := range []map[string]any{{"mid": tally, "action": "stop"}, {"mid": tally, "action": "start"},
			{"mid": tally, "action": "delete"}, {"mid": tally, "action": "remove"}} {
			if e := refusedAs(t, h, b, who, "agent_control", args); !strings.Contains(e, "Only @u-alice") {
				t.Fatalf("%s %v: %s", who.Name, args, e)
			}
		}
	}
	if e := refusedAs(t, h, b, carol, "agent_update", map[string]any{"mid": tally, "description": "mine"}); !strings.Contains(e, "Only @u-alice can change Tally") {
		t.Fatal(e)
	}
	for _, args := range []map[string]any{{"mid": tally, "action": "stop"}, {"mid": tally, "action": "reset_session"}} {
		if e := refused(t, h, b, "agent_control", args); !strings.Contains(e, "Only @u-alice") {
			t.Fatalf("the space owner %v: %s", args, e)
		}
	}
	if e := refused(t, h, b, "agent_update", map[string]any{"mid": tally, "dms": "everyone"}); !strings.Contains(e, "Only @u-alice") {
		t.Fatal(e)
	}
	if e := refused(t, h, b, "agent_move", map[string]any{"mid": tally, "node_id": "n_mac"}); !strings.Contains(e, "Only @u-alice") {
		t.Fatal(e)
	}
	// She changes it; a move stays among her computers.
	callAs(t, h, b, alice, "agent_update", map[string]any{"mid": tally, "description": "counts beans", "dms": "everyone"})
	callAs(t, h, b, alice, "agent_control", map[string]any{"mid": tally, "action": "stop"})
	callAs(t, h, b, alice, "agent_control", map[string]any{"mid": tally, "action": "start"})
	if e := refusedAs(t, h, b, alice, "agent_move", map[string]any{"mid": tally, "node_id": "n_mac"}); !strings.Contains(e, "not one of the agent owner's computers") {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, alice, "agent_update", map[string]any{"mid": tally, "node_id": "n_ws"}); !strings.Contains(e, "not one of the agent owner's computers") {
		t.Fatal(e)
	}
	// The space owner removes it from the space; nothing on her computer is touched.
	setups := len(s.nodes.callsTo("n_alice"))
	if out := call(t, h, b, "agent_control", map[string]any{"mid": tally, "action": "remove"}); out["removed"] != tally {
		t.Fatalf("%v", out)
	}
	for _, c := range s.nodes.callsTo("n_alice")[setups:] {
		if strings.HasSuffix(c, " team_setup") || strings.HasSuffix(c, " agent_delete") {
			t.Fatalf("removing it touched her computer: %v", c)
		}
	}
	space := call(t, h, b, "space_get", map[string]any{})
	if memberByName(space, "Tally") != nil {
		t.Fatal("still a member")
	}
	if f := space["space"].(map[string]any)["formers"].(map[string]any)[tally]; f == nil {
		t.Fatal("its messages lost its name")
	}
}

func TestAPersonsAgentsLeaveWithThem(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	tally := aliceAgent(t, s, "Tally", map[string]any{"access": "everyone", "folder": "/home/me/agents/tally"})
	if _, err := tc.MemberChange(context.Background(), fleetID, alice.UserID, "leave", "", ""); err != nil {
		t.Fatal(err)
	}
	space := call(t, h, b, "space_get", map[string]any{})
	if memberByName(space, "Tally") != nil {
		t.Fatal("her agent stayed")
	}
	if f := space["space"].(map[string]any)["formers"].(map[string]any)[tally]; f == nil {
		t.Fatal("not shown as removed")
	}
	before := s.nodes.promptCount()
	r := call(t, h, b, "post", map[string]any{"cid": "general", "text": "@tally are you there?"})
	if r["message"].(map[string]any)["mentions"] != nil {
		t.Fatalf("a mention still names it: %v", r)
	}
	if line := lastSystem(msgs(t, h, b, "general", "")); !strings.Contains(line, "Their agents left with them: @tally") {
		t.Fatalf("system line: %q", line)
	}
	if s.nodes.promptCount() != before {
		t.Fatal("it was woken")
	}
}

// Its reminders are its owner's to see (and those for someone, theirs).
func TestAnAgentsRemindersAreItsOwners(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	tally := aliceAgent(t, s, "Tally", nil)
	aliceMID := personMID(alice.UserID)
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	_, err := tc.setReminder(context.Background(), fl, fl.members[tally], tally, "in 30 minutes", "", "tidy notes", "", "", "", aliceMID, "remind me", nil)
	fl.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	if list := callAs(t, h, b, alice, "reminders", map[string]any{"mid": tally})["reminders"].([]any); len(list) != 1 {
		t.Fatalf("hers: %v", list)
	}
	if list := call(t, h, b, "reminders", map[string]any{"mid": tally})["reminders"].([]any); len(list) != 0 {
		t.Fatalf("the space owner sees her agent's reminders: %v", list)
	}
}

// The space owner is asked to name the space once, on any device.
func TestTheNamePromptIsRememberedForTheSpace(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	if v := call(t, h, b, "space_get", map[string]any{})["space"].(map[string]any)["name_asked_at"]; v != nil {
		t.Fatalf("asked already: %v", v)
	}
	if e := refusedAs(t, h, b, alice, "space_update", map[string]any{"name_asked": true}); !strings.Contains(e, "Only the space owner") {
		t.Fatal(e)
	}
	call(t, h, b, "space_update", map[string]any{"name_asked": true})
	if v := call(t, h, b, "space_get", map[string]any{})["space"].(map[string]any)["name_asked_at"]; v == nil || v == "" {
		t.Fatal("not remembered")
	}
}

func lastSystem(list []map[string]any) string {
	for i := len(list) - 1; i >= 0; i-- {
		if list[i]["kind"] == "system" {
			return str(list[i]["text"])
		}
	}
	return ""
}
