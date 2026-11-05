package hosted

// The dispatcher: wake rules, per-agent queues kept in the bucket, one turn
// at a time per agent on its node, the turn's final text posted for it,
// status, retries, guards and plain-language failure lines
// (docs/apps/team-chat.md, "Waking" and "Dispatching a turn").

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

type tcRecipient struct {
	agent string
	wake  tcWake
}

// wakesFor decides which agents a new message wakes, and which agents a
// mention in a public channel adds to it. stopped: a wake was held back
// because the agent chain reached its hop limit.
func (t *TeamChat) wakesFor(fl *tcFleet, c *tcConv, m *tcConvMsgs, e *tcEvent) (out []tcRecipient, joined []string, stopped bool) {
	if e.Kind != "post" {
		return nil, nil, false
	}
	sender := fl.members[e.From]
	fromAgent := sender != nil && sender.agent()
	for _, mid := range sortedKeys(fl.members) {
		a := fl.members[mid]
		if !a.agent() || mid == e.From {
			continue
		}
		reason := ""
		switch {
		case c.Kind == "dm" && c.has(mid):
			reason = "dm"
		case contains(e.Mentions, mid):
			if c.has(mid) {
				reason = "mention"
			} else if c.Kind == "channel" && !c.Private {
				reason = "mention"
				joined = append(joined, mid)
			}
		case e.Root != "" && fl.visible(c, mid) && fl.wakesInThread(m, mid, e.Root):
			reason = "thread"
		case c.Kind == "channel" && c.has(mid) && (c.AgentReply == "all" || a.Listen == "all"):
			reason = "all"
		}
		if reason == "" {
			continue
		}
		if fromAgent && (reason == "thread" || reason == "all") {
			// Agents wake each other only by asking: a DM or an @mention.
			// Otherwise every "noted" in a thread they all follow would
			// start another round of turns.
			continue
		}
		if fromAgent && e.Hop >= fl.space.Settings.HopLimit {
			stopped = true
			continue
		}
		out = append(out, tcRecipient{mid, tcWake{ID: e.ID, Event: e.ID, CID: e.CID, Root: e.Root, Kind: "post",
			Reason: reason, From: e.From, Hop: e.Hop, At: e.At}})
	}
	return out, joined, stopped
}

// enqueue adds a wake event to an agent's queue (once) and starts a turn
// when it can; fl.mu is held.
func (t *TeamChat) enqueue(ctx context.Context, fl *tcFleet, mid string, w tcWake) {
	q := fl.queues[mid]
	for _, have := range q {
		if have.key() == w.key() {
			return
		}
	}
	if tr := fl.turns[mid]; tr != nil && tr.State == "running" {
		for _, have := range tr.Events {
			if have.key() == w.key() {
				return
			}
		}
	}
	next := append(append([]tcWake(nil), q...), w)
	if err := t.put(ctx, fl, "queue."+mid, next); err != nil {
		log.Printf("[team-chat] %s queue %s: %v", fl.fid, mid, err)
		return
	}
	fl.queues[mid] = next
	t.emitStatus(fl, mid)
	t.kick(fl.fid, mid)
}

func (t *TeamChat) emitStatus(fl *tcFleet, mid string) {
	if m := fl.members[mid]; m != nil && m.agent() {
		t.emit(fl, map[string]any{"type": "agent_status", "mid": mid, "status": t.statusOf(fl, m)})
	}
}

// statusOf is an agent's status: ready, working, attention or offline.
func (t *TeamChat) statusOf(fl *tcFleet, m *tcMember) map[string]any {
	l := fl.liveOf(m.MID)
	out := map[string]any{"queue": len(fl.queues[m.MID])}
	node, known := fl.nodes[m.NodeID]
	node = withMemberNode(node, m)
	tr := fl.turns[m.MID]
	out["computer"] = nodeLabel(node, m.NodeID)
	offline := capFirst(nodeLabel(node, m.NodeID)) + " is offline"
	if node.Kind == "sandbox" {
		offline = "Your Workspace is paused"
	}
	switch {
	case m.State == "stopped":
		out["state"], out["reason"] = "offline", m.Name+" is stopped"
	case tr != nil:
		out["state"], out["since"], out["cid"], out["root"] = "working", tr.Started, tr.Cause.CID, tr.Cause.Root
		out["line"] = l.line
		if tr.State == "starting" {
			out["line"] = "Starting"
		}
		if known && !node.Online {
			out["line"] = offline + "; waiting for it"
		}
	case known && !node.Online:
		out["state"], out["reason"] = "offline", offline
	case m.Attention != nil:
		out["state"], out["reason"], out["fix"] = "attention", m.Attention.Reason, m.Attention.Fix
	case l.paused != "":
		out["state"], out["reason"] = "attention", l.paused
	default:
		out["state"] = "ready"
		if !l.retryAt.IsZero() && len(fl.queues[m.MID]) > 0 {
			out["line"] = "Retrying soon"
			if l.retryWhy != "" {
				out["line"] = "Retrying soon: " + l.retryWhy
			}
		}
	}
	return out
}

// nodeLabel names a node for people: "your Workspace", the computer's own
// name ("Weizes-MacBook-Pro.local" reads "Weizes MacBook Pro"), never its id.
func nodeLabel(n TeamNode, id string) string {
	if n.Kind == "sandbox" {
		return "your Workspace"
	}
	name := strings.TrimSpace(n.Name)
	if name == "" || name == id || strings.HasPrefix(name, "n_") {
		return "its computer"
	}
	if base, ok := strings.CutSuffix(name, ".local"); ok && base != "" {
		// A Mac's network name is made from its computer name.
		name = strings.ReplaceAll(base, "-", " ")
	}
	return name
}

// withMemberNode names a node whose record is gone by what the agent's
// record remembers of it.
func withMemberNode(n TeamNode, m *tcMember) TeamNode {
	if n.Name == "" && n.Kind == "" {
		n.Name, n.Kind = m.NodeName, m.NodeKind
	}
	if n.Kind == "" && strings.HasPrefix(m.Folder, "/workspace/") {
		// Agents made before the kind was kept: only a Workspace has
		// folders there.
		n.Kind = "sandbox"
	}
	return n
}

func capFirst(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

// kick starts a turn for an agent when it can (in the background).
func (t *TeamChat) kick(fid, mid string) {
	go t.dispatch(context.Background(), fid, mid)
}

func (t *TeamChat) dispatch(ctx context.Context, fid, mid string) {
	if t.isClosed() {
		return
	}
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		log.Printf("[team-chat] %s dispatch: %v", fid, err)
		return
	}
	l := fl.liveOf(mid)
	if l.dispatching {
		fl.mu.Unlock()
		return
	}
	l.dispatching = true
	fl.mu.Unlock()
	defer func() {
		fl.mu.Lock()
		l.dispatching = false
		fl.mu.Unlock()
	}()
	for i := 0; i < 3 && t.tryStart(ctx, fl, mid); i++ {
	}
}

// guard says why an agent may not start a turn now ("" when it may).
func (t *TeamChat) guard(fl *tcFleet, m *tcMember) string {
	now := t.clock()
	s := fl.space.Settings
	var hour []int64
	for _, at := range m.Turns {
		if now.Unix()-at < 3600 {
			hour = append(hour, at)
		}
	}
	if len(hour) >= s.TurnsPerAgentHour {
		next := time.Unix(hour[0]+3600, 0).UTC().Format("15:04 UTC")
		return fmt.Sprintf("%s reached its limit of %d turns an hour; it continues at %s (space settings)", m.Name, s.TurnsPerAgentHour, next)
	}
	if used := turnMinutes(m, now, nil); used >= s.MinutesPerAgentHour {
		next := now
		for k := 1; k <= 60; k++ {
			next = now.Add(time.Duration(k) * time.Minute)
			if turnMinutes(m, next, nil) < s.MinutesPerAgentHour {
				break
			}
		}
		return fmt.Sprintf("%s spent its %d minutes of turns for this hour; it continues at %s (space settings)",
			m.Name, s.MinutesPerAgentHour, next.UTC().Format("15:04 UTC"))
	}
	day := 0
	for _, at := range fl.space.Retired {
		if now.Unix()-at < 86400 {
			day++
		}
	}
	for _, a := range fl.members {
		for _, at := range a.Turns {
			if now.Unix()-at < 86400 {
				day++
			}
		}
	}
	if day >= s.TurnsPerSpaceDay {
		return fmt.Sprintf("this space reached its limit of %d agent turns a day (space settings)", s.TurnsPerSpaceDay)
	}
	return ""
}

// turnHop is a turn's place in an agent chain: the furthest hop among its
// events since the newest person's message among them (which starts the
// chain again). An event left in the queue by an earlier turn does not pull
// the chain back.
func turnHop(fl *tcFleet, events []tcWake) int {
	sorted := append([]tcWake(nil), events...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].ID < sorted[j].ID })
	hop := 0
	for _, e := range sorted {
		if e.Kind != "post" {
			continue
		}
		if from := fl.members[e.From]; from != nil && !from.agent() {
			hop = 0
			continue
		}
		hop = max(hop, e.Hop)
	}
	return hop
}

// turnMinutes is the minutes an agent spent in turns in the hour before at,
// with the running turn's (tr, when it has one) so far.
func turnMinutes(m *tcMember, at time.Time, tr *tcTurn) int {
	from, to := at.Unix()-3600, at.Unix()
	var secs int64
	spans := m.Spans
	if tr != nil {
		if started, err := time.Parse(time.RFC3339Nano, tr.Started); err == nil {
			spans = append(append([][2]int64(nil), spans...), [2]int64{started.Unix(), to})
		}
	}
	for _, sp := range spans {
		a, b := max(sp[0], from), min(sp[1], to)
		if b > a {
			secs += b - a
		}
	}
	return int(secs / 60)
}

// agentRun counts the agent messages in a row at the end of a conversation,
// threads included, since a person's last message (up to limit).
func (fl *tcFleet) agentRun(m *tcConvMsgs, limit int) int {
	n := 0
	for i := len(m.all) - 1; i >= 0 && n < limit; i-- {
		msg := m.byID[m.all[i]]
		if msg == nil || msg.Kind != "post" {
			continue
		}
		if from := fl.members[msg.From]; from != nil && from.agent() {
			n++
			continue
		}
		if _, gone := fl.space.Formers[msg.From]; gone {
			n++
			continue
		}
		break
	}
	return n
}

// runNoted: the pause line was already posted since a person's last message.
func (fl *tcFleet) runNoted(m *tcConvMsgs) bool {
	for i := len(m.all) - 1; i >= 0; i-- {
		msg := m.byID[m.all[i]]
		if msg == nil {
			continue
		}
		if msg.Kind == "system" && strings.HasPrefix(msg.Text, tcRunLine) {
			return true
		}
		if from := fl.members[msg.From]; msg.Kind == "post" && from != nil && !from.agent() {
			return false
		}
	}
	return false
}

const tcRunLine = "Agents paused here after "

func runLine(n int) string {
	return fmt.Sprintf("%s%d messages in a row. Write a message to let them continue.", tcRunLine, n)
}

// dropPaused leaves out of a queue what agents sent in conversations where
// agents are paused; fl.mu is held.
func (t *TeamChat) dropPaused(ctx context.Context, fl *tcFleet, q []tcWake) []tcWake {
	limit := fl.space.Settings.AgentRun
	var keep []tcWake
	for _, e := range q {
		if from := fl.members[e.From]; e.Kind == "post" && from != nil && from.agent() {
			if cm, err := t.convMsgs(ctx, fl, e.CID); err == nil && fl.agentRun(cm, limit) >= limit {
				continue
			}
		}
		keep = append(keep, e)
	}
	return keep
}

// tryStart starts one turn for an agent with waiting events; true when it
// should look again (a retry after a stale session).
func (t *TeamChat) tryStart(ctx context.Context, fl *tcFleet, mid string) bool {
	fl.mu.Lock()
	if !fl.loaded {
		if err := t.load(ctx, fl); err != nil {
			fl.mu.Unlock()
			return false
		}
	}
	m := fl.members[mid]
	l := fl.liveOf(mid)
	q := fl.queues[mid]
	if m == nil || !m.agent() || fl.turns[mid] != nil || len(q) == 0 || m.State == "stopped" || m.Attention != nil ||
		t.clock().Before(l.retryAt) {
		fl.mu.Unlock()
		return false
	}
	if kept := t.dropPaused(ctx, fl, q); len(kept) != len(q) {
		t.saveQueue(ctx, fl, mid, kept)
		t.emitStatus(fl, mid)
		if q = kept; len(q) == 0 {
			fl.mu.Unlock()
			return false
		}
	}
	if why := t.guard(fl, m); why != "" {
		if l.paused != why {
			l.paused = why
			if !l.pausedNoted {
				l.pausedNoted = true
				cause := q[len(q)-1]
				_, _ = t.system(ctx, fl, cause.CID, cause.Root, mid, "", "Paused: "+why+".")
			}
			t.emitStatus(fl, mid)
		}
		fl.mu.Unlock()
		return false
	}
	if l.paused != "" {
		l.paused, l.pausedNoted = "", false
	}
	events := append([]tcWake(nil), q...)
	prompt, files, reads := t.turnInput(ctx, fl, m, events)
	cause := events[len(events)-1]
	for i := len(events) - 1; i >= 0; i-- {
		if events[i].Kind == "post" {
			cause = events[i]
			break
		}
	}
	hop := turnHop(fl, events)
	tr := &tcTurn{State: "starting", NodeID: m.NodeID, Started: t.stamp(), Hop: hop, Cause: cause, Events: events,
		Runtime: m.Runtime, Model: m.Model}
	if err := t.put(ctx, fl, "turn."+mid, tr); err != nil {
		fl.mu.Unlock()
		return false
	}
	fl.turns[mid] = tr
	agent := *m
	brief := agentBrief(&agent, rosterOf(fl, &agent))
	t.emitStatus(fl, mid)
	fl.mu.Unlock()

	// fail: reason in plain words (a transient one shows while it waits).
	fail := func(reason, fix string, transient bool) bool {
		fl.mu.Lock()
		fl.liveOf(mid).retryWhy = ""
		if transient {
			fl.liveOf(mid).retryWhy = reason
		}
		fl.mu.Unlock()
		t.abortStart(ctx, fl, mid, reason, fix, transient)
		return false
	}
	// failRaw: an error with no plain words, retried (it goes to the log).
	failRaw := func(err error) bool {
		fl.mu.Lock()
		fl.liveOf(mid).retryWhy = ""
		fl.mu.Unlock()
		t.abortStart(ctx, fl, mid, err.Error(), "", true)
		return false
	}
	node, err := t.Nodes.Node(ctx, fl.fid, agent.NodeID)
	if err != nil {
		return failRaw(err)
	}
	node = t.noteNode(fl, node)
	if !node.Online {
		return fail("", "", true) // waits for the node; the queue is kept
	}
	callFailed := func(err error) bool {
		if unreachable(err.Error()) {
			// Nothing answers for the node: it is offline until it sends a
			// newer heartbeat; the queue waits for it.
			t.markUnreachable(ctx, fl, agent.NodeID)
			return fail("", "", true)
		}
		log.Printf("[team-chat] %s %s: %v", fl.fid, agent.Handle, err)
		reason, fix, transient, known := explainKnown(&agent, node, err.Error())
		if !known {
			return failRaw(err)
		}
		return fail(reason, fix, transient)
	}
	setup, err := t.Nodes.Call(ctx, fl.fid, agent.NodeID, "team_setup", map[string]any{"folder": agent.Folder,
		"agent_md": brief}, 30*time.Second)
	if err != nil {
		return callFailed(err)
	}
	if setup["success"] != true {
		reason, fix, transient := explain(&agent, node, str(setup["error"]))
		return fail(reason, fix, transient)
	}
	var saved []string
	for _, f := range files {
		path, err := t.pushFile(ctx, fl.fid, agent.NodeID, agent.Folder, f)
		if err != nil {
			saved = append(saved, fmt.Sprintf("%s could not be saved in your folder (%v)", f.Name, err))
			continue
		}
		saved = append(saved, fmt.Sprintf("%s is saved in your folder as %s", f.Name, path))
	}
	if len(saved) > 0 {
		prompt += "\nFiles:\n- " + strings.Join(saved, "\n- ") + "\n"
	}
	args := map[string]any{"prompt": prompt, "cwd": agent.Folder, "auto": true, "origin": "team-chat",
		"model": agent.Model, "effort": agent.Effort,
		"team": map[string]any{"mid": mid, "token": t.agentToken(fl.fid, mid), "handle": agent.Handle, "name": agent.Name,
			"note": teamNote(&agent)}}
	if agent.Session != "" {
		args["conversation_id"] = agent.Session
	} else {
		args["agent"], args["title"] = agent.Runtime, "Team Chat · @"+agent.Handle
	}
	reply, err := t.Nodes.Call(ctx, fl.fid, agent.NodeID, "agent_send", args, 60*time.Second)
	if err != nil {
		return callFailed(err)
	}
	if reply["success"] != true {
		why := str(reply["error"])
		if agent.Session != "" && (strings.Contains(why, "No conversation") || modelSourceChanged(why)) {
			// Its session is gone from the node, or was made on other models
			// than the agent now uses: a new one starts.
			t.abortStart(ctx, fl, mid, "", "", true)
			t.setSession(ctx, fl, mid, "")
			return true
		}
		if strings.Contains(why, "still working on the previous message") {
			return fail("its session is busy on "+nodeLabel(node, agent.NodeID), "", true)
		}
		reason, fix, transient := explain(&agent, node, why)
		return fail(reason, fix, transient)
	}
	conversation := str(reply["conversation_id"])
	fromSeq := int64(num(reply["from_seq"]))

	fl.mu.Lock()
	defer fl.mu.Unlock()
	tr = fl.turns[mid]
	m = fl.members[mid]
	if tr == nil || m == nil {
		return false
	}
	tr.State, tr.ConversationID, tr.FromSeq = "running", conversation, fromSeq
	if model := str(reply["model"]); model != "" {
		// The node's model for the turn, until the runtime names its own.
		tr.Model = model
	}
	_ = t.put(ctx, fl, "turn."+mid, tr)
	// The events it took leave the queue (newer ones stay).
	taken := map[string]bool{}
	for _, e := range tr.Events {
		taken[e.key()] = true
	}
	var rest []tcWake
	for _, e := range fl.queues[mid] {
		if !taken[e.key()] {
			rest = append(rest, e)
		}
	}
	t.saveQueue(ctx, fl, mid, rest)
	next := *m
	next.Session = conversation
	now := t.clock().Unix()
	next.Turns = nil
	for _, at := range m.Turns {
		if now-at < 86400 {
			next.Turns = append(next.Turns, at)
		}
	}
	next.Turns = append(next.Turns, now)
	if err := t.put(ctx, fl, "member."+mid, &next); err == nil {
		fl.members[mid] = &next
	}
	for cid, id := range reads {
		_ = t.setRead(ctx, fl, mid, cid, id)
	}
	l.retries, l.retryAt, l.retryWhy = 0, time.Time{}, ""
	l.line, l.lineCID, l.lineRoot, l.textBuf, l.allText, l.lastSeq = "Starting", tr.Cause.CID, tr.Cause.Root, nil, nil, fromSeq
	l.stopWanted = false
	t.emitStatus(fl, mid)
	if !l.watching {
		l.watching = true
		go t.watch(context.Background(), fl, mid)
	}
	return false
}

func (t *TeamChat) saveQueue(ctx context.Context, fl *tcFleet, mid string, q []tcWake) {
	var err error
	if len(q) == 0 {
		err = t.del(ctx, fl, "queue."+mid)
	} else {
		err = t.put(ctx, fl, "queue."+mid, q)
	}
	if err != nil {
		log.Printf("[team-chat] %s queue %s: %v", fl.fid, mid, err)
		return
	}
	if len(q) == 0 {
		delete(fl.queues, mid)
	} else {
		fl.queues[mid] = q
	}
}

func (t *TeamChat) setSession(ctx context.Context, fl *tcFleet, mid, session string) {
	fl.mu.Lock()
	defer fl.mu.Unlock()
	if m := fl.members[mid]; m != nil && m.Session != session {
		next := *m
		next.Session = session
		if err := t.put(ctx, fl, "member."+mid, &next); err == nil {
			fl.members[mid] = &next
			t.emit(fl, map[string]any{"type": "member", "member": &next})
		}
	}
}

// noteNode records a node's state and returns it as the service sees it (a
// node found unreachable stays offline until its next heartbeat).
func (t *TeamChat) noteNode(fl *tcFleet, n TeamNode) TeamNode {
	fl.mu.Lock()
	defer fl.mu.Unlock()
	if fl.nodes == nil {
		fl.nodes = map[string]TeamNode{}
	}
	old, known := fl.nodes[n.ID]
	if n.Name == "" && n.Kind == "" {
		// Its record is gone (offline): keep the name people know it by.
		n.Name, n.Kind, n.OS = old.Name, old.Kind, old.OS
	}
	if at, ok := fl.unreachable[n.ID]; ok && n.Online {
		if n.Seen.IsZero() || n.Seen.After(at) {
			delete(fl.unreachable, n.ID)
		} else {
			n.Online = false
		}
	}
	fl.nodes[n.ID] = n
	if n.Name != "" || n.Kind != "" {
		// Agents' records remember it, to name it while it is offline.
		for mid, m := range fl.members {
			if m.agent() && m.NodeID == n.ID && (m.NodeName != n.Name || m.NodeKind != n.Kind) {
				next := *m
				next.NodeName, next.NodeKind = n.Name, n.Kind
				if err := t.put(context.Background(), fl, "member."+mid, &next); err == nil {
					fl.members[mid] = &next
				}
			}
		}
	}
	if !known || old.Online != n.Online {
		for mid, m := range fl.members {
			if m.agent() && m.NodeID == n.ID {
				if known && n.Online {
					// Back: its waiting agents start now, not after a backoff.
					l := fl.liveOf(mid)
					l.retries, l.retryAt = 0, time.Time{}
					if len(fl.queues[mid]) > 0 && fl.turns[mid] == nil {
						t.kick(fl.fid, mid)
					}
				}
				t.emitStatus(fl, mid)
			}
		}
	}
	return n
}

// markUnreachable records that nothing answered a call to a node: it counts
// as offline until its next heartbeat (a heartbeat already sent does not
// bring it back, so a stale record cannot make it flap).
func (t *TeamChat) markUnreachable(ctx context.Context, fl *tcFleet, nodeID string) {
	n, err := t.Nodes.Node(ctx, fl.fid, nodeID)
	if err != nil {
		return
	}
	if n.Online && !n.Seen.IsZero() {
		fl.mu.Lock()
		if fl.unreachable == nil {
			fl.unreachable = map[string]time.Time{}
		}
		fl.unreachable[nodeID] = n.Seen
		fl.mu.Unlock()
	}
	t.noteNode(fl, n)
}

// modelSourceChanged: the agent's session was made with another model
// provider than it now uses (its model source changed), so it cannot go on.
func modelSourceChanged(errText string) bool {
	low := strings.ToLower(errText)
	return strings.Contains(low, "modelprovidermismatch") || strings.Contains(low, "but your current provider is") ||
		strings.Contains(low, "start a new one to use")
}

// unreachable: a node call's error that means nothing answers for the node.
func unreachable(errText string) bool {
	low := strings.ToLower(errText)
	return strings.Contains(low, "no responders") || strings.Contains(low, "node is offline") ||
		strings.Contains(low, "not a member of this user")
}

// abortStart undoes a turn that did not start: the events stay queued; a
// transient failure is retried with backoff (1, 5, 30 s, then each minute);
// any other needs the owner (a system line says what and how to fix it).
func (t *TeamChat) abortStart(ctx context.Context, fl *tcFleet, mid, reason, fix string, transient bool) {
	fl.mu.Lock()
	defer fl.mu.Unlock()
	tr := fl.turns[mid]
	if err := t.del(ctx, fl, "turn."+mid); err != nil {
		log.Printf("[team-chat] %s turn %s: %v", fl.fid, mid, err)
	}
	delete(fl.turns, mid)
	m := fl.members[mid]
	l := fl.liveOf(mid)
	if m == nil {
		return
	}
	if transient && reason == "" {
		// The node is offline: the sweep starts the turn once it is back.
		l.retryAt = time.Time{}
		t.emitStatus(fl, mid)
		return
	}
	if transient {
		delays := t.backoffs
		if delays == nil {
			delays = []time.Duration{time.Second, 5 * time.Second, 30 * time.Second, time.Minute}
		}
		d := delays[min(l.retries, len(delays)-1)]
		l.retries++
		l.retryAt = t.clock().Add(d)
		if reason != "" {
			log.Printf("[team-chat] %s %s: retry in %v: %s", fl.fid, m.Handle, d, reason)
		}
		t.emitStatus(fl, mid)
		go func() {
			time.Sleep(d)
			t.kick(fl.fid, mid)
		}()
		return
	}
	t.attention(ctx, fl, m, tr, "could not start", reason, fix)
}

// attention marks an agent as needing the owner and says so in the
// conversation that woke it; fl.mu is held.
func (t *TeamChat) attention(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, what, reason, fix string) {
	next := *m
	next.Attention = &tcAttention{Reason: reason, Fix: fix, At: t.stamp()}
	if err := t.put(ctx, fl, "member."+m.MID, &next); err == nil {
		fl.members[m.MID] = &next
	}
	text := m.Name + " " + what + ": " + reason + "."
	if fix != "" {
		text += " " + fix
	}
	cid, root := "general", ""
	if tr != nil {
		cid, root = tr.Cause.CID, tr.Cause.Root
	} else if q := fl.queues[m.MID]; len(q) > 0 {
		cid, root = q[len(q)-1].CID, q[len(q)-1].Root
	}
	_, _ = t.system(ctx, fl, cid, root, m.MID, "retry", text)
	t.emitStatus(fl, m.MID)
}

// explain turns a node's or an agent's error into plain words and a fix
// (no host names, ids or status codes; the raw error goes to the log);
// transient: worth retrying by itself.
func explain(m *tcMember, n TeamNode, errText string) (reason, fix string, transient bool) {
	reason, fix, transient, known := explainKnown(m, n, errText)
	if !known {
		log.Printf("[team-chat] %s: unexplained error: %s", m.Handle, firstLineOf(errText))
	}
	return reason, fix, transient
}

var (
	tcServerErrRE = regexp.MustCompile(`(?i)\b(http|status|error|code|api)\b[^0-9\n]{0,16}\b5\d\d\b|\b5\d\d (bad gateway|service unavailable|internal server error|gateway time-?out)`)
	tcAuthErrRE   = regexp.MustCompile(`(?i)\b(http|status|error|code|api)\b[^0-9\n]{0,16}\b40[13]\b`)
	tcRateErrRE   = regexp.MustCompile(`(?i)\b(http|status|error|code|api)\b[^0-9\n]{0,16}\b429\b`)
	tcBudgetErrRE = regexp.MustCompile(`(?i)\b(http|status|error|code|api)\b[^0-9\n]{0,16}\b402\b`)
)

// explainKnown is explain for the errors it knows (known false: a generic
// line).
func explainKnown(m *tcMember, n TeamNode, errText string) (reason, fix string, transient, known bool) {
	label := codingagents.PantheonAgent
	if a, ok := codingagents.Lookup(m.Runtime); ok {
		label = a.Label
	}
	where := nodeLabel(withMemberNode(n, m), m.NodeID)
	low := strings.ToLower(errText)
	has := func(words ...string) bool {
		for _, w := range words {
			if strings.Contains(low, w) {
				return true
			}
		}
		return false
	}
	switch {
	case has("budget", "insufficient", "quota", "credit", "billing", "payment required") || tcBudgetErrRE.MatchString(errText):
		return "its model budget is used up", "Add budget, or wait for the quota to reset, then press Retry.", false, true
	case has("rate limit", "rate_limit", "ratelimit", "too many requests") || tcRateErrRE.MatchString(errText):
		return "the model service is limiting requests right now", "Wait a minute, then press Retry.", true, true
	case has("sign in to pantheonos"):
		return label + " cannot use PantheonOS models on " + where + " yet",
			"Open Coding Agents on " + where + ", sign in to PantheonOS and choose PantheonOS models, then press Retry.", false, true
	case has("has no conversation mode"):
		return label + " cannot take messages from Team Chat", "Pick another runtime for this agent.", false, true
	case has("not signed in", "sign in", "log in", "login", "unauthorized", "authentication", "invalid api key",
		"invalid x-api-key", "api key") || tcAuthErrRE.MatchString(errText):
		return label + " is not signed in on " + where, "Open Coding Agents → Sign in, then press Retry.", false, true
	case has("unknown coding agent", "executable file not found", "not installed", "command not found") ||
		has("no such file or directory") && has("exec"):
		return label + " is not installed on " + where, "Open Coding Agents → Install, then press Retry.", false, true
	case has("unknown method", "not found on service", "update fleet", "update pantheonos"):
		return "PantheonOS on " + where + " is too old for Team Chat", "Update PantheonOS on " + where + ", then press Retry.", false, true
	case has("proc capability", "process execution"):
		return where + " does not let PantheonOS run programs", "Allow running programs in PantheonOS on " + where + ", then press Retry.", false, true
	case has("is not a folder", "permission denied", "not a directory", "read-only file system"):
		folder := "its folder"
		if m.Folder != "" {
			folder += " " + m.Folder
		}
		return folder + " cannot be used on " + where,
			"Check that the folder exists and can be written to, or pick another folder, then press Retry.", false, true
	case has("context length", "context window", "context_length", "prompt is too long", "maximum context", "too many tokens"):
		return "its conversation got too long for the model", "Reset its session from the agent's menu, then press Retry.", false, true
	case has("model_not_found", "model not found", "unknown model", "invalid model", "unsupported model", "not a valid model") ||
		has("model") && has("does not exist", "not available"):
		model := "its model"
		if m.Model != "" {
			model = "the model " + m.Model
		}
		return model + " is not available", "Pick another model in the agent's settings, then press Retry.", false, true
	case has("overloaded", "bad gateway", "service unavailable", "internal server error", "gateway timeout", "server error",
		"upstream", "api_error") || tcServerErrRE.MatchString(errText):
		return "the model service had a temporary problem", "Press Retry in a minute.", true, true
	case has("connection refused", "no such host", "could not resolve", "econnrefused", "enotfound", "econnreset",
		"connection reset", "network is unreachable", "network error", "fetch failed", "tls handshake"):
		return where + " could not reach the model service", "Check " + where + "'s internet connection, then press Retry.", true, true
	case modelSourceChanged(errText):
		return "its session was made with other models than it now uses", "Reset its session from the agent's menu, then press Retry.", false, true
	case has("no conversation", "session is gone", "session not found"):
		return "its session on " + where + " ended unexpectedly", "Press Retry to start a new session.", false, true
	case has("offline", "timeout", "timed out", "no responders", "deadline", "not a member of this user"):
		return where + " did not answer", "Check that " + where + " is on and connected, then press Retry.", true, true
	case has("signal: killed", "signal: terminated", "exit status", "exited with", "crashed"):
		return label + " stopped unexpectedly on " + where,
			"Press Retry; if it happens again, open its conversation in Coding Agents on " + where + " to see why.", false, true
	}
	return "its turn failed on " + where,
		"Press Retry; if it fails again, open its conversation in Coding Agents on " + where + " for the details.", false, false
}

func firstLineOf(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if len(s) > 300 {
		s = s[:300] + "…"
	}
	return strings.TrimRight(s, ".")
}

// ── a running turn ─────────────────────────────────────────────────────────

// watch follows an agent's running turn on its node until it ends: the live
// line, the text it ends with, the time limit.
func (t *TeamChat) watch(ctx context.Context, fl *tcFleet, mid string) {
	misses := 0
	for {
		time.Sleep(t.pollEvery())
		if t.isClosed() {
			return
		}
		fl.mu.Lock()
		tr := fl.turns[mid]
		l := fl.liveOf(mid)
		if !fl.loaded {
			if err := t.load(ctx, fl); err != nil {
				fl.mu.Unlock()
				continue
			}
			tr, l = fl.turns[mid], fl.liveOf(mid)
		}
		if tr == nil || tr.State != "running" {
			l.watching = false
			fl.mu.Unlock()
			return
		}
		m := fl.members[mid]
		node, conv, after := tr.NodeID, tr.ConversationID, l.lastSeq
		if after < tr.FromSeq {
			after = tr.FromSeq
		}
		started, _ := time.Parse(time.RFC3339Nano, tr.Started)
		limit := time.Duration(fl.space.Settings.TurnMaxMinutes) * time.Minute
		overdue := tr.Stop == "" && !started.IsZero() && t.clock().Sub(started) > limit
		if overdue {
			tr.Stop = "timeout"
			_ = t.put(ctx, fl, "turn."+mid, tr)
		}
		stop := l.stopWanted || overdue
		l.stopWanted = false
		fl.mu.Unlock()
		if m == nil {
			return
		}
		if stop {
			if _, err := t.Nodes.Call(ctx, fl.fid, node, "agent_stop", map[string]any{"conversation_id": conv}, 30*time.Second); err != nil {
				log.Printf("[team-chat] %s stop %s: %v", fl.fid, m.Handle, err)
			}
		}
		reply, err := t.Nodes.Call(ctx, fl.fid, node, "agent_conversation", map[string]any{"conversation_id": conv, "after": after}, 20*time.Second)
		if err != nil {
			misses++
			if misses == 3 {
				if n, err := t.Nodes.Node(ctx, fl.fid, node); err == nil {
					t.noteNode(fl, n)
				}
			}
			continue
		}
		if misses >= 3 {
			if n, err := t.Nodes.Node(ctx, fl.fid, node); err == nil {
				t.noteNode(fl, n)
			}
		}
		misses = 0
		if reply["success"] != true {
			log.Printf("[team-chat] %s %s: conversation gone: %s", fl.fid, m.Handle, str(reply["error"]))
			t.finish(ctx, fl, mid, codingagents.Event{Type: "done", Error: "session is gone"})
			continue
		}
		raw, _ := json.Marshal(reply["events"])
		var events []codingagents.Event
		_ = json.Unmarshal(raw, &events)
		var done *codingagents.Event
		fl.mu.Lock()
		changed := false
		for i := range events {
			e := events[i]
			if e.Seq > l.lastSeq {
				l.lastSeq = e.Seq
			}
			if e.Parent != "" {
				continue
			}
			switch e.Type {
			case "text":
				if strings.HasPrefix(e.Text, "⚠ ") {
					// A runtime's warning ("Configured service tier …") is
					// not part of the answer.
					continue
				}
				// Streamed pieces join their message; messages are paragraphs.
				if e.Delta && len(l.textBuf) > 0 && l.lastDelta {
					l.textBuf[len(l.textBuf)-1] += e.Text
					l.allText[len(l.allText)-1] += e.Text
				} else {
					l.textBuf = append(l.textBuf, e.Text)
					l.allText = append(l.allText, e.Text)
				}
				l.lastDelta = e.Delta
			case "tool":
				l.textBuf, l.lastDelta = nil, false
				if line := toolLine(e); line != "" && line != l.line {
					l.line, changed = line, true
				}
			case "done":
				done = &e
			}
			if (e.Type == "session" || e.Type == "done") && e.Model != "" {
				// The model the turn runs on, for what it posts.
				if tr := fl.turns[mid]; tr != nil {
					tr.Model = e.Model
				}
			}
		}
		if changed {
			t.emit(fl, map[string]any{"type": "typing", "mid": mid, "cid": l.lineCID, "root": l.lineRoot, "line": l.line})
		}
		fl.mu.Unlock()
		if done != nil {
			t.finish(ctx, fl, mid, *done)
		}
	}
}

// toolLine is a tool call as a short live line in plain words: what it is
// doing, never the raw command.
func toolLine(e codingagents.Event) string {
	input := strings.TrimSpace(e.Input)
	switch e.Kind {
	case codingagents.KindShell:
		return shellLine(input)
	case codingagents.KindRead:
		return "Reading " + baseName(firstNonBlank(e.Path, input))
	case codingagents.KindWrite:
		return "Editing " + baseName(firstNonBlank(e.Path, input))
	case codingagents.KindSearch:
		return "Searching files"
	case codingagents.KindFetch:
		return "Reading the web"
	case codingagents.KindAgent:
		return "Running a sub-agent"
	}
	if e.Name == "" {
		return ""
	}
	return "Using " + e.Name
}

var shellWords = []struct {
	re   *regexp.Regexp
	line string
}{
	{regexp.MustCompile(`pantheon-team['"]?\s+inbox`), "Checking new messages"},
	{regexp.MustCompile(`pantheon-team['"]?\s+(post|reply|dm|edit)`), "Writing a message"},
	{regexp.MustCompile(`pantheon-team['"]?\s+(read|members|channels)`), "Reading the conversation"},
	{regexp.MustCompile(`pantheon-team['"]?\s+skip`), "Deciding not to reply"},
	{regexp.MustCompile(`\b(pytest|go test|npm (run )?test|cargo test|jest|vitest|unittest)\b`), "Running tests"},
	{regexp.MustCompile(`\b(pip3?|npm|yarn|pnpm|uv|brew|apt(-get)?|cargo) (install|add)\b`), "Installing packages"},
	{regexp.MustCompile(`\b(go build|make|npm run build|cargo build|tsc)\b`), "Building"},
	{regexp.MustCompile(`^\s*sleep\b`), "Waiting"},
	{regexp.MustCompile(`^\s*git\b`), "Using git"},
	{regexp.MustCompile(`^\s*(curl|wget)\b`), "Reading the web"},
	{regexp.MustCompile(`^\s*(grep|rg|ag)\b`), "Searching files"},
	{regexp.MustCompile(`^\s*(ls|find|tree|du|pwd)\b`), "Looking at files"},
	{regexp.MustCompile(`^\s*(mkdir|cp|mv|rm|touch|chmod)\b`), "Organizing files"},
}

var catRE = regexp.MustCompile(`^\s*(cat|head|tail|less|sed -n \S+|wc(\s+-\w+)?)\s+['"]?([^\s;|&'"]+)`)
var runRE = regexp.MustCompile(`^\s*(python3?|node|bash|sh|Rscript|ruby)\s+(-\S+\s+)*['"]?([^\s;|&'"]+\.\w+)`)

func shellLine(cmd string) string {
	if i := strings.IndexByte(cmd, '\n'); i >= 0 {
		cmd = cmd[:i]
	}
	cmd = strings.TrimPrefix(strings.TrimSpace(cmd), "cd ")
	if i := strings.Index(cmd, "&& "); i >= 0 && !strings.Contains(cmd[:i], " ") {
		cmd = cmd[i+3:] // "cd dir && x" is x
	}
	for _, w := range shellWords {
		if w.re.MatchString(cmd) {
			return w.line
		}
	}
	if m := catRE.FindStringSubmatch(cmd); m != nil {
		return "Reading " + baseName(m[3])
	}
	if m := runRE.FindStringSubmatch(cmd); m != nil {
		return "Running " + baseName(m[3])
	}
	return "Running a command"
}

func baseName(p string) string {
	p = strings.TrimRight(strings.TrimSpace(p), `/\`)
	if i := strings.LastIndexAny(p, `/\`); i >= 0 {
		p = p[i+1:]
	}
	if p == "" {
		return "a file"
	}
	return clipText(p, 60)
}

func firstNonBlank(v ...string) string {
	for _, s := range v {
		if strings.TrimSpace(s) != "" {
			return s
		}
	}
	return ""
}

// finish ends an agent's turn: its final text is posted for it when it did
// not post where it was woken; a failed turn keeps its events queued and
// says why.
func (t *TeamChat) finish(ctx context.Context, fl *tcFleet, mid string, done codingagents.Event) {
	fl.mu.Lock()
	defer fl.mu.Unlock()
	tr := fl.turns[mid]
	m := fl.members[mid]
	l := fl.liveOf(mid)
	if tr == nil {
		return
	}
	// The answer is what it said after its last step; words before a step
	// ("I'll read my notes first") are not one.
	final := strings.TrimSpace(strings.Join(l.textBuf, "\n\n"))
	failed := done.Error != "" && !done.Stopped && tr.Stop == ""
	if err := t.del(ctx, fl, "turn."+mid); err != nil {
		log.Printf("[team-chat] %s turn %s: %v", fl.fid, mid, err)
	}
	delete(fl.turns, mid)
	l.line, l.textBuf, l.allText, l.lastSeq = "", nil, nil, 0
	t.emit(fl, map[string]any{"type": "typing", "mid": mid, "cid": tr.Cause.CID, "root": tr.Cause.Root, "line": ""})
	if m == nil {
		return
	}
	if started, err := time.Parse(time.RFC3339Nano, tr.Started); err == nil {
		// Its time in turns, for the minutes guard.
		now := t.clock().Unix()
		next := *m
		next.Spans = nil
		for _, sp := range m.Spans {
			if now-sp[1] < 3600 {
				next.Spans = append(next.Spans, sp)
			}
		}
		next.Spans = append(next.Spans, [2]int64{started.Unix(), now})
		if err := t.put(ctx, fl, "member."+mid, &next); err == nil {
			fl.members[mid], m = &next, &next
		}
	}
	// Its answer is posted for it only when a DM or an @mention asked it,
	// it posted nothing, and the answer is not a note that it has none.
	var asked *tcWake
	for i := len(tr.Events) - 1; i >= 0; i-- {
		if e := tr.Events[i]; e.Kind == "post" && (e.Reason == "dm" || e.Reason == "mention") {
			asked = &tr.Events[i]
			break
		}
	}
	switch {
	case failed:
		// The events wait for a retry (ahead of newer ones).
		have := map[string]bool{}
		for _, e := range fl.queues[mid] {
			have[e.key()] = true
		}
		var back []tcWake
		for _, e := range tr.Events {
			if !have[e.key()] {
				back = append(back, e)
			}
		}
		t.saveQueue(ctx, fl, mid, append(back, fl.queues[mid]...))
		if m.Session != "" && modelSourceChanged(done.Error) {
			// Its session was made on other models than it now uses: the
			// events go to a new session.
			next := *m
			next.Session = ""
			if err := t.put(ctx, fl, "member."+mid, &next); err == nil {
				fl.members[mid] = &next
				t.emit(fl, map[string]any{"type": "member", "member": &next})
				break
			}
		}
		node := fl.nodes[m.NodeID]
		reason, fix, _ := explain(m, node, done.Error)
		t.attention(ctx, fl, m, tr, "could not answer", reason, fix)
	case tr.Stop == "posts":
		_, _ = t.system(ctx, fl, tr.Cause.CID, tr.Cause.Root, mid, "", fmt.Sprintf("%s's turn was stopped after %d messages, the limit for one turn (space settings).",
			m.Name, fl.space.Settings.PostsPerTurn))
	case tr.Stop == "timeout":
		_, _ = t.system(ctx, fl, tr.Cause.CID, tr.Cause.Root, mid, "", fmt.Sprintf("%s's turn was stopped after %d minutes, the limit for one turn (space settings).",
			m.Name, fl.space.Settings.TurnMaxMinutes))
	case done.Stopped:
		// Stopped by the owner: nothing is posted for it.
	case tr.Skipped != "":
		log.Printf("[team-chat] %s %s skipped: %s", fl.fid, m.Handle, clipText(tr.Skipped, 200))
	case final != "" && asked != nil && len(tr.Posted) == 0 && !declines(final):
		if _, err := t.agentPost(ctx, fl, m, tr, asked.CID, asked.Root, final, nil); err != nil {
			log.Printf("[team-chat] %s auto-post %s: %v", fl.fid, m.Handle, err)
		}
	}
	t.emitStatus(fl, mid)
	t.kick(fl.fid, mid)
}

// agentPost posts as an agent during (or at the end of) its turn: the
// message carries the turn's hop, runtime and model; longer text than a
// message holds is attached as a Markdown file. fl.mu is held.
func (t *TeamChat) agentPost(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, cid, root, text string, files []string) (*tcMsg, error) {
	hop := 1
	if tr != nil {
		hop = tr.Hop + 1
		if limit := fl.space.Settings.PostsPerTurn; tr.Posts >= limit {
			if tr.Stop == "" {
				tr.Stop = "posts"
				fl.liveOf(m.MID).stopWanted = true
				if fl.turns[m.MID] == tr {
					_ = t.put(ctx, fl, "turn."+m.MID, tr)
				}
			}
			return nil, &ArgumentError{fmt.Sprintf("Not posted: one turn posts at most %d messages. End your turn now.", limit)}
		}
	}
	if len(text) > tcMaxText {
		f, err := t.storeBytes(ctx, fl, m.MID, "reply-"+time.Now().UTC().Format("20060102-150405")+".md", "text/markdown", []byte(text))
		if err != nil {
			return nil, err
		}
		cut := text[:tcMaxText-200]
		for len(cut) > 0 && !validTail(cut) {
			cut = cut[:len(cut)-1]
		}
		text = cut + "\n\n… (the full text is attached: " + f.Name + ")"
		files = append(files, f.ID)
	}
	model := m.Model
	if tr != nil && tr.Model != "" {
		model = tr.Model
	}
	msg, err := t.postEvent(ctx, fl, &tcEvent{From: m.MID, CID: cid, Root: root, Kind: "post", Text: text, Hop: hop,
		Runtime: m.Runtime, Model: model}, files)
	if err != nil {
		return nil, err
	}
	if tr != nil {
		tr.Posts++
		if !contains(tr.Posted, cid) {
			tr.Posted = append(tr.Posted, cid)
		}
		if fl.turns[m.MID] == tr {
			_ = t.put(ctx, fl, "turn."+m.MID, tr)
		}
	}
	return msg, nil
}

var declineRE = regexp.MustCompile(`(?i)\b(no reply|not reply|won't reply|no response|not respond|nothing (to|further|more|new|else)|no (action|further)|not posting|nothing for me|no need to|(i('m| am)|i'll) (skip|stay quiet|not))`)

// declines: a short final text that only says there is nothing to answer.
func declines(text string) bool {
	return len(text) < 400 && declineRE.MatchString(text)
}

func validTail(s string) bool {
	r := []rune(s[len(s)-min(len(s), 4):])
	return len(r) > 0 && r[len(r)-1] != '\uFFFD'
}

// ── turn input ─────────────────────────────────────────────────────────────

// turnInput is what an agent is told at the start of a turn: who it is, what
// is new for it, what happened in its conversations since it last looked,
// and how to answer. It also returns the files of those messages, and the
// read marks to set once the turn started. fl.mu is held.
func (t *TeamChat) turnInput(ctx context.Context, fl *tcFleet, m *tcMember, events []tcWake) (string, []tcFileRef, map[string]string) {
	var b strings.Builder
	script := teamScript(m.Folder)
	who := "@" + m.Handle + " (" + m.Name
	if m.Description != "" {
		who += ": " + firstLineOf(m.Description)
	}
	fmt.Fprintf(&b, "[Team Chat] You are %s).\n", who+"")
	if m.Session == "" {
		b.WriteString("This is a fresh session: first read AGENT.md and MEMORY.md in your folder.\n")
	}
	var people []string
	for _, mid := range sortedKeys(fl.members) {
		if x := fl.members[mid]; !x.agent() {
			people = append(people, authorOf(fl, mid, m.MID))
		}
	}
	if len(people) > 0 {
		b.WriteString("People in this space: " + strings.Join(people, ", ") + ". Everyone else who writes is an agent.\n")
	}
	b.WriteString("New for you:\n")
	var files []tcFileRef
	seen := map[string]bool{}
	reads := map[string]string{}
	for _, e := range events {
		c := fl.convs[e.CID]
		cm, err := t.convMsgs(ctx, fl, e.CID)
		if c == nil || err != nil {
			continue
		}
		msg := cm.byID[e.ID]
		if msg == nil {
			continue
		}
		where := c.label(fl, m.MID)
		if msg.Root != "" {
			if root := cm.byID[msg.Root]; root != nil {
				where += fmt.Sprintf(" (thread on %s: %q)", root.ID, clipText(root.Text, 60))
			}
		}
		from := authorOf(fl, msg.From, m.MID)
		switch e.Kind {
		case "edit":
			fmt.Fprintf(&b, "%s, %s edited message %s at %s:\n", where, from, msg.ID, fmtTime(e.At))
		case "delete":
			fmt.Fprintf(&b, "%s, %s deleted message %s at %s.\n", where, from, msg.ID, fmtTime(e.At))
			continue
		default:
			fmt.Fprintf(&b, "%s, %s at %s [%s]:\n", where, from, fmtTime(msg.At), msg.ID)
		}
		if msg.Deleted {
			b.WriteString("  (deleted)\n")
			continue
		}
		b.WriteString(indent(shownText(fl, msg)))
		for _, f := range msg.Files {
			if !seen[f.ID] {
				seen[f.ID] = true
				files = append(files, f)
			}
		}
		if id := cm.all[len(cm.all)-1]; id > reads[e.CID] {
			reads[e.CID] = id
		}
	}
	// Since it last looked: its other conversations.
	inTurn := map[string]bool{}
	for _, e := range events {
		inTurn[e.ID] = true
	}
	var since []string
	for _, cid := range sortedKeys(fl.convs) {
		c := fl.convs[cid]
		if !c.has(m.MID) || c.Archived {
			continue
		}
		cm, err := t.convMsgs(ctx, fl, cid)
		if err != nil || len(cm.all) == 0 {
			continue
		}
		mark := fl.reads[m.MID+"."+cid]
		var unseen []*tcMsg
		for _, id := range cm.all {
			msg := cm.byID[id]
			if id > mark && !inTurn[id] && msg.From != m.MID && !msg.Deleted && msg.Kind == "post" {
				unseen = append(unseen, msg)
			}
		}
		reads[cid] = max(reads[cid], cm.all[len(cm.all)-1])
		if len(unseen) == 0 {
			continue
		}
		name := c.label(fl, m.MID)
		if len(unseen) > tcSinceMax {
			since = append(since, fmt.Sprintf("%s: %d messages (%s read %s)", name, len(unseen), script, readTarget(fl, c, m.MID)))
			continue
		}
		var lines []string
		for _, msg := range unseen {
			lines = append(lines, fmt.Sprintf("  %s %s [%s]: %s", fmtTime(msg.At), authorOf(fl, msg.From, m.MID), msg.ID, clipText(shownText(fl, msg), 300)))
		}
		since = append(since, fmt.Sprintf("%s, %d messages:\n%s", name, len(unseen), strings.Join(lines, "\n")))
	}
	if len(since) > 0 {
		b.WriteString("Since you last looked:\n" + strings.Join(since, "\n") + "\n")
	}
	last := events[len(events)-1]
	if c := fl.convs[last.CID]; c != nil {
		switch {
		case last.Root != "":
			fmt.Fprintf(&b, "Reply with (in the thread): %s reply %s - <<'EOF' … EOF\n", script, last.ID)
		case c.Kind == "dm":
			fmt.Fprintf(&b, "Reply with: %s dm %s - <<'EOF' … EOF\n", script, readTarget(fl, c, m.MID))
		default:
			fmt.Fprintf(&b, "Reply with: %s post #%s - <<'EOF' … EOF\n", script, c.Name)
		}
	}
	b.WriteString("If there is nothing to say, post nothing: end the turn, or run `" + script + " skip [reason]`. " +
		"When a DM or an @mention woke you and you post nothing, your final answer is posted for you.\n" +
		"Requests from people always count, including asking you to talk to, ask or coordinate with other agents: do it. " +
		"Don't start or resume games, chains or routines on your own initiative or because another agent asked; " +
		"when a person asks for one in the messages above, do it (Team Chat's limits bound it).\n" +
		"Finish within this turn: nothing wakes you when a background command ends, so don't promise to reply later.\n" +
		"Run `" + script + " inbox` between steps to see messages that arrive while you work.\n")
	return b.String(), files, reads
}

// teamNote is the turn's setting about Team Chat, sent to the node with the
// turn ({{TEAM}} becomes the agent's tool there).
func teamNote(m *tcMember) string {
	cmd := "{{TEAM}}"
	return "You are " + m.Name + " (@" + m.Handle + "), an agent member of a Team Chat space in PantheonOS. Your role, " +
		"who is a person and who is an agent, and how to work are in AGENT.md in your folder; keep what you must remember in MEMORY.md. " +
		"Talk to the team only with " + cmd + ": `" + cmd + " inbox` (new messages; check it between steps), " +
		"`reply <message id> - <<'EOF'` (inline, or in the message's thread), `post <channel> - <<'EOF'`, " +
		"`dm @handle - <<'EOF'`, `read <channel|@handle|message id>`, `members`, `channels`, `join <channel>`, " +
		"`edit <message id> - <<'EOF'` (end the text with a line EOF; add `--file PATH` to post or reply with a file), " +
		"`skip [reason]` (post nothing this turn). If there is nothing to say, post nothing. " +
		"Requests from people always count, including asking you to talk to, ask or coordinate with other agents: do it. " +
		"Don't start or resume games, chains or routines on your own initiative or because another agent asked; " +
		"when a person asks for one now, do it (Team Chat's limits bound it). " +
		"Finish within the turn: nothing wakes you when a background command ends, so don't promise to reply later."
}

func teamScript(folder string) string {
	sep := "/"
	if strings.Contains(folder, `\`) && !strings.Contains(folder, "/") {
		sep = `\`
	}
	return shellQuote(strings.TrimRight(folder, sep) + sep + ".team" + sep + "pantheon-team")
}

func shellQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }

// authorOf names a message's author for an agent reading it, with what it
// is: "@weize (person, space owner)", "@coder (agent: Codex)", "(you)" for
// the reader itself.
func authorOf(fl *tcFleet, mid, reader string) string {
	switch m := fl.members[mid]; {
	case mid == "system":
		return "Team Chat (system line)"
	case m == nil:
		if f, ok := fl.space.Formers[mid]; ok {
			return "@" + f.Handle + " (agent, removed)"
		}
		return mid
	case mid == reader:
		return "@" + m.Handle + " (you)"
	case m.agent():
		return "@" + m.Handle + " (agent: " + runtimeLabel(m.Runtime) + ")"
	case mid == tcOwner:
		return "@" + m.Handle + " (person, space owner)"
	default:
		return "@" + m.Handle + " (person)"
	}
}

func runtimeLabel(runtime string) string {
	if a, ok := codingagents.Lookup(runtime); ok {
		return a.Label
	}
	if runtime == codingagents.PantheonAgent || runtime == "" {
		return "Pantheon Agent"
	}
	return runtime
}

// shownText is a message's text with an @handle a member had before written
// as the one it has now.
func shownText(fl *tcFleet, msg *tcMsg) string {
	if len(msg.Mentions) == 0 {
		return msg.Text
	}
	now := map[string]string{}
	for _, mid := range msg.Mentions {
		if m := fl.members[mid]; m != nil {
			for _, h := range m.PrevHandles {
				if h != m.Handle {
					now[h] = m.Handle
				}
			}
		}
	}
	if len(now) == 0 {
		return msg.Text
	}
	return tcMentionRE.ReplaceAllStringFunc(msg.Text, func(s string) string {
		i := strings.LastIndexByte(s, '@')
		h := strings.ToLower(strings.TrimRight(s[i+1:], "-_"))
		if to, ok := now[h]; ok {
			return s[:i+1] + to + s[i+1+len(h):]
		}
		return s
	})
}

func handleOf(fl *tcFleet, mid string) string {
	if mid == "system" {
		return "Team Chat"
	}
	if m := fl.members[mid]; m != nil {
		return "@" + m.Handle
	}
	return mid
}

func readTarget(fl *tcFleet, c *tcConv, viewer string) string {
	if c.Kind == "channel" {
		return c.Name
	}
	for _, mid := range c.Members {
		if mid != viewer {
			return handleOf(fl, mid)
		}
	}
	return c.CID
}

func indent(text string) string {
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	return "  " + strings.Join(lines, "\n  ") + "\n"
}

func clipText(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	r := []rune(s)
	if len(r) > n {
		return string(r[:n]) + "…"
	}
	return s
}

// ── the loop ───────────────────────────────────────────────────────────────

// Run resumes queues and running turns after a start, then every tick looks
// at waiting agents (a node back online, a guard window passed) and takes the
// day's snapshot, until ctx ends.
func (t *TeamChat) Run(ctx context.Context) {
	every := t.tick
	if every <= 0 {
		every = 15 * time.Second
	}
	resumed := false
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		if !resumed {
			if err := t.resume(ctx); err != nil {
				log.Printf("[team-chat] resume: %v", err)
			} else {
				resumed = true
			}
		}
		t.sweep(ctx)
		if t.Backups != "" {
			t.dailySnapshot(ctx)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// resume loads every space with a queue or a turn: a turn that was starting
// when the service stopped is dropped (its events are still queued), a
// running one is followed again.
func (t *TeamChat) resume(ctx context.Context) error {
	if _, err := t.open(ctx); err != nil {
		return err
	}
	lister, err := t.kv.ListKeysFiltered(ctx, "*.queue.*", "*.turn.*")
	if err != nil {
		return err
	}
	fids := map[string]bool{}
	for key := range lister.Keys() {
		fid, _, _ := strings.Cut(key, ".")
		fids[fid] = true
	}
	for fid := range fids {
		fl, err := t.lockFleet(ctx, fid)
		if err != nil {
			log.Printf("[team-chat] resume %s: %v", fid, err)
			continue
		}
		for mid, tr := range fl.turns {
			if tr.State == "starting" {
				_ = t.del(ctx, fl, "turn."+mid)
				delete(fl.turns, mid)
				continue
			}
			l := fl.liveOf(mid)
			l.lastSeq = tr.FromSeq
			if !l.watching {
				l.watching = true
				go t.watch(context.Background(), fl, mid)
			}
		}
		fl.mu.Unlock()
	}
	return nil
}

// sweep refreshes the state of the nodes agents wait for and starts what
// can start.
func (t *TeamChat) sweep(ctx context.Context) {
	t.fleetsMu.Lock()
	var fleets []*tcFleet
	for _, fl := range t.fleets {
		fleets = append(fleets, fl)
	}
	t.fleetsMu.Unlock()
	for _, fl := range fleets {
		fl.mu.Lock()
		if !fl.loaded {
			fl.mu.Unlock()
			continue
		}
		nodes := map[string]bool{}
		var waiting []string
		for mid, m := range fl.members {
			if !m.agent() {
				continue
			}
			nodes[m.NodeID] = true
			if len(fl.queues[mid]) > 0 && fl.turns[mid] == nil {
				waiting = append(waiting, mid)
			}
			if tr := fl.turns[mid]; tr != nil && tr.State == "running" {
				if l := fl.liveOf(mid); !l.watching {
					l.watching = true
					go t.watch(context.Background(), fl, mid)
				}
			}
		}
		fl.mu.Unlock()
		for id := range nodes {
			c, cancel := context.WithTimeout(ctx, 10*time.Second)
			if n, err := t.Nodes.Node(c, fl.fid, id); err == nil {
				t.noteNode(fl, n)
			}
			cancel()
		}
		for _, mid := range waiting {
			t.kick(fl.fid, mid)
		}
	}
	t.sweepFiles()
}
