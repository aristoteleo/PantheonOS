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
