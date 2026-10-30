package codingagentsapp

import "os/exec"

func setProcessGroup(*exec.Cmd) {}

func interrupt(cmd *exec.Cmd) { kill(cmd) }

func kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}

// noFollow: Windows has no such flag; upload parts get fresh random names
// and are checked with Lstat instead.
const noFollow = 0
