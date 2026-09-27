//go:build windows

package selfupdate

import (
	"os"
	"os/exec"
)

// Restart starts the updated binary with the same arguments in this console,
// then exits (Windows cannot replace a running process image).
func Restart(executable string) error {
	cmd := exec.Command(executable, os.Args[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Env = os.Environ()
	if err := cmd.Start(); err != nil {
		return err
	}
	os.Exit(0)
	return nil
}
