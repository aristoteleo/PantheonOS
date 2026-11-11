package lifecycle

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"
)

var (
	errNoStartTicks = errors.New("process start ticks unavailable on this platform")
	errProcessGone  = errors.New("process is gone")
	instanceRE      = regexp.MustCompile(`^[a-f0-9]{32}$`)
)

type appProcess struct {
	PID       int
	Instance  string
	Component string
}

type processTable struct {
	parent, group map[int]int
	apps          []appProcess
}

// Test seams: the process scan and the signals the reaper sends.
var (
	scanAppProcesses = scanProcesses
	signalStray      = signalProcess
	strayExists      = processExists
	strayGrace       = 10 * time.Second
	strayKillWait    = killWait
)

// reapStrays ends App processes the ledger does not run, so no new
// generation starts beside an old one that holds the instance's locks and
// data (2026-11-11: "Project registry is busy" until killed by hand). A
// process belongs to an instance when its stdout or stderr is the instance's
// component log. For an instance with nothing running every such process is a
// stray; for a running instance only a backend this node started and lost
// track of (its direct child, not a recorded leader or in a recorded group).
// Other processes of a running instance that left its process group (a
// daemonized window server, a session of its own) are its own business. It
// sends SIGTERM, waits strayGrace, then SIGKILL. only limits it to one
// instance id ("" = all). Called with the serial lock held (no start is
// between creating its process and recording it). Returns an error if a
// stray survives.
func (m *Manager) reapStrays(ctx context.Context, only string) error {
	strays, err := m.findStrays(ctx, only)
	if err != nil || len(strays) == 0 {
		return err
	}
	return endStrays(ctx, strays)
}

// findStrays lists the processes reapStrays ends, without signalling any.
func (m *Manager) findStrays(ctx context.Context, only string) ([]appProcess, error) {
	table, err := scanAppProcesses(filepath.Join(m.root, "data"))
	if err != nil || len(table.apps) == 0 {
		return nil, err
	}
	// The recorded leaders whose processes still run (or whose state cannot
	// be read: never kill what might be live and recorded), and their instances.
	keep, running := map[int]bool{}, map[string]bool{}
	for id, in := range m.Snapshot().Instances {
		for _, r := range in.Resources {
			if r.Runtime != "process" || r.PID <= 0 {
				continue
			}
			c, cancel := context.WithTimeout(ctx, 2*time.Second)
			alive, err := m.driver.Alive(c, r)
			cancel()
			if alive || err != nil {
				keep[r.PID], running[id] = true, true
			}
		}
	}
	self := os.Getpid()
	var strays []appProcess
	for _, p := range table.apps {
		if (only != "" && p.Instance != only) || p.PID == self {
			continue
		}
		if running[p.Instance] && (table.parent[p.PID] != self || keep[p.PID] || keep[table.group[p.PID]]) {
			continue
		}
		strays = append(strays, p)
	}
	return strays, nil
}

func endStrays(ctx context.Context, strays []appProcess) error {
	sort.Slice(strays, func(i, j int) bool { return strays[i].PID < strays[j].PID })
	for _, p := range strays {
		log.Printf("[lifecycle] instance %s: ending stray %s process %d (not a running recorded resource)", p.Instance, p.Component, p.PID)
		_ = signalStray(p.PID, false)
	}
	alive := func() []appProcess {
		var left []appProcess
		for _, p := range strays {
			if strayExists(p.PID) {
				left = append(left, p)
			}
		}
		return left
	}
	wait := func(d time.Duration) []appProcess {
		deadline := time.Now().Add(d)
		for {
			left := alive()
			if len(left) == 0 || time.Now().After(deadline) || ctx.Err() != nil {
				return left
			}
			time.Sleep(100 * time.Millisecond)
		}
	}
	left := wait(strayGrace)
	for _, p := range left {
		log.Printf("[lifecycle] instance %s: stray process %d ignored SIGTERM; killing", p.Instance, p.PID)
		_ = signalStray(p.PID, true)
	}
	if left = wait(strayKillWait); len(left) > 0 {
		return fmt.Errorf("a process of a previous generation (%d) is still running", left[0].PID)
	}
	return nil
}
