//go:build linux

package groupnetwork

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"time"

	"golang.org/x/sys/unix"
)

type namespaceHandle struct {
	file     *os.File
	identity string
}

// OpenContainerNamespace pins an fd, not a reusable PID/path. The lifecycle
// driver must check the owned container before AND after obtaining this handle.
// Docker and Fleet must run on the same Linux host/PID namespace.
func OpenContainerNamespace(pid int) (NamespaceHandle, error) {
	if pid <= 0 {
		return nil, fmt.Errorf("missing original container PID")
	}
	f, err := os.Open(fmt.Sprintf("/proc/%d/ns/net", pid))
	if err != nil {
		return nil, err
	}
	ok := false
	defer func() {
		if !ok {
			f.Close()
		}
	}()
	var fs unix.Statfs_t
	var stat unix.Stat_t
	if err = unix.Fstatfs(int(f.Fd()), &fs); err != nil {
		return nil, err
	}
	kind, err := unix.IoctlRetInt(int(f.Fd()), unix.NS_GET_NSTYPE)
	if err != nil || fs.Type != unix.NSFS_MAGIC || kind != unix.CLONE_NEWNET {
		return nil, fmt.Errorf("container handle is not a network namespace")
	}
	if err = unix.Fstat(int(f.Fd()), &stat); err != nil {
		return nil, err
	}
	var self unix.Stat_t
	if err = unix.Stat("/proc/self/ns/net", &self); err != nil {
		return nil, err
	}
	if self.Dev == stat.Dev && self.Ino == stat.Ino {
		return nil, fmt.Errorf("refusing the Fleet host network namespace")
	}
	ok = true
	return &namespaceHandle{file: f, identity: fmt.Sprintf("%d:%d", stat.Dev, stat.Ino)}, nil
}

func (h *namespaceHandle) Identity() string { return h.identity }
func (h *namespaceHandle) Close() error     { return h.file.Close() }

// Dial opens only a numeric loopback TCP socket inside this pinned namespace.
// The temporary OS thread is restored before returning the socket; on a failed
// restoration it is retired, never returned to the Go scheduler's thread pool.
func (h *namespaceHandle) Dial(ctx context.Context, port int) (net.Conn, error) {
	if port < 1 || port > 65535 {
		return nil, fmt.Errorf("invalid declared container port")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	type result struct {
		conn net.Conn
		err  error
	}
	ready := make(chan result, 1)
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		original, err := os.Open("/proc/thread-self/ns/net")
		if err != nil {
			ready <- result{err: err}
			return
		}
		defer original.Close()
		if err = unix.Setns(int(h.file.Fd()), unix.CLONE_NEWNET); err != nil {
			ready <- result{err: err}
			return
		}
		conn, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "tcp4", fmt.Sprintf("127.0.0.1:%d", port))
		if restoreErr := unix.Setns(int(original.Fd()), unix.CLONE_NEWNET); restoreErr != nil {
			if conn != nil {
				conn.Close()
			}
			ready <- result{err: fmt.Errorf("cannot restore Fleet network namespace: %w", restoreErr)}
			// Keep a lock held through Goexit so this thread is destroyed.
			runtime.LockOSThread()
			runtime.Goexit()
		}
		ready <- result{conn: conn, err: err}
	}()
	// DialContext is bounded/cancellable. Wait for restoration even if cancelled.
	r := <-ready
	return r.conn, r.err
}

func (h *namespaceHandle) Attach(ctx context.Context, name string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if !regexp.MustCompile(`^pf-group-[a-f0-9]{24}$`).MatchString(name) {
		return fmt.Errorf("invalid node-generated namespace name")
	}
	// Stat checks a closed handle before any filesystem mutation.
	if _, err := h.file.Stat(); err != nil {
		return err
	}
	root := "/var/run/netns"
	if err := os.MkdirAll(root, 0755); err != nil {
		return err
	}
	target := filepath.Join(root, name)
	f, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_RDONLY, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	// Bind the held fd. An exited/reused container PID cannot retarget it.
	if err = unix.Mount(fmt.Sprintf("/proc/self/fd/%d", h.file.Fd()), target, "", unix.MS_BIND, ""); err != nil {
		os.Remove(target)
		return fmt.Errorf("attach original container namespace: %w", err)
	}
	return nil
}
