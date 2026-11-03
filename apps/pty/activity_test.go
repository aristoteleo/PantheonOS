//go:build !windows

package ptyapp

import (
	"fmt"
	"os/exec"
	"slices"
	"sync"
	"testing"
	"time"

	"github.com/aristoteleo/pantheonos/os/codingagents"
)

// meterRun feeds a meter one write per step (every 100 ms of simulated time)
// and returns whether it was working at each tick.
type meterRun struct {
	m     *activityMeter
	now   time.Time
	ticks []bool
	next  time.Time
}

func newMeterRun() *meterRun {
	t0 := time.Unix(1000, 0)
	// Past the startup grace.
	m := newActivityMeter(80, 24, t0.Add(-time.Hour))
	return &meterRun{m: m, now: t0, next: t0.Add(activityTick)}
}

// step advances 100 ms after writing out (nil: nothing).
func (r *meterRun) step(out string) {
	if out != "" {
		r.m.write([]byte(out))
	}
	r.now = r.now.Add(100 * time.Millisecond)
	for !r.next.After(r.now) {
		w, _, _ := r.m.tick(r.next)
		r.ticks = append(r.ticks, w)
		r.next = r.next.Add(activityTick)
	}
}

func (r *meterRun) everWorking() bool {
	for _, w := range r.ticks {
		if w {
			return true
		}
	}
	return false
}

func (r *meterRun) working() bool { return len(r.ticks) > 0 && r.ticks[len(r.ticks)-1] }

// Output that changes nothing on screen is not work: a cursor shown, hidden
// and moved, the same screen drawn again and again, a full-screen program's
// identical repaint.
func TestActivityIgnoresOutputThatChangesNoCell(t *testing.T) {
	r := newMeterRun()
	r.step("\x1b[2J\x1b[HReady> ")
	for i := 0; i < 60; i++ {
		switch i % 3 {
		case 0:
			r.step("\x1b[?25l\x1b[5;10H\x1b[?25h")
		case 1:
			r.step("\x1b[H\x1b[2KReady> \x1b[K")
		case 2:
			r.step("\x1b]0;title\x07\x1b[1;8H")
		}
	}
	if r.everWorking() {
		t.Fatalf("cursor-only output counted as work: %v", r.ticks)
	}
}

// A clock ticking once a second changes a cell every other tick: not work.
func TestActivityIgnoresAClock(t *testing.T) {
	r := newMeterRun()
	for i := 0; i < 100; i++ {
		out := ""
		if i%10 == 0 {
			out = fmt.Sprintf("\r12:00:%02d", i/10)
		}
		r.step(out)
	}
	if r.everWorking() {
		t.Fatalf("a clock counted as work: %v", r.ticks)
	}
}

// Text streaming in, or a spinner turning in place, is work; once it stops,
// the agent is idle again within a few seconds.
func TestActivityWorkingThenIdle(t *testing.T) {
	r := newMeterRun()
	spinner := []string{"|", "/", "-", "\\"}
	for i := 0; i < 30; i++ {
		r.step("\r" + spinner[i%4] + " Thinking")
	}
	if !r.working() {
		t.Fatalf("a turning spinner is not work: %v", r.ticks)
	}
	for i := 0; i < 15; i++ {
		r.step(fmt.Sprintf("line %d of the answer\r\n", i))
	}
	if !r.working() {
		t.Fatalf("streamed text is not work: %v", r.ticks)
	}
	for i := 0; i < 35; i++ {
		r.step("")
	}
	if r.working() {
		t.Fatalf("still working 3.5 s after the screen settled: %v", r.ticks)
	}
}

// A spinner whose frames come round every half second looks the same at
// every half second: what counts is each frame changing the screen.
func TestActivitySeesASpinnerInStepWithTheTick(t *testing.T) {
	r := newMeterRun()
	frames := []string{"|", "/", "-", "\\", "."}
	for i := 0; i < 40; i++ {
		r.step("\r" + frames[i%len(frames)] + " thinking")
	}
	if !r.working() {
		t.Fatalf("a spinner turning with the tick is not work: %v", r.ticks)
	}
}

// A resize, or a keystroke, while the agent works does not end its work.
func TestActivityKeepsWorkingThroughAResize(t *testing.T) {
	r := newMeterRun()
	for i := 0; i < 20; i++ {
		r.step(fmt.Sprintf("\rthinking %d", i))
	}
	if !r.working() {
		t.Fatalf("%v", r.ticks)
	}
	r.m.resized(100, 30, r.now)
	before := len(r.ticks)
	for i := 0; i < 30; i++ {
		r.step(fmt.Sprintf("\rthinking %d", i))
	}
	for _, w := range r.ticks[before:] {
		if !w {
			t.Fatalf("idle across a resize: %v", r.ticks)
		}
	}
}

// What the user types comes back as its echo: theirs, not the agent's.
func TestActivityIgnoresTyping(t *testing.T) {
	r := newMeterRun()
	for _, c := range "please refactor the parser and add tests" {
		r.m.typed(r.now)
		r.step(string(c))
	}
	if r.everWorking() {
		t.Fatalf("typing counted as work: %v", r.ticks)
	}
}

func TestActivityNotices(t *testing.T) {
	m := newActivityMeter(80, 24, time.Unix(0, 0))
	// Codex's own, recorded: its state, then the notification.
	m.write([]byte("\x1b]7501;state=working:app=codex\x1b\\\x1b]7501;state=blocked:app=codex:kind=permission\x1b\\" +
		"\x1b]9;Approval requested: rm -rf build\x07\x1b]9;4;1;50\x07\x1b]777;notify;Gemini;Waiting for confirmation\x1b\\" +
		"\x1b]7501;state=idle:app=codex\x07\x1b]7501;?\x1b\\"))
	_, _, got := m.tick(time.Unix(10, 0))
	want := []codingagents.Activity{{State: "working"}, {State: "waiting", Detail: "permission"},
		{Notice: "Approval requested: rm -rf build"}, {Notice: "Gemini: Waiting for confirmation"}, {State: "ready"}}
	if !slices.Equal(got, want) {
		t.Fatalf("signals %+v", got)
	}
}

// A coding agent's terminal launch reports its screen to the Coding Agents
// service: working while the command prints, idle after, closed at the end.
func TestPTYReportsActivityOfAnAgentLaunch(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("no sh")
	}
	t.Setenv("PANTHEON_PTY_BANNER", "0")
	old := activityStartup
	activityStartup = 0
	defer func() { activityStartup = old }()
	var mu sync.Mutex
	var got []string
	command := "for i in $(seq 25); do echo working $i; sleep 0.1; done; sleep 3; exit 0"
	codingagents.WatchCommand(command, "tAct", func(a codingagents.Activity) {
		mu.Lock()
		defer mu.Unlock()
		switch {
		case a.Closed:
			got = append(got, "closed")
		case a.Working:
			got = append(got, "working")
		default:
			got = append(got, "idle")
		}
	})
	defer codingagents.ForgetTerminal("tAct")
	app := NewApp(nil, "", t.TempDir())
	defer app.Close()
	res := app.open(80, 24, "", "/bin/sh", command)
	if ok, _ := res["success"].(bool); !ok {
		t.Fatalf("open: %v", res)
	}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := len(got)
		mu.Unlock()
		if n >= 2 {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	app.closeSession(res["session_id"].(string))
	time.Sleep(700 * time.Millisecond)
	mu.Lock()
	defer mu.Unlock()
	if len(got) < 3 || got[0] != "working" || got[1] != "idle" || got[len(got)-1] != "closed" {
		t.Fatalf("reports %v", got)
	}
}
