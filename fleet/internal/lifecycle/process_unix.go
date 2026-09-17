//go:build !windows

package lifecycle

import (
	"os/exec"
	"syscall"
)

func configureProcess(c *exec.Cmd)       { c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }
func terminateProcess(pid int) error     { return syscall.Kill(-pid, syscall.SIGTERM) }
func killCommandGroup(c *exec.Cmd) error { return syscall.Kill(-c.Process.Pid, syscall.SIGKILL) }
