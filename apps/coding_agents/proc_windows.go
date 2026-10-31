package codingagentsapp

import (
	"fmt"
	"os"
	"os/exec"
)

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

func ownedByMe(os.FileInfo) bool { return true }

// linksSend: the command agents message linked groups with is sh, which a
// Windows node's agents do not have; they can still receive.
const linksSend = false

const nonBlock = 0

func fileKey(fi os.FileInfo) string {
	return fmt.Sprintf("%s/%d/%d", fi.Name(), fi.Size(), fi.ModTime().UnixNano())
}
