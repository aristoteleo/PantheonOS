package codingagentsapp

import "golang.org/x/sys/unix"

type procInfo struct {
	pid, ppid, pgid, sid int
	start                int64 // microseconds
}

// listProcs is every process of this computer that can be seen.
func listProcs() []procInfo {
	all, err := unix.SysctlKinfoProcSlice("kern.proc.all")
	if err != nil {
		return nil
	}
	out := make([]procInfo, 0, len(all))
	for _, k := range all {
		pid := int(k.Proc.P_pid)
		sid, err := unix.Getsid(pid)
		if err != nil {
			sid = -1
		}
		st := k.Proc.P_starttime
		out = append(out, procInfo{pid: pid, ppid: int(k.Eproc.Ppid), pgid: int(k.Eproc.Pgid), sid: sid,
			start: int64(st.Sec)*1e6 + int64(st.Usec)})
	}
	return out
}
