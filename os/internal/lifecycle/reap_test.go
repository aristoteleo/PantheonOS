package lifecycle

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"testing"
	"time"
)

// fakeProcesses replaces the process scan and signals: pids in alive run
// until signalled; ignoreTERM pids only end on SIGKILL.
type fakeProcesses struct {
	mu         sync.Mutex
	table      processTable
	alive      map[int]bool
	ignoreTERM map[int]bool
	signals    []string
}

func (f *fakeProcesses) install(t *testing.T) {
	scan, signal, exists, grace, kill := scanAppProcesses, signalStray, strayExists, strayGrace, strayKillWait
	t.Cleanup(func() {
		scanAppProcesses, signalStray, strayExists, strayGrace, strayKillWait = scan, signal, exists, grace, kill
	})
	strayGrace, strayKillWait = 200*time.Millisecond, 200*time.Millisecond
	scanAppProcesses = func(string) (processTable, error) {
		f.mu.Lock()
		defer f.mu.Unlock()
		out := processTable{parent: f.table.parent, group: f.table.group}
		for _, p := range f.table.apps {
			if f.alive[p.PID] {
				out.apps = append(out.apps, p)
			}
		}
		return out, nil
	}
	signalStray = func(pid int, kill bool) error {
		f.mu.Lock()
		defer f.mu.Unlock()
		name := "TERM"
		if kill {
			name = "KILL"
		}
		f.signals = append(f.signals, name+":"+itoa(pid))
		if kill || !f.ignoreTERM[pid] {
			f.alive[pid] = false
		}
		return nil
	}
	strayExists = func(pid int) bool { f.mu.Lock(); defer f.mu.Unlock(); return f.alive[pid] }
}

func itoa(n int) string {
	return string(rune('0'+n/100%10)) + string(rune('0'+n/10%10)) + string(rune('0'+n%10))
}

func (f *fakeProcesses) sent() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := append([]string{}, f.signals...)
	sort.Strings(out)
	return out
}

// A running instance keeps its recorded leader, its group and processes that
// left it (a daemonized server); a backend the node started and lost track of
// is ended (SIGKILL after the grace for one that ignores SIGTERM), and so is
// every process of a stopped instance.
func TestReapStraysKeepsRecordedProcessesAndEndsTheRest(t *testing.T) {
	m, f, d := setup(t)
	if op := submit(t, m, d, "install", "install", "s", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if op := submit(t, m, d, "start", "start", "s", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	running := m.instanceID(d, "s")
	leader, self := 50, os.Getpid()
	m.update(func() { m.ledger.Instances[running].Resources[0].PID = leader })
	_ = f
	other := "0123456789abcdef0123456789abcdef"
	procs := &fakeProcesses{
		table: processTable{
			parent: map[int]int{leader: self, 101: leader, 102: 101, 103: self + 7, 200: self, 201: self, 300: self + 7},
			group:  map[int]int{leader: leader, 101: leader, 102: 102, 103: 103, 200: 200, 201: 201, 300: 300},
			apps: []appProcess{{leader, running, "backend"}, {101, running, "backend"}, {102, running, "backend"},
				{103, running, "backend"}, {200, running, "backend"}, {201, running, "backend"}, {300, other, "backend"}},
		},
		alive:      map[int]bool{leader: true, 101: true, 102: true, 103: true, 200: true, 201: true, 300: true},
		ignoreTERM: map[int]bool{201: true},
	}
	procs.install(t)
	m.serial.Lock()
	err := m.reapStrays(context.Background(), running)
	m.serial.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	got := procs.sent()
	want := []string{"KILL:201", "TERM:200", "TERM:201"}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] || got[2] != want[2] {
		t.Fatal("signals", got)
	}
	for _, pid := range []int{leader, 101, 102, 103, 300} {
		if !procs.alive[pid] {
			t.Fatal("ended a process it had to keep (or another instance's)", pid)
		}
	}
	m.serial.Lock()
	err = m.reapStrays(context.Background(), "")
	m.serial.Unlock()
	if err != nil || procs.alive[300] {
		t.Fatal("stopped instance's process kept", err)
	}
}

// A start never runs beside a process of an earlier generation: the stray is
// ended first, and one that cannot be ended fails the start.
func TestStartEndsStrayOfEarlierGenerationFirst(t *testing.T) {
	m, _, d := setup(t)
	submit(t, m, d, "install", "install", "s", 0)
	id := m.instanceID(d, "s")
	procs := &fakeProcesses{
		table: processTable{parent: map[int]int{400: 1}, group: map[int]int{400: 400},
			apps: []appProcess{{400, id, "backend"}}},
		alive: map[int]bool{400: true}, ignoreTERM: map[int]bool{},
	}
	procs.install(t)
	if op := submit(t, m, d, "start", "start", "s", 0); op.State != "succeeded" {
		t.Fatal(op)
	}
	if procs.alive[400] {
		t.Fatal("start ran beside a stray of its instance")
	}
	submit(t, m, d, "stop", "stop", "s", 1)
	procs.mu.Lock()
	procs.alive[400], procs.ignoreTERM[400] = true, true
	procs.mu.Unlock()
	signal := signalStray
	signalStray = func(pid int, kill bool) error { return nil } // cannot be ended
	defer func() { signalStray = signal }()
	if op := submit(t, m, d, "start2", "start", "s", 2); op.State != "failed" {
		t.Fatal("started beside a live stray", op.State)
	}
}

var _ = filepath.Join
