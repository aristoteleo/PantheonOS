package codingagentsapp

import (
	"os"
	"strconv"
	"strings"
)

type procInfo struct {
	pid, ppid, pgid, sid int
	start                int64 // clock ticks since boot
}

// listProcs is every process of this computer that can be seen (/proc).
func listProcs() []procInfo {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil
	}
	var out []procInfo
	for _, e := range entries {
		pid, err := strconv.Atoi(e.Name())
		if err != nil {
			continue
		}
		b, err := os.ReadFile("/proc/" + e.Name() + "/stat")
		if err != nil {
			continue
		}
		// pid (comm) state ppid pgrp session … starttime (field 22)
		s := string(b)
		i := strings.LastIndexByte(s, ')')
		if i < 0 {
			continue
		}
		f := strings.Fields(s[i+1:])
		if len(f) < 20 {
			continue
		}
		ppid, _ := strconv.Atoi(f[1])
		pgid, _ := strconv.Atoi(f[2])
		sid, _ := strconv.Atoi(f[3])
		start, _ := strconv.ParseInt(f[19], 10, 64)
		out = append(out, procInfo{pid: pid, ppid: ppid, pgid: pgid, sid: sid, start: start})
	}
	return out
}

// procArgs is a process's argument list (nil when it cannot be read).
func procArgs(pid int) []string {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/cmdline")
	if err != nil || len(b) == 0 {
		return nil
	}
	return strings.Split(strings.TrimSuffix(string(b), "\x00"), "\x00")
}

// procVouch: this system can read another process's arguments.
const procVouch = true
