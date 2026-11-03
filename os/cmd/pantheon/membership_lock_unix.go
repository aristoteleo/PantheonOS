//go:build !windows

package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

const membershipLockName = "node.lock"

// errMembershipBusy: another node process runs from this state directory.
type errMembershipBusy struct{ pid int }

func (e errMembershipBusy) Error() string {
	if e.pid > 0 {
		return fmt.Sprintf("another node (pid %d) already runs from this state directory", e.pid)
	}
	return "another node already runs from this state directory"
}

// lockMembership holds dir for this process: two nodes with one node id would
// share one identity. The lock is released when the process exits or execs
// into a Fleet update (the descriptor is close-on-exec), so the update
// re-acquires it. The returned release is for retargeting to another dir.
func lockMembership(dir string) (func(), error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, membershipLockName)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := unix.Flock(int(f.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		b, _ := os.ReadFile(path)
		_ = f.Close()
		if errors.Is(err, unix.EWOULDBLOCK) {
			pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
			return nil, errMembershipBusy{pid: pid}
		}
		return nil, err
	}
	_ = f.Truncate(0)
	_, _ = f.WriteAt([]byte(strconv.Itoa(os.Getpid())+"\n"), 0)
	return func() { _ = unix.Flock(int(f.Fd()), unix.LOCK_UN); _ = f.Close() }, nil
}

// takeOverMembership replaces the node running from dir with this one: the
// same fleet's install command, run again, restarts its node on the new
// release. It stops only the process holding this membership's lock.
func takeOverMembership(dir string, busy errMembershipBusy, wait time.Duration) (func(), error) {
	if busy.pid <= 0 || busy.pid == os.Getpid() {
		return nil, busy
	}
	if err := syscall.Kill(busy.pid, syscall.SIGTERM); err != nil && !errors.Is(err, syscall.ESRCH) {
		return nil, busy
	}
	deadline := time.Now().Add(wait)
	for {
		release, err := lockMembership(dir)
		var again errMembershipBusy
		if err == nil || !errors.As(err, &again) || time.Now().After(deadline) {
			return release, err
		}
		time.Sleep(200 * time.Millisecond)
	}
}
