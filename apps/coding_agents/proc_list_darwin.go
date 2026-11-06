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

// procArgs is a process's argument list (nil when it cannot be read).
// KERN_PROCARGS2: argc (int32), the executable's path, NULs, then argc
// NUL-terminated arguments (then its environment).
func procArgs(pid int) []string {
	b, err := unix.SysctlRaw("kern.procargs2", pid)
	if err != nil || len(b) < 4 {
		return nil
	}
	argc := int(int32(uint32(b[0]) | uint32(b[1])<<8 | uint32(b[2])<<16 | uint32(b[3])<<24))
	rest := b[4:]
	i := 0
	for i < len(rest) && rest[i] != 0 { // the executable's path
		i++
	}
	for i < len(rest) && rest[i] == 0 {
		i++
	}
	var out []string
	for len(out) < argc && i < len(rest) {
		j := i
		for j < len(rest) && rest[j] != 0 {
			j++
		}
		out = append(out, string(rest[i:j]))
		i = j + 1
	}
	return out
}

// procVouch: this system can read another process's arguments.
const procVouch = true
