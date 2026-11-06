package hosted

// Tasks: a message with a state and one assignee (docs/apps/team-chat.md,
// "Phase 2"). The task's id is its message's; every change of its state or
// assignee is a system line in the message's thread.

import (
	"context"
	"fmt"
	"sort"
	"strings"
)

const tcMaxTasks = 5000

type tcTask struct {
	ID        string `json:"id"`
	CID       string `json:"cid"`
	Root      string `json:"root"` // the thread its lines go in
	Title     string `json:"title"`
	State     string `json:"state"` // todo | in_progress | in_review | done | closed
	Assignee  string `json:"assignee"`
	CreatedBy string `json:"created_by"`
	Created   string `json:"created"`
	Updated   string `json:"updated"`
	UpdatedBy string `json:"updated_by,omitempty"`
}

var tcTaskStates = []string{"todo", "in_progress", "in_review", "done", "closed"}

// taskStateLabel is a state as people read it.
func taskStateLabel(state string) string {
	return map[string]string{"todo": "Todo", "in_progress": "In progress", "in_review": "In review", "done": "Done",
		"closed": "Closed"}[state]
}

// taskState reads a state as people or agents write it ("In review",
// "in-review", "review").
func taskState(s string) (string, bool) {
	k := strings.NewReplacer(" ", "_", "-", "_").Replace(strings.ToLower(strings.TrimSpace(s)))
	switch k {
	case "todo", "to_do", "open":
		return "todo", true
	case "in_progress", "progress", "doing", "started":
		return "in_progress", true
	case "in_review", "review":
		return "in_review", true
	case "done", "closed":
		return k, true
	}
	return "", false
}

// taskTitle is a message's first line, short.
func taskTitle(text string) string {
	for _, line := range strings.Split(text, "\n") {
		if strings.TrimSpace(line) != "" {
			return clipText(line, 120)
		}
	}
	return "(a file)"
}

// makeTask makes a message a task in Todo; fl.mu is held.
func (t *TeamChat) makeTask(ctx context.Context, fl *tcFleet, by string, msg *tcMsg) (*tcTask, error) {
	if fl.tasks[msg.ID] != nil {
		return nil, &ArgumentError{"this message is already a task"}
	}
	if msg.Kind != "post" || msg.Deleted {
		return nil, &ArgumentError{"only a message someone wrote can be a task"}
	}
	if len(fl.tasks) >= tcMaxTasks {
		return nil, &ArgumentError{fmt.Sprintf("a space holds at most %d tasks", tcMaxTasks)}
	}
	root := msg.Root
	if root == "" {
		root = msg.ID
	}
	now := t.stamp()
	tk := &tcTask{ID: msg.ID, CID: msg.CID, Root: root, Title: taskTitle(msg.Text), State: "todo", CreatedBy: by,
		Created: now, Updated: now, UpdatedBy: by}
	if err := t.put(ctx, fl, "task."+tk.ID, tk); err != nil {
		return nil, err
	}
	fl.tasks[tk.ID] = tk
	t.emit(fl, map[string]any{"type": "task", "task": tk})
	return tk, nil
}

// taskOf finds a task by its id (a message id, with or without a leading #).
func (t *TeamChat) taskOf(fl *tcFleet, id string) (*tcTask, error) {
	id = strings.TrimPrefix(strings.TrimSpace(id), "#")
	if tk := fl.tasks[id]; tk != nil {
		return tk, nil
	}
	return nil, &ArgumentError{"no task " + id}
}

// taskChange sets a task's state and assignee (nil: unchanged) as by, with
// the rights of a person or of an agent; fl.mu is held. claim: an agent
// takes the task (it fails when someone else holds it).
func (t *TeamChat) taskChange(ctx context.Context, fl *tcFleet, by string, tk *tcTask, state, assignee *string, claim bool) (*tcTask, error) {
	actor := fl.members[by]
	if actor == nil {
		return nil, &ArgumentError{"no member " + by}
	}
	isAgent := actor.agent()
	next := *tk
	if claim {
		switch {
		case tk.Assignee == by:
			return tk, nil
		case tk.Assignee != "":
			return nil, &ArgumentError{fmt.Sprintf("Not claimed: task %s is already claimed by %s. Don't work on it.", tk.ID, handleOf(fl, tk.Assignee))}
		case tk.State == "done" || tk.State == "closed":
			return nil, &ArgumentError{fmt.Sprintf("Not claimed: task %s is %s.", tk.ID, taskStateLabel(tk.State))}
		}
		next.Assignee = by
		if tk.State == "todo" {
			next.State = "in_progress"
		}
	}
	if state != nil {
		s, ok := taskState(*state)
		if !ok {
			return nil, &ArgumentError{"a task's state is todo, in_progress, in_review, done or closed"}
		}
		if isAgent && (s == "done" || s == "closed") {
			return nil, &ArgumentError{fmt.Sprintf("Not changed: a person moves a task to %s. When your work is done, move it to "+
				"in_review with a summary: task state %s in_review - <<'EOF' … EOF", taskStateLabel(s), tk.ID)}
		}
		next.State = s
	}
	if assignee != nil {
		a := strings.TrimSpace(*assignee)
		if strings.HasPrefix(a, "@") {
			m := t.memberByHandle(fl, a)
			if m == nil {
				return nil, &ArgumentError{"no member " + a}
			}
			a = m.MID
		}
		if a != "" && fl.members[a] == nil {
			return nil, &ArgumentError{"no member " + a}
		}
		next.Assignee = a
	}
	if isAgent && !claim {
		// An agent changes a task it holds, or (only to take or hand it on)
		// one nobody holds.
		if tk.Assignee != "" && tk.Assignee != by {
			return nil, &ArgumentError{fmt.Sprintf("Not changed: task %s is claimed by %s, not you. Don't work on it.", tk.ID, handleOf(fl, tk.Assignee))}
		}
		if tk.Assignee == "" && assignee == nil && state != nil && next.State != "todo" {
			// Working on a task nobody holds is claiming it.
			next.Assignee = by
		}
	}
	if next.State == tk.State && next.Assignee == tk.Assignee {
		return tk, nil
	}
	next.Updated, next.UpdatedBy = t.stamp(), by
	if err := t.put(ctx, fl, "task."+tk.ID, &next); err != nil {
		return nil, err
	}
	fl.tasks[tk.ID] = &next
	t.emit(fl, map[string]any{"type": "task", "task": &next})
	t.taskLine(ctx, fl, by, tk, &next, claim)
	return &next, nil
}

// taskLine says what changed in the task's thread; fl.mu is held.
func (t *TeamChat) taskLine(ctx context.Context, fl *tcFleet, by string, old, tk *tcTask, claim bool) {
	who := handleOf(fl, by)
	var text string
	switch {
	case claim:
		text = fmt.Sprintf("%s claimed this task · %s", who, taskStateLabel(tk.State))
	case old.Assignee != tk.Assignee && old.State != tk.State:
		to := "nobody"
		if tk.Assignee != "" {
			to = handleOf(fl, tk.Assignee)
		}
		text = fmt.Sprintf("%s moved this task to %s and assigned it to %s", who, taskStateLabel(tk.State), to)
	case old.Assignee != tk.Assignee && tk.Assignee == "":
		text = fmt.Sprintf("%s unassigned this task · %s", who, taskStateLabel(tk.State))
	case old.Assignee != tk.Assignee:
		text = fmt.Sprintf("%s assigned this task to %s · %s", who, handleOf(fl, tk.Assignee), taskStateLabel(tk.State))
	default:
		text = fmt.Sprintf("%s moved this task to %s", who, taskStateLabel(tk.State))
	}
	cid := tk.CID
	if fl.convs[cid] == nil || fl.convs[cid].Archived {
		return
	}
	_, _ = t.postEvent(ctx, fl, &tcEvent{From: "system", CID: cid, Root: tk.Root, Kind: "system", Text: text, Task: tk.ID, By: by}, nil)
}

// taskList is a space's tasks, newest first, filtered.
func (t *TeamChat) taskList(fl *tcFleet, viewer, cid, assignee, createdBy, state string) []*tcTask {
	var out []*tcTask
	for _, tk := range fl.tasks {
		c := fl.convs[tk.CID]
		if c == nil || !fl.visible(c, viewer) {
			continue
		}
		if cid != "" && tk.CID != cid || assignee != "" && tk.Assignee != assignee ||
			createdBy != "" && tk.CreatedBy != createdBy || state != "" && tk.State != state {
			continue
		}
		out = append(out, tk)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID > out[j].ID })
	return out
}

// taskNote tells an agent what a task message asks of it, for its turn.
func taskNote(fl *tcFleet, tk *tcTask, me, script string) string {
	state := taskStateLabel(tk.State)
	switch {
	case tk.State == "done" || tk.State == "closed":
		return fmt.Sprintf("(This is task %s, %s.)", tk.ID, state)
	case tk.Assignee == "":
		return fmt.Sprintf("(This is task %s, %s, nobody holds it. If it is for you, claim it before you work on it: %s task claim %s. "+
			"When you are done, move it to In review with a summary: %s task state %s in_review - <<'EOF' … EOF. A person sets Done.)",
			tk.ID, state, script, tk.ID, script, tk.ID)
	case tk.Assignee == me:
		return fmt.Sprintf("(This is task %s, %s, yours. When you are done, move it to In review with a summary: "+
			"%s task state %s in_review - <<'EOF' … EOF. A person sets Done.)", tk.ID, state, script, tk.ID)
	default:
		return fmt.Sprintf("(This is task %s, %s, claimed by %s: it is not yours, don't work on it.)", tk.ID, state, handleOf(fl, tk.Assignee))
	}
}

// taskFor is the task a message is, or whose thread it is in.
func taskFor(fl *tcFleet, msg *tcMsg) *tcTask {
	if tk := fl.tasks[msg.ID]; tk != nil {
		return tk
	}
	if msg.Root != "" {
		for _, tk := range fl.tasks {
			if tk.Root == msg.Root && tk.CID == msg.CID {
				return tk
			}
		}
	}
	return nil
}

// agentTask serves `pantheon-team task …`; fl.mu is held.
func (t *TeamChat) agentTask(ctx context.Context, fl *tcFleet, m *tcMember, tr *tcTurn, req agentRequest) (string, error) {
	sub := arg(req, 0)
	rest := agentRequest{Args: nil, Text: req.Text, Opts: req.Opts, Files: req.Files}
	if len(req.Args) > 1 {
		rest.Args = req.Args[1:]
	}
	// A task to claim, move or hand on is one in a conversation the turn's
	// person sees too.
	seenTask := func(id string) (*tcTask, error) {
		tk, err := t.taskOf(fl, id)
		if err != nil {
			return nil, err
		}
		if c := fl.convs[tk.CID]; !fl.visible(c, m.MID) {
			return nil, &ArgumentError{"no task " + tk.ID}
		}
		return tk, nil
	}
	switch sub {
	case "create":
		if arg(rest, 0) == "" {
			return "", fmt.Errorf("Not created: say where. %s", strings.Replace(tcQuoteHint, "post '#general'", "task create '#general'", 1))
		}
		if err := needText("task create", rest, 1); err != nil {
			return "", err
		}
		c := t.channelNamed(fl, arg(rest, 0))
		if c == nil || !fl.visible(c, m.MID) {
			return "", fmt.Errorf("Not created: no channel %s (see: channels)", arg(rest, 0))
		}
		msg, err := t.agentPost(ctx, fl, m, tr, "", c.CID, "", body(rest, 1), rest.Files)
		if err != nil {
			return "", err
		}
		if _, err := t.makeTask(ctx, fl, m.MID, msg); err != nil {
			return "", err
		}
		return fmt.Sprintf("Created task %s in #%s (Todo).", msg.ID, c.Name), nil
	case "claim":
		tk, err := seenTask(arg(rest, 0))
		if err != nil {
			return "", err
		}
		next, err := t.taskChange(ctx, fl, m.MID, tk, nil, nil, true)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("You hold task %s (%s). When you are done: task state %s in_review - <<'EOF' summary EOF.",
			next.ID, taskStateLabel(next.State), next.ID), nil
	case "state":
		tk, err := seenTask(arg(rest, 0))
		if err != nil {
			return "", err
		}
		state := arg(rest, 1)
		if state == "" {
			return "", fmt.Errorf("Not changed: say the state: task state %s todo|in_progress|in_review", tk.ID)
		}
		// The summary (stdin, or words after the state) goes in the
		// task's thread first.
		summary := strings.TrimSpace(body(rest, 2))
		s, ok := taskState(state)
		if !ok {
			return "", fmt.Errorf("Not changed: a task's state is todo, in_progress or in_review")
		}
		if s == "done" || s == "closed" {
			_, err := t.taskChange(ctx, fl, m.MID, tk, &s, nil, false)
			return "", err // refused: a person sets these
		}
		if tk.Assignee != "" && tk.Assignee != m.MID {
			return "", fmt.Errorf("Not changed: task %s is claimed by %s, not you. Don't work on it.", tk.ID, handleOf(fl, tk.Assignee))
		}
		if summary != "" {
			if _, err := t.agentPost(ctx, fl, m, tr, "", tk.CID, tk.Root, summary, rest.Files); err != nil {
				return "", err
			}
		}
		next, err := t.taskChange(ctx, fl, m.MID, tk, &state, nil, false)
		if err != nil {
			return "", err
		}
		out := fmt.Sprintf("Task %s is %s.", next.ID, taskStateLabel(next.State))
		if summary != "" {
			out += " Your summary is in its thread."
		}
		return out, nil
	case "assign":
		tk, err := seenTask(arg(rest, 0))
		if err != nil {
			return "", err
		}
		who := arg(rest, 1)
		if who == "" {
			return "", fmt.Errorf("Not changed: say who: task assign %s @handle", tk.ID)
		}
		if x := t.memberByHandle(fl, who); x != nil {
			// Handed only to an agent the turn's person may use, or a
			// person still in the space.
			p := tr.WorksFor
			if p == "" {
				p = ownerOf(m)
			}
			if x.agent() && x.MID != m.MID && !fl.canUse(p, x) {
				return "", fmt.Errorf("Not changed: %s (%s asked you.)", fl.notUsable(x), handleOf(fl, p))
			}
			if !x.agent() && !fl.person(x.MID) {
				return "", fmt.Errorf("Not changed: @%s is no longer in this space", x.Handle)
			}
		}
		next, err := t.taskChange(ctx, fl, m.MID, tk, nil, &who, false)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("Task %s is assigned to %s (%s).", next.ID, handleOf(fl, next.Assignee), taskStateLabel(next.State)), nil
	case "list", "":
		cid, assignee, state := "", "", ""
		if in := req.Opts["in"]; in != "" {
			c := t.channelNamed(fl, in)
			if c == nil {
				return "", fmt.Errorf("no channel %s", in)
			}
			cid = c.CID
		}
		if a := req.Opts["assignee"]; a != "" {
			x := t.memberByHandle(fl, a)
			if x == nil {
				return "", fmt.Errorf("no member %s", a)
			}
			assignee = x.MID
		}
		if s := req.Opts["state"]; s != "" {
			v, ok := taskState(s)
			if !ok {
				return "", fmt.Errorf("a task's state is todo, in_progress, in_review, done or closed")
			}
			state = v
		}
		var lines []string
		for _, tk := range t.taskList(fl, m.MID, cid, assignee, "", state) {
			if state == "" && (tk.State == "done" || tk.State == "closed") {
				continue
			}
			who := "nobody holds it"
			if tk.Assignee != "" {
				who = "claimed by " + handleOf(fl, tk.Assignee)
			}
			lines = append(lines, fmt.Sprintf("[%s] %s · %s · %s: %s", tk.ID, taskStateLabel(tk.State), who,
				fl.convs[tk.CID].label(fl, m.MID), tk.Title))
			if len(lines) == 50 {
				break
			}
		}
		if len(lines) == 0 {
			return "No tasks.", nil
		}
		return strings.Join(lines, "\n"), nil
	}
	return "", fmt.Errorf("pantheon-team task: create '#channel' | claim ID | state ID todo|in_progress|in_review | assign ID @handle | list")
}
