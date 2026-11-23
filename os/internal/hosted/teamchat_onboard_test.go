package hosted

// A person's first visit to a space (docs/apps/team-chat.md, "People: first
// visit, profile, removal"): an account whose name is a role word joins as
// @person; their page asks them for a name first, checked by a dry run, and
// remembers on the member record that they confirmed it.

import (
	"context"
	"strings"
	"testing"
)

func TestAPersonConfirmsTheirNameOnce(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	adm := person("u-admin", "admin", "f_adm0000000000000", "member")
	adm.Username = "admin"
	space := callAs(t, h, b, adm, "space_get", map[string]any{})
	me := space["me"].(string)
	var rec map[string]any
	for _, m := range space["members"].([]any) {
		if m.(map[string]any)["mid"] == me {
			rec = m.(map[string]any)
		}
	}
	if rec == nil || rec["handle"] != "person" || rec["name"] != "person" {
		t.Fatalf("a reserved account name joins as person: %v", rec)
	}
	if _, ok := rec["named_at"]; ok {
		t.Fatalf("not confirmed yet: %v", rec)
	}
	// A dry run gives the server's reasons and saves nothing.
	if e := refusedAs(t, h, b, adm, "me_update", map[string]any{"name": "Admin", "dry_run": true}); !strings.Contains(e, "role in the space") {
		t.Fatalf("reserved name: %s", e)
	}
	if e := refusedAs(t, h, b, adm, "me_update", map[string]any{"handle": "admin", "dry_run": true}); !strings.Contains(e, "reserved") {
		t.Fatalf("reserved handle: %s", e)
	}
	if e := refusedAs(t, h, b, adm, "me_update", map[string]any{"name": "Alice", "dry_run": true}); !strings.Contains(e, "too close") {
		t.Fatalf("another member's name: %s", e)
	}
	if r := callAs(t, h, b, adm, "me_update", map[string]any{"name": "Dana Admiral", "handle": "dana", "dry_run": true}); r["ok"] != true {
		t.Fatalf("dry run: %v", r)
	}
	if got := memberByName(callAs(t, h, b, adm, "space_get", map[string]any{}), "person"); got == nil {
		t.Fatal("a dry run changed the record")
	}
	r := callAs(t, h, b, adm, "me_update", map[string]any{"name": "Dana", "handle": "dana", "confirm": true})
	m := r["member"].(map[string]any)
	if m["name"] != "Dana" || m["handle"] != "dana" || m["named_at"] == nil || m["named_at"] == "" {
		t.Fatalf("confirmed: %v", m)
	}
	// Every page reads it from the record (another device, a reload).
	if got := memberByName(callAs(t, h, b, adm, "space_get", map[string]any{}), "Dana"); got == nil || got["named_at"] == nil {
		t.Fatalf("named_at kept: %v", got)
	}
	// The owner is never asked: confirm does nothing for them.
	o := call(t, h, b, "me_update", map[string]any{"name": "Olivia", "confirm": true})["member"].(map[string]any)
	if _, ok := o["named_at"]; ok {
		t.Fatalf("owner: %v", o)
	}
	// The joined line names them by their handle now (about = their id).
	found := false
	for _, raw := range call(t, h, b, "messages", map[string]any{"cid": "general", "limit": 50})["messages"].([]any) {
		msg := raw.(map[string]any)
		if msg["about"] == me && msg["action"] == "joined" {
			found = true
		}
	}
	if !found {
		t.Fatal("no joined line about them")
	}
	// Removed: their next call is refused, and #general says so.
	if _, err := s.tc.MemberChange(context.Background(), fleetID, adm.UserID, "remove", "", ""); err != nil {
		t.Fatal(err)
	}
	if e := refusedAs(t, h, b, adm, "space_get", map[string]any{}); !strings.Contains(e, "no longer in this space") {
		t.Fatalf("removed: %s", e)
	}
	said := false
	for _, raw := range call(t, h, b, "messages", map[string]any{"cid": "general", "limit": 50})["messages"].([]any) {
		if strings.Contains(raw.(map[string]any)["text"].(string), "@dana was removed from the space.") {
			said = true
		}
	}
	if !said {
		t.Fatal("no removed line")
	}
}

// Only an agent's owner messages it directly unless they let everyone who
// may use it: a DM is refused to open and, once the owner takes it back, to
// write in (it stays readable); @mentions in channels go by access alone.
func TestDirectMessagesToAnAgentAreItsOwnersToAllow(t *testing.T) {
	s := newSharedSpace(t)
	h, b := s.h, s.b
	if e := refusedAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{s.listed}}); !strings.Contains(e, "can message Listed directly") {
		t.Fatalf("default: %s", e)
	}
	if e := refusedAs(t, h, b, bob, "conv_create", map[string]any{"kind": "dm", "members": []string{s.listed}}); !strings.Contains(e, "listed people only") {
		t.Fatalf("not listed: %s", e)
	}
	call(t, h, b, "agent_update", map[string]any{"mid": s.listed, "dms": "everyone"})
	dm := callAs(t, h, b, alice, "conv_create", map[string]any{"kind": "dm", "members": []string{s.listed}})["conversation"].(map[string]any)["cid"].(string)
	callAs(t, h, b, alice, "post", map[string]any{"cid": dm, "text": "hello Listed"})
	if e := refusedAs(t, h, b, bob, "conv_create", map[string]any{"kind": "dm", "members": []string{s.listed}}); !strings.Contains(e, "listed people only") {
		t.Fatalf("everyone still means people who may use it: %s", e)
	}
	call(t, h, b, "agent_update", map[string]any{"mid": s.listed, "dms": "only_me"})
	if e := refusedAs(t, h, b, alice, "post", map[string]any{"cid": dm, "text": "still there?"}); !strings.Contains(e, "can message Listed directly") {
		t.Fatalf("taken back: %s", e)
	}
	if msgs := callAs(t, h, b, alice, "messages", map[string]any{"cid": dm})["messages"].([]any); len(msgs) == 0 {
		t.Fatal("the old DM stays readable")
	}
	callAs(t, h, b, alice, "conv_join", map[string]any{"cid": s.team})
	call(t, h, b, "conv_join", map[string]any{"cid": s.team, "mid": s.listed})
	r := callAs(t, h, b, alice, "post", map[string]any{"cid": s.team, "text": "@listed a question"})
	if r["not_woken"] != nil {
		t.Fatalf("a mention goes by access: %v", r["not_woken"])
	}
	// Its owner always may.
	call(t, h, b, "conv_create", map[string]any{"kind": "dm", "members": []string{s.listed}})
}
