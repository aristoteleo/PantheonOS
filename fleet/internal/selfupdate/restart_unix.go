//go:build !windows

package selfupdate

import (
	"os"
	"syscall"
)

// Restart replaces this process with the updated binary, keeping its pid and
// environment (a launcher waiting on the process keeps waiting). argv is the
// full argument vector, argv[0] included.
func Restart(executable string, argv []string) error {
	return syscall.Exec(executable, argv, os.Environ())
}
