//go:build !windows

package selfupdate

import (
	"os"
	"syscall"
)

// Restart replaces this process with the updated binary, keeping its pid,
// arguments and environment (a launcher waiting on the process keeps waiting).
func Restart(executable string) error {
	return syscall.Exec(executable, os.Args, os.Environ())
}
