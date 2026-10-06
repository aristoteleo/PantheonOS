//go:build !windows

package profilelock

import (
	"fmt"

	"golang.org/x/sys/unix"
)

func protect(fd int) error {
	var info unix.Stat_t
	if err := unix.Fstat(fd, &info); err != nil || info.Mode&unix.S_IFMT != unix.S_IFREG {
		return fmt.Errorf("inherited local profile lock must be an open regular file")
	}
	flags, err := unix.FcntlInt(uintptr(fd), unix.F_GETFD, 0)
	if err != nil {
		return fmt.Errorf("inspect inherited local profile lock: %w", err)
	}
	_, err = unix.FcntlInt(uintptr(fd), unix.F_SETFD, flags|unix.FD_CLOEXEC)
	return err
}
