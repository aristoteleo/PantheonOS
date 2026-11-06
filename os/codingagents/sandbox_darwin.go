//go:build darwin

package codingagents

import (
	"context"
	"os/exec"
	"sync"
	"time"
)

// sandboxExec is macOS's sandbox-exec; a variable so tests can make it
// missing.
var sandboxExec = "/usr/bin/sandbox-exec"

var (
	sandboxOnce sync.Once
	sandboxWhy  string
)

// sandboxKind is the sandbox this computer runs turns in ("" and why when
// none): macOS's Seatbelt, checked once by running a profile.
func sandboxKind() (kind, why string) {
	sandboxOnce.Do(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := exec.CommandContext(ctx, sandboxExec, "-p", "(version 1)(allow default)", "/usr/bin/true").Run(); err != nil {
			sandboxWhy = "sandbox-exec does not run here (" + err.Error() + ")"
		}
	})
	if sandboxWhy != "" {
		return "", sandboxWhy
	}
	return "seatbelt", ""
}

// Wrap is the command that runs program with args in the sandbox.
func (s Sandbox) Wrap(args []string) (string, []string, error) {
	if kind, _ := SandboxKind(); kind == "" {
		return "", nil, ErrNoSandbox
	}
	return sandboxExec, append([]string{"-p", s.SeatbeltProfile(), s.Program}, args...), nil
}
