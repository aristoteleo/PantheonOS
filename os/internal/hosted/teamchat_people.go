package hosted

// People in a space (docs/apps/team-chat.md, "Phase 3 as built"): the owner
// and the people the Hub let in, each connected with a space grant that
// carries who they are. Who sees which conversation, who may use which
// agent, and the change events each person gets.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"
)

// TeamChatGrantID names a person's space grants on the App gateway, so the
// Hub and the controller drop them together (the Hub computes the same).
func TeamChatGrantID(space, userID string) string {
	sum := sha256.Sum256([]byte(space + "/" + userID))
	return "tcm-" + hex.EncodeToString(sum[:])[:32]
}

// personMID is the member id of a person who is not the owner.
func personMID(userID string) string {
	sum := sha256.Sum256([]byte("team-chat-person\n" + userID))
	return "p" + hex.EncodeToString(sum[:])[:12]
}

// tcCaller is who makes a call: the owner, or a person by a space grant.
type tcCaller struct {
	mid  string
	role string // owner | admin | member
}

func (c tcCaller) owner() bool { return c.role == "owner" }
func (c tcCaller) admin() bool { return c.role == "owner" || c.role == "admin" }

var errNotInSpace = &ArgumentError{"You are no longer in this space."}

// callerOf resolves a call's person: the owner's browser and the owner's
// Apps act as the owner; a space grant as its person, whose record is made or
// brought up to date (fl.mu is held).
func (t *TeamChat) callerOf(ctx context.Context, fl *tcFleet, m *Member) (tcCaller, error) {
	if m == nil {
		return tcCaller{mid: tcOwner, role: "owner"}, nil
	}
	if m.UserID == "" || !desktopFleetRE.MatchString(m.Fleet) || (m.Role != "admin" && m.Role != "member") {
		return tcCaller{}, errNotInSpace
	}
	mid := personMID(m.UserID)
	rec := fl.members[mid]
	switch {
	case rec == nil:
		handle := handleFrom(m.Username)
		if handle == "" || !tcHandleRE.MatchString(handle) {
			handle = "person"
		}
		base := handle
		for i := 2; t.handleFree(fl, handle, mid) != nil; i++ {
			suffix := fmt.Sprint(i)
			handle = strings.TrimRight(base[:min(len(base), 32-len(suffix))], "-_") + suffix
		}
		name := strings.TrimSpace(m.Name)
		if name == "" {
			name = m.Username
		}
		if r := []rune(name); len(r) > 64 {
			name = string(r[:64])
		}
		rec = &tcMember{MID: mid, Kind: "person", Handle: handle, Name: name, Created: t.stamp(), Role: m.Role,
			Fleet: m.Fleet, Joined: m.Joined}
		if err := t.welcome(ctx, fl, rec, "joined"); err != nil {
			return tcCaller{}, err
		}
	case rec.Removed != "":
		removed, _ := time.Parse(time.RFC3339Nano, rec.Removed)
		if m.Joined <= removed.Unix() {
			// A grant from before the removal.
			return tcCaller{}, errNotInSpace
		}
		next := *rec
		next.Removed, next.Role, next.Fleet, next.Joined = "", m.Role, m.Fleet, m.Joined
		if err := t.welcome(ctx, fl, &next, "joined"); err != nil {
			return tcCaller{}, err
		}
		rec = &next
	case rec.Role != m.Role || rec.Fleet != m.Fleet || rec.Joined != m.Joined:
		next := *rec
		next.Role, next.Fleet, next.Joined = m.Role, m.Fleet, m.Joined
		if err := t.put(ctx, fl, "member."+mid, &next); err != nil {
			return tcCaller{}, err
		}
		fl.members[mid] = &next
		t.emit(fl, map[string]any{"type": "member", "member": &next})
		rec = &next
	}
	return tcCaller{mid: mid, role: rec.Role}, nil
}

// welcome stores a person who joined (again), puts them in #general and
// says so there.
func (t *TeamChat) welcome(ctx context.Context, fl *tcFleet, rec *tcMember, verb string) error {
	if err := t.put(ctx, fl, "member."+rec.MID, rec); err != nil {
		return err
	}
	fl.members[rec.MID] = rec
	t.emit(fl, map[string]any{"type": "member", "member": rec})
	if g := fl.convs["general"]; g != nil && !g.has(rec.MID) {
		next := *g
		next.Members = append(append([]string(nil), g.Members...), rec.MID)
		if err := t.saveConv(ctx, fl, &next); err != nil {
			return err
		}
	}
	_, _ = t.system(ctx, fl, "general", "", "", "", "@"+rec.Handle+" "+verb+" the space.")
	return nil
}

// MemberChange is the Hub's change to a person of a space (through the
// controller, with their grants dropped at the same time): "remove" (by
// someone else), "leave" (by themselves) or "role" (admin | member).
func (t *TeamChat) MemberChange(ctx context.Context, fid, userID, action, role string) (bool, error) {
	if userID == "" {
		return false, &ArgumentError{"no person"}
	}
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return false, err
	}
	defer fl.mu.Unlock()
	mid := personMID(userID)
	rec := fl.members[mid]
	if rec == nil || rec.Removed != "" {
		return false, nil
	}
	switch action {
	case "role":
		if role != "admin" && role != "member" {
			return false, &ArgumentError{"role is admin or member"}
		}
		next := *rec
		next.Role = role
		if err := t.put(ctx, fl, "member."+mid, &next); err != nil {
			return false, err
		}
		fl.members[mid] = &next
		t.emit(fl, map[string]any{"type": "member", "member": &next})
		return true, nil
	case "remove", "leave":
	default:
		return false, &ArgumentError{"action is remove, leave or role"}
	}
	// They hear it first: once removed they get nothing more.
	t.emit(fl, map[string]any{"type": "removed", "_to": mid})
	now := t.stamp()
	next := *rec
	next.Removed, next.Role = now, ""
	if err := t.put(ctx, fl, "member."+mid, &next); err != nil {
		return false, err
	}
	fl.members[mid] = &next
	for _, cid := range sortedKeys(fl.convs) {
		c := fl.convs[cid]
		if c.Kind != "channel" || !c.has(mid) {
			continue
		}
		nc := *c
		nc.Members = nil
		for _, x := range c.Members {
			if x != mid {
				nc.Members = append(nc.Members, x)
			}
		}
		if err := t.saveConv(ctx, fl, &nc); err != nil {
			return false, err
		}
	}
	for _, r := range fl.rems {
		if r.For == mid {
			_ = t.cancelReminder(ctx, fl, r)
		}
	}
	t.emit(fl, map[string]any{"type": "member", "member": &next})
	text := "@" + rec.Handle + " was removed from the space."
	if action == "leave" {
		text = "@" + rec.Handle + " left the space."
	}
	_, _ = t.system(ctx, fl, "general", "", "", "", text)
	return true, nil
}

// ── who sees what ──────────────────────────────────────────────────────────

// ownerOf is the person an agent belongs to (every Phase 3 agent is the
// space owner's).
func ownerOf(a *tcMember) string {
	if a.CreatedBy != "" {
		return a.CreatedBy
	}
	return tcOwner
}

// person reports whether mid is a person still in the space.
func (fl *tcFleet) person(mid string) bool {
	m := fl.members[mid]
	return m != nil && !m.agent() && m.Removed == ""
}

// visible: whether mid may read a conversation. An agent: its conversations
// and public channels. A person: public channels, the conversations they
// are in, and the DMs of agents they own (a DM only of agents that are gone,
// its owner).
func (fl *tcFleet) visible(c *tcConv, mid string) bool {
	m := fl.members[mid]
	if m == nil {
		return false
	}
	if m.agent() {
		return c.has(mid) || c.Kind == "channel" && !c.Private
	}
	if m.Removed != "" {
		return false
	}
	return c.Kind == "channel" && !c.Private || fl.inConv(c, mid)
}

// inConv: the conversations whose news is a person's (unread, Activity,
// notifications): those they are in, and their agents' DMs.
func (fl *tcFleet) inConv(c *tcConv, mid string) bool {
	if c.has(mid) {
		return true
	}
	if c.Kind != "dm" {
		return false
	}
	people := false
	for _, x := range c.Members {
		a := fl.members[x]
		if a != nil && a.agent() && ownerOf(a) == mid {
			return true
		}
		if a != nil && !a.agent() {
			people = true
		}
	}
	// Agents' DM whose agents are all gone: their owner's.
	return !people && mid == tcOwner
}

// canUse: whether a person may DM, wake, add or assign an agent.
func (fl *tcFleet) canUse(personID string, a *tcMember) bool {
	if a == nil || !a.agent() {
		return false
	}
	if personID == ownerOf(a) {
		return true
	}
	if !fl.person(personID) {
		return false
	}
	switch a.Access {
	case "everyone":
		return true
	case "listed":
		return contains(a.AccessList, personID)
	}
	return false
}

// notUsable says in plain words why a person may not use an agent.
func (fl *tcFleet) notUsable(a *tcMember) string {
	owner := "its owner"
	if o := fl.members[ownerOf(a)]; o != nil {
		owner = "@" + o.Handle
	}
	if a.Access == "listed" {
		return fmt.Sprintf("%s is shared with listed people only; ask %s to add you.", a.Name, owner)
	}
	return fmt.Sprintf("Only %s can use %s.", owner, a.Name)
}

// usable is every agent a person may use.
func (fl *tcFleet) usable(personID string) []string {
	out := []string{}
	for _, mid := range sortedKeys(fl.members) {
		if a := fl.members[mid]; a.agent() && fl.canUse(personID, a) {
			out = append(out, mid)
		}
	}
	return out
}

// behalfOf is who an agent's turn works for: the people whose messages (or
// whose agents' messages, or reminders) are among its events.
func (fl *tcFleet) behalfOf(events []tcWake) []string {
	var out []string
	add := func(mid string) {
		if mid != "" && !contains(out, mid) {
			out = append(out, mid)
		}
	}
	for _, w := range events {
		if m := fl.members[w.From]; m != nil && !m.agent() {
			add(w.From)
		}
		if w.For != "" {
			add(w.For)
		}
		for _, b := range w.Behalf {
			add(b)
		}
	}
	return out
}

// actorsOf: whose access a message's wakes are checked against: a person's
// own message, an agent's message on behalf of its turn's people (its owner
// when none).
func (fl *tcFleet) actorsOf(e *tcEvent) []string {
	sender := fl.members[e.From]
	if sender == nil {
		return nil
	}
	if !sender.agent() {
		return []string{e.From}
	}
	if len(e.Behalf) > 0 {
		return e.Behalf
	}
	return []string{ownerOf(sender)}
}

// ── what each person is sent ───────────────────────────────────────────────

// memberView is a member as a person sees it: a person's own Fleet never;
// an agent's computer, folder and session only to the owner.
func (fl *tcFleet) memberView(m *tcMember, viewer string) *tcMember {
	if m == nil {
		return nil
	}
	v := *m
	v.Fleet = ""
	if m.MID == tcOwner {
		v.Role = "owner"
	}
	if m.agent() && viewer != tcOwner {
		v.Folder, v.NodeID, v.Session, v.Turns, v.Spans = "", "", "", nil, nil
	}
	return &v
}

// statusView leaves out where an agent works when the viewer does not see
// that conversation.
func (fl *tcFleet) statusView(status map[string]any, viewer string) map[string]any {
	cid, _ := status["cid"].(string)
	if cid == "" {
		return status
	}
	if c := fl.convs[cid]; c != nil && fl.visible(c, viewer) {
		return status
	}
	out := map[string]any{}
	for k, v := range status {
		if k != "cid" && k != "root" && k != "line" {
			out[k] = v
		}
	}
	return out
}

// viewers are the people who get change events: the owner and every person
// in the space.
func (fl *tcFleet) viewers() []string {
	out := []string{tcOwner}
	for _, mid := range sortedKeys(fl.members) {
		m := fl.members[mid]
		if mid != tcOwner && !m.agent() && m.Removed == "" && m.Fleet != "" {
			out = append(out, mid)
		}
	}
	return out
}

// subjectOf is where a person's events go: the owner's App namespace, or the
// person's own (so only their own browser credential follows it).
func (fl *tcFleet) subjectOf(viewer string) string {
	if viewer == tcOwner {
		return tcSubject(fl.fid)
	}
	m := fl.members[viewer]
	if m == nil || m.Fleet == "" {
		return ""
	}
	return fmt.Sprintf("fleet.%s.apps.team-chat.spaces.%s", m.Fleet, fl.fid)
}

// revOf is a person's event revision now.
func (fl *tcFleet) revOf(viewer string) uint64 {
	if fl.vseq == nil {
		fl.vseq = map[string]uint64{}
	}
	if fl.vseq[viewer] == 0 {
		// Revisions of this process start past any of an earlier one.
		fl.vseq[viewer] = uint64(time.Now().UnixMilli()) * 1000
	}
	return fl.vseq[viewer]
}

// eventFor is an event as one person gets it, or nil when it is not theirs.
func (fl *tcFleet) eventFor(event map[string]any, viewer string) map[string]any {
	if to, ok := event["_to"].(string); ok && to != viewer {
		return nil
	}
	sees := func(cid string) bool {
		c := fl.convs[cid]
		return c != nil && fl.visible(c, viewer)
	}
	out := make(map[string]any, len(event))
	for k, v := range event {
		if k != "_to" {
			out[k] = v
		}
	}
	switch event["type"] {
	case "message":
		msg, _ := event["message"].(*tcMsg)
		if msg == nil || !sees(msg.CID) {
			return nil
		}
	case "conv":
		if c, _ := event["conversation"].(*tcConv); c != nil && !fl.visible(c, viewer) {
			return nil
		}
	case "typing", "read":
		cid, _ := event["cid"].(string)
		if !sees(cid) {
			return nil
		}
		if event["type"] == "read" {
			if mid, _ := event["mid"].(string); mid != viewer && !fl.person(mid) && mid != tcOwner {
				return nil // agents' read marks are theirs
			}
		}
	case "task":
		if tk, _ := event["task"].(*tcTask); tk == nil || !sees(tk.CID) {
			return nil
		}
	case "member":
		if m, _ := event["member"].(*tcMember); m != nil {
			out["member"] = fl.memberView(m, viewer)
		}
	case "agent_status":
		if st, _ := event["status"].(map[string]any); st != nil {
			out["status"] = fl.statusView(st, viewer)
		}
	case "notify":
		if mid, _ := event["mid"].(string); mid != viewer {
			return nil
		}
	case "reminder":
		if viewer == tcOwner {
			break
		}
		r, _ := event["reminder"].(*tcRem)
		forMID, _ := event["for"].(string)
		if r != nil {
			forMID = r.For
		}
		if forMID != viewer {
			return nil
		}
		delete(out, "for")
	}
	return out
}

// emit announces a change to each person it concerns, each with their own
// revisions; fl.mu is held, so revisions are in order. A browser that sees a
// gap reads the space again.
func (t *TeamChat) emit(fl *tcFleet, event map[string]any) {
	fl.seq++
	if t.nc == nil {
		return
	}
	for _, viewer := range fl.viewers() {
		ev := fl.eventFor(event, viewer)
		subject := fl.subjectOf(viewer)
		if ev == nil || subject == "" {
			continue
		}
		fl.revOf(viewer)
		fl.vseq[viewer]++
		ev["rev"] = fl.vseq[viewer]
		raw, err := json.Marshal(ev)
		if err != nil {
			continue
		}
		if err := t.nc.Publish(subject, raw); err != nil {
			log.Printf("[team-chat] %s notify: %v", fl.fid, err)
		}
	}
}

// convGone tells the people who saw a conversation before a change and no
// longer do (fl.mu is held).
func (t *TeamChat) convGone(fl *tcFleet, before []string, c *tcConv) {
	for _, viewer := range before {
		if !fl.visible(c, viewer) {
			t.emit(fl, map[string]any{"type": "conv", "cid": c.CID, "gone": true, "_to": viewer})
		}
	}
}

// seers are the people who see a conversation now.
func (fl *tcFleet) seers(c *tcConv) []string {
	var out []string
	for _, v := range fl.viewers() {
		if fl.visible(c, v) {
			out = append(out, v)
		}
	}
	return out
}

// readsBy is other people's read marks in the conversations a person sees.
func (fl *tcFleet) readsBy(me string) map[string]map[string]string {
	out := map[string]map[string]string{}
	for key, id := range fl.reads {
		mid, cid, _ := strings.Cut(key, ".")
		if mid == me || !(fl.person(mid) || mid == tcOwner) {
			continue
		}
		c := fl.convs[cid]
		if c == nil || !fl.visible(c, me) || !c.has(mid) && !fl.inConv(c, mid) {
			continue
		}
		if out[cid] == nil {
			out[cid] = map[string]string{}
		}
		out[cid][mid] = id
	}
	return out
}
