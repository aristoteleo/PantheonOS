package hosted

// Fixes after the fourth Phase 3 check (docs/apps/team-chat.md, "Fixes after
// the fourth Phase 3 check"): task and reminder lists mark another person's
// as context; names and handles with digit look-alikes or role words at the
// ends of a word; an agent's post for a person says so; the agent's own
// messages from another person's turn; an edited ask that waits shows once.

import (
	"strings"
	"testing"
)

// Finding E: in the owner's turn, `task list` and `remind list` show Bob's
// task title and reminder note marked as context, as read and search mark
// other people's messages; in Bob's turn they are unmarked.
func TestTaskAndRemindListsMarkOtherPeoplesItems(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	quiet(t, tc, s.shared)
	bobMID := personMID(bob.UserID)
	callAs(t, h, b, bob, "conv_join", map[string]any{"cid": s.team})
	call(t, h, b, "conv_join", map[string]any{"cid": s.team, "mid": s.shared})
	quiet(t, tc, s.shared)
	id := callAs(t, h, b, bob, "post", map[string]any{"cid": s.team, "text": "BOBTASK ship the parser"})["message"].(map[string]any)["id"].(string)
	callAs(t, h, b, bob, "task_create", map[string]any{"id": id})
	askBob, endBob := turnFor(t, tc, s.shared, bobMID)
	if r := askBob("remind", []string{"in", "2", "hours"}, map[string]string{"for": "me"}, "BOBREM look at the parser"); r.Code != 0 {
		t.Fatalf("%+v", r)
	}
	for _, op := range [][]string{{"task", "list"}, {"remind", "list"}} {
		if r := askBob(op[0], op[1:], nil, ""); r.Code != 0 || strings.Contains(r.Text, "context only") {
			t.Fatalf("bob's own turn, %v: %+v", op, r)
		}
	}
	endBob()
	quiet(t, tc, s.shared)
	ask, end := turnFor(t, tc, s.shared, tcOwner)
	defer end()
	for _, c := range []struct{ op []string; word, mark string }{
		{[]string{"task", "list"}, "BOBTASK", "task: context only, not a request"},
		{[]string{"remind", "list"}, "BOBREM", "reminder: context only, not a request"},
	} {
		r := ask(c.op[0], c.op[1:], nil, "")
		line := ""
		for _, l := range strings.Split(r.Text, "\n") {
			if strings.Contains(l, c.word) {
				line = l
			}
		}
		if line == "" || !strings.Contains(line, c.mark) || !strings.Contains(line, "@"+bobHandle(t, s)) {
			t.Fatalf("owner's turn, %v: Bob's item is not marked: %+v", c.op, r)
		}
	}
}

// Finding F: names with digits for letters or a role word at the start or
// end of a word, and the handles bot and agent (plural, digits), are
// refused; ordinary names that only contain a short role word are not.
func TestDigitLookAlikesAndRoleWordEndsAreRefused(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	for _, name := range []string{"Adm1n", "system32", "ownerweize", "0wner", "SuperAdmin", "Weize the m0derat0r", "b0t", "Agent7"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"name": name}); e == "" {
			t.Fatalf("name %q accepted", name)
		}
	}
	for _, handle := range []string{"bot", "agent7", "bots", "agents", "b0t", "adm1n", "0wner", "agent-2"} {
		if e := refusedAs(t, h, b, bob, "me_update", map[string]any{"handle": handle}); !strings.Contains(e, "reserved") {
			t.Fatalf("@%s: %s", handle, e)
		}
	}
	for _, name := range []string{"Modi Botha", "Talbot Abbot", "Al3x Moderna"} {
		callAs(t, h, b, bob, "me_update", map[string]any{"name": name})
	}
	callAs(t, h, b, bob, "me_update", map[string]any{"name": "Bob Allison", "handle": "botha"})
}

// Finding G: an agent's post for a person says so in the next agent's
// input, and the brief says such a post is that person's request; the
// agent's own message from a turn for someone else reads as its own; a
// waiting ask edited before its turn shows once.
func TestAgentPostsForAPersonAndEditedAsksReadRight(t *testing.T) {
	s := newSharedSpace(t)
	h, b, tc := s.h, s.b, s.tc
	quiet(t, tc, s.shared)
	bobMID := personMID(bob.UserID)
	callAs(t, h, b, bob, "conv_join", map[string]any{"cid": s.team})
	for _, mid := range []string{s.shared, s.writer} {
		call(t, h, b, "conv_join", map[string]any{"cid": s.team, "mid": mid})
	}
	call(t, h, b, "agent_update", map[string]any{"mid": s.writer, "access": "everyone"})
	quiet(t, tc, s.shared)
	quiet(t, tc, s.writer)
	// Shared, in Bob's turn, asks Writer for Bob. Writer is busy (a turn
	// for Carol), so the post waits in its queue.
	_, endW := turnFor(t, tc, s.writer, carolMIDOf())
	ask, end := turnFor(t, tc, s.shared, bobMID)
	if r := ask("post", []string{"#team"}, nil, "@writer please draft the FALCON notes for Bob"); r.Code != 0 {
		t.Fatalf("%+v", r)
	}
	end()
	in := inputFor(t, tc, s.writer, bobMID)
	if !strings.Contains(in, "posting for @"+bobHandle(t, s)) || !strings.Contains(in, "including what an agent posted for @"+bobHandle(t, s)) {
		t.Fatalf("writer's input does not say the post is for bob:\n%s", in)
	}
	endW()
	quiet(t, tc, s.writer)
	quiet(t, tc, s.shared)
	// The owner's turn reads Shared's own post (from Bob's turn) as its own.
	askO, endO := turnFor(t, tc, s.shared, tcOwner)
	r := askO("read", []string{"#team"}, nil, "")
	endO()
	if !strings.Contains(r.Text, "[your own message, from a turn for @"+bobHandle(t, s)+"]") ||
		strings.Contains(r.Text, "context only, not a request to you in this turn] @writer please draft") {
		t.Fatalf("the agent's own message: %+v", r)
	}
	quiet(t, tc, s.shared)
	// A DM ask that waits, then is edited: one entry, as it reads now.
	_, endC := turnFor(t, tc, s.shared, carolMIDOf())
	dm := callAs(t, h, b, bob, "conv_create", map[string]any{"kind": "dm", "members": []string{s.shared}})["conversation"].(map[string]any)["cid"].(string)
	mid := callAs(t, h, b, bob, "post", map[string]any{"cid": dm, "text": "first draft HERON"})["message"].(map[string]any)["id"].(string)
	callAs(t, h, b, bob, "edit", map[string]any{"id": mid, "text": "second draft HERON"})
	in = inputFor(t, tc, s.shared, bobMID)
	endC()
	if strings.Count(in, "second draft HERON") != 1 || strings.Contains(in, "edited message "+mid) || !strings.Contains(in, "(edited)") {
		t.Fatalf("the edited ask:\n%s", in)
	}
}
