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
	"regexp"
	"strings"
	"time"
	"unicode"

	"golang.org/x/text/unicode/norm"
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
		if reservedHandle(handle) {
			// No number makes a reserved word free.
			handle = "person"
		}
		base := handle
		for i := 2; t.handleFree(fl, handle, mid) != nil; i++ {
			suffix := fmt.Sprint(i)
			handle = strings.TrimRight(base[:min(len(base), 32-len(suffix))], "-_") + suffix
		}
		name := plainName(m.Name)
		if name == "" {
			name = plainName(m.Username)
		}
		if r := []rune(name); len(r) > 64 {
			name = string(r[:64])
		}
		if nameTrouble(name) != "" || reservedName(name) || fl.mimicked(mid, name) != nil {
			// A name that reads like another member's: they go by their
			// handle until they choose another.
			name = handle
		}
		rec = &tcMember{MID: mid, Kind: "person", Handle: handle, Name: name, Created: t.stamp(), Role: m.Role,
			Fleet: m.Fleet, Joined: m.Joined, Membership: m.Membership}
		if err := t.welcome(ctx, fl, rec, "joined"); err != nil {
			return tcCaller{}, err
		}
	case rec.Removed != "":
		if grantEnded(rec, m) {
			// A grant from before the removal.
			return tcCaller{}, errNotInSpace
		}
		next := *rec
		next.Removed, next.Role, next.Fleet, next.Joined, next.Membership = "", m.Role, m.Fleet, m.Joined, m.Membership
		if err := t.welcome(ctx, fl, &next, "joined"); err != nil {
			return tcCaller{}, err
		}
		rec = &next
	case rec.Role != m.Role || rec.Fleet != m.Fleet || rec.Joined != m.Joined || rec.Membership != m.Membership:
		next := *rec
		next.Role, next.Fleet, next.Joined, next.Membership = m.Role, m.Fleet, m.Joined, m.Membership
		if err := t.put(ctx, fl, "member."+mid, &next); err != nil {
			return tcCaller{}, err
		}
		fl.members[mid] = &next
		t.emit(fl, map[string]any{"type": "member", "member": &next})
		rec = &next
	}
	return tcCaller{mid: mid, role: rec.Role}, nil
}

// grantEnded: whether a grant of a removed person is from the membership
// that ended. With the Hub's membership ids it is the same id (so leaving
// and joining again within a second works, whatever the clocks say);
// without them, a grant from no later than the removal's second.
func grantEnded(rec *tcMember, m *Member) bool {
	if m.Membership != "" && rec.Ended != "" {
		return m.Membership == rec.Ended
	}
	removed, _ := time.Parse(time.RFC3339Nano, rec.Removed)
	return m.Joined <= removed.Unix()
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
	// about and action let pages name them by their handle now.
	_, _ = t.system(ctx, fl, "general", "", rec.MID, verb, "@"+rec.Handle+" "+verb+" the space.")
	return nil
}

// MemberChange is the Hub's change to a person of a space (through the
// controller, with their grants dropped at the same time): "remove" (by
// someone else), "leave" (by themselves) or "role" (admin | member).
//
// membership is the Hub's id of the membership a remove or leave ends
// ("" from an older Hub: the person's current one).
func (t *TeamChat) MemberChange(ctx context.Context, fid, userID, action, role, membership string) (bool, error) {
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
	next.Ended = firstNonBlank(membership, rec.Membership)
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
	t.endWorkFor(ctx, fl, mid)
	t.emit(fl, map[string]any{"type": "member", "member": &next})
	text, verb := "@"+rec.Handle+" was removed from the space.", "removed"
	if action == "leave" {
		text, verb = "@"+rec.Handle+" left the space.", "left"
	}
	_, _ = t.system(ctx, fl, "general", "", mid, verb, text)
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
// and public channels, and while it serves a turn for people other than its
// owner, only those they see too. A person: public channels, the
// conversations they are in, and the DMs of agents they own (a DM only of
// agents that are gone, its owner). The owner sees every channel of their
// space, private ones they are not in included.
func (fl *tcFleet) visible(c *tcConv, mid string) bool {
	m := fl.members[mid]
	if m == nil || c == nil {
		return false
	}
	if m.agent() {
		if !(c.has(mid) || c.Kind == "channel" && !c.Private) {
			return false
		}
		for _, p := range fl.scoped[mid] {
			if !fl.visible(c, p) {
				return false
			}
		}
		return true
	}
	if m.Removed != "" {
		return false
	}
	if mid == tcOwner && c.Kind == "channel" {
		return true
	}
	return c.Kind == "channel" && !c.Private || fl.inConv(c, mid)
}

// wakeFor is the one person an event works for: a person's own message,
// the person an agent's message (or a reminder) was made for, a person's
// reminder; "" when nobody (an agent's own reminder: its owner's).
func (fl *tcFleet) wakeFor(w tcWake) string {
	if w.Kind != "reminder" {
		if m := fl.members[w.From]; m != nil && !m.agent() {
			return w.From
		}
	}
	if len(w.Behalf) > 0 {
		return w.Behalf[0]
	}
	if w.Kind == "reminder" {
		return w.For
	}
	return ""
}

// worksFor is the person a turn of agent a that takes event w works for:
// the event's person, else the agent's owner.
func (fl *tcFleet) worksFor(a *tcMember, w tcWake) string {
	if p := fl.wakeFor(w); p != "" {
		return p
	}
	return ownerOf(a)
}

// scopeFor is whom agent a's view is cut down to while it works for p:
// nobody when p is its owner (whose turns keep the agent's whole view).
func scopeFor(a *tcMember, p string) []string {
	if p == "" || p == ownerOf(a) {
		return nil
	}
	return []string{p}
}

// fits: whether event e may join a turn of agent a that works for p: it
// works for the same person, who sees its conversation, so a turn never
// carries one person's words to another (docs/apps/team-chat.md).
func (fl *tcFleet) fits(a *tcMember, p string, e tcWake) bool {
	if fl.worksFor(a, e) != p {
		return false
	}
	if c := fl.convs[e.CID]; c != nil && p != ownerOf(a) && !fl.visible(c, p) {
		return false
	}
	return true
}

// turnEvents are the queued events one turn takes, and whom it works for:
// the oldest, and every later one that works for the same person (the rest
// wait for a later turn).
func (fl *tcFleet) turnEvents(a *tcMember, q []tcWake) ([]tcWake, string) {
	if len(q) == 0 {
		return nil, ownerOf(a)
	}
	p := fl.worksFor(a, q[0])
	out := []tcWake{q[0]}
	for _, e := range q[1:] {
		if fl.fits(a, p, e) {
			out = append(out, e)
		}
	}
	return out, p
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

// actorsOf: whose access a message's wakes are checked against: a person's
// own message; an agent's message, the person its turn worked for (its
// owner for an old message that names nobody).
func (fl *tcFleet) actorsOf(e *tcEvent) []string {
	sender := fl.members[e.From]
	if sender == nil {
		return nil
	}
	if !sender.agent() {
		return []string{e.From}
	}
	if len(e.Behalf) > 0 {
		return e.Behalf[:1]
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
	if avatarOK(v.Avatar) != nil {
		v.Avatar = ""
	}
	if viewer != tcOwner {
		if m.agent() {
			v.NodeName, v.NodeOS = fl.friendlyComputer(m), ""
			v.Folder, v.NodeID, v.Session, v.Turns, v.Spans = "", "", "", nil, nil
		}
		if !m.agent() && m.MID != viewer {
			// People's earlier handles start with their account's name: the
			// owner's are nobody else's to see, and a person sees only their own.
			v.PrevHandles = nil
		}
	}
	return &v
}

// statusView is an agent's status as a person sees it: where it works only
// when they see that conversation; for anyone but the owner, its computer by
// a friendly name and its problems without the owner's fixes.
func (fl *tcFleet) statusView(status map[string]any, mid, viewer string) map[string]any {
	out := map[string]any{}
	hide := false
	if cid, _ := status["cid"].(string); cid != "" {
		c := fl.convs[cid]
		hide = c == nil || !fl.visible(c, viewer)
	}
	for k, v := range status {
		if hide && (k == "cid" || k == "root" || k == "line") {
			continue
		}
		out[k] = v
	}
	a := fl.members[mid]
	if viewer == tcOwner || a == nil || !a.agent() {
		return out
	}
	friendly := fl.friendlyComputer(a)
	raw, _ := status["computer"].(string)
	plain := func(s string) string {
		if raw != "" && raw != friendly {
			s = strings.ReplaceAll(s, capFirst(raw), friendly)
			s = strings.ReplaceAll(s, raw, friendly)
		}
		s = strings.ReplaceAll(s, "Your Workspace", friendly)
		return strings.ReplaceAll(s, "your Workspace", friendly)
	}
	out["computer"] = friendly
	if line, ok := out["line"].(string); ok {
		if strings.HasPrefix(line, "Retrying soon") {
			line = "Retrying soon"
		}
		out["line"] = plain(line)
	}
	if reason, ok := out["reason"].(string); ok {
		out["reason"] = plain(reason)
	}
	if out["state"] == "attention" && out["paused"] != "limit" {
		out["reason"] = a.Name + " couldn't answer right now"
	}
	delete(out, "fix")
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
		out["message"] = fl.msgView(msg, viewer)
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
	case "space":
		if viewer != tcOwner {
			delete(out, "usage")
		}
	case "member":
		if m, _ := event["member"].(*tcMember); m != nil {
			out["member"] = fl.memberView(m, viewer)
		}
	case "agent_status":
		if st, _ := event["status"].(map[string]any); st != nil {
			mid, _ := event["mid"].(string)
			out["status"] = fl.statusView(st, mid, viewer)
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

// ── names people cannot take ───────────────────────────────────────────────

// confusable folds characters that read like others.
var confusable = strings.NewReplacer("0", "o", "1", "l", "|", "l", "!", "l", "3", "e", "5", "s", "$", "s", "@", "a",
	"rn", "m", "vv", "w", "а", "a", "е", "e", "о", "o", "р", "p", "с", "c", "х", "x", "у", "y", "і", "i", "ӏ", "l", "ı", "i")

// skeleton is how a name reads: case, accents, spacing and punctuation
// left out, look-alike characters folded.
func skeleton(name string) string {
	var b strings.Builder
	for _, r := range norm.NFKD.String(strings.ToLower(name)) {
		if !unicode.Is(unicode.Mn, r) {
			b.WriteRune(r)
		}
	}
	folded := confusable.Replace(b.String())
	b.Reset()
	for _, r := range folded {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// oneEdit: a and b differ by at most one inserted, removed or changed rune.
func oneEdit(a, b string) bool {
	x, y := []rune(a), []rune(b)
	if len(x) < len(y) {
		x, y = y, x
	}
	if len(x)-len(y) > 1 {
		return false
	}
	i, j, edits := 0, 0, 0
	for i < len(x) && j < len(y) {
		if x[i] == y[j] {
			i, j = i+1, j+1
			continue
		}
		if edits++; edits > 1 {
			return false
		}
		if len(x) == len(y) {
			j++
		}
		i++
	}
	return edits+(len(x)-i) <= 1
}

// mimicked is the member whose name or handle text reads like (another
// person may not take it): the same skeleton as anyone's, or one letter off
// the owner's or an admin's.
func (fl *tcFleet) mimicked(self, text string) *tcMember {
	sk := skeleton(text)
	if sk == "" {
		return nil
	}
	for _, mid := range sortedKeys(fl.members) {
		m := fl.members[mid]
		if mid == self || !m.agent() && m.Removed != "" {
			continue
		}
		boss := mid == tcOwner || m.Role == "admin"
		for _, other := range []string{m.Name, m.Handle} {
			o := skeleton(other)
			if o == "" {
				continue
			}
			if o == sk || boss && len([]rune(o)) >= 5 && len([]rune(sk)) >= 5 && oneEdit(o, sk) {
				return m
			}
		}
	}
	return nil
}

// tooClose refuses a person's name or handle that reads like another
// member's.
func (fl *tcFleet) tooClose(self, what, text string) error {
	if self == tcOwner {
		return nil
	}
	if m := fl.mimicked(self, text); m != nil {
		return &ArgumentError{fmt.Sprintf("That %s is too close to @%s (%s). Choose another.", what, m.Handle, m.Name)}
	}
	return nil
}

// reservedHandles name the space's own words: a person or agent may not
// take them (a mention of one would read as a call to everyone, or as
// Team Chat itself).
var reservedHandles = map[string]bool{"owner": true, "admin": true, "everyone": true, "here": true, "all": true,
	"channel": true, "system": true, "team-chat": true, "teamchat": true, "team_chat": true, "bot": true, "agent": true,
	// The platform and its staff (from the fifth Phase 3 check): a
	// mention of one would read as PantheonOS or its support speaking.
	"pantheon": true, "pantheonos": true, "support": true, "staff": true, "root": true, "sudo": true,
	"superuser": true, "sysadmin": true, "helpdesk": true}

// digitLooks folds digits that pass for letters ("Adm1n", "0wner", "syst3m")
// when names and handles are checked against role words; 1 reads as i
// here, and as l in a second look (lookAlikes). A name's words come folded
// by skeleton, which already reads 1 as l: lookAlikes also reads l as i.
var digitLooks = strings.NewReplacer("0", "o", "1", "i", "3", "e", "4", "a", "5", "s", "7", "t", "8", "b", "9", "g")

// lookAlikes are the ways one folded word (or handle) may be read: as it
// is, with its digits read as letters (1 as i, then as l; l as i), and without the
// digits, - and _ at its ends ("system32", "_admin").
func lookAlikes(w string) []string {
	return []string{w, digitLooks.Replace(w), digitLooks.Replace(strings.ReplaceAll(w, "1", "l")),
		strings.ReplaceAll(digitLooks.Replace(w), "l", "i"), strings.Trim(w, "0123456789-_")}
}

// isRoleWord: w (one folded word of a name) is a role word, singular or
// plural, read with digits as letters, or starts or ends with one of the
// role words of five letters or more ("ownerweize", "superadmin",
// "system32"); the short ones (mod, bot) match only whole ("bot7", "b0t"),
// so names such as Botha or Modi stay free.
func isRoleWord(w string) bool {
	for _, v := range lookAlikes(w) {
		if v == "" {
			continue
		}
		if roleWords[v] || roleWords[strings.TrimSuffix(v, "s")] {
			return true
		}
		for rw := range roleWords {
			if len(rw) >= 5 && !wholeRoleWords[rw] && (strings.HasPrefix(v, rw) || strings.HasSuffix(strings.TrimSuffix(v, "s"), rw)) {
				return true
			}
		}
	}
	return false
}

// roleSuffixRE: a name that ends in a role ("Weize (owner)", "Sam admin").
var roleSuffixRE = regexp.MustCompile(`(?i)[\s(\[{<-]*\b(owner|admin|administrator|moderator)\b[\s)\]}>.]*$`)

// blank runes draw nothing though they are letters or symbols.
var blankRunes = map[rune]bool{0x115F: true, 0x1160: true, 0x3164: true, 0xFFA0: true, 0x2800: true, 0x180E: true}

// nameTrouble says why text cannot be a name (a person's or an agent's),
// or "": no line breaks or other control characters, no direction or
// invisible (format) characters such as U+202E or U+200B, and something
// visible.
func nameTrouble(n string) string {
	visible := false
	for _, r := range n {
		switch {
		case r == '\n' || r == '\r' || unicode.Is(unicode.Cc, r):
			return "A name is one line, without control characters."
		case unicode.Is(unicode.Cf, r) || unicode.Is(unicode.Zl, r) || unicode.Is(unicode.Zp, r):
			return "A name cannot hold invisible or text-direction characters."
		case unicode.IsSpace(r) || blankRunes[r] || unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Me, r):
		case unicode.IsGraphic(r):
			visible = true
		}
	}
	if !visible {
		return "A name needs letters you can see."
	}
	return ""
}

// plainName drops what a name cannot hold (for an account's name taken at
// join): control, format and direction characters, extra spaces.
func plainName(n string) string {
	var b strings.Builder
	for _, r := range n {
		if unicode.Is(unicode.Cc, r) || unicode.Is(unicode.Cf, r) || unicode.Is(unicode.Zl, r) || unicode.Is(unicode.Zp, r) {
			b.WriteRune(' ')
			continue
		}
		b.WriteRune(r)
	}
	return strings.Join(strings.Fields(b.String()), " ")
}

// roleWords may not be a word of a person's name (singular or plural): a
// name with one reads as a role in the space, Team Chat itself, or an agent
// ("Weize (owner)", "Owner of the space", "weize_owner", "coder (agent)").
var roleWords = map[string]bool{"owner": true, "admin": true, "administrator": true, "moderator": true, "mod": true,
	"agent": true, "bot": true, "system": true, "teamchat": true, "everyone": true,
	// The platform and its staff (from the fifth Phase 3 check).
	"pantheon": true, "pantheonos": true, "support": true, "staff": true, "root": true, "sudo": true,
	"superuser": true, "sysadmin": true, "helpdesk": true}

// wholeRoleWords are role words of five letters or more that count only as
// a whole word, as the short ones do: names such as Staffan stay free.
var wholeRoleWords = map[string]bool{"staff": true}

// reservedName: a person's name that reads as Team Chat itself, a role or
// an agent: one of its words (split at anything not a letter or digit, and
// at a change of letter case) is a role word, singular or plural, or the
// whole name is a reserved handle.
func reservedName(n string) bool {
	sk := skeleton(n)
	if sk == "teamchat" || sk == "system" || reservedHandle(sk) || sk == "spaceowner" || sk == "spaceadmin" {
		return true
	}
	words := nameWords(n)
	for i, w := range words {
		if isRoleWord(w) {
			return true
		}
		if w == "team" && i+1 < len(words) && strings.TrimSuffix(words[i+1], "s") == "chat" {
			return true
		}
	}
	return roleSuffixRE.MatchString(strings.TrimSpace(n))
}

// nameWords are a name's words, each folded as skeleton folds a name
// ("WeizeOwner" and "weize_owner" are weize, owner).
func nameWords(n string) []string {
	var words []string
	var cur []rune
	var prev rune
	flush := func() {
		if w := skeleton(string(cur)); w != "" {
			words = append(words, w)
		}
		cur = cur[:0]
	}
	for _, r := range norm.NFKC.String(n) {
		switch {
		case !unicode.IsLetter(r) && !unicode.IsDigit(r):
			flush()
		case unicode.IsUpper(r) && unicode.IsLower(prev):
			flush()
			cur = append(cur, r)
		default:
			cur = append(cur, r)
		}
		prev = r
	}
	flush()
	return words
}

// reservedHandle: a handle that is one of the space's own words, also with
// a plural s, digits, - or _ after it ("admins", "everyone1", "owner_2",
// "agent7", "bots"), or with digits that pass for letters ("adm1n",
// "0wner").
func reservedHandle(h string) bool {
	low := strings.ToLower(h)
	for _, v := range []string{low, digitLooks.Replace(low), digitLooks.Replace(strings.ReplaceAll(low, "1", "l"))} {
		base := strings.TrimRight(v, "0123456789-_")
		if base == "" {
			base = v
		}
		if reservedHandles[base] || reservedHandles[strings.TrimSuffix(base, "s")] || strings.HasPrefix(base, "pantheon") {
			return true
		}
	}
	return false
}

// avatarRE: an avatar is an uploaded image (a data URL); without one, the
// page draws one.
var avatarRE = regexp.MustCompile(`^data:image/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+=*$`)

func avatarOK(v string) error {
	if v == "" || avatarRE.MatchString(v) {
		return nil
	}
	return &ArgumentError{"An avatar is an uploaded image (PNG, JPEG, GIF or WebP)."}
}

// friendlyComputer names an agent's computer for people other than the
// owner: whose it is and what it is, never its network name.
func (fl *tcFleet) friendlyComputer(a *tcMember) string {
	owner := "The owner"
	if o := fl.members[ownerOf(a)]; o != nil && strings.TrimSpace(o.Name) != "" {
		owner = strings.TrimSpace(o.Name)
	}
	whose := owner + "'s "
	if strings.HasSuffix(owner, "s") {
		whose = owner + "' "
	}
	lower := strings.ToLower(a.NodeName)
	switch {
	case a.NodeKind == "sandbox" || a.NodeKind == "pod" || a.NodeKind == "" && strings.HasPrefix(a.Folder, "/workspace/"):
		return whose + "Workspace"
	case a.NodeOS == "darwin" || a.NodeOS == "macos" || a.NodeOS == "" && (strings.Contains(lower, "mac") || strings.HasSuffix(lower, ".local")):
		return whose + "Mac"
	case a.NodeOS == "windows":
		return whose + "PC"
	}
	return whose + "computer"
}

// msgView is a message as a person sees it: a line about an agent that
// could not answer tells the owner what went wrong and how to fix it; anyone
// else reads only that it could not answer.
func (fl *tcFleet) msgView(msg *tcMsg, viewer string) *tcMsg {
	if msg == nil || viewer == tcOwner || msg.Kind != "system" || msg.Action != "retry" {
		return msg
	}
	v := *msg
	name := "The agent"
	if a := fl.members[msg.About]; a != nil {
		name = a.Name
	}
	v.Text, v.Action = name+" couldn't answer right now.", ""
	return &v
}

func (fl *tcFleet) msgsView(page []*tcMsg, viewer string) []*tcMsg {
	if viewer == tcOwner {
		return page
	}
	out := make([]*tcMsg, len(page))
	for i, msg := range page {
		out[i] = fl.msgView(msg, viewer)
	}
	return out
}

// ── one person's asks per turn ──────────────────────────────────────────────

// askOf is the one person a message works for: a person's own message; an
// agent's, the person its turn worked for (its owner for an old message
// that names nobody); "" for Team Chat's own lines.
func (fl *tcFleet) askOf(msg *tcMsg) string {
	if msg == nil || msg.Kind != "post" {
		return ""
	}
	sender := fl.members[msg.From]
	switch {
	case sender == nil:
		return ""
	case !sender.agent():
		return msg.From
	case len(msg.behalf) > 0:
		return msg.behalf[0]
	}
	return ownerOf(sender)
}

// addresses: whether msg asks agent a something: a DM to it, an @mention of
// it, a message that woke it, or a reply in a thread it follows.
func (fl *tcFleet) addresses(c *tcConv, cm *tcConvMsgs, msg *tcMsg, a string) bool {
	if msg == nil || msg.From == a || msg.Kind != "post" {
		return false
	}
	if c != nil && c.Kind == "dm" && c.has(a) {
		return true
	}
	if contains(msg.Mentions, a) || contains(msg.woke, a) {
		return true
	}
	return cm != nil && msg.Root != "" && fl.followsThread(cm, a, msg.Root)
}

// othersAsk: msg asks agent a something for someone other than p, the person
// a turn of a works for. Such a message never reaches that turn: it is
// answered in a turn of its own, for its person (docs/apps/team-chat.md,
// "One person's asks per turn").
func (fl *tcFleet) othersAsk(c *tcConv, cm *tcConvMsgs, msg *tcMsg, a *tcMember, p string) bool {
	return fl.addresses(c, cm, msg, a.MID) && fl.askOf(msg) != p
}

// waiting: msg is in agent a's queue, waiting for a turn of its own.
func (fl *tcFleet) waiting(a string, msgID string) bool {
	for _, e := range fl.queues[a] {
		if e.ID == msgID && e.Kind != "reminder" {
			return true
		}
	}
	return false
}

// turnPerson is the person agent a's running turn works for ("" outside a
// turn).
func (fl *tcFleet) turnPerson(a *tcMember) string {
	if tr := fl.turns[a.MID]; tr != nil {
		return tr.WorksFor
	}
	return ""
}

// asShown is how a turn of agent a that works for p shows msg's text when it
// reads a conversation or searches: another person's ask that still waits
// for its own turn is not shown; another person's message is marked as
// context, not a request.
func (fl *tcFleet) asShown(c *tcConv, cm *tcConvMsgs, msg *tcMsg, a *tcMember, p, text string) string {
	if p == "" || msg == nil || msg.Kind != "post" {
		return text
	}
	who := fl.askOf(msg)
	if who == "" || who == p {
		return text
	}
	if msg.From == a.MID {
		// Its own message, from a turn for someone else.
		return fmt.Sprintf("[your own message, from a turn for %s]", handleOf(fl, who)) + " " + text
	}
	if fl.othersAsk(c, cm, msg, a, p) && fl.waiting(a.MID, msg.ID) {
		return fmt.Sprintf("(not shown: %s's request to you; it waits for a turn of its own, for them)", handleOf(fl, who))
	}
	return contextMark(fl, msg, who) + " " + text
}

// contextMark marks another person's message (or an agent's written for
// them) as context for a turn that works for someone else.
func contextMark(fl *tcFleet, msg *tcMsg, who string) string {
	if msg.From == who {
		return fmt.Sprintf("[%s's message: context only, not a request to you in this turn]", handleOf(fl, who))
	}
	return fmt.Sprintf("[written for %s: context only, not a request to you in this turn]", handleOf(fl, who))
}

// endWorkFor ends everything that would run later for a person who left or
// was removed: their reminders and every reminder set in their turns
// (recurring ones too), the queued messages that work for them, and a turn
// running for them; fl.mu is held.
func (t *TeamChat) endWorkFor(ctx context.Context, fl *tcFleet, mid string) {
	for _, r := range fl.rems {
		if r.For == mid || contains(r.Behalf, mid) {
			_ = t.cancelReminder(ctx, fl, r)
		}
	}
	for _, amid := range sortedKeys(fl.queues) {
		q := fl.queues[amid]
		var keep []tcWake
		for _, e := range q {
			if fl.wakeFor(e) == mid || e.For == mid || contains(e.Behalf, mid) {
				continue
			}
			keep = append(keep, e)
		}
		if len(keep) != len(q) {
			t.saveQueue(ctx, fl, amid, keep)
			t.emitStatus(fl, amid)
		}
	}
	for _, amid := range sortedKeys(fl.turns) {
		tr := fl.turns[amid]
		if tr == nil || tr.WorksFor != mid || tr.Stop != "" {
			continue
		}
		tr.Stop = "user"
		_ = t.put(ctx, fl, "turn."+amid, tr)
		if tr.State != "running" {
			continue
		}
		fid, node, conv := fl.fid, tr.NodeID, tr.ConversationID
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			_, _ = t.Nodes.Call(ctx, fid, node, "agent_stop", map[string]any{"conversation_id": conv}, 30*time.Second)
		}()
	}
}

// goneFor: whether a reminder works for a person who is no longer in the
// space (its For, or the person whose turn set it).
func (fl *tcFleet) goneFor(r *tcRem) bool {
	for _, p := range append([]string{r.For}, r.Behalf...) {
		if x := fl.members[p]; x != nil && !x.agent() && x.Removed != "" {
			return true
		}
	}
	return false
}
