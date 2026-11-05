package hosted

import (
	"context"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/internal/apptransport"
)

// remindRig is a space with one agent (Writer, on n_mac, or a Pantheon
// Agent on the Workspace) that a person asked in #general to remind them.
type remindRig struct {
	t     *testing.T
	tc    *TeamChat
	nodes *fakeAgents
	clock *fakeClock
	h     *Host
	mine  apptransport.Binding
	agent string
	ask   string
}

func newRemindRig(t *testing.T, runtime string, app func(string) (string, bool)) *remindRig {
	t.Helper()
	url := jetStream(t)
	nodes := newFakeAgents()
	nodes.gone, nodes.deaf = map[string]bool{}, map[string]bool{}
	nodes.detect = append(nodes.detect, map[string]any{"id": "pantheon", "conversation": true, "signed_in": true})
	nodes.reply = func(string) (string, string) { return "", "" }
	clock := &fakeClock{now: time.Date(2026, 11, 5, 18, 0, 0, 0, time.UTC)}
	tc := teamOn(t, url, nodes)
	tc.now, tc.remTick, tc.tick = clock.Now, 20*time.Millisecond, time.Hour
	tc.backoffs = []time.Duration{time.Minute}
	tc.AgentApp = app
	tc.appRecheck, tc.appWait = 20*time.Millisecond, time.Hour
	h, mine, _ := teamHost(t, tc)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go tc.Run(ctx)
	node := "n_mac"
	if runtime == "pantheon" {
		node = "n_ws"
	}
	r := &remindRig{t: t, tc: tc, nodes: nodes, clock: clock, h: h, mine: mine}
	r.agent = makeAgent(t, h, mine, "Writer", node, runtime, nil)
	r.ask = call(t, h, mine, "post", map[string]any{"cid": "general", "text": "@writer remind me later"})["message"].(map[string]any)["id"].(string)
	if app == nil {
		eventually(t, "the asked turn", func() bool { return nodes.promptCount() == 1 })
		eventually(t, "turn end", func() bool { return r.status()["state"] == "ready" })
	}
	return r
}

func (r *remindRig) status() map[string]any {
	return call(r.t, r.h, r.mine, "space_get", map[string]any{})["agent_status"].(map[string]any)[r.agent].(map[string]any)
}

func (r *remindRig) set(f func()) { r.nodes.mu.Lock(); f(); r.nodes.mu.Unlock() }

// remind sets a reminder for the person, due in a minute.
func (r *remindRig) remind(note string) {
	r.t.Helper()
	if out := agentSays(r.tc, r.agent, "remind", []string{"in", "1", "minute"}, map[string]string{"about": r.ask}, note); out.Code != 0 ||
		!strings.Contains(out.Text, "It is for @owner") {
		r.t.Fatalf("%v", out)
	}
}

// delivered waits for "⏰ Reminder for @owner: <note>" in #general and checks
// that the reminder left the store and the agent's queue.
func (r *remindRig) delivered(note string) {
	r.t.Helper()
	want := "⏰ Reminder for @owner: " + note
	eventually(r.t, want, func() bool {
		for _, m := range msgs(r.t, r.h, r.mine, "general", "") {
			if m["text"] == want {
				return true
			}
		}
		return false
	})
	for _, x := range call(r.t, r.h, r.mine, "reminders", map[string]any{})["reminders"].([]any) {
		if x.(map[string]any)["note"] == note {
			r.t.Fatalf("still listed: %v", x)
		}
	}
	fl, err := r.tc.lockFleet(context.Background(), fleetID)
	if err != nil {
		r.t.Fatal(err)
	}
	defer fl.mu.Unlock()
	for _, w := range fl.queues[r.agent] {
		if w.forPerson(fl) {
			r.t.Fatalf("still queued: %+v", w)
		}
	}
}

// queue puts a due person's reminder wake straight in the agent's queue
// (as fireDue does when the agent looked able to run), with a person's
// message after it.
func (r *remindRig) queue(note string, withPost bool) {
	r.t.Helper()
	fl, err := r.tc.lockFleet(context.Background(), fleetID)
	if err != nil {
		r.t.Fatal(err)
	}
	r.tc.enqueue(context.Background(), fl, r.agent, tcWake{ID: r.ask, Event: "rem-x" + note, CID: "general", Kind: "reminder",
		Reason: "reminder", From: r.agent, At: r.clock.Now().Format(time.RFC3339Nano), Rem: "rx", Note: note, For: tcOwner})
	fl.mu.Unlock()
	if withPost {
		call(r.t, r.h, r.mine, "post", map[string]any{"cid": "general", "text": "@writer and also this"})
	}
}

func (r *remindRig) noTurnSince(n int) {
	r.t.Helper()
	time.Sleep(100 * time.Millisecond)
	if got := r.nodes.promptCount(); got != n {
		r.t.Fatalf("a turn ran (%d -> %d)", n, got)
	}
}

// A person's reminder reaches them on time whatever keeps its agent from a
// turn: each limit, an offline or silent computer, a Workspace whose Agent
// App is not ready, a used-up budget, a stopped agent.
func TestAPersonsReminderIsPostedWhenTheTurnCannotRun(t *testing.T) {
	limits := map[string]map[string]any{
		"hourly turns": {"turns_per_agent_hour": 1},
		"daily turns":  {"turns_per_space_day": 1},
		"turn minutes": {"turn_minutes_per_agent_hour": 1},
	}
	for name, settings := range limits {
		t.Run("limit at due: "+name, func(t *testing.T) {
			r := newRemindRig(t, "claude", nil)
			if name == "turn minutes" {
				fl, _ := r.tc.lockFleet(context.Background(), fleetID)
				m := *fl.members[r.agent]
				now := r.clock.Now().Unix()
				m.Spans = [][2]int64{{now - 600, now}}
				fl.members[r.agent] = &m
				fl.mu.Unlock()
			}
			call(t, r.h, r.mine, "space_update", map[string]any{"settings": settings})
			r.remind("check the oven")
			n := r.nodes.promptCount()
			r.clock.Add(time.Minute)
			r.delivered("check the oven")
			r.noTurnSince(n)
		})
		t.Run("limit trips the queued turn: "+name, func(t *testing.T) {
			r := newRemindRig(t, "claude", nil)
			if name == "turn minutes" {
				fl, _ := r.tc.lockFleet(context.Background(), fleetID)
				m := *fl.members[r.agent]
				now := r.clock.Now().Unix()
				m.Spans = [][2]int64{{now - 600, now}}
				fl.members[r.agent] = &m
				fl.mu.Unlock()
			}
			call(t, r.h, r.mine, "space_update", map[string]any{"settings": settings})
			n := r.nodes.promptCount()
			r.queue("deploy window opens", true)
			r.delivered("deploy window opens")
			r.noTurnSince(n)
			// The person's message still waits, with the pause line.
			if s := r.status(); s["queue"] != float64(1) || s["state"] != "attention" {
				t.Fatalf("%v", s)
			}
			if lines := systemLines(msgs(t, r.h, r.mine, "general", "")); len(lines) == 0 || !strings.HasPrefix(lines[len(lines)-1], "Paused: ") {
				t.Fatalf("%v", lines)
			}
		})
	}

	t.Run("only reminders queued: no pause line", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		call(t, r.h, r.mine, "space_update", map[string]any{"settings": map[string]any{"turns_per_agent_hour": 1}})
		r.queue("one", false)
		r.queue("two", false)
		r.delivered("one")
		r.delivered("two") // both, though in one conversation
		if lines := systemLines(msgs(t, r.h, r.mine, "general", "")); len(lines) != 0 {
			t.Fatalf("%v", lines)
		}
		if s := r.status(); s["queue"] != float64(0) {
			t.Fatalf("%v", s)
		}
	})

	t.Run("12 in a row: posted though agents are paused there", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		call(t, r.h, r.mine, "space_update", map[string]any{"settings": map[string]any{"agent_messages_in_a_row": 2}})
		for i := 0; i < 2; i++ {
			agentSays(r.tc, r.agent, "post", []string{"#general"}, nil, "agent line")
		}
		r.set(func() { r.nodes.reply = func(string) (string, string) { return "Time to stretch!", "" } })
		r.remind("stretch")
		r.clock.Add(time.Minute)
		eventually(t, "the reminder", func() bool {
			l := msgs(t, r.h, r.mine, "general", "")
			for _, m := range l {
				if m["text"] == "⏰ Reminder for @owner: stretch" || m["text"] == "@owner Time to stretch!" {
					return true
				}
			}
			return false
		})
	})

	t.Run("computer offline at due", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		r.set(func() { r.nodes.online["n_mac"] = false })
		r.remind("water the plants")
		n := r.nodes.promptCount()
		r.clock.Add(time.Minute)
		r.delivered("water the plants")
		r.noTurnSince(n)
	})

	t.Run("computer gone after the turn was queued", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		r.set(func() { r.nodes.gone["n_mac"] = true })
		n := r.nodes.promptCount()
		r.queue("gone", true)
		r.delivered("gone")
		r.noTurnSince(n)
		if s := r.status(); s["state"] != "offline" || s["queue"] != float64(1) {
			t.Fatalf("%v", s)
		}
	})

	t.Run("computer silent (no responders)", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		r.set(func() { r.nodes.deaf["n_mac"] = true })
		n := r.nodes.promptCount()
		r.queue("silent", false)
		r.delivered("silent")
		r.noTurnSince(n)
	})

	t.Run("Workspace's Agent App not ready", func(t *testing.T) {
		var mu sync.Mutex
		state := "starting"
		r := newRemindRig(t, "pantheon", func(string) (string, bool) { mu.Lock(); defer mu.Unlock(); return state, true })
		r.remind("stand up")
		r.clock.Add(time.Minute)
		r.delivered("stand up")
		if r.nodes.promptCount() != 0 {
			t.Fatal("a turn ran")
		}
		if s := r.status(); s["state"] != "starting" || s["queue"] != float64(1) {
			t.Fatalf("the person's message should still wait: %v", s)
		}
	})

	t.Run("Workspace paused (its node offline)", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		r.set(func() { r.nodes.online["n_mac"] = false })
		n := r.nodes.promptCount()
		r.queue("ws paused", false)
		r.delivered("ws paused")
		r.noTurnSince(n)
	})

	t.Run("budget used up", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		r.set(func() { r.nodes.sendErr = "LiteLLM: Budget has been exceeded (402)" })
		r.queue("pay rent", true)
		r.delivered("pay rent")
		eventually(t, "attention", func() bool { return r.status()["state"] == "attention" })
		if s := r.status(); s["queue"] != float64(1) {
			t.Fatalf("%v", s)
		}
	})

	t.Run("a transient failure with a long retry", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		r.set(func() { r.nodes.sendErr = "the model service is overloaded" })
		r.queue("later", false)
		r.delivered("later")
	})

	t.Run("stopped after the turn was queued", func(t *testing.T) {
		r := newRemindRig(t, "claude", nil)
		call(t, r.h, r.mine, "agent_control", map[string]any{"mid": r.agent, "action": "stop"})
		n := r.nodes.promptCount()
		r.queue("stopped", true)
		r.delivered("stopped")
		r.noTurnSince(n)
	})
}

// The turn input tells the agent the time now in the space's time zone, and
// remind answers with a phrase to quote.
func TestTheAgentKnowsTheTimeAndQuotesRemindsAnswer(t *testing.T) {
	r := newRemindRig(t, "claude", nil)
	call(t, r.h, r.mine, "space_update", map[string]any{"time_zone": "America/Los_Angeles"})
	if p := r.nodes.lastPrompt(); !strings.Contains(p, "Now: ") {
		t.Fatal(p)
	}
	call(t, r.h, r.mine, "post", map[string]any{"cid": "general", "text": "@writer what time is it"})
	eventually(t, "a turn", func() bool { return r.nodes.promptCount() == 2 })
	if p := r.nodes.lastPrompt(); !strings.Contains(p, "Now: Thu Nov 5 10:00 America/Los_Angeles (18:00 UTC)") {
		t.Fatal(p)
	}
	out := agentSays(r.tc, r.agent, "remind", []string{"in", "2", "minutes"}, map[string]string{"about": r.ask}, "check the CI")
	if out.Code != 0 || !strings.Contains(out.Text, `"at 10:02 today (Thu Nov 5, America/Los_Angeles)"`) {
		t.Fatalf("%v", out)
	}
	out = agentSays(r.tc, r.agent, "remind", []string{"9:00"}, map[string]string{"about": r.ask, "tz": "Asia/Tokyo"}, "tokyo")
	if out.Code != 0 || !strings.Contains(out.Text, `"at 16:00 today (Thu Nov 5, America/Los_Angeles)"`) {
		t.Fatalf("%v", out)
	}
	out = agentSays(r.tc, r.agent, "remind", []string{"tomorrow", "9:00"}, map[string]string{"about": r.ask}, "standup")
	if out.Code != 0 || !strings.Contains(out.Text, `"at 09:00 tomorrow (Fri Nov 6, America/Los_Angeles)"`) {
		t.Fatalf("%v", out)
	}
	out = agentSays(r.tc, r.agent, "remind", []string{"2026-11-09", "09:00"}, map[string]string{"about": r.ask}, "monday")
	if out.Code != 0 || !strings.Contains(out.Text, `"at 09:00 on Mon Nov 9 (America/Los_Angeles)"`) {
		t.Fatalf("%v", out)
	}
	if !strings.Contains(tcFinishRule, "only after remind answers") || !strings.Contains(tcFinishRule, "person's language") {
		t.Fatal(tcFinishRule)
	}
}
