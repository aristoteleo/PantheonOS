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

// reapStrays ends every process that writes to an App instance's component
// log but belongs to no running resource the ledger records for it: a backend
// the node lost track of, or a child left behind by one. Such a process holds
// the instance's locks and data, so no new generation could start beside it
// (2026-11-11: "Project registry is busy" until killed by hand). It sends
// SIGTERM, waits strayGrace, then SIGKILL. only limits it to one instance id
// ("" = all). Called with the serial lock held (no start is between creating
// its process and recording it). Returns an error if a stray survives.
func (m *Manager) reapStrays(ctx context.Context, only string) error {
	table, err := scanAppProcesses(filepath.Join(m.root, "data"))
	if err != nil || len(table.apps) == 0 {
		return err
	}
	// The recorded leaders whose processes still run (or whose state cannot
	// be read: never kill what might be live and recorded).
	keep := map[int]bool{}
	for _, in := range m.Snapshot().Instances {
		for _, r := range in.Resources {
			if r.Runtime != "process" || r.PID <= 0 {
				continue
			}
			c, cancel := context.WithTimeout(ctx, 2*time.Second)
			alive, err := m.driver.Alive(c, r)
			cancel()
			if alive || err != nil {
				keep[r.PID] = true
			}
		}
	}
	self := os.Getpid()
	var strays []appProcess
	for _, p := range table.apps {
		if (only != "" && p.Instance != only) || p.PID == self || owned(p.PID, keep, table) {
			continue
		}
		strays = append(strays, p)
	}
	if len(strays) == 0 {
		return nil
	}
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

// owned reports whether pid belongs to a kept leader: in its process group,
// or a descendant of it (a child that started its own session).
func owned(pid int, keep map[int]bool, table processTable) bool {
	if keep[table.group[pid]] {
		return true
	}
	for i := 0; i < 64 && pid > 1; i++ {
		if keep[pid] {
			return true
		}
		pid = table.parent[pid]
	}
	return false
}
