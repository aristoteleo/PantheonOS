//go:build windows

package selfupdate

import (
	"os"
	"os/exec"
)

// Restart starts the updated binary in this console, then exits (Windows
// cannot replace a running process image). argv[0] is ignored.
func Restart(executable string, argv []string) error {
	cmd := exec.Command(executable, argv[1:]...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	cmd.Env = os.Environ()
	if err := cmd.Start(); err != nil {
		return err
	}
	os.Exit(0)
	return nil
}
