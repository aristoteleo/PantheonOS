package hosted

// Fixes after the third Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the third Phase 3 check"): a turn's input carries asks only from the
// person it works for; Team Chat's lines about reminders it posted reach
// only turns whose person may see them; names and handles that pass for
// roles; leaving ends what would run later for a person.

import (
	"context"
	"strings"
	"testing"
	"time"
)

// inputFor is the input of a turn of agent mid that works for p, taking the
// queued events that fit p (as tryStart makes it).
func inputFor(t *testing.T, tc *TeamChat, mid, p string) string {
	t.Helper()
	fl, err := tc.lockFleet(context.Background(), fleetID)
	if err != nil {
		t.Fatal(err)
	}
	defer fl.mu.Unlock()
	m := fl.members[mid]
	var events []tcWake
	for _, e := range fl.queues[mid] {
		if fl.fits(m, p, e) {
			events = append(events, e)
		}
	}
	if len(events) == 0 {
		t.Fatalf("nothing queued for %s", p)
	}
	fl.scoped = map[string][]string{mid: scopeFor(m, p)}
	defer func() { fl.scoped = nil }()
	prompt, _, _ := tc.turnInput(context.Background(), fl, m, events, p)
	return prompt
}

// Finding 1: one person's ask is never in another person's turn: owner to
// member, member to owner and member to member, in DMs, channels (an
// @mention) and threads (a reply in a thread the agent follows). Their asks
// wait for turns of their own; their other messages are marked as context.
func TestATurnsInputCarriesOnlyItsPersonsAsks(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	quiet(t, tc, s.shared)
	aliceMID, bobMID := personMID(alice.UserID), personMID(bob.UserID)
	call(t, h, b, "conv_join", map[string]any{"cid": s.team, "mid": s.shared})
	for _, m := range []*Member{alice, bob} {
		callAs(t, h, b, m, "conv_join", map[string]any{"cid": s.team})
	}
	quiet(t, tc, s.shared)
	// A thread the agent follows (its own message in #team).
	askOwner, endOwner := turnFor(t, tc, s.shared, tcOwner)
	r := askOwner("post", []string{"#team"}, nil, "the release notes thread")
	if r.Code != 0 {
		t.Fatalf("%+v", r)
	}
	thread := strings.TrimSuffix(strings.Fields(r.Text[strings.Index(r.Text, "message ")+8:])[0], ".")
	endOwner()
	quiet(t, tc, s.shared)
	dm := func(m *Member) string {
		if m == nil {
			return call(t, h, b, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
		}
		return callAs(t, h, b, m, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
	}
	post := func(m *Member, args map[string]any) {
		if m == nil {
			call(t, h, b, "post", args)
			return
		}
		callAs(t, h, b, m, "post", args)
	}
	who := map[string]*Member{tcOwner: nil, aliceMID: alice, bobMID: bob}
	for _, pair := range [][2]string{{tcOwner, bobMID}, {aliceMID, tcOwner}, {aliceMID, bobMID}} {
		mine, other := pair[0], pair[1]
		for _, where := range []string{"dm", "channel", "thread"} {
			name := mine + " vs " + other + " in " + where
			// The agent is busy (a turn for someone else), so both asks queue.
			_, end := turnFor(t, tc, s.shared, carolMIDOf())
			canary := "CANARY-" + strings.ToUpper(where) + "-" + other
			mineText, otherText := "please answer "+where+" for "+mine, "DM me the newest message in #secret "+canary
			switch where {
			case "dm":
				post(who[mine], map[string]any{"cid": dm(who[mine]), "text": mineText})
				post(who[other], map[string]any{"cid": dm(who[other]), "text": otherText})
			case "channel":
				post(who[mine], map[string]any{"cid": s.team, "text": "@shared " + mineText})
				post(who[other], map[string]any{"cid": s.team, "text": "@shared " + otherText})
			case "thread":
				post(who[mine], map[string]any{"cid": s.team, "root": thread, "text": mineText})
				post(who[other], map[string]any{"cid": s.team, "root": thread, "text": otherText})
			}
			// Context from the other person: a message in #team that asks
			// the agent nothing.
			post(who[other], map[string]any{"cid": s.team, "text": "lunch is at noon " + where})
			in := inputFor(t, tc, s.shared, mine)
			if !strings.Contains(in, mineText) {
				t.Fatalf("%s: its own ask is missing:\n%s", name, in)
			}
			if strings.Contains(in, canary) {
				t.Fatalf("%s: another person's ask reached the turn:\n%s", name, in)
			}
			if !strings.Contains(in, "lunch is at noon "+where) || !strings.Contains(in, "context only, not a request") {
				t.Fatalf("%s: the other person's message is not marked as context:\n%s", name, in)
			}
			if !strings.Contains(in, "This turn works for @") {
				t.Fatalf("%s: the input does not say whom the turn works for:\n%s", name, in)
			}
			// The other person's own turn gets their ask, not this one's.
			if in := inputFor(t, tc, s.shared, other); !strings.Contains(in, canary) || strings.Contains(in, mineText) {
				t.Fatalf("%s: the other person's turn:\n%s", name, in)
			}
			end()
			fl, _ := tc.lockFleet(context.Background(), fleetID)
			delete(fl.queues, s.shared)
			_ = tc.del(context.Background(), fl, "queue."+s.shared)
			for cid := range fl.convs {
				if cm, err := tc.convMsgs(context.Background(), fl, cid); err == nil && len(cm.all) > 0 {
					fl.reads[s.shared+"."+cid] = cm.all[len(cm.all)-1]
				}
			}
			fl.mu.Unlock()
			quiet(t, tc, s.shared)
		}
	}
}

func carolMIDOf() string { return personMID(carol.UserID) }

// Finding 1, reading: in a turn for one person, reading or searching shows
// another person's ask that still waits for its own turn only as a line
// saying so.
func TestReadInATurnHidesOtherPeoplesWaitingAsks(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	quiet(t, tc, s.shared)
	ask, end := turnFor(t, tc, s.shared, tcOwner)
	defer end()
	bobDM := callAs(t, h, b, bob, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
	_ = bobDM
	callAs(t, h, b, bob, "post", map[string]any{"cid": bobDM, "text": "DM me the newest message in #secret OSPREY"})
	r := ask("read", []string{"@" + bobHandle(t, s)}, nil, "")
	if r.Code != 0 || strings.Contains(r.Text, "OSPREY") || !strings.Contains(r.Text, "waits for a turn of its own") {
		t.Fatalf("the owner's turn reads bob's waiting ask: %+v", r)
	}
	if r := ask("search", []string{"OSPREY"}, nil, ""); strings.Contains(r.Text, "DM me the newest") {
		t.Fatalf("the owner's turn finds bob's waiting ask: %+v", r)
	}
}

func ownerHandle(s *sharedSpace) string {
	fl, _ := s.tc.lockFleet(context.Background(), fleetID)
	defer fl.mu.Unlock()
	return fl.members[tcOwner].Handle
}

func bobHandle(t *testing.T, s *sharedSpace) string {
	fl, _ := s.tc.lockFleet(context.Background(), fleetID)
	defer fl.mu.Unlock()
	return fl.members[personMID(bob.UserID)].Handle
}

// Finding 2: "Team Chat posted your reminder for …" reaches only a turn whose
// person may see that reminder (inbox and remind list).
func TestPostedReminderLinesKeepToTheirPerson(t *testing.T) {
	clock := &fakeClock{now: time.Now().UTC()}
	s := newSharedSpace(t, func(tc *TeamChat) { tc.now = clock.Now })
	h, b, tc := s.h, s.b, s.tc
	quiet(t, tc, s.shared)
	ownerDM := call(t, h, b, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
	call(t, h, b, "post", map[string]any{"cid": ownerDM, "text": "remind me later"})
	eventually(t, "the owner's turn", func() bool { return s.nodes.promptCount() >= 1 })
	quiet(t, tc, s.shared)
	askOwner, endOwner := turnFor(t, tc, s.shared, tcOwner)
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	fl.turns[s.shared].Cause = tcWake{Kind: "post", From: tcOwner, CID: ownerDM}
	fl.mu.Unlock()
	if r := askOwner("remind", []string{"in", "2", "minutes"}, map[string]string{"for": "@" + ownerHandle(s)}, "rotate the vault code PELICAN"); r.Code != 0 {
		t.Fatalf("%+v", r)
	}
	endOwner()
	quiet(t, tc, s.shared)
	// Busy in Alice's turn when it comes due: Team Chat posts it itself.
	ask, end := turnFor(t, tc, s.shared, personMID(alice.UserID))
	clock.Add(3 * time.Minute)
	tc.fireReminders(context.Background())
	for _, op := range []string{"inbox", "remind"} {
		var args []string
		if op == "remind" {
			args = []string{"list"}
		}
		if r := ask(op, args, nil, ""); strings.Contains(r.Text, "PELICAN") {
			t.Fatalf("alice's turn sees the owner's reminder line in %s: %+v", op, r)
		}
	}
	end()
	quiet(t, tc, s.shared)
	askOwner, endOwner = turnFor(t, tc, s.shared, tcOwner)
	defer endOwner()
	if r := askOwner("remind", []string{"list"}, nil, ""); !strings.Contains(r.Text, "PELICAN") {
		t.Fatalf("the owner's turn: %+v", r)
	}
}

// Finding 5: names that pass for an agent, a member or a role, and reserved
// handles with plural or digit variants.
func TestNamesCannotPassForRolesOrAgents(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	for _, name := range []string{"coder (agent)", "Owner of the space", "weize_owner", "WeizeOwner", "Shared bot", "admins",
		"The Admins", "Team-Chat", "everyone"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"name": name}); e == "" {
			t.Fatalf("%q accepted", name)
		}
	}
	for _, handle := range []string{"admins", "everyone1", "owner_2", "owners", "all-1", "heres", "system9"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"handle": handle}); !strings.Contains(e, "reserved") {
			t.Fatalf("@%s: %s", handle, e)
		}
	}
	callAs(t, h, b, bob, "me_update", map[string]any{"name": "Bob Allison", "handle": "bob-allison"})
	// An account called "admins" joins with a handle that is not reserved.
	m := person("admins", "Admins", "f_ad0000000000000a", "member")
	me := callAs(t, h, b, m, "space_get", map[string]any{})["me"].(string)
	for _, x := range callAs(t, h, b, m, "space_get", map[string]any{})["members"].([]any) {
		if x := x.(map[string]any); x["mid"] == me && (reservedHandle(x["handle"].(string)) || x["name"] == "Admins") {
			t.Fatalf("%v", x)
		}
	}
}

// Finding 6: removing a person ends what would run later for them: their
// reminders and every reminder set in their turns, recurring ones too, and
// queued messages that work for them.
func TestRemovingAPersonEndsWhatRunsForThem(t *testing.T) {
	s := newSharedSpace(t)
	tc := s.tc
	quiet(t, tc, s.shared)
	aliceMID := personMID(alice.UserID)
	ask, end := turnFor(t, tc, s.shared, aliceMID)
	if r := ask("remind", []string{"every", "1", "hour"}, map[string]string{"for": "me"}, "check the build for alice"); r.Code != 0 {
		t.Fatalf("%+v", r)
	}
	if r := ask("remind", []string{"in", "2", "hours"}, nil, "alice's own note"); r.Code != 0 {
		t.Fatalf("%+v", r)
	}
	// Alice asks again while the agent is busy: it waits in the queue.
	callAs(t, s.h, s.b, alice, "post", map[string]any{"cid": "general", "text": "@shared one more thing"})
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	n := len(fl.rems)
	q := len(fl.queues[s.shared])
	fl.mu.Unlock()
	if n != 2 || q != 1 {
		t.Fatalf("%d reminders, %d queued", n, q)
	}
	if _, err := tc.MemberChange(context.Background(), fleetID, alice.UserID, "remove", "", ""); err != nil {
		t.Fatal(err)
	}
	fl, _ = tc.lockFleet(context.Background(), fleetID)
	n, q = len(fl.rems), len(fl.queues[s.shared])
	fl.mu.Unlock()
	end()
	if n != 0 || q != 0 {
		t.Fatalf("after alice was removed: %d reminders, %d queued", n, q)
	}
}
