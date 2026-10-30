package codingagents

import "os/exec"

func detach(*exec.Cmd) {}

// killGroup kills a detached command.
func killGroup(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}
