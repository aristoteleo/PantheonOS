//go:build !windows

package codingagentsapp

import (
	"fmt"
	"os"
	"os/exec"
	"syscall"
)

// A turn runs in its own process group so Stop reaches the tools the agent
// started, not only the agent.
func setProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func interrupt(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGINT)
	}
}

func kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}

// noFollow makes opening a link fail instead of reaching its target.
const noFollow = syscall.O_NOFOLLOW

// ownedByMe reports whether a file belongs to the user this service runs as.
func ownedByMe(fi os.FileInfo) bool {
	st, ok := fi.Sys().(*syscall.Stat_t)
	return ok && int(st.Uid) == os.Geteuid()
}

// linksSend: agents here can message linked groups (the command is sh).
const linksSend = true

// nonBlock keeps opening a FIFO from waiting for a writer.
const nonBlock = syscall.O_NONBLOCK

// fileKey identifies a file's contents in place: device, inode, size and
// change time.
func fileKey(fi os.FileInfo) string {
	if st, ok := fi.Sys().(*syscall.Stat_t); ok {
		return fmt.Sprintf("%d/%d/%d/%d", st.Dev, st.Ino, fi.Size(), fi.ModTime().UnixNano())
	}
	return fmt.Sprintf("%s/%d/%d", fi.Name(), fi.Size(), fi.ModTime().UnixNano())
}

// setSession: a Team Chat turn runs in a session (and process group) of its
// own, so what its agent leaves running can be found and ended with it.
func setSession(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
}

// turnProcs are the processes of a turn whose agent is leader: its session
// and process group, and its descendants (by parent), each with its start
// time.
func turnProcs(leader int) map[int]int64 {
	procs := listProcs()
	out := map[int]int64{}
	kids := map[int][]int{}
	for _, p := range procs {
		kids[p.ppid] = append(kids[p.ppid], p.pid)
	}
	byPID := map[int]procInfo{}
	for _, p := range procs {
		byPID[p.pid] = p
		if p.pgid == leader || p.sid == leader {
			out[p.pid] = p.start
		}
	}
	stack := []int{leader}
	for len(stack) > 0 {
		pid := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		for _, k := range kids[pid] {
			if _, ok := out[k]; !ok {
				out[k] = byPID[k].start
			}
			stack = append(stack, k)
		}
	}
	return out
}

// endTurnProcs ends what a turn left running once its agent has exited:
// every process still in its session or process group, and every process
// seen as its descendant during the turn that is still that process (same
// start time). It returns how many it ended.
func endTurnProcs(leader int, seen map[int]int64) int {
	if leader <= 1 {
		return 0
	}
	self := os.Getpid()
	n := 0
	for _, p := range listProcs() {
		if p.pid <= 1 || p.pid == self || p.pid == os.Getppid() {
			continue
		}
		start, known := seen[p.pid]
		if p.sid == leader || p.pgid == leader || known && start == p.start {
			if syscall.Kill(p.pid, syscall.SIGKILL) == nil {
				n++
			}
		}
	}
	_ = syscall.Kill(-leader, syscall.SIGKILL)
	return n
}
