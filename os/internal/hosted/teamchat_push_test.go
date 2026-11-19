package hosted

import (
	"strings"
	"testing"
)

// Which messages reach the owner's devices: DMs, mentions of the owner and
// replies in threads the owner follows; everything where the bell says so;
// nothing where it is muted, and never the owner's own or a system line.
func TestWhichMessagesNotifyTheOwnersDevices(t *testing.T) {
	fl := &tcFleet{fid: "f_test", notify: map[string]string{}, follows: map[string]bool{}, members: map[string]*tcMember{
		tcOwner:  {MID: tcOwner, Kind: "person", Handle: "ada", Name: "Ada"},
		"writer": {MID: "writer", Kind: "agent", Handle: "writer", Name: "Writer"},
	}}
	general := &tcConv{CID: "general", Kind: "channel", Name: "general", Members: []string{tcOwner, "writer"}}
	dm := &tcConv{CID: "dm-writer-owner", Kind: "dm", Members: []string{tcOwner, "writer"}}
	other := &tcConv{CID: "agents", Kind: "channel", Name: "agents", Members: []string{"writer"}}
	m := newConvMsgs()
	m.byID["root1"] = &tcMsg{ID: "root1", CID: "general", From: tcOwner, Kind: "post", Text: "Draft the notes?"}
	m.byID["root2"] = &tcMsg{ID: "root2", CID: "general", From: "writer", Kind: "post", Text: "Unrelated"}
	post := func(c *tcConv, from, text, root string, mentions ...string) (TeamPush, bool) {
		return pushFor(fl, c, m, &tcEvent{ID: "m1", CID: c.CID, From: from, Kind: "post", Text: text, Root: root, Mentions: mentions})
	}

	if n, ok := post(dm, "writer", "The notes are ready.", ""); !ok || n.Title != "Writer" || n.Body != "The notes are ready." ||
		n.URL != "/app/team-chat?conv=dm-writer-owner" || n.Tag != "team-chat.dm-writer-owner" {
		t.Fatalf("a DM: %+v %v", n, ok)
	}
	if n, ok := post(general, "writer", "@ada   done,\nsee the file", "", tcOwner); !ok || n.Title != "Writer in #general" || n.Body != "@ada done, see the file" {
		t.Fatalf("a mention: %+v %v", n, ok)
	}
	if n, ok := post(general, "writer", "Here they are.", "root1"); !ok || n.URL != "/app/team-chat?conv=general&thread=root1" {
		t.Fatalf("a reply in the owner's thread: %+v %v", n, ok)
	}
	for name, got := range map[string]bool{
		"a channel message that names nobody": second(post(general, "writer", "Working on it.", "")),
		"a reply in a thread not followed":    second(post(general, "writer", "More.", "root2")),
		"the owner's own message":             second(post(dm, tcOwner, "Thanks", "")),
		"a conversation the owner is not in":  second(post(other, "writer", "@ada hello", "", tcOwner)),
		"a system line":                       second(pushFor(fl, dm, m, &tcEvent{ID: "s1", CID: dm.CID, From: "system", Kind: "system", Text: "Writer joined"})),
	} {
		if got {
			t.Errorf("%s notified", name)
		}
	}
	// The bell: everything in a channel, nothing in a muted DM.
	fl.notify[tcOwner+".general"] = "all"
	fl.notify[tcOwner+"."+dm.CID] = "mute"
	if _, ok := post(general, "writer", "Working on it.", ""); !ok {
		t.Error("a channel set to everything did not notify")
	}
	if _, ok := post(dm, "writer", "Ready.", ""); ok {
		t.Error("a muted DM notified")
	}
	// A long message is cut on a character, and a file alone is named.
	fl.notify[tcOwner+"."+dm.CID] = "all"
	if n, _ := post(dm, "writer", strings.Repeat("字", 200), ""); !strings.HasSuffix(n.Body, "…") || len(n.Body) > tcPushBody+3 || !strings.HasPrefix(n.Body, "字") {
		t.Errorf("a long body: %d bytes", len(n.Body))
	}
	if n, _ := pushFor(fl, dm, m, &tcEvent{ID: "f1", CID: dm.CID, From: "writer", Kind: "post", Files: []tcFileRef{{ID: "x", Name: "notes.md"}}}); n.Body != "Sent notes.md" {
		t.Errorf("a file alone: %q", n.Body)
	}
}

func second(_ TeamPush, ok bool) bool { return ok }
