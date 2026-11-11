//go:build linux

package lifecycle

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// processStat reads ppid, process group and start ticks from /proc/<pid>/stat.
// The command name may contain spaces and parentheses: fields follow the last ')'.
func processStat(pid int) (ppid, pgrp int, start uint64, err error) {
	raw, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "stat"))
	if err != nil {
		if os.IsNotExist(err) {
			err = errProcessGone
		}
		return 0, 0, 0, err
	}
	i := strings.LastIndexByte(string(raw), ')')
	if i < 0 {
		return 0, 0, 0, errProcessGone
	}
	f := strings.Fields(string(raw[i+1:]))
	if len(f) < 20 { // f[0] is field 3 (state); starttime is field 22
		return 0, 0, 0, errProcessGone
	}
	ppid, _ = strconv.Atoi(f[1])
	pgrp, _ = strconv.Atoi(f[2])
	start, err = strconv.ParseUint(f[19], 10, 64)
	return ppid, pgrp, start, err
}

func processStartTicks(pid int) (uint64, error) {
	_, _, start, err := processStat(pid)
	return start, err
}

// scanProcesses lists this host's processes (parent and group of each) and
// those whose stdout or stderr is a component log under dataRoot
// (<dataRoot>/<instance>/<component>.log): every process the driver started
// for an App, and every child that inherited its output. Processes this node
// cannot inspect (another user's) are not listed.
func scanProcesses(dataRoot string) (processTable, error) {
	table := processTable{parent: map[int]int{}, group: map[int]int{}}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return table, err
	}
	prefix := filepath.Clean(dataRoot) + string(filepath.Separator)
	for _, e := range entries {
		pid, err := strconv.Atoi(e.Name())
		if err != nil || pid <= 0 {
			continue
		}
		ppid, pgrp, start, err := processStat(pid)
		if err != nil {
			continue
		}
		table.parent[pid], table.group[pid] = ppid, pgrp
		for _, fd := range []string{"1", "2"} {
			target, err := os.Readlink(filepath.Join("/proc", e.Name(), "fd", fd))
			if err != nil || !strings.HasPrefix(target, prefix) {
				continue
			}
			instance, file, ok := strings.Cut(strings.TrimPrefix(target, prefix), string(filepath.Separator))
			if !ok || strings.Contains(file, string(filepath.Separator)) || !strings.HasSuffix(file, ".log") || !instanceRE.MatchString(instance) {
				continue
			}
			table.apps = append(table.apps, appProcess{PID: pid, Instance: instance, Component: strings.TrimSuffix(file, ".log"), Start: start})
			break
		}
	}
	return table, nil
}
