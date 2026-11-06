//go:build !darwin && !linux && !windows

package codingagentsapp

type procInfo struct {
	pid, ppid, pgid, sid int
	start                int64
}

// listProcs: not known on this system (only the process group is ended).
func listProcs() []procInfo { return nil }

func procArgs(int) []string { return nil }

// procVouch: this system cannot read another process's arguments (Team
// Chat requests are bound to the turn by its credential only).
const procVouch = false
