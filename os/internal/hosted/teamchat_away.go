package hosted

// An agent's computer away for a while with messages waiting for it: its
// owner is told once per time away (docs/apps/team-chat.md, "Offline
// computers": the nudge).

import (
	"context"
	"fmt"
	"log"
	"net/url"
	"time"
)

// tcNudgeAfter: how long an agent's computer is away, with messages waiting
// for it, before its owner is told.
const tcNudgeAfter = 10 * time.Minute

func (t *TeamChat) nudgeAfter() time.Duration {
	if t.NudgeAfter > 0 {
		return t.NudgeAfter
	}
	return tcNudgeAfter
}

// awayWaiting: an agent's messages waiting for its computer, which is away
// (none while it is online, or the agent is stopped). fl.mu is held.
func (fl *tcFleet) awayWaiting(m *tcMember) (TeamNode, []map[string]any) {
	n, known := fl.nodes[m.NodeID]
	if !known || n.Online || m.State == "stopped" {
		return n, nil
	}
	tr := fl.turns[m.MID]
	return n, waitingOf(fl, m, tr != nil && tr.State == "running")
}

// nudges tells each agent's owner, once per time its computer is away, that
// messages wait for it (an Activity row, its status's `nudge`, a push), keeps
// the count while it waits, and takes it back once the computer is back,
// the agent moved or nothing waits any more.
func (t *TeamChat) nudges(ctx context.Context, fl *tcFleet) {
	type push struct {
		fleet string
		n     TeamPush
	}
	var pushes []push
	fl.mu.Lock()
	now := t.clock()
	for _, mid := range sortedKeys(fl.members) {
		m := fl.members[mid]
		if !m.agent() {
			continue
		}
		l := fl.liveOf(mid)
		node, waiting := fl.awayWaiting(m)
		if len(waiting) == 0 || (l.nudge != nil && l.nudge.Node != m.NodeID) {
			if l.nudge != nil {
				log.Printf("[team-chat] %s %s (%s): nudge taken back (%s)", fl.fid, m.Handle, mid, map[bool]string{true: "nothing waits or it is back", false: "moved"}[l.nudge.Node == m.NodeID])
				l.nudge = nil
				t.emitStatus(fl, mid)
			}
			if len(waiting) == 0 {
				l.awaySeen = time.Time{}
			}
			continue
		}
		since := node.Away
		if since.IsZero() {
			if l.awaySeen.IsZero() {
				l.awaySeen = now
			}
			since = l.awaySeen
		}
		switch {
		case l.nudge == nil && now.Sub(since) >= t.nudgeAfter():
			l.nudge = &tcNudge{At: now, Count: len(waiting), Node: m.NodeID, Since: since}
			label := nodeLabel(withMemberNode(node, m), m.NodeID)
			text := nudgeText(m.Name, len(waiting), label, withMemberNode(node, m).Kind, since, now, spaceLoc(fl))
			log.Printf("[team-chat] %s %s (%s): nudge sent to its owner: %d waiting, %s away since %s", fl.fid, m.Handle, mid,
				len(waiting), m.NodeID, since.UTC().Format(time.RFC3339))
			title := m.Name + " is waiting for " + label
			if withMemberNode(node, m).Kind == "sandbox" {
				title = m.Name + " is waiting for your Cloud Workspace"
			}
			pushes = append(pushes, push{fl.fleetOf(m), TeamPush{Title: title, Body: text,
				URL: "/app/team-chat?" + url.Values{"agent": {mid}}.Encode(), Tag: "team-chat.away." + mid}})
			t.emitStatus(fl, mid)
		case l.nudge != nil && l.nudge.Count != len(waiting):
			// Still away: the count follows (no new push).
			l.nudge.Count = len(waiting)
			t.emitStatus(fl, mid)
		}
	}
	fl.mu.Unlock()
	if t.Push != nil {
		for _, p := range pushes {
			t.Push(p.fleet, p.n)
		}
	}
}

// nudgeText: "2 messages waiting for Tally: acct4-local offline since
// 8:07 PM" ("… your Cloud Workspace is paused" for a Workspace).
func nudgeText(name string, count int, label, kind string, since, now time.Time, loc *time.Location) string {
	what := fmt.Sprintf("%d messages", count)
	if count == 1 {
		what = "1 message"
	}
	if kind == "sandbox" {
		return fmt.Sprintf("%s waiting for %s: your Cloud Workspace is paused", what, name)
	}
	return fmt.Sprintf("%s waiting for %s: %s offline since %s", what, name, label, sinceClock(since, now, loc))
}

// sinceClock: "8:07 PM" today, else "Nov 22, 8:07 PM", in the space's time zone.
func sinceClock(at, now time.Time, loc *time.Location) string {
	a, n := at.In(loc), now.In(loc)
	if a.Year() == n.Year() && a.YearDay() == n.YearDay() {
		return a.Format("3:04 PM")
	}
	return a.Format("Jan 2, 3:04 PM")
}

// nudgeView is a status's `nudge` for its owner.
func nudgeView(n *tcNudge) map[string]any {
	return map[string]any{"at": n.At.UTC().Format(time.RFC3339Nano), "count": n.Count, "since": n.Since.UTC().Format(time.RFC3339)}
}

// awayRows are Activity's rows for the agents of mine whose computer is away
// with messages waiting, once I was told. fl.mu is held.
func (t *TeamChat) awayRows(fl *tcFleet, me string) []map[string]any {
	var rows []map[string]any
	for _, mid := range sortedKeys(fl.members) {
		m := fl.members[mid]
		if !m.agent() || ownerOf(m) != me {
			continue
		}
		l := fl.liveOf(mid)
		if l.nudge == nil {
			continue
		}
		node := withMemberNode(fl.nodes[m.NodeID], m)
		rows = append(rows, map[string]any{"kind": "away", "cid": "", "root": "", "mid": mid, "count": l.nudge.Count, "mentions": 0,
			"node_id": m.NodeID, "computer": nodeLabel(node, m.NodeID), "workspace": node.Kind == "sandbox",
			"since": l.nudge.Since.UTC().Format(time.RFC3339), "at": l.nudge.At.UTC().Format(time.RFC3339Nano),
			"text": nudgeText(m.Name, l.nudge.Count, nodeLabel(node, m.NodeID), node.Kind, l.nudge.Since, t.clock(), spaceLoc(fl))})
	}
	return rows
}

// agentsOnNodes lists my agents with the computer each lives on and its
// state, for the Fleet App and the computers menu. fl.mu is held.
func (t *TeamChat) agentsOnNodes(fl *tcFleet, me string) map[string]any {
	agents := []map[string]any{}
	for _, mid := range sortedKeys(fl.members) {
		m := fl.members[mid]
		if !m.agent() || ownerOf(m) != me || m.NodeID == "" {
			continue
		}
		st := t.statusOf(fl, m)
		// workspace: it lives on the Cloud Workspace (whose node is not listed while it is paused).
		agents = append(agents, map[string]any{"mid": mid, "handle": m.Handle, "name": m.Name, "node_id": m.NodeID,
			"workspace": withMemberNode(fl.nodes[m.NodeID], m).Kind == "sandbox", "state": st["state"], "queue": st["queue"]})
	}
	return map[string]any{"agents": agents}
}
