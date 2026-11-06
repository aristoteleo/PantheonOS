package hosted

// What is new for a person (unread, followed threads, tasks In review),
// search over a space's messages, notification levels and a conversation's
// files (docs/apps/team-chat.md, "Phase 2").

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"
)

// tcUnread is what is new for a person in a conversation or a thread.
type tcUnread struct {
	Count    int    `json:"count"`
	Mentions int    `json:"mentions"`
	First    string `json:"first,omitempty"` // first unread message
	Last     string `json:"last,omitempty"`  // the conversation's (or thread's) last message
	CID      string `json:"cid,omitempty"`
	lastNew  string
}

// newFor: a message counts as new for me (not mine, not reporting my own
// action, not deleted).
func newFor(msg *tcMsg, me string) bool {
	return msg != nil && msg.From != me && msg.By != me && !msg.Deleted
}

// threadMark is where a person last read a thread: its own mark, else the
// conversation's.
func (fl *tcFleet) threadMark(me, cid, root string) string {
	if id := fl.treads[me+"."+root]; id != "" {
		return id
	}
	return fl.reads[me+"."+cid]
}

// unreadIn counts a conversation's new top-level messages and the new
// replies of the threads the person follows; fl.mu is held.
func (fl *tcFleet) unreadIn(m *tcConvMsgs, me, cid string) (conv tcUnread, threads map[string]*tcUnread) {
	mark := fl.reads[me+"."+cid]
	if n := len(m.all); n > 0 {
		conv.Last = m.all[n-1]
	}
	for _, id := range m.top {
		msg := m.byID[id]
		if id <= mark || !newFor(msg, me) {
			continue
		}
		conv.Count++
		if conv.First == "" {
			conv.First = id
		}
		conv.lastNew = id
		if contains(msg.Mentions, me) {
			conv.Mentions++
		}
	}
	threads = map[string]*tcUnread{}
	for root, replies := range m.replies {
		if len(replies) == 0 || m.byID[root] == nil || !fl.followsThread(m, me, root) {
			continue
		}
		tmark := fl.threadMark(me, cid, root)
		u := &tcUnread{CID: cid, Last: replies[len(replies)-1]}
		for _, id := range replies {
			msg := m.byID[id]
			if id <= tmark || !newFor(msg, me) {
				continue
			}
			u.Count++
			if u.First == "" {
				u.First = id
			}
			u.lastNew = id
			if contains(msg.Mentions, me) {
				u.Mentions++
			}
		}
		if u.Count > 0 {
			threads[root] = u
			conv.Count += u.Count
			conv.Mentions += u.Mentions
		}
	}
	return conv, threads
}

// setThreadRead moves a person's mark in a thread; fl.mu is held.
func (t *TeamChat) setThreadRead(ctx context.Context, fl *tcFleet, mid, cid, root, id string) error {
	key := mid + "." + root
	if fl.treads[key] >= id {
		return nil
	}
	if err := t.put(ctx, fl, "tread."+key, id); err != nil {
		return err
	}
	fl.treads[key] = id
	if fl.person(mid) {
		t.emit(fl, map[string]any{"type": "read", "mid": mid, "cid": cid, "root": root, "id": id})
	}
	return nil
}

// readAll marks every conversation and followed thread read.
func (t *TeamChat) readAll(ctx context.Context, fl *tcFleet, me string) (any, error) {
	marked := 0
	for _, cid := range sortedKeys(fl.convs) {
		c := fl.convs[cid]
		if !fl.visible(c, me) || !fl.inConv(c, me) {
			continue
		}
		m, err := t.convMsgs(ctx, fl, cid)
		if err != nil {
			return nil, err
		}
		conv, threads := fl.unreadIn(m, me, cid)
		if conv.Count == 0 {
			continue
		}
		if len(m.top) > 0 && m.top[len(m.top)-1] > fl.reads[me+"."+cid] {
			if err := t.setRead(ctx, fl, me, cid, m.top[len(m.top)-1]); err != nil {
				return nil, err
			}
		}
		for root, u := range threads {
			if err := t.setThreadRead(ctx, fl, me, cid, root, u.Last); err != nil {
				return nil, err
			}
		}
		marked++
	}
	return map[string]any{"marked": marked}, nil
}

// activity is the person's rows of what is new: conversations, followed
// threads, tasks In review; newest first.
func (t *TeamChat) activity(ctx context.Context, fl *tcFleet, me, filter string) (any, error) {
	switch filter {
	case "":
		filter = "all"
	case "all", "mentions", "unread", "review":
	default:
		return nil, &ArgumentError{"filter is all, mentions, unread or review"}
	}
	rows := []map[string]any{}
	if filter != "review" {
		for _, cid := range sortedKeys(fl.convs) {
			c := fl.convs[cid]
			if !fl.visible(c, me) || !fl.inConv(c, me) || c.Archived {
				continue
			}
			m, err := t.convMsgs(ctx, fl, cid)
			if err != nil {
				return nil, err
			}
			conv, threads := fl.unreadIn(m, me, cid)
			top := conv.Count
			for _, u := range threads {
				top -= u.Count
			}
			if top > 0 {
				mentions := conv.Mentions
				for _, u := range threads {
					mentions -= u.Mentions
				}
				if filter != "mentions" || mentions > 0 {
					last := m.byID[conv.lastNew]
					rows = append(rows, map[string]any{"kind": "conv", "cid": cid, "root": "", "count": top, "mentions": mentions,
						"first_unread": conv.First, "last": fl.msgView(last, me), "at": last.At})
				}
			}
			for root, u := range threads {
				if filter == "mentions" && u.Mentions == 0 {
					continue
				}
				last := m.byID[u.lastNew]
				rows = append(rows, map[string]any{"kind": "thread", "cid": cid, "root": root, "count": u.Count, "mentions": u.Mentions,
					"first_unread": u.First, "last": fl.msgView(last, me), "at": last.At, "root_message": fl.msgView(m.byID[root], me)})
			}
		}
	}
	if filter == "all" || filter == "review" {
		for _, tk := range t.taskList(fl, me, "", "", "", "in_review") {
			// A task waits for the person who made it (an agent's or a
			// person's no longer in the space: for the owner).
			if creator := fl.members[tk.CreatedBy]; tk.CreatedBy != me && !(me == tcOwner && (creator == nil || creator.agent() || creator.Removed != "")) {
				continue
			}
			var last *tcMsg
			if m, err := t.convMsgs(ctx, fl, tk.CID); err == nil {
				last = m.byID[tk.ID]
			}
			rows = append(rows, map[string]any{"kind": "task", "cid": tk.CID, "root": tk.Root, "count": 0, "mentions": 0,
				"first_unread": tk.ID, "last": last, "at": tk.Updated, "task": tk})
		}
	}
	sort.SliceStable(rows, func(i, j int) bool { return fmt.Sprint(rows[i]["at"]) > fmt.Sprint(rows[j]["at"]) })
	return map[string]any{"rows": rows}, nil
}

// ── search ─────────────────────────────────────────────────────────────────

// searchTime reads a search bound: a date (in the space's time zone), an
// RFC 3339 time, or a message id. A date as `before` means before that day;
// as `after`, after it.
func searchTime(fl *tcFleet, s string, after bool) (string, string, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", "", nil
	}
	if ts, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return ts.UTC().Format(time.RFC3339Nano), "", nil
	}
	if day, err := time.ParseInLocation("2006-01-02", s, spaceLoc(fl)); err == nil {
		if after {
			day = day.AddDate(0, 0, 1)
		}
		return day.UTC().Format(time.RFC3339Nano), "", nil
	}
	if tcIDRE.MatchString(s) && len(s) == 26 {
		return "", s, nil
	}
	return "", "", &ArgumentError{"a time is a date (2026-11-05), an RFC 3339 time or a message id"}
}

// atOf: a message's time, comparable as text with searchTime's (RFC 3339 UTC).
func atOf(msg *tcMsg) string {
	ts, err := time.Parse(time.RFC3339Nano, msg.At)
	if err != nil {
		return msg.At
	}
	return ts.UTC().Format(time.RFC3339Nano)
}

// search finds messages that have every word (text or file names), newest
// first; fl.mu is held.
func (t *TeamChat) search(ctx context.Context, fl *tcFleet, me, q, cid, from, before, after string, limit int) (map[string]any, error) {
	start := time.Now()
	words := strings.Fields(strings.ToLower(q))
	if len(words) == 0 && from == "" {
		return nil, &ArgumentError{"type a word to search for"}
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	if from != "" {
		if strings.HasPrefix(from, "@") {
			who := t.memberByHandle(fl, from)
			if who == nil {
				h := strings.ToLower(strings.TrimPrefix(from, "@"))
				for mid, f := range fl.space.Formers {
					if f.Handle == h {
						who = &tcMember{MID: mid}
					}
				}
			}
			if who == nil {
				return nil, &ArgumentError{"no member " + from}
			}
			from = who.MID
		}
	}
	beforeAt, beforeID, err := searchTime(fl, before, false)
	if err != nil {
		return nil, err
	}
	afterAt, afterID, err := searchTime(fl, after, true)
	if err != nil {
		return nil, err
	}
	var found []*tcMsg
	for _, c := range sortedKeys(fl.convs) {
		conv := fl.convs[c]
		if cid != "" && c != cid || !fl.visible(conv, me) {
			continue
		}
		m, err := t.convMsgs(ctx, fl, c)
		if err != nil {
			return nil, err
		}
	next:
		for i := len(m.all) - 1; i >= 0; i-- {
			id := m.all[i]
			msg := m.byID[id]
			if msg.Kind != "post" || msg.Deleted || from != "" && msg.From != from ||
				beforeID != "" && id >= beforeID || afterID != "" && id <= afterID {
				continue
			}
			if beforeAt != "" || afterAt != "" {
				at := atOf(msg)
				if beforeAt != "" && at >= beforeAt || afterAt != "" && at < afterAt {
					continue
				}
			}
			for _, w := range words {
				if !strings.Contains(msg.lower, w) {
					continue next
				}
			}
			found = append(found, msg)
		}
	}
	sort.Slice(found, func(i, j int) bool { return found[i].ID > found[j].ID })
	total := len(found)
	if len(found) > limit {
		found = found[:limit]
	}
	return map[string]any{"results": found, "total": total, "took_ms": time.Since(start).Milliseconds()}, nil
}

// agentSearch serves `pantheon-team search …`; fl.mu is held.
func (t *TeamChat) agentSearch(ctx context.Context, fl *tcFleet, m *tcMember, req agentRequest) (string, error) {
	cid := ""
	if in := req.Opts["in"]; in != "" {
		c := t.channelNamed(fl, in)
		if c == nil && strings.HasPrefix(in, "@") {
			if other := t.memberByHandle(fl, in); other != nil {
				c = fl.convs[dmID(m.MID, other.MID)]
			}
		}
		if c == nil {
			return "", fmt.Errorf("no channel %s", in)
		}
		cid = c.CID
	}
	limit := 20
	if n, err := fmt.Sscan(req.Opts["limit"], &limit); n != 1 || err != nil {
		limit = 20
	}
	q := strings.TrimSpace(strings.Join(req.Args, " ") + " " + req.Text)
	res, err := t.search(ctx, fl, m.MID, q, cid, req.Opts["from"], req.Opts["before"], req.Opts["after"], limit)
	if err != nil {
		return "", err
	}
	found := res["results"].([]*tcMsg)
	if len(found) == 0 {
		return "No messages found.", nil
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%d found", res["total"])
	if res["total"].(int) > len(found) {
		fmt.Fprintf(&b, " (the newest %d; narrow with --in, --from or --limit)", len(found))
	}
	b.WriteString(":\n")
	for _, msg := range found {
		where := fl.convs[msg.CID].label(fl, m.MID)
		if msg.Root != "" {
			where += " (thread on " + msg.Root + ")"
		}
		text := clipText(shownText(fl, msg), 400)
		if p := fl.turnPerson(m); p != "" {
			cm, _ := t.convMsgs(ctx, fl, msg.CID)
			text = fl.asShown(fl.convs[msg.CID], cm, msg, m, p, text)
		}
		fmt.Fprintf(&b, "%s %s, %s [%s]:\n%s", fmtTime(msg.At), where, authorOf(fl, msg.From, m.MID), msg.ID, indent(text))
	}
	return b.String(), nil
}

// ── notifications and files ────────────────────────────────────────────────

func (t *TeamChat) notifySet(ctx context.Context, fl *tcFleet, me, cid, level string) (any, error) {
	if _, err := t.conv(fl, cid); err != nil {
		return nil, err
	}
	if level != "all" && level != "mentions" && level != "mute" {
		return nil, &ArgumentError{"level is all, mentions or mute"}
	}
	key := me + "." + cid
	if fl.notify[key] != level {
		if err := t.put(ctx, fl, "notify."+key, level); err != nil {
			return nil, err
		}
		fl.notify[key] = level
		t.emit(fl, map[string]any{"type": "notify", "cid": cid, "level": level, "mid": me})
	}
	return map[string]any{"cid": cid, "level": level}, nil
}

func (t *TeamChat) convFiles(ctx context.Context, fl *tcFleet, me, cid string) (any, error) {
	c, err := t.conv(fl, cid)
	if err != nil {
		return nil, err
	}
	if !fl.visible(c, me) {
		return nil, &ArgumentError{"You are not in this conversation."}
	}
	m, err := t.convMsgs(ctx, fl, cid)
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for i := len(m.all) - 1; i >= 0 && len(out) < 500; i-- {
		msg := m.byID[m.all[i]]
		if msg.Deleted {
			continue
		}
		for _, f := range msg.Files {
			out = append(out, map[string]any{"file": f, "id": msg.ID, "root": msg.Root, "from": msg.From, "at": msg.At})
		}
	}
	return map[string]any{"files": out}, nil
}
