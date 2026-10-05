package main

import (
	"errors"
	"os"
	"strconv"
	"strings"
	"syscall"
)

// A local product can run beside the user's existing Fleet or another profile.
// Never fall back to a process-name signal when its private broker is missing.
func reloadOwnedNATS(path string) error {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 32 {
		return errors.New("owned NATS PID is unavailable")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil || pid <= 1 || pid == os.Getpid() {
		return errors.New("invalid owned NATS PID")
	}
	process, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	return process.Signal(syscall.Signal(1)) // SIGHUP on supported POSIX local profiles
}
