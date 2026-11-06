package hosted

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeClock is a clock tests move by hand.
type fakeClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) Add(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

func agentSays(tc *TeamChat, mid, op string, args []string, opts map[string]string, text string) agentReply {
	return tc.asTurn(context.Background(), fleetID, agentRequest{MID: mid, Token: tc.agentToken(fleetID, mid), Op: op,
		Args: args, Opts: opts, Text: text})
}

func systemLines(list []map[string]any) []string {
	var out []string
	for _, m := range list {
		if m["from"] == "system" {
			out = append(out, fmt.Sprint(m["text"]))
		}
	}
	return out
}

// T20/T21/T22: a message made a task; an agent claims it, works, moves it to
// In review with a summary; a second claim fails; the person sets Done;
// every change is a line in the task's thread; filters.
func TestTasksClaimReviewDoneAndLines(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	coder := makeAgent(t, h, mine, "Coder", "n_ws", "codex", nil)
	ch := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "docs", "members": []string{writer, coder}})["conversation"].(map[string]any)["cid"].(string)

	out := call(t, h, mine, "post", map[string]any{"cid": ch, "text": "@writer draft the release notes\nfrom CHANGES.md", "as_task": true})
	task := out["task"].(map[string]any)
	id := out["message"].(map[string]any)["id"].(string)
	if task["state"] != "todo" || task["id"] != id || task["title"] != "@writer draft the release notes" || task["assignee"] != "" {
		t.Fatalf("%v", task)
	}
	// The mentioned agent is woken and told to claim it first.
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 1 })
	if p := nodes.lastPrompt(); !strings.Contains(p, "This is task "+id+", Todo, nobody holds it") || !strings.Contains(p, "task claim "+id) {
		t.Fatalf("turn input: %s", p)
	}
	// Writer claims; Coder's claim then fails and changes nothing.
	if r := agentSays(tc, writer, "task", []string{"claim", id}, nil, ""); r.Code != 0 || !strings.Contains(r.Text, "In progress") {
		t.Fatalf("%v", r)
	}
	r := agentSays(tc, coder, "task", []string{"claim", id}, nil, "")
	if r.Code == 0 || !strings.Contains(r.Text, "already claimed by @writer") {
		t.Fatalf("second claim: %v", r)
	}
	if r := agentSays(tc, coder, "task", []string{"state", id, "in_review"}, nil, "mine now"); r.Code == 0 || !strings.Contains(r.Text, "claimed by @writer") {
		t.Fatalf("someone else's task moved: %v", r)
	}
	// An agent cannot set Done; In review with a summary in the thread.
	if r := agentSays(tc, writer, "task", []string{"state", id, "done"}, nil, ""); r.Code == 0 || !strings.Contains(r.Text, "a person moves a task to Done") {
		t.Fatalf("%v", r)
	}
	if r := agentSays(tc, writer, "task", []string{"state", id, "in_review"}, nil, "Drafted: 3 sections in NOTES.md."); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	call(t, h, mine, "task_update", map[string]any{"id": id, "state": "done"})
	thread := msgs(t, h, mine, ch, id)
	lines := systemLines(thread)
	want := []string{"@writer claimed this task · In progress", "@writer moved this task to In review", "@owner moved this task to Done"}
	if strings.Join(lines, "|") != strings.Join(want, "|") {
		t.Fatalf("thread lines: %q", lines)
	}
	if s := lastFrom(thread, writer); s == nil || s["text"] != "Drafted: 3 sections in NOTES.md." {
		t.Fatalf("summary: %v", thread)
	}
	for _, m := range thread {
		if m["from"] == "system" && m["task"] != id {
			t.Fatalf("a task line without its task: %v", m)
		}
	}
	// Make task on an existing message; once only; the board's filters.
	other := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "Fix the CI"})["message"].(map[string]any)["id"].(string)
	call(t, h, mine, "task_create", map[string]any{"id": other})
	if e := refused(t, h, mine, "task_create", map[string]any{"id": other}); !strings.Contains(e, "already a task") {
		t.Fatal(e)
	}
	call(t, h, mine, "task_update", map[string]any{"id": other, "assignee": coder, "state": "in_progress"})
	byCh := call(t, h, mine, "tasks", map[string]any{"cid": ch})["tasks"].([]any)
	byCoder := call(t, h, mine, "tasks", map[string]any{"assignee": coder})["tasks"].([]any)
	done := call(t, h, mine, "tasks", map[string]any{"state": "done"})["tasks"].([]any)
	if len(byCh) != 1 || len(byCoder) != 1 || byCoder[0].(map[string]any)["id"] != other || len(done) != 1 {
		t.Fatalf("filters: %v %v %v", byCh, byCoder, done)
	}
	if l := systemLines(msgs(t, h, mine, "general", other)); len(l) != 1 || l[0] != "@owner moved this task to In progress and assigned it to @coder" {
		t.Fatalf("%q", l)
	}
	space := call(t, h, mine, "space_get", map[string]any{})
	if len(space["tasks"].([]any)) != 2 {
		t.Fatalf("%v", space["tasks"])
	}
	// An agent's task list and task create.
	if r := agentSays(tc, coder, "task", []string{"list"}, nil, ""); r.Code != 0 || !strings.Contains(r.Text, "Fix the CI") || strings.Contains(r.Text, "release notes") {
		t.Fatalf("%v", r)
	}
	if r := agentSays(tc, coder, "task", []string{"create", "#docs"}, nil, "Update the README"); r.Code != 0 || !strings.Contains(r.Text, "Todo") {
		t.Fatalf("%v", r)
	}
	// Tasks survive a restart.
	tc2 := teamOn(t, url, nodes)
	h2, mine2, _ := teamHost(t, tc2)
	if n := len(call(t, h2, mine2, "tasks", map[string]any{})["tasks"].([]any)); n != 3 {
		t.Fatalf("after restart: %d tasks", n)
	}
}

func TestReminderTimes(t *testing.T) {
	la, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		t.Fatal(err)
	}
	// Wednesday Nov 5 2026, 10:00 in Los Angeles.
	now := time.Date(2026, 11, 5, 10, 0, 0, 0, la)
	cases := []struct {
		when  string
		at    time.Time
		every bool
	}{
		{"in 2 minutes", now.Add(2 * time.Minute), false},
		{"2m", now.Add(2 * time.Minute), false},
		{"in an hour", now.Add(time.Hour), false},
		{"14:30", time.Date(2026, 11, 5, 14, 30, 0, 0, la), false},
		{"9:15", time.Date(2026, 11, 6, 9, 15, 0, 0, la), false}, // passed today: tomorrow
		{"tomorrow 9:00", time.Date(2026, 11, 6, 9, 0, 0, 0, la), false},
		{"at 3pm", time.Date(2026, 11, 5, 15, 0, 0, 0, la), false},
		{"monday 10:00", time.Date(2026, 11, 9, 10, 0, 0, 0, la), false},
		{"2026-11-07 08:00", time.Date(2026, 11, 7, 8, 0, 0, 0, la), false},
		{"every 5 minutes", now.Add(5 * time.Minute), true},
		{"every weekday 10:00", time.Date(2026, 11, 6, 10, 0, 0, 0, la), true}, // 10:00 today is not after now
		{"every mon,wed 9:30", time.Date(2026, 11, 9, 9, 30, 0, 0, la), true},
		{"every day 18:00", time.Date(2026, 11, 5, 18, 0, 0, 0, la), true},
	}
	for _, c := range cases {
		at, sched, err := parseWhen(c.when, now, la)
		if err != nil || !at.Equal(c.at) || (sched != nil) != c.every {
			t.Errorf("%q: %v %v %v, want %v", c.when, at, sched, err, c.at)
		}
	}
	for _, bad := range []string{"", "soon", "every 10 seconds", "check the CI", "minute", "25:00"} {
		if _, _, err := parseWhen(bad, now, la); err == nil {
			t.Errorf("%q was read as a time", bad)
		}
	}
	// A weekday repeat skips the weekend; missed times are not made up.
	s, _ := parseEvery("weekday 10:00", now)
	fri := time.Date(2026, 11, 6, 10, 0, 0, 0, la)
	if next := s.next(fri, la); !next.Equal(time.Date(2026, 11, 9, 10, 0, 0, 0, la)) {
		t.Fatalf("after Friday: %v", next)
	}
	iv, _ := parseEvery("5 minutes", now)
	if next := iv.nextAfterMissed(now, now.Add(17*time.Minute), la); !next.Equal(now.Add(20 * time.Minute)) {
		t.Fatalf("missed: %v", next)
	}
}

// T23: a reminder set in a turn fires on time, wakes only its agent with the
// message it is tied to (in its thread), posts nothing by itself, and
// survives a controller restart in between.
func TestAReminderWakesOnlyItsAgentAndSurvivesARestart(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.reply = func(string) (string, string) { return "", "" } // posts nothing by itself
	clock := &fakeClock{now: time.Date(2026, 11, 5, 18, 0, 0, 0, time.UTC)}
	tc := teamOn(t, url, nodes)
	tc.now = clock.Now
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	makeAgent(t, h, mine, "Coder", "n_ws", "codex", nil)
	root := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "CI run for 0.9"})["message"].(map[string]any)["id"].(string)
	ask := call(t, h, mine, "post", map[string]any{"cid": "general", "root": root, "text": "@writer remind me to check the CI in 2 minutes"})["message"].(map[string]any)["id"].(string)
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 1 })
	// The fake turn ended at once; set the reminder as that turn would have
	// (the turn is gone, so it is tied with --about).
	r := agentSays(tc, writer, "remind", []string{"in", "2", "minutes", "check", "the", "CI"}, map[string]string{"about": ask}, "")
	// "remind me" from a person: the reminder is theirs; its id is not for
	// people.
	if r.Code != 0 || !strings.Contains(r.Text, "It is for @owner") || !strings.Contains(r.Text, `quoting this time exactly (it is in the space's time zone): "at 18:02 today (Thu Nov 5, UTC)"`) ||
		!strings.Contains(r.Text, "Don't show the reminder id") {
		t.Fatalf("%v", r)
	}
	list := call(t, h, mine, "reminders", map[string]any{"mid": writer})["reminders"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["at"] != "2026-11-05T18:02:00Z" || list[0].(map[string]any)["root"] != root ||
		list[0].(map[string]any)["for"] != tcOwner {
		t.Fatalf("%v", list)
	}
	before := len(msgs(t, h, mine, "general", root))
	// (Writer's turn has ended: a reminder due during a turn is posted
	// without one.)
	eventually(t, "turn end", func() bool {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["state"] == "ready"
	})

	// The controller stops; the time passes; a new one starts.
	tc.Close()
	clock.Add(2*time.Minute + 3*time.Second)
	tc2 := teamOn(t, url, nodes)
	tc2.now, tc2.remTick, tc2.tick = clock.Now, 20*time.Millisecond, time.Hour
	h2, mine2, _ := teamHost(t, tc2)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc2.Run(ctx)
	eventually(t, "the reminder's turn", func() bool { return nodes.promptCount() == 2 })
	p := nodes.lastPrompt()
	if !strings.Contains(p, "Reminder for @owner") || !strings.Contains(p, "Remind @owner: check the CI") || !strings.Contains(p, "thread on "+root) ||
		!strings.Contains(p, "remind me to check the CI") || !strings.Contains(p, "reply "+ask) || !strings.Contains(p, "This reminder is for @owner, a person") {
		t.Fatalf("reminder turn input: %s", p)
	}
	nodes.mu.Lock()
	cwd := nodes.sends[1]["cwd"]
	nodes.mu.Unlock()
	if cwd != "/home/me/PantheonAgents/writer" {
		t.Fatalf("woke %v", cwd)
	}
	time.Sleep(150 * time.Millisecond)
	if nodes.promptCount() != 2 {
		t.Fatal("the reminder woke more than once, or another agent")
	}
	// The agent posted nothing: the reminder is posted for the person, in
	// the thread, once.
	after := msgs(t, h2, mine2, "general", root)
	if len(after) != before+1 || after[len(after)-1]["text"] != "⏰ Reminder for @owner: check the CI" || after[len(after)-1]["from"] != writer {
		t.Fatalf("reminder for a person: %v", after[before:])
	}
	if n := len(call(t, h2, mine2, "reminders", map[string]any{})["reminders"].([]any)); n != 0 {
		t.Fatalf("a one-off reminder stayed (%d)", n)
	}
}

// T24: a recurring reminder is listed, fires on each time, and cancelling it
// (by the agent, or the person) removes it. A stopped agent is woken when
// started again; deleting an agent cancels its reminders.
func TestRecurringRemindersStoppedAgentsAndDelete(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.reply = func(string) (string, string) { return "", "" }
	clock := &fakeClock{now: time.Date(2026, 11, 5, 18, 0, 0, 0, time.UTC)}
	tc := teamOn(t, url, nodes)
	tc.now, tc.remTick, tc.tick = clock.Now, 20*time.Millisecond, time.Hour
	h, mine, _ := teamHost(t, tc)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc.Run(ctx)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	call(t, h, mine, "space_update", map[string]any{"time_zone": "Europe/Berlin"})
	if e := refused(t, h, mine, "space_update", map[string]any{"time_zone": "Mars/Base"}); !strings.Contains(e, "unknown time zone") {
		t.Fatal(e)
	}
	r := agentSays(tc, writer, "remind", []string{"every weekday 10:00"}, nil, "stand-up notes")
	if r.Code != 0 || !strings.Contains(r.Text, "Europe/Berlin") || !strings.Contains(r.Text, "then every weekday 10:00") {
		t.Fatalf("%v", r)
	}
	r = agentSays(tc, writer, "remind", []string{"in", "1", "minute"}, map[string]string{"every": "1 minute"}, "ping")
	if r.Code != 0 {
		t.Fatalf("%v", r)
	}
	listed := agentSays(tc, writer, "remind", []string{"list"}, nil, "")
	if !strings.Contains(listed.Text, "every weekday 10:00") || !strings.Contains(listed.Text, "every 1 minute") {
		t.Fatalf("%v", listed)
	}
	var ping map[string]any
	for _, x := range call(t, h, mine, "reminders", map[string]any{"mid": writer})["reminders"].([]any) {
		if x.(map[string]any)["note"] == "ping" {
			ping = x.(map[string]any)
		}
	}
	if ping == nil || ping["every"] != "1 minute" || ping["time_zone"] != "Europe/Berlin" {
		t.Fatalf("%v", ping)
	}
	for i := 1; i <= 2; i++ {
		clock.Add(time.Minute)
		eventually(t, fmt.Sprintf("firing %d", i), func() bool { return nodes.promptCount() == i })
		if !strings.Contains(nodes.lastPrompt(), "ping") {
			t.Fatal(nodes.lastPrompt())
		}
	}
	// Cancelled by the agent: nothing more.
	if r := agentSays(tc, writer, "remind", []string{"cancel", ping["id"].(string)}, nil, ""); r.Code != 0 {
		t.Fatalf("%v", r)
	}
	clock.Add(time.Minute)
	time.Sleep(100 * time.Millisecond)
	if nodes.promptCount() != 2 {
		t.Fatal("a cancelled reminder fired")
	}
	// A stopped agent's reminder waits for it.
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "stop"})
	agentSays(tc, writer, "remind", []string{"in", "2", "minutes"}, nil, "after the stop")
	clock.Add(2 * time.Minute)
	time.Sleep(150 * time.Millisecond)
	if nodes.promptCount() != 2 {
		t.Fatal("a stopped agent was woken")
	}
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "start"})
	eventually(t, "the waiting reminder", func() bool { return strings.Contains(nodes.lastPrompt(), "after the stop") })
	// Deleting the agent cancels its reminders (the weekday one is left).
	if n := len(call(t, h, mine, "reminders", map[string]any{})["reminders"].([]any)); n != 1 {
		t.Fatalf("%d reminders", n)
	}
	eventually(t, "the turn's end", func() bool {
		s := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
		return s["state"] == "ready"
	})
	call(t, h, mine, "agent_control", map[string]any{"mid": writer, "action": "delete"})
	if n := len(call(t, h, mine, "reminders", map[string]any{})["reminders"].([]any)); n != 0 {
		t.Fatalf("%d reminders after delete", n)
	}
}

// T25: unread and Activity across channels, DMs and threads; a thread's own
// read mark; Mark all read.
func TestActivityUnreadThreadsAndReview(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	ch := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "docs", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	dm := call(t, h, mine, "conv_create", map[string]any{"kind": "dm", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	root := call(t, h, mine, "post", map[string]any{"cid": ch, "text": "Plan for 0.9"})["message"].(map[string]any)["id"].(string)
	other := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "unrelated"})["message"].(map[string]any)["id"].(string)
	call(t, h, mine, "post", map[string]any{"cid": ch, "root": root, "text": "how far along?"})

	w := func(cid, root, text string) string {
		r := agentSays(tc, writer, "post", []string{cid}, nil, text)
		if root != "" {
			r = agentSays(tc, writer, "reply", []string{root}, nil, text)
		}
		if r.Code != 0 {
			t.Fatalf("%v", r)
		}
		return strings.Fields(r.Text)[len(strings.Fields(r.Text))-1]
	}
	// Writer: one top-level message mentioning me in #docs, two replies in
	// my thread, one DM; and a reply in a thread I don't follow.
	call(t, h, mine, "me_update", map[string]any{"handle": "weize"})
	first := strings.TrimSuffix(w("docs", "", "@weize the draft is up"), ".")
	reply1 := strings.TrimSuffix(w("", root, "step one done"), ".")
	w("", root, "step two done @weize")
	agentSays(tc, writer, "dm", []string{"@weize"}, nil, "psst")
	// A thread the person does not follow: the writer's own top-level message.
	agentRoot := strings.TrimSuffix(w("general", "", "my own note"), ".")
	call(t, h, mine, "read", map[string]any{"cid": "general", "id": agentRoot})
	fl, _ := tc.lockFleet(context.Background(), fleetID)
	if _, err := tc.agentPost(context.Background(), fl, fl.members[writer], nil, "", "general", agentRoot, "a reply nobody follows", nil); err != nil {
		t.Fatal(err)
	}
	fl.mu.Unlock()

	space := call(t, h, mine, "space_get", map[string]any{})
	u := space["unread"].(map[string]any)
	docs := u[ch].(map[string]any)
	if docs["count"] != float64(3) || docs["mentions"] != float64(2) || docs["first"] != first {
		t.Fatalf("docs unread: %v", docs)
	}
	if g := u["general"].(map[string]any); g["count"] != float64(0) {
		t.Fatalf("an unfollowed thread's reply counted: %v", g)
	}
	tu := space["thread_unread"].(map[string]any)[root].(map[string]any)
	if tu["count"] != float64(2) || tu["first"] != reply1 {
		t.Fatalf("thread unread: %v", tu)
	}
	rows := call(t, h, mine, "activity", map[string]any{})["rows"].([]any)
	kinds := map[string]map[string]any{}
	for _, r := range rows {
		r := r.(map[string]any)
		kinds[fmt.Sprint(r["kind"], "/", r["cid"])] = r
	}
	if len(rows) != 3 || kinds["conv/"+ch]["count"] != float64(1) || kinds["thread/"+ch]["first_unread"] != reply1 ||
		kinds["thread/"+ch]["root"] != root || kinds["conv/"+dm]["count"] != float64(1) {
		t.Fatalf("activity: %v", rows)
	}
	if m := call(t, h, mine, "activity", map[string]any{"filter": "mentions"})["rows"].([]any); len(m) != 2 {
		t.Fatalf("mentions: %v", m)
	}
	// Reading the thread clears only the thread.
	call(t, h, mine, "read", map[string]any{"cid": ch, "id": tu["last"], "root": root})
	if n := len(call(t, h, mine, "activity", map[string]any{"filter": "unread"})["rows"].([]any)); n != 2 {
		t.Fatalf("after reading the thread: %d rows", n)
	}
	// In review.
	call(t, h, mine, "task_create", map[string]any{"id": other})
	call(t, h, mine, "task_update", map[string]any{"id": other, "state": "in_review"})
	review := call(t, h, mine, "activity", map[string]any{"filter": "review"})["rows"].([]any)
	if len(review) != 1 || review[0].(map[string]any)["first_unread"] != other {
		t.Fatalf("review: %v", review)
	}
	// My own task line is not news for me.
	if g := call(t, h, mine, "space_get", map[string]any{})["unread"].(map[string]any)["general"].(map[string]any); g["count"] != float64(0) {
		t.Fatalf("my own task line counted: %v", g)
	}
	// Mark all read.
	call(t, h, mine, "read_all", map[string]any{})
	if n := len(call(t, h, mine, "activity", map[string]any{"filter": "unread"})["rows"].([]any)); n != 0 {
		t.Fatalf("after Mark all read: %d rows", n)
	}
	// Writing in a thread reads it.
	w("", root, "step three")
	call(t, h, mine, "post", map[string]any{"cid": ch, "root": root, "text": "thanks"})
	if tu := call(t, h, mine, "space_get", map[string]any{})["thread_unread"].(map[string]any); len(tu) != 0 {
		t.Fatalf("%v", tu)
	}
}

// T26: search by words, channel, author and time over 10,000 messages in
// under a second; an agent finds the same.
func TestSearchTenThousandMessages(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.hold = true
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	ch := call(t, h, mine, "conv_create", map[string]any{"kind": "channel", "name": "load", "members": []string{writer}})["conversation"].(map[string]any)["cid"].(string)
	fl, err := tc.lockFleet(context.Background(), fleetID)
	if err != nil {
		t.Fatal(err)
	}
	words := []string{"alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"}
	for i := 0; i < 10000; i++ {
		cid, from := "general", tcOwner
		if i%2 == 1 {
			cid, from = ch, writer
		}
		text := fmt.Sprintf("message %d about %s and %s", i, words[i%8], words[(i/8)%8])
		if i == 7777 {
			text = "the Zanzibar deploy key rotated"
		}
		if _, err := tc.append(context.Background(), fl, &tcEvent{ID: tcNewID(time.Now()), CID: cid, From: from, Kind: "post", Text: text,
			At: time.Now().UTC().Format(time.RFC3339Nano)}); err != nil {
			t.Fatal(err)
		}
	}
	fl.mu.Unlock()
	res := call(t, h, mine, "search", map[string]any{"q": "zanzibar ROTATED"})
	if res["total"] != float64(1) || res["took_ms"].(float64) >= 1000 {
		t.Fatalf("%v", res)
	}
	start := time.Now()
	res = call(t, h, mine, "search", map[string]any{"q": "alpha bravo", "cid": ch, "from": "@writer", "limit": 10})
	if took := time.Since(start); took > time.Second {
		t.Fatalf("search took %v", took)
	}
	results := res["results"].([]any)
	if res["total"].(float64) == 0 || len(results) != 10 {
		t.Fatalf("%v", res["total"])
	}
	for _, r := range results {
		r := r.(map[string]any)
		if r["cid"] != ch || r["from"] != writer || !strings.Contains(r["text"].(string), "alpha") || !strings.Contains(r["text"].(string), "bravo") {
			t.Fatalf("%v", r)
		}
	}
	if res := call(t, h, mine, "search", map[string]any{"q": "alpha", "from": "@writer", "cid": "general"}); res["total"] != float64(0) {
		t.Fatalf("filtered by channel and author: %v", res["total"])
	}
	today := time.Now().UTC().Format("2006-01-02")
	if res := call(t, h, mine, "search", map[string]any{"q": "zanzibar", "before": today}); res["total"] != float64(0) {
		t.Fatalf("before today: %v", res["total"])
	}
	if res := call(t, h, mine, "search", map[string]any{"q": "zanzibar", "after": "2000-01-01"}); res["total"] != float64(1) {
		t.Fatalf("after 2000: %v", res["total"])
	}
	r := agentSays(tc, writer, "search", []string{"zanzibar"}, nil, "")
	if r.Code != 0 || !strings.Contains(r.Text, "1 found") || !strings.Contains(r.Text, "Zanzibar deploy key") {
		t.Fatalf("%v", r)
	}
	r = agentSays(tc, writer, "search", []string{"alpha"}, map[string]string{"in": "#load", "from": "@owner"}, "")
	if r.Code != 0 || !strings.Contains(r.Text, "No messages found") {
		t.Fatalf("%v", r)
	}
}

func TestNotifyLevelsFilesAndStartingCopy(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	tc := teamOn(t, url, nodes)
	h, mine, _ := teamHost(t, tc)
	call(t, h, mine, "notify_set", map[string]any{"cid": "general", "level": "mute"})
	if e := refused(t, h, mine, "notify_set", map[string]any{"cid": "general", "level": "loud"}); !strings.Contains(e, "level") {
		t.Fatal(e)
	}
	if n := call(t, h, mine, "space_get", map[string]any{})["notify"].(map[string]any); n["general"] != "mute" {
		t.Fatalf("%v", n)
	}
	up := call(t, h, mine, "file_put", map[string]any{"name": "a.txt", "mime": "text/plain", "data": "aGk=", "final": true})
	fid := up["file"].(map[string]any)["id"].(string)
	call(t, h, mine, "post", map[string]any{"cid": "general", "text": "see file", "files": []string{fid}})
	files := call(t, h, mine, "files", map[string]any{"cid": "general"})["files"].([]any)
	if len(files) != 1 || files[0].(map[string]any)["file"].(map[string]any)["name"] != "a.txt" {
		t.Fatalf("%v", files)
	}
	// Starting: the first time, or again.
	fl := &tcFleet{members: map[string]*tcMember{"a1": {MID: "a1", Kind: "agent", Runtime: "pantheon"}}}
	if s := appStartingWhy(fl); !strings.Contains(s, "for the first time") {
		t.Fatal(s)
	}
	fl.members["a1"].Session = "conv1"
	if s := appStartingWhy(fl); !strings.Contains(s, "again") || strings.Contains(s, "first time") {
		t.Fatal(s)
	}
}

// A person's "remind me" is theirs: when it comes due the agent posts it,
// else its final text or the note is posted for the person (also when the
// turn fails or the agent cannot run); an agent's own reminder posts
// nothing. --tz and an ISO time with its offset; the agent confirms in the
// space's time zone; last_ok and unfollows.
func TestRemindersForPeopleAlwaysReachThem(t *testing.T) {
	url := jetStream(t)
	nodes := newFakeAgents()
	setReply := func(text, errText string) {
		nodes.mu.Lock()
		nodes.reply = func(string) (string, string) { return text, errText }
		nodes.mu.Unlock()
	}
	setReply("", "")
	clock := &fakeClock{now: time.Date(2026, 11, 5, 18, 0, 0, 0, time.UTC)}
	tc := teamOn(t, url, nodes)
	tc.now, tc.remTick, tc.tick = clock.Now, 20*time.Millisecond, time.Hour
	h, mine, _ := teamHost(t, tc)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go tc.Run(ctx)
	writer := makeAgent(t, h, mine, "Writer", "n_mac", "claude", nil)
	call(t, h, mine, "space_update", map[string]any{"time_zone": "Europe/Berlin"})

	// Another time zone: --tz, or a time with its offset; confirmed in the
	// space's zone.
	r := agentSays(tc, writer, "remind", []string{"9:00"}, map[string]string{"tz": "Asia/Tokyo"}, "tokyo stand-up")
	if r.Code != 0 || !strings.Contains(r.Text, "Fri Nov 6 09:00 Asia/Tokyo (Fri Nov 6 01:00 Europe/Berlin in the space's time zone)") ||
		!strings.Contains(r.Text, `"at 01:00 tomorrow (Fri Nov 6, Europe/Berlin)"`) {
		t.Fatalf("%v", r)
	}
	r = agentSays(tc, writer, "remind", []string{"2026-11-06T09:00+09:00"}, nil, "offset")
	if r.Code != 0 || !strings.Contains(r.Text, "Fri Nov 6 01:00 Europe/Berlin") {
		t.Fatalf("%v", r)
	}
	if r := agentSays(tc, writer, "remind", []string{"in", "1", "minute"}, map[string]string{"tz": "Mars/Base"}, "x"); r.Code == 0 || !strings.Contains(r.Text, "unknown time zone") {
		t.Fatalf("%v", r)
	}
	if r := agentSays(tc, writer, "remind", []string{"help"}, nil, ""); !strings.Contains(r.Text, "--tz") || !strings.Contains(r.Text, "--for") {
		t.Fatalf("%v", r)
	}
	for _, x := range call(t, h, mine, "reminders", map[string]any{})["reminders"].([]any) {
		rem := x.(map[string]any)
		if rem["at"] != "2026-11-06T00:00:00Z" || rem["for"] != nil {
			t.Fatalf("%v", rem)
		}
		call(t, h, mine, "reminder_cancel", map[string]any{"id": rem["id"]})
	}

	// "remind me": the agent's final text is posted for the person.
	ask := call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer remind me to stretch in a minute"})["message"].(map[string]any)["id"].(string)
	eventually(t, "writer's turn", func() bool { return nodes.promptCount() == 1 })
	r = agentSays(tc, writer, "remind", []string{"in", "1", "minute"}, map[string]string{"about": ask}, "stretch")
	if r.Code != 0 || !strings.Contains(r.Text, "It is for @owner") {
		t.Fatalf("%v", r)
	}
	eventually(t, "turn end", func() bool {
		s := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
		return s["state"] == "ready"
	})
	setReply("Time to stretch!", "")
	n0 := len(msgs(t, h, mine, "general", ""))
	clock.Add(time.Minute)
	eventually(t, "the reminder's post", func() bool { return len(msgs(t, h, mine, "general", "")) == n0+1 })
	if last := msgs(t, h, mine, "general", ""); last[n0]["text"] != "@owner Time to stretch!" || last[n0]["from"] != writer {
		t.Fatalf("%v", last[n0])
	}
	var me map[string]any
	for _, x := range call(t, h, mine, "space_get", map[string]any{})["members"].([]any) {
		if x.(map[string]any)["mid"] == writer {
			me = x.(map[string]any)
		}
	}
	if me["last_ok"] == nil {
		t.Fatalf("no last_ok: %v", me)
	}

	// Its own follow-up: nothing is posted.
	setReply("", "")
	eventually(t, "turn end", func() bool {
		return call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)["state"] == "ready"
	})
	agentSays(tc, writer, "remind", []string{"in", "1", "minute"}, map[string]string{"about": ask, "for": "me"}, "look at the logs")
	turns := nodes.promptCount()
	n1 := len(msgs(t, h, mine, "general", ""))
	clock.Add(time.Minute)
	eventually(t, "the own reminder's turn", func() bool { return nodes.promptCount() == turns+1 })
	if !strings.Contains(nodes.lastPrompt(), "your own follow-up") {
		t.Fatal(nodes.lastPrompt())
	}
	time.Sleep(100 * time.Millisecond)
	if n := len(msgs(t, h, mine, "general", "")); n != n1 {
		t.Fatalf("an own reminder posted (%d -> %d)", n1, n)
	}

	// The turn fails: the note is posted for the person, not kept waiting.
	setReply("", "something odd at 0x1f")
	agentSays(tc, writer, "remind", []string{"in", "1", "minute"}, map[string]string{"about": ask}, "drink water")
	clock.Add(time.Minute)
	eventually(t, "the fallback", func() bool {
		l := msgs(t, h, mine, "general", "")
		return len(l) > 0 && l[len(l)-1]["text"] == "⏰ Reminder for @owner: drink water"
	})
	st := call(t, h, mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[writer].(map[string]any)
	if st["state"] != "attention" || st["queue"] != float64(0) {
		t.Fatalf("%v", st)
	}

	// It cannot run (it needs attention): posted at once, no turn.
	agentSays(tc, writer, "remind", []string{"in", "1", "minute"}, map[string]string{"about": ask}, "call mom")
	turns = nodes.promptCount()
	clock.Add(time.Minute)
	eventually(t, "the posted reminder", func() bool {
		l := msgs(t, h, mine, "general", "")
		return l[len(l)-1]["text"] == "⏰ Reminder for @owner: call mom"
	})
	time.Sleep(100 * time.Millisecond)
	if nodes.promptCount() != turns {
		t.Fatal("an agent that cannot run was woken")
	}

	// An explicit unfollow is listed.
	call(t, h, mine, "follow", map[string]any{"root": ask, "cid": "general", "on": false})
	if u := call(t, h, mine, "space_get", map[string]any{})["unfollows"].([]any); len(u) != 1 || u[0] != ask {
		t.Fatalf("%v", u)
	}
}
