package hosted

// Fixes after the first Phase 3 check (docs/apps/team-chat.md, "Phase 3 as
// built"): the owner is never locked out of a channel, a shared agent reads
// for a person only what that person sees, shared folders, look-alike names,
// what admins and members are sent.

import (
	"context"
	"strings"
	"testing"
	"time"
)

// An admin cannot take the owner out of a channel (or make it one the owner
// cannot see); the owner sees, joins, reads and archives every channel of
// their space, a private one without people left in it included.
func TestTheOwnerIsNeverLockedOutOfAChannel(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	carolMID := personMID(carol.UserID)
	pub := call(t, h, b, "conv_create", map[string]any{"kind": "channel", "name": "owner-pub"})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, b, "post", map[string]any{"cid": pub, "text": "the owner's history"})
	callAs(t, h, b, carol, "conv_join", map[string]any{"cid": pub})
	// The check's repro: private, with only the admin in it.
	if e := refusedAs(t, h, b, carol, "conv_update", map[string]any{"cid": pub, "private": true, "members": []string{carolMID}}); !strings.Contains(e, "space owner stays") {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, carol, "conv_leave", map[string]any{"cid": pub, "mid": tcOwner}); !strings.Contains(e, "space owner stays") {
		t.Fatal(e)
	}
	// Made private with the owner kept: still the owner's.
	callAs(t, h, b, carol, "conv_update", map[string]any{"cid": pub, "private": true})
	if !contains(convIDs(call(t, h, b, "space_get", map[string]any{})), pub) {
		t.Fatal("the owner lost the channel")
	}
	if got := msgs(t, h, b, pub, ""); len(got) == 0 {
		t.Fatal("the owner cannot read the channel")
	}
	// A private channel the owner was never in: listed, readable, joinable
	// and archivable by the owner, nothing unread until they join.
	bobsCh := callAs(t, h, b, bob, "conv_create", map[string]any{"kind": "channel", "name": "bob-alice", "private": true,
		"members": []string{personMID(alice.UserID), s.shared}})["conversation"].(map[string]any)["cid"].(string)
	callAs(t, h, b, bob, "post", map[string]any{"cid": bobsCh, "text": "between us"})
	space := call(t, h, b, "space_get", map[string]any{})
	if !contains(convIDs(space), bobsCh) {
		t.Fatal("the owner does not see a member's private channel")
	}
	if u := space["unread"].(map[string]any)[bobsCh].(map[string]any); u["count"] != float64(0) {
		t.Fatalf("unread in a channel the owner is not in: %v", u)
	}
	if contains(convIDs(callAs(t, h, b, carol, "space_get", map[string]any{})), bobsCh) {
		t.Fatal("an admin who is not in it sees a private channel")
	}
	if got := msgs(t, h, b, bobsCh, ""); len(got) != 1 {
		t.Fatalf("the owner reads %v", got)
	}
	call(t, h, b, "conv_join", map[string]any{"cid": bobsCh})
	call(t, h, b, "conv_leave", map[string]any{"cid": bobsCh})
	// Its people all removed: still the owner's to see and archive.
	for _, m := range []*Member{alice, bob} {
		if _, err := tc.MemberChange(context.Background(), fleetID, m.UserID, "remove", ""); err != nil {
			t.Fatal(err)
		}
	}
	if !contains(convIDs(call(t, h, b, "space_get", map[string]any{})), bobsCh) {
		t.Fatal("an orphaned private channel is hidden from the owner")
	}
	out := call(t, h, b, "conv_archive", map[string]any{"cid": bobsCh})
	if out["conversation"].(map[string]any)["archived"] != true {
		t.Fatalf("%v", out)
	}
}

// A shared agent whose turn works for a person reads, searches and is handed
// only what that person sees; a turn never carries one person's DM to
// another; the owner's turns keep the agent's whole view.
func TestASharedAgentReadsForAPersonOnlyWhatTheySee(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc, nodes := s.h, s.b, s.tc, s.nodes
	eventually(t, "writer idle", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return fl.turns[s.writer] == nil && len(fl.queues[s.writer]) == 0
	})
	base := nodes.promptCount()
	nodes.mu.Lock()
	nodes.hold = true
	nodes.mu.Unlock()
	// The owner's turn runs (held) while Alice and Bob ask.
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@shared warm up"})
	running := func(n int) func() bool {
		return func() bool {
			fl, _ := tc.lockFleet(context.Background(), fleetID)
			defer fl.mu.Unlock()
			return fl.turns[s.shared] != nil && fl.turns[s.shared].State == "running" && nodes.promptCount() == base+n
		}
	}
	eventually(t, "the owner's turn", running(1))
	adm := callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
	callAs(t, h, b, alice, "post", map[string]any{"cid": adm, "text": "the code word is kumquat"})
	callAs(t, h, b, bob, "post", map[string]any{"cid": "general", "text": "@shared what did alice tell you?"})
	tok := tc.agentToken(fleetID, s.shared)
	ask := func(op string, args []string, opts map[string]string) agentReply {
		return tc.AgentRequest(context.Background(), fleetID, agentRequest{MID: s.shared, Token: tok, Op: op, Args: args, Opts: opts})
	}
	// The owner's turn: the whole view; its inbox takes Alice's DM (it fits
	// with the turn so far) but not Bob's ask together with it.
	if r := ask("read", []string{"@u-alice"}, nil); r.Code != 0 || !strings.Contains(r.Text, "kumquat") {
		t.Fatalf("the owner's turn: %+v", r)
	}
	inbox := ask("inbox", nil, nil)
	if !strings.Contains(inbox.Text, "kumquat") || strings.Contains(inbox.Text, "what did alice tell you") {
		t.Fatalf("inbox: %+v", inbox)
	}
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	left := len(fl.queues[s.shared])
	fl.mu.Unlock()
	if left != 1 {
		t.Fatalf("%d left in the queue, want Bob's ask", left)
	}
	nodes.release <- struct{}{}
	// Bob's turn: Alice's DM is not his to see, whatever he asks.
	eventually(t, "bob's turn", running(2))
	if p := nodes.lastPrompt(); strings.Contains(p, "kumquat") || !strings.Contains(p, "what did alice tell you") {
		t.Fatalf("bob's turn input: %s", p)
	}
	if r := ask("read", []string{"@u-alice"}, nil); r.Code == 0 || strings.Contains(r.Text, "kumquat") {
		t.Fatalf("read for bob: %+v", r)
	}
	if r := ask("search", []string{"kumquat"}, nil); strings.Contains(r.Text, "kumquat is") || !strings.Contains(r.Text, "No messages found") {
		t.Fatalf("search for bob: %+v", r)
	}
	if r := ask("search", []string{"kumquat"}, map[string]string{"in": "@u-alice"}); strings.Contains(r.Text, "code word") {
		t.Fatalf("search in alice's DM for bob: %+v", r)
	}
	if r := ask("read", []string{"general"}, nil); r.Code != 0 || !strings.Contains(r.Text, "what did alice tell you") {
		t.Fatalf("bob's turn reads #general: %+v", r)
	}
	// Two people's asks in one turn: only what both see.
	nodes.release <- struct{}{}
	eventually(t, "shared idle", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return fl.turns[s.shared] == nil
	})
	fl, _ = tc.lockFleet(context.Background(), fleetID)
	events := []tcWake{{CID: adm, From: personMID(alice.UserID), Kind: "post"}, {CID: "general", From: personMID(bob.UserID), Kind: "post"},
		{CID: "general", From: personMID(alice.UserID), Kind: "post"}}
	took := fl.turnEvents(fl.members[s.shared], events)
	fl.mu.Unlock()
	if len(took) != 2 || took[0].CID != adm || took[1].From != personMID(alice.UserID) {
		t.Fatalf("one turn took %v (Alice's asks, not Bob's)", took)
	}
	// Outside a turn, a request works for the people its last turn did.
	if r := ask("read", []string{"@u-alice"}, nil); r.Code == 0 {
		t.Fatalf("after bob's turn: %+v", r)
	}
	nodes.mu.Lock()
	nodes.hold = false
	nodes.mu.Unlock()
	call(t, h, b, "post", map[string]any{"cid": "general", "text": "@shared and now for me"})
	eventually(t, "the owner's second turn", func() bool { return strings.Contains(nodes.lastPrompt(), "and now for me") })
	eventually(t, "shared idle again", func() bool {
		fl, _ := tc.lockFleet(context.Background(), fleetID)
		defer fl.mu.Unlock()
		return fl.turns[s.shared] == nil
	})
	if r := ask("read", []string{"@u-alice"}, nil); r.Code != 0 || !strings.Contains(r.Text, "kumquat") {
		t.Fatalf("the owner's view: %+v", r)
	}
}

// A shared agent's folder is not the whole disk, a home folder, or a folder
// that holds home folders (also through ..), and nothing is written there
// before the check.
func TestASharedAgentCannotWorkInAHomeFolderOrItsParent(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, b, _ := teamHost(t, tc)
	for _, folder := range []string{"/Users", "/Users/", "/home", "/home/", "/root", "/var/root", "/home/me/..", "~/..", "/", "/home/me", "~"} {
		nodes.mu.Lock()
		nodes.setups = nil
		nodes.mu.Unlock()
		e := refused(t, h, b, "agent_create", map[string]any{"spec": map[string]any{"name": "Spy", "node_id": "n_mac", "runtime": "claude",
			"folder": folder, "access": "everyone"}})
		if !strings.Contains(e, "A shared agent cannot work in") {
			t.Fatalf("%s: %s", folder, e)
		}
		nodes.mu.Lock()
		for _, args := range nodes.setups {
			if args["check_only"] != true {
				t.Fatalf("%s: written before the check: %v", folder, args)
			}
		}
		nodes.mu.Unlock()
	}
	own := makeAgent(t, h, b, "Own", "n_mac", "claude", nil)
	for _, folder := range []string{"/home", "/home/me/../", "/Users"} {
		if e := refused(t, h, b, "agent_update", map[string]any{"mid": own, "access": "everyone", "folder": folder}); !strings.Contains(e, "A shared agent cannot work in") {
			t.Fatalf("%s: %s", folder, e)
		}
	}
	if call(t, h, b, "agent_update", map[string]any{"mid": own, "access": "everyone"})["agent"].(map[string]any)["access"] != "everyone" {
		t.Fatal("a folder of its own was refused")
	}
	for folder, want := range map[string]string{"/srv/agents/x": "", "/Users/x/PantheonAgents/w": "", "/Users": "home folders",
		`C:\Users`: "home folders", `C:\Users\me`: "a home folder", "/opt": "", "/home/me": "a home folder"} {
		if got := sharedFolderRefusal(folder, "/home/me", false, false); !strings.Contains(got, want) || want == "" && got != "" {
			t.Errorf("%s: %q", folder, got)
		}
	}
	if got := sharedFolderRefusal("/data", "/data/people/me", false, false); !strings.Contains(got, "holds a home folder") {
		t.Errorf("a parent of the home folder: %q", got)
	}
}

// A person's name or handle cannot read like another member's (the owner's
// above all): case, spacing, punctuation, accents and look-alike letters
// ignored.
func TestANameCannotMimicAnotherMember(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	call(t, h, b, "me_update", map[string]any{"name": "Weize", "handle": "weize"})
	for _, name := range []string{"Weize", "weize", "W e i z e", "We.ize", "Wéize", "WEIZE!", "Weize1", "Weizee", "Wei-ze", "Shared"} {
		if e := refusedAs(t, h, b, alice, "me_update", map[string]any{"name": name}); !strings.Contains(e, "too close to") {
			t.Fatalf("%q: %s", name, e)
		}
	}
	for _, handle := range []string{"weize_", "we-ize", "u-bob", "w3ize"} {
		if e := refusedAs(t, h, b, alice, "me_update", map[string]any{"handle": handle}); !strings.Contains(e, "too close to") && !strings.Contains(e, "taken") {
			t.Fatalf("%q: %s", handle, e)
		}
	}
	got := callAs(t, h, b, alice, "me_update", map[string]any{"name": "Alice Liddell", "handle": "alice"})["member"].(map[string]any)
	if got["name"] != "Alice Liddell" || got["handle"] != "alice" {
		t.Fatalf("%v", got)
	}
	// Someone joining with a look-alike account name goes by their handle.
	fake := person("u-fake", "Weize", "f_fa4e000000000000", "member")
	if me := memberByName(callAs(t, h, b, fake, "space_get", map[string]any{}), "u-fake"); me == nil {
		t.Fatal("a look-alike account name was kept")
	}
}

// An admin starts, stops and stops a turn of agents they may use, and is
// answered without the agent's folder, computer or turns; Retry and a
// session reset are the owner's.
func TestAnAdminsAgentControlIsRedactedAndResetIsTheOwners(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	if e := refusedAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.shared, "action": "reset_session"}); !strings.Contains(e, "Only the space owner can reset an agent's session") {
		t.Fatal(e)
	}
	if e := refusedAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.shared, "action": "retry"}); !strings.Contains(e, "Only the space owner can retry") {
		t.Fatal(e)
	}
	for _, action := range []string{"stop", "start"} {
		out := callAs(t, h, b, carol, "agent_control", map[string]any{"mid": s.shared, "action": action})
		a, _ := out["agent"].(map[string]any)
		if a == nil || out["status"] == nil {
			t.Fatalf("%s: %v", action, out)
		}
		for _, k := range []string{"folder", "node_id", "turns", "turn_spans", "session_conversation_id"} {
			if a[k] != nil {
				t.Fatalf("%s: %s reaches an admin: %v", action, k, a)
			}
		}
	}
	if out := call(t, h, b, "agent_control", map[string]any{"mid": s.shared, "action": "reset_session"}); out["agent"].(map[string]any)["folder"] == nil {
		t.Fatalf("the owner's answer: %v", out)
	}
}

// An avatar is an uploaded image or none (drawn by the page); a stored
// address of anything else never reaches a browser.
func TestAvatarsAreImages(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	for _, bad := range []string{"javascript:alert(1)", "https://tracker.example/p.gif", "data:text/html;base64,PGI+", "data:image/svg+xml;base64,PHN2Zz4="} {
		if e := refusedAs(t, h, b, alice, "me_update", map[string]any{"avatar": bad}); !strings.Contains(e, "uploaded image") {
			t.Fatalf("%q: %s", bad, e)
		}
		if e := refused(t, h, b, "agent_update", map[string]any{"mid": s.shared, "avatar": bad}); !strings.Contains(e, "uploaded image") {
			t.Fatalf("agent %q: %s", bad, e)
		}
	}
	png := "data:image/png;base64,iVBORw0KGgo="
	if got := callAs(t, h, b, alice, "me_update", map[string]any{"avatar": png})["member"].(map[string]any)["avatar"]; got != png {
		t.Fatalf("%v", got)
	}
	callAs(t, h, b, alice, "me_update", map[string]any{"avatar": ""})
	// One stored before this check is left out.
	fl, _ := s.tc.lockFleet(context.Background(), fleetID)
	next := *fl.members[personMID(bob.UserID)]
	next.Avatar = "https://tracker.example/p.gif"
	fl.members[next.MID] = &next
	fl.mu.Unlock()
	if m := memberByName(callAs(t, h, b, alice, "space_get", map[string]any{}), "Bob"); m["avatar"] != nil {
		t.Fatalf("%v", m)
	}
}

// People other than the owner never get the owner's account name (earlier
// handles), the computer's network name, usage counts, or the owner's fix of
// an agent's failure.
func TestMembersSeeNoAccountComputerUsageOrFixes(t *testing.T) {
	s := newSharedSpace(t)
	h, b, nodes := s.h, s.b, s.nodes
	call(t, h, b, "me_update", map[string]any{"handle": "weize"})
	space := callAs(t, h, b, alice, "space_get", map[string]any{})
	if space["usage"] != nil {
		t.Fatal("usage reaches a member")
	}
	if call(t, h, b, "space_get", map[string]any{})["usage"] == nil {
		t.Fatal("the owner lost usage")
	}
	if owner := memberByName(space, "Owner"); owner["prev_handles"] != nil {
		t.Fatalf("the owner's earlier handles reach a member: %v", owner)
	}
	writer := memberByName(space, "Writer")
	if writer["node_name"] != "Owner's computer" || memberByName(space, "Shared")["node_name"] != "Owner's Workspace" {
		t.Fatalf("%v / %v", writer["node_name"], memberByName(space, "Shared")["node_name"])
	}
	st := space["agent_status"].(map[string]any)[s.writer].(map[string]any)
	if st["computer"] != "Owner's computer" {
		t.Fatalf("%v", st)
	}
	// A failure line: the owner reads why and the fix; Alice that it could
	// not answer.
	nodes.mu.Lock()
	nodes.reply = func(string) (string, string) { return "", "Claude Code is not signed in. Run claude and log in." }
	nodes.mu.Unlock()
	callAs(t, h, b, alice, "post", map[string]any{"cid": "general", "text": "@shared hi"})
	eventually(t, "the failure line", func() bool {
		line := lastFrom(msgs(t, h, b, "general", ""), "system")
		return line != nil && line["action"] == "retry"
	})
	owner := lastFrom(msgs(t, h, b, "general", ""), "system")
	if !strings.Contains(owner["text"].(string), "Sign in") {
		t.Fatalf("the owner's line: %v", owner)
	}
	page := callAs(t, h, b, alice, "messages", map[string]any{"cid": "general"})["messages"].([]any)
	line := page[len(page)-1].(map[string]any)
	if line["text"] != "Shared couldn't answer right now." || line["action"] != nil {
		t.Fatalf("alice's line: %v", line)
	}
	st = callAs(t, h, b, alice, "space_get", map[string]any{})["agent_status"].(map[string]any)[s.shared].(map[string]any)
	if st["state"] != "attention" || st["fix"] != nil || st["reason"] != "Shared couldn't answer right now" {
		t.Fatalf("alice's status: %v", st)
	}
}

// Join, leave and remove lines name the person by id, so pages show their
// handle now.
func TestJoinAndLeaveLinesNameThePerson(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	if _, err := tc.MemberChange(context.Background(), fleetID, bob.UserID, "remove", ""); err != nil {
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)
	seen := map[string]string{}
	for _, m := range msgs(t, h, b, "general", "") {
		if m["kind"] == "system" && m["action"] != nil {
			seen[m["action"].(string)] = m["about"].(string)
		}
	}
	if seen["joined"] != personMID(carol.UserID) && seen["joined"] != personMID(alice.UserID) || seen["removed"] != personMID(bob.UserID) {
		t.Fatalf("%v", seen)
	}
}
