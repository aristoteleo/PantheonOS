//go:build linux

package codingagents

import (
	"context"
	"os/exec"
	"strings"
	"sync"
	"time"
)

var (
	sandboxOnce sync.Once
	bwrapPath   string
	sandboxWhy  string
)

// sandboxKind is the sandbox this computer runs turns in ("" and why when
// none): bubblewrap, when it is installed and can make its namespaces here
// (checked once). Landlock is not used: without bubblewrap a shared turn is
// refused.
func sandboxKind() (kind, why string) {
	sandboxOnce.Do(func() {
		path, err := exec.LookPath("bwrap")
		if err != nil {
			sandboxWhy = "bubblewrap (bwrap) is not installed"
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if out, err := exec.CommandContext(ctx, path, "--ro-bind", "/", "/", "--unshare-pid", "--unshare-ipc",
			"--die-with-parent", "--", "/bin/true").CombinedOutput(); err != nil {
			sandboxWhy = "bubblewrap cannot run here (" + strings.TrimSpace(string(out)+" "+err.Error()) + ")"
			return
		}
		bwrapPath = path
	})
	if bwrapPath == "" {
		return "", sandboxWhy
	}
	return "bubblewrap", ""
}

// sandboxLinuxReads are the system folders a sandboxed turn sees (read
// only) on Linux.
var sandboxLinuxReads = []string{"/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/etc", "/opt",
	"/run/systemd/resolve", "/nix/store"}

// Wrap is the command that runs program with args in the sandbox: a new
// mount namespace that holds only the system, the runtime's install, the
// agent's folder and home, a fresh /tmp, /proc of its own process
// namespace (so no other process can be signalled, traced or seen) and no
// IPC with the rest. The network is the computer's (bubblewrap cannot keep
// loopback ports apart from the internet).
func (s Sandbox) Wrap(args []string) (string, []string, error) {
	if kind, _ := SandboxKind(); kind == "" {
		return "", nil, ErrNoSandbox
	}
	a := []string{"--die-with-parent", "--new-session", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
		"--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"}
	for _, p := range sandboxLinuxReads {
		a = append(a, "--ro-bind-try", p, p)
	}
	for _, p := range s.Reads {
		a = append(a, "--ro-bind-try", p, p)
	}
	a = append(a, "--bind", s.Folder, s.Folder, "--bind", s.Dir, s.Dir)
	if s.TeamDir != "" {
		a = append(a, "--ro-bind", s.TeamDir, s.TeamDir)
		if s.Inbox != "" {
			a = append(a, "--bind", s.Inbox, s.Inbox)
		}
	}
	a = append(a, "--chdir", s.Folder, "--", s.Program)
	return bwrapPath, append(a, args...), nil
}
