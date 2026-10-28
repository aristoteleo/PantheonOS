//go:build !windows

package codingagentsapp

import (
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
