package hosted

// Requests of an agent's `pantheon-team` tool. The agent writes them into its
// folder's inbox; its node's Coding Agents service sends them here, on
// fleet.<fid>.apps.team-chat.rpc, with the agent's identity (the token the
// service gave the node with the agent's turn). The answer is text for the
// agent and an exit status.

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strconv"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
)

type agentRequest struct {
	MID   string            `json:"mid"`
	Token string            `json:"token"`
	Op    string            `json:"op"`
	Args  []string          `json:"args"`
	Opts  map[string]string `json:"opts"`
	Text  string            `json:"text"`
	Files []string          `json:"files"`
	// file_put
	Upload filePutArgs `json:"upload"`
}

type agentReply struct {
	Code int    `json:"code"`
	Text string `json:"text"`
	File any    `json:"file,omitempty"`
}

func (t *TeamChat) serveAgent(msg *nats.Msg) {
	// fleet.<fid>.apps.team-chat.rpc
	parts := strings.Split(msg.Subject, ".")
	if len(parts) != 5 || msg.Reply == "" {
		return
	}
	fid := parts[1]
	go func() {
		var req agentRequest
		var out agentReply
		if err := json.Unmarshal(msg.Data, &req); err != nil {
			out = agentReply{2, "pantheon-team: an unreadable request", nil}
		} else {
			ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
			out = t.AgentRequest(ctx, fid, req)
			cancel()
		}
		raw, _ := json.Marshal(out)
		if err := msg.Respond(raw); err != nil {
			log.Printf("[team-chat] %s answer: %v", fid, err)
		}
	}()
}

// AgentRequest serves one request of an agent's tool.
func (t *TeamChat) AgentRequest(ctx context.Context, fid string, req agentRequest) agentReply {
	if !desktopFleetRE.MatchString(fid) || !t.checkToken(fid, req.MID, req.Token) {
		return agentReply{1, "pantheon-team: this agent is not known to Team Chat", nil}
	}
	if req.Op == "file_put" {
		res, err := t.filePut(ctx, fid, req.MID, req.Upload)
		if err != nil {
			return agentReply{1, err.Error(), nil}
		}
		return agentReply{0, "", res}
	}
	fl, err := t.lockFleet(ctx, fid)
	if err != nil {
		return agentReply{1, "Team Chat is not available right now: " + err.Error(), nil}
	}
	defer fl.mu.Unlock()
	m := fl.members[req.MID]
	if m == nil || !m.agent() {
		return agentReply{1, "pantheon-team: this agent was removed from the space", nil}
	}
	text, err := t.agentOp(ctx, fl, m, req)
	if err != nil {
		return agentReply{1, err.Error(), nil}
	}
	return agentReply{0, text, nil}
}

func arg(req agentRequest, i int) string {
	if i < len(req.Args) {
		return strings.TrimSpace(req.Args[i])
	}
	return ""
}

// body is the request's text: stdin, else the arguments after the first n.
func body(req agentRequest, n int) string {
	if strings.TrimSpace(req.Text) != "" {
		return req.Text
	}
	if len(req.Args) > n {
		return strings.Join(req.Args[n:], " ")
	}
	return ""
}

// agentOp runs one request as agent m; fl.mu is held.
func (t *TeamChat) agentOp(ctx context.Context, fl *tcFleet, m *tcMember, req agentRequest) (string, error) {
	tr := fl.turns[m.MID]
	switch req.Op {
	case "inbox":
		return t.agentInbox(ctx, fl, m, tr), nil
	case "skip":
		why := strings.TrimSpace(strings.Join(req.Args, " "))
		if why == "" {
			why = "nothing to say"
		}
		if tr == nil {
			return "Nothing to skip: you have no turn running.", nil
		}
		tr.Skipped = why
		_ = t.put(ctx, fl, "turn."+m.MID, tr)
		return "Noted: nothing is posted for you this turn. End your turn now.", nil
	case "members":
		var lines []string
		for _, mid := range sortedKeys(fl.members) {
			x := fl.members[mid]
			line := fmt.Sprintf("@%s  %s", x.Handle, x.Name)
			if x.agent() {
				st := t.statusOf(fl, x)
				line += fmt.Sprintf("  (agent, %s", st["state"])
				if x.Description != "" {
					line += ": " + clipText(x.Description, 80)
				}
				line += ")"
			} else {
				line += "  (person)"
			}
			if mid == m.MID {
				line += "  ← you"
			}
			lines = append(lines, line)
		}
		return strings.Join(lines, "\n"), nil
	case "channels":
		var lines []string
		for _, cid := range sortedKeys(fl.convs) {
			c := fl.convs[cid]
			if c.Kind != "channel" || c.Archived || !fl.visible(c, m.MID) {
				continue
			}
			line := "#" + c.Name
			if c.Private {
				line += " (private)"
			}
			if c.has(m.MID) {
				line += " (member)"
			} else {
				line += " (join with: join " + c.Name + ")"
			}
			if c.Topic != "" {
				line += " — " + clipText(c.Topic, 80)
			}
			lines = append(lines, line)
		}
		return strings.Join(lines, "\n"), nil
	case "join":
		c := t.channelNamed(fl, arg(req, 0))
		if c == nil || !fl.visible(c, m.MID) {
			return "", fmt.Errorf("no channel %s (see: channels)", arg(req, 0))
		}
		if _, err := t.convMembership(ctx, fl, m.MID, c.CID, m.MID, true); err != nil {
			return "", err
		}
		return "Joined #" + c.Name + ".", nil
	case "read":
		return t.agentRead(ctx, fl, m, arg(req, 0), req.Opts)
	case "post":
		c := t.channelNamed(fl, arg(req, 0))
		if c == nil && strings.HasPrefix(arg(req, 0), "@") {
			return t.agentDM(ctx, fl, m, tr, arg(req, 0), body(req, 1), req.Files)
		}
		if c == nil || !fl.visible(c, m.MID) {
			return "", fmt.Errorf("no channel %s (see: channels)", arg(req, 0))
		}
		msg, err := t.agentPost(ctx, fl, m, tr, c.CID, "", body(req, 1), req.Files)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("Posted to #%s as message %s.", c.Name, msg.ID), nil
	case "reply":
		target, err := t.findMsg(ctx, fl, arg(req, 0), "")
		if err != nil {
			return "", err
		}
		c := fl.convs[target.CID]
		if !fl.visible(c, m.MID) {
			return "", fmt.Errorf("you are not in the conversation of message %s", target.ID)
		}
		// A reply goes where the message is: in a DM or to a top-level
		// channel message inline, to a message in a thread in that thread.
		root := target.Root
		if c.Kind == "dm" {
			root = ""
		}
		msg, err := t.agentPost(ctx, fl, m, tr, c.CID, root, body(req, 1), req.Files)
		if err != nil {
			return "", err
		}
		if root == "" {
			return fmt.Sprintf("Replied in %s as message %s.", c.label(fl, m.MID), msg.ID), nil
		}
		return fmt.Sprintf("Replied in the thread of %s (%s) as message %s.", root, c.label(fl, m.MID), msg.ID), nil
	case "dm":
		return t.agentDM(ctx, fl, m, tr, arg(req, 0), body(req, 1), req.Files)
	case "edit":
		msg, err := t.change(ctx, fl, m.MID, "edit", arg(req, 0), "", body(req, 1))
		if err != nil {
			return "", err
		}
		return "Edited message " + msg.ID + ".", nil
	}
	return "", fmt.Errorf("pantheon-team: unknown command %q (inbox, read, post, reply, dm, members, channels, join, edit, skip)", req.Op)
}

func (t *TeamChat) channelNamed(fl *tcFleet, name string) *tcConv {
	n := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(name), "#"))
	if c := fl.convs[n]; c != nil && c.Kind == "channel" {
		return c
	}
	for _, c := range fl.convs {
		if c.Kind == "channel" && c.Name == n {
			return c
		}
	}
	return nil
}

func (t *TeamChat) memberByHandle(fl *tcFleet, handle string) *tcMember {
	h := strings.ToLower(strings.TrimPrefix(strings.TrimSpace(handle), "@"))
	for _, m := range fl.members {
		if m.Handle == h {
			return m
		}
	}
	return nil
}

func (t *TeamChat) agentDM(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, handle, text string, files []string) (string, error) {
	other := t.memberByHandle(fl, handle)
	if other == nil {
		return "", fmt.Errorf("no member %s (see: members)", handle)
	}
	if other.MID == m.MID {
		return "", fmt.Errorf("that is you")
	}
	res, err := t.convCreate(ctx, fl, m.MID, "dm", "", false, []string{other.MID}, "", "")
	if err != nil {
		return "", err
	}
	c := res.(map[string]any)["conversation"].(*tcConv)
	msg, err := t.agentPost(ctx, fl, m, tr, c.CID, "", text, files)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("Sent to @%s as message %s.", other.Handle, msg.ID), nil
}

// agentInbox hands the running turn the events that arrived since it started
// (they leave the queue, so the next turn does not get them again).
func (t *TeamChat) agentInbox(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn) string {
	q := fl.queues[m.MID]
	if len(q) == 0 {
		return "Nothing new since your turn started."
	}
	var b strings.Builder
	b.WriteString("New since your turn started:\n")
	for _, e := range q {
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
			where += " (thread on " + msg.Root + ")"
		}
		switch e.Kind {
		case "delete":
			fmt.Fprintf(&b, "%s: %s deleted message %s.\n", where, handleOf(fl, e.From), msg.ID)
			continue
		case "edit":
			fmt.Fprintf(&b, "%s: %s edited message %s:\n", where, handleOf(fl, e.From), msg.ID)
		default:
			fmt.Fprintf(&b, "%s, %s at %s [%s]:\n", where, handleOf(fl, msg.From), fmtTime(msg.At), msg.ID)
		}
		if msg.Deleted {
			b.WriteString("  (deleted)\n")
		} else {
			b.WriteString(indent(msg.Text))
		}
		for _, f := range msg.Files {
			fmt.Fprintf(&b, "  (file %s, %d bytes: it reaches your folder in your next turn)\n", f.Name, f.Size)
		}
		if id := cm.all[len(cm.all)-1]; id > fl.reads[m.MID+"."+e.CID] {
			_ = t.setRead(ctx, fl, m.MID, e.CID, id)
		}
	}
	if tr != nil {
		// What it took raises its turn's hop: its next post is one hop
		// past the furthest message it saw.
		for _, e := range q {
			if e.Kind == "post" && e.Hop > tr.Hop {
				tr.Hop = e.Hop
			}
		}
		tr.Events = append(tr.Events, q...)
		_ = t.put(ctx, fl, "turn."+m.MID, tr)
	}
	t.saveQueue(ctx, fl, m.MID, nil)
	t.emitStatus(fl, m.MID)
	return b.String()
}

// agentRead: a channel, a DM (@handle) or a thread (message id), newest last.
func (t *TeamChat) agentRead(ctx context.Context, fl *tcFleet, m *tcMember, target string, opts map[string]string) (string, error) {
	limit := 30
	if n, err := strconv.Atoi(opts["limit"]); err == nil && n > 0 {
		limit = min(n, 200)
	}
	var c *tcConv
	root := ""
	switch {
	case strings.HasPrefix(target, "@"):
		other := t.memberByHandle(fl, target)
		if other == nil {
			return "", fmt.Errorf("no member %s", target)
		}
		c = fl.convs[dmID(m.MID, other.MID)]
		if c == nil {
			return "You have no messages with " + target + ".", nil
		}
	default:
		c = t.channelNamed(fl, target)
		if c == nil {
			msg, err := t.findMsg(ctx, fl, target, "")
			if err != nil {
				return "", fmt.Errorf("no channel, member or message %s", target)
			}
			c, root = fl.convs[msg.CID], msg.ID
			if msg.Root != "" {
				root = msg.Root
			}
		}
	}
	if !fl.visible(c, m.MID) {
		return "", fmt.Errorf("you are not in %s", c.label(fl, m.MID))
	}
	cm, err := t.convMsgs(ctx, fl, c.CID)
	if err != nil {
		return "", err
	}
	page, more := cm.page(root, opts["before"], opts["after"], limit)
	var b strings.Builder
	head := c.label(fl, m.MID)
	if root != "" {
		head += ", thread " + root
	}
	if more && len(page) > 0 {
		head += fmt.Sprintf(" (older messages: --before %s)", page[0].ID)
	}
	b.WriteString(head + ":\n")
	for _, msg := range page {
		text := msg.Text
		if msg.Deleted {
			text = "(deleted)"
		}
		suffix := ""
		if msg.EditedAt != "" && !msg.Deleted {
			suffix = " (edited)"
		}
		if msg.ReplyCount > 0 && root == "" {
			suffix += fmt.Sprintf(" (%d replies: read %s)", msg.ReplyCount, msg.ID)
		}
		fmt.Fprintf(&b, "%s %s [%s]%s:\n%s", fmtTime(msg.At), handleOf(fl, msg.From), msg.ID, suffix, indent(text))
		for _, f := range msg.Files {
			fmt.Fprintf(&b, "  (file %s, %d bytes)\n", f.Name, f.Size)
		}
	}
	if len(page) > 0 {
		last := page[len(page)-1].ID
		if root == "" && last > fl.reads[m.MID+"."+c.CID] {
			_ = t.setRead(ctx, fl, m.MID, c.CID, last)
		}
	} else {
		b.WriteString("(no messages)\n")
	}
	return b.String(), nil
}
