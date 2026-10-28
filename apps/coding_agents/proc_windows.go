package codingagentsapp

import "os/exec"

func setProcessGroup(*exec.Cmd) {}

func interrupt(cmd *exec.Cmd) { kill(cmd) }

func kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}
